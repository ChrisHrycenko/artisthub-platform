"""
routes/ai.py

ArtistHub AI Blueprint — Phase 9A: Read-Only RAG Assistant.

API Contract
------------

POST /api/ai/ask

Request body (JSON):
    {
        "question": "<string, 3-1000 chars>"
    }

Success response (200):
    {
        "status": "success",
        "data": {
            "answer":           "<string>",
            "grounding_status": "grounded" | "insufficient_evidence",
            "sources":          [
                {
                    "source_org":      "<string>",
                    "document_title":  "<string>",
                    "source_url":      "<string>",
                    "topic":           "<string>",
                    "authority":       "<string>"
                },
                ...
            ],
            "requires_professional_review": <bool>,
            "model_id":         "<string>",
            "correlation_id":   "<string>",
            "prompt_template_version": "9a-v1"
        }
    }

Error responses:
    400  — validation error (missing/malformed request body)
    503  — watsonx.ai upstream error or credentials not configured

Phase 9A guarantees
--------------------
- READ-ONLY: this route NEVER writes to any creator-domain table
  (Artist, Fan, MusicRelease, SocialPost, MerchProduct, Follow, Order).
- It NEVER reads private creator documents.
- It NEVER forwards raw question text to the Kafka event payload
  (only question_length is emitted — PII-safe).
- It uses the Transactional Outbox pattern for ai.question.received
  and ai.answer.generated events, consistent with all other events.
- Correlation ID is propagated from flask.g.correlation_id (set by
  the before_request middleware in create_app()).

Safety guards
-------------
Professional-review keywords trigger a disclaimer in the response.
The system instruction in rag_retrieval.build_prompt() instructs the
model not to give individualised legal/tax/financial advice.

Grounding pipeline:
    question
    → marshmallow validate
    → RAG retrieve (global knowledge corpus)
    → if no evidence: return insufficient_evidence fallback
    → build_prompt (system instruction + evidence + question)
    → watsonx.ai generate
    → attach provenance / professional-review flag
    → outbox events
    → success response

Observability
-------------
All structured log records contain the correlation_id.
Prometheus metrics (counter labels: grounding_status) are added via
the Flask route — counter labels are low-cardinality.
"""

import logging
import time
from typing import Optional

from flask import Blueprint, g, request
from marshmallow import ValidationError

from app.extensions import db
from app.models.outbox import OutboxEvent  # noqa: F401 — used indirectly
from app.schemas.ai import AiAskSchema
from app.services.event_factory import (
    build_ai_question_received,
    build_ai_answer_generated,
)
from app.services.rag_retrieval import retrieve, build_prompt
from app.services.watsonx_client import (
    get_watsonx_client,
    WatsonxError,
    WatsonxUnavailableError,
)
from app.utils.responses import success, error

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__)

_ask_schema = AiAskSchema()

# ------------------------------------------------------------------ #
# Prompt template version — bump when the system instruction changes   #
# ------------------------------------------------------------------ #
PROMPT_TEMPLATE_VERSION = "9a-v1"

# ------------------------------------------------------------------ #
# Professional-review indicator keywords                               #
# Answers touching these topics include the professional-review flag.  #
# ------------------------------------------------------------------ #
_PROFESSIONAL_REVIEW_KEYWORDS = [
    "contract", "agreement", "lawsuit", "sue", "litigation",
    "tax", "income", "deduction", "accounting", "audit",
    "legal advice", "attorney", "lawyer", "liable", "liability",
    "specific to my", "in my situation", "my case",
    "investment", "securities", "financial advice",
]

# ------------------------------------------------------------------ #
# Insufficient-evidence fallback text                                  #
# ------------------------------------------------------------------ #
_INSUFFICIENT_EVIDENCE_TEXT = (
    "I don't have enough sourced information in my current knowledge base "
    "to answer that question reliably. I can help with music-business topics "
    "such as royalties, performing rights organisations (PROs), mechanical "
    "royalties, SoundExchange, master vs. composition rights, ISRC/ISWC "
    "identifiers, copyright basics, and release preparation. "
    "For questions outside these areas, please consult a qualified professional "
    "such as a music attorney or entertainment accountant."
)


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _correlation_id() -> Optional[str]:
    """Return the correlation ID from flask.g, or None if unavailable."""
    return getattr(g, "correlation_id", None)


def _session_type() -> str:
    """
    Return a safe string describing the current session type.

    Returns 'artist', 'fan', or 'anonymous' — never leaks personal data.
    """
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            from app.models.artist import Artist
            if isinstance(current_user._get_current_object(), Artist):
                return "artist"
            return "fan"
    except Exception:  # pragma: no cover
        pass
    return "anonymous"


def _requires_professional_review(text: str) -> bool:
    """
    Return True if the answer text touches professional-review territory.

    Uses a keyword scan so the flag is never based on the question itself
    (which might be high-cardinality / PII-adjacent).
    """
    lower = text.lower()
    return any(kw in lower for kw in _PROFESSIONAL_REVIEW_KEYWORDS)


def _emit_outbox_events(
    question_length: int,
    session_type: str,
    grounding_status: str,
    sources_count: int,
    model_id: str,
    latency_ms: int,
    requires_review: bool,
    corr_id: Optional[str],
) -> None:
    """
    Write ai.question.received + ai.answer.generated to the outbox.

    Both events are written in a single database commit inside the request
    context.  The outbox relay delivers them to Kafka asynchronously.
    No domain table is modified — only the event_outbox table is written.

    Failures here are logged but do NOT affect the HTTP response: the
    creator receives their answer even if the outbox write fails.
    """
    try:
        q_event = build_ai_question_received(
            question_length=question_length,
            session_type=session_type,
            correlation_id=corr_id,
        )
        a_event = build_ai_answer_generated(
            grounding_status=grounding_status,
            sources_count=sources_count,
            model_id=model_id,
            latency_ms=latency_ms,
            requires_professional_review=requires_review,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            correlation_id=corr_id,
        )
        db.session.add(q_event)
        db.session.add(a_event)
        db.session.commit()
        logger.info(
            "AI outbox events committed | corr=%s grounding=%s",
            corr_id, grounding_status,
        )
    except Exception as exc:
        db.session.rollback()
        logger.error(
            "AI outbox event write failed | corr=%s err=%s",
            corr_id, exc,
        )


# ------------------------------------------------------------------ #
# POST /api/ai/ask                                                     #
# ------------------------------------------------------------------ #

@ai_bp.post("/ai/ask")
def ask():
    """
    POST /api/ai/ask

    Accept a creator's music-business question and return an
    educationally grounded answer with source citations.

    Phase 9A: global knowledge corpus only; no creator-domain writes;
    no private documents.

    Request body:
        { "question": "<string>" }

    Response:
        { "status": "success", "data": { ... } }
        or
        { "status": "error",   "error": "..." }
    """
    corr_id = _correlation_id()
    start_time = time.monotonic()

    # ---------------------------------------------------------------- #
    # 1. Validate request body                                          #
    # ---------------------------------------------------------------- #
    body = request.get_json(silent=True) or {}
    try:
        validated = _ask_schema.load(body)
    except ValidationError as ve:
        first_msg = next(iter(ve.messages.values()))
        msg = first_msg[0] if isinstance(first_msg, list) else str(first_msg)
        logger.info(
            "AI ask validation error | corr=%s err=%s", corr_id, msg
        )
        return error(msg, 400)

    question: str = validated["question"]
    session_type: str = _session_type()

    logger.info(
        "AI ask received | corr=%s question_len=%d session=%s",
        corr_id, len(question), session_type,
    )

    # ---------------------------------------------------------------- #
    # 2. Retrieve authoritative evidence                                #
    # ---------------------------------------------------------------- #
    evidence = retrieve(question=question, correlation_id=corr_id)

    if not evidence:
        # No passages met the minimum relevance threshold.
        latency_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "AI ask: insufficient evidence | corr=%s latency_ms=%d",
            corr_id, latency_ms,
        )
        _emit_outbox_events(
            question_length=len(question),
            session_type=session_type,
            grounding_status="insufficient_evidence",
            sources_count=0,
            model_id="n/a",
            latency_ms=latency_ms,
            requires_review=False,
            corr_id=corr_id,
        )
        return success({
            "answer": _INSUFFICIENT_EVIDENCE_TEXT,
            "grounding_status": "insufficient_evidence",
            "sources": [],
            "requires_professional_review": False,
            "model_id": "n/a",
            "correlation_id": corr_id,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        }, 200)

    # ---------------------------------------------------------------- #
    # 3. Build prompt and call watsonx.ai                               #
    # ---------------------------------------------------------------- #
    prompt = build_prompt(question=question, evidence=evidence)

    try:
        client = get_watsonx_client()
        result = client.generate(
            prompt=prompt,
            correlation_id=corr_id,
        )
    except WatsonxUnavailableError as exc:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(
            "watsonx client unavailable | corr=%s err=%s",
            corr_id, exc,
        )
        _emit_outbox_events(
            question_length=len(question),
            session_type=session_type,
            grounding_status="upstream_error",
            sources_count=len(evidence),
            model_id="unavailable",
            latency_ms=latency_ms,
            requires_review=False,
            corr_id=corr_id,
        )
        return error(
            "ArtistHub AI is not available at this time. "
            "Please try again later.",
            503,
        )
    except WatsonxError as exc:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(
            "watsonx generation error | corr=%s err=%s",
            corr_id, exc,
        )
        _emit_outbox_events(
            question_length=len(question),
            session_type=session_type,
            grounding_status="upstream_error",
            sources_count=len(evidence),
            model_id="error",
            latency_ms=latency_ms,
            requires_review=False,
            corr_id=corr_id,
        )
        return error(
            "ArtistHub AI encountered an error generating your answer. "
            "Please try again.",
            503,
        )

    # ---------------------------------------------------------------- #
    # 4. Post-process: professional-review flag, citations              #
    # ---------------------------------------------------------------- #
    answer_text: str = result.generated_text.strip()
    model_id: str = result.model_id
    needs_review: bool = _requires_professional_review(answer_text)
    sources = [r.to_citation_dict() for r in evidence]

    latency_ms = int((time.monotonic() - start_time) * 1000)

    logger.info(
        "AI ask complete | corr=%s grounding=grounded sources=%d "
        "model=%s latency_ms=%d review=%s",
        corr_id, len(sources), model_id, latency_ms, needs_review,
    )

    # ---------------------------------------------------------------- #
    # 5. Emit outbox events (non-blocking: failures are logged only)    #
    # ---------------------------------------------------------------- #
    _emit_outbox_events(
        question_length=len(question),
        session_type=session_type,
        grounding_status="grounded",
        sources_count=len(sources),
        model_id=model_id,
        latency_ms=latency_ms,
        requires_review=needs_review,
        corr_id=corr_id,
    )

    # ---------------------------------------------------------------- #
    # 6. Return response                                                 #
    # ---------------------------------------------------------------- #
    return success({
        "answer": answer_text,
        "grounding_status": "grounded",
        "sources": sources,
        "requires_professional_review": needs_review,
        "model_id": model_id,
        "correlation_id": corr_id,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
    }, 200)
