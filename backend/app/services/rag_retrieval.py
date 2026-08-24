"""
services/rag_retrieval.py

Phase 9A RAG (Retrieval-Augmented Generation) retrieval layer.

This module is responsible for retrieving relevant authoritative passages
from the global knowledge corpus given a creator's question.  It returns
structured evidence — not raw text — so the caller can attach provenance
metadata to every generated answer.

Phase 9A implementation
-----------------------
Uses a lightweight keyword/BM25-style retrieval against the in-process
knowledge corpus defined in knowledge_corpus.py.  This is intentionally
simple: Phase 9A validates the end-to-end RAG pipeline without requiring a
live vector store.  Phase 9B will replace this with an IBM watsonx.data
vector index backed by IBM embedding models, using the same RetrievalResult
interface.

Privacy / IP boundary
---------------------
Phase 9A retrieval is GLOBAL KNOWLEDGE ONLY.  This module:
  - NEVER accepts a creator_id scoping argument.
  - NEVER queries private creator documents.
  - NEVER returns passages that are not from the global corpus.

These constraints are enforced architecturally (no private document store
exists in this module) and by the missing creator_id parameter.

Usage:
    from app.services.rag_retrieval import retrieve, RetrievalResult

    results = retrieve(question="How do mechanical royalties work?")
    for r in results:
        print(r.text, r.source_url)
"""

import logging
import math
import re
from typing import List, Optional

from app.services.knowledge_corpus import get_corpus, KnowledgePassage

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Configuration                                                        #
# ------------------------------------------------------------------ #

#: Maximum number of passages returned per query.
MAX_RESULTS = 4

#: Minimum relevance score (0–1) for a passage to be included.
MIN_SCORE_THRESHOLD = 0.05


# ------------------------------------------------------------------ #
# RetrievalResult value object                                         #
# ------------------------------------------------------------------ #

class RetrievalResult:
    """
    A single retrieved knowledge passage with provenance metadata.

    Attributes:
        passage_id:     Corpus-internal identifier for the passage.
        text:           The passage text (educational content).
        source_org:     Authoritative organisation that produced the content.
        document_title: Title of the source document.
        source_url:     Canonical public URL for the source.
        topic:          Domain topic tag.
        authority:      Authority tier ("primary_source" / "government" /
                        "platform_doc").
        score:          Retrieval relevance score (0–1, higher = more relevant).
    """

    __slots__ = (
        "passage_id",
        "text",
        "source_org",
        "document_title",
        "source_url",
        "topic",
        "authority",
        "score",
    )

    def __init__(
        self,
        passage_id: str,
        text: str,
        source_org: str,
        document_title: str,
        source_url: str,
        topic: str,
        authority: str,
        score: float,
    ) -> None:
        """Initialise a RetrievalResult."""
        self.passage_id = passage_id
        self.text = text
        self.source_org = source_org
        self.document_title = document_title
        self.source_url = source_url
        self.topic = topic
        self.authority = authority
        self.score = score

    def to_citation_dict(self) -> dict:
        """
        Return a citation dict suitable for the API response.

        The frontend and governance audit layer use this representation.
        Internal fields (score, text) are intentionally excluded from the
        citation dict to keep the public API surface minimal.
        """
        return {
            "source_org": self.source_org,
            "document_title": self.document_title,
            "source_url": self.source_url,
            "topic": self.topic,
            "authority": self.authority,
        }

    def __repr__(self) -> str:
        return (
            f"<RetrievalResult id={self.passage_id!r} "
            f"score={self.score:.3f} org={self.source_org!r}>"
        )


# ------------------------------------------------------------------ #
# Retrieval engine                                                     #
# ------------------------------------------------------------------ #

def _tokenise(text: str) -> List[str]:
    """
    Tokenise text to lowercase alphabetic words for keyword matching.

    Strips punctuation, lowercases, and splits on whitespace.
    """
    return re.findall(r"[a-z]+", text.lower())


def _bm25_score(
    query_tokens: List[str],
    passage: KnowledgePassage,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """
    Compute a simplified BM25 relevance score between a query and a passage.

    Searches the passage text AND the keywords list for query term matches,
    giving the keywords extra weight (they are curated synonyms and acronyms).

    Args:
        query_tokens:  Tokenised query words.
        passage:       Corpus passage dict.
        avg_doc_len:   Average document length across the corpus (for normalisation).
        k1:            BM25 term saturation parameter.
        b:             BM25 length normalisation parameter.

    Returns:
        Float relevance score (higher = more relevant).
    """
    # Combine passage text tokens with keyword tokens (keywords weighted ×3).
    text_tokens = _tokenise(passage["text"])
    keyword_tokens = _tokenise(" ".join(passage.get("keywords", [])))
    # Duplicate keywords to give them higher effective term frequency.
    doc_tokens = text_tokens + keyword_tokens * 3

    doc_len = len(doc_tokens)
    # Term frequency map for this passage.
    tf: dict = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1

    score = 0.0
    for token in set(query_tokens):
        if token not in tf:
            continue
        f = tf[token]
        idf = math.log(1 + 1)  # Simplified: corpus is small, use constant IDF.
        numerator = f * (k1 + 1)
        denominator = f + k1 * (1 - b + b * doc_len / max(avg_doc_len, 1))
        score += idf * (numerator / denominator)

    return score


def retrieve(
    question: str,
    max_results: int = MAX_RESULTS,
    min_score: float = MIN_SCORE_THRESHOLD,
    correlation_id: Optional[str] = None,
) -> List[RetrievalResult]:
    """
    Retrieve the most relevant knowledge passages for a given question.

    Phase 9A: global knowledge corpus only.  No private creator documents.

    Args:
        question:       The creator's natural-language question.
        max_results:    Maximum number of passages to return (default 4).
        min_score:      Minimum relevance score threshold (default 0.05).
        correlation_id: Optional trace ID for logging.

    Returns:
        List of RetrievalResult, sorted descending by relevance score.
        Empty list if no passages meet the minimum score threshold.
    """
    corpus = get_corpus()
    if not corpus:
        logger.warning(
            "Knowledge corpus is empty | corr=%s", correlation_id
        )
        return []

    query_tokens = _tokenise(question)
    if not query_tokens:
        return []

    # Compute average document length once per call.
    avg_doc_len = sum(
        len(_tokenise(p["text"]) + _tokenise(" ".join(p.get("keywords", []))) * 3)
        for p in corpus
    ) / len(corpus)

    scored: List[tuple] = []
    for passage in corpus:
        score = _bm25_score(query_tokens, passage, avg_doc_len)
        if score >= min_score:
            scored.append((score, passage))

    # Sort descending by score.
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_results]

    results = [
        RetrievalResult(
            passage_id=p["id"],
            text=p["text"],
            source_org=p["source_org"],
            document_title=p["document_title"],
            source_url=p["source_url"],
            topic=p["topic"],
            authority=p["authority"],
            score=score,
        )
        for score, p in top
    ]

    logger.info(
        "RAG retrieval complete | question_len=%d results=%d "
        "top_score=%.3f corr=%s",
        len(question),
        len(results),
        results[0].score if results else 0.0,
        correlation_id,
    )
    return results


# ------------------------------------------------------------------ #
# Prompt assembly                                                      #
# ------------------------------------------------------------------ #

_SYSTEM_INSTRUCTION = (
    "You are ArtistHub AI, an educational assistant for independent musicians. "
    "Answer the user's music-business question using ONLY the provided source "
    "passages. If the passages do not contain sufficient information to answer "
    "the question, say that you do not have enough sourced information to answer "
    "reliably. "
    "Do NOT invent sources, royalty rates, organisation names, or legal facts. "
    "Do NOT give definitive individualised legal, tax, accounting, or financial "
    "advice. When a question requires professional judgement, provide useful "
    "educational context and recommend the user consult a qualified professional. "
    "Do NOT modify any creator data, releases, posts, or accounts."
)


def build_prompt(
    question: str,
    evidence: List[RetrievalResult],
) -> str:
    """
    Assemble the final watsonx.ai prompt from retrieved evidence and the question.

    Structure:
        [SYSTEM]
        <system instruction>

        [SOURCES]
        SOURCE 1: <org> — <title>
        <passage text>
        ---
        ...

        [QUESTION]
        <creator question>

        [ANSWER]

    Args:
        question: The creator's question.
        evidence: Retrieved passages from the global knowledge corpus.

    Returns:
        Assembled prompt string ready to submit to watsonx.ai.
    """
    parts = [
        "[SYSTEM]",
        _SYSTEM_INSTRUCTION,
        "",
    ]

    if evidence:
        parts.append("[SOURCES]")
        for i, r in enumerate(evidence, 1):
            parts.append(
                f"SOURCE {i} [{r.source_org} — {r.document_title}]"
            )
            parts.append(r.text)
            parts.append("---")
        parts.append("")

    parts += [
        "[QUESTION]",
        question,
        "",
        "[ANSWER]",
    ]

    return "\n".join(parts)
