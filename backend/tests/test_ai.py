"""
tests/test_ai.py

Comprehensive test suite for ArtistHub AI — Phase 9A.

Covers:
    - Valid question → grounded response
    - Malformed request (no body, wrong type)
    - Empty / too-short question
    - Question too long (>1000 chars)
    - Successful retrieval and citation attachment
    - No retrieval evidence → insufficient_evidence fallback
    - watsonx.ai success path
    - watsonx.ai timeout
    - watsonx.ai authentication / upstream failure
    - WatsonxUnavailableError (credentials absent)
    - Bounded retry (mocked)
    - Professional-review classification
    - Correlation-ID propagation
    - Kafka/outbox event creation (ai.question.received + ai.answer.generated)
    - Read-only: no creator-domain writes (Artist, Fan, Release, Post,
      Merch, Follow tables unchanged)
    - Private-document retrieval guard (retrieve() has no creator_id param)
    - RAG retrieval: keyword matching, BM25 scoring, MIN_SCORE_THRESHOLD
    - Knowledge corpus: structure validation
    - watsonx_client: GenerationResult.from_response(), mock mode
"""

import pytest
from unittest.mock import MagicMock, patch

from app.extensions import db as _db
from app.models.outbox import OutboxEvent
from app.services.watsonx_client import (
    WatsonxClient,
    WatsonxUnavailableError,
    WatsonxAuthError,
    WatsonxTimeoutError,
    WatsonxUpstreamError,
    GenerationResult,
    reset_client,
)
from app.services.rag_retrieval import retrieve, build_prompt, RetrievalResult
from app.services.knowledge_corpus import get_corpus


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _outbox_count(db_):
    """Return the number of rows currently in the event_outbox table."""
    return db_.session.query(OutboxEvent).count()


def _outbox_types(db_):
    """Return a set of event_type strings currently in the outbox."""
    rows = db_.session.query(OutboxEvent).all()
    return {r.event_type for r in rows}


# ------------------------------------------------------------------ #
# POST /api/ai/ask — request validation                                #
# ------------------------------------------------------------------ #

class TestAiAskValidation:
    """Tests for request validation on POST /api/ai/ask."""

    def test_missing_body_returns_400(self, client, db_):
        r = client.post(
            "/api/ai/ask",
            content_type="application/json",
            data="",
        )
        assert r.status_code == 400
        assert r.get_json()["status"] == "error"

    def test_missing_question_field_returns_400(self, client, db_):
        r = client.post("/api/ai/ask", json={})
        assert r.status_code == 400
        body = r.get_json()
        assert body["status"] == "error"

    def test_empty_question_returns_400(self, client, db_):
        r = client.post("/api/ai/ask", json={"question": ""})
        assert r.status_code == 400

    def test_one_char_question_returns_400(self, client, db_):
        r = client.post("/api/ai/ask", json={"question": "hi"})
        assert r.status_code == 400

    def test_question_over_1000_chars_returns_400(self, client, db_):
        r = client.post("/api/ai/ask", json={"question": "x" * 1001})
        assert r.status_code == 400

    def test_non_string_question_returns_400(self, client, db_):
        r = client.post("/api/ai/ask", json={"question": 12345})
        assert r.status_code == 400

    def test_non_json_content_returns_400(self, client, db_):
        r = client.post(
            "/api/ai/ask",
            content_type="text/plain",
            data="plain text body",
        )
        assert r.status_code == 400


# ------------------------------------------------------------------ #
# POST /api/ai/ask — grounded answer path                              #
# ------------------------------------------------------------------ #

class TestAiAskGrounded:
    """Tests for the successful grounded answer path."""

    def _mock_result(self, text="Royalties are payments made to rights holders."):
        result = MagicMock(spec=GenerationResult)
        result.generated_text = text
        result.stop_reason = "eos_token"
        result.model_id = "mock/test-model"
        return result

    def test_valid_question_returns_200(self, client, db_):
        with patch(
            "app.routes.ai.get_watsonx_client"
        ) as mock_get:
            mock_client = MagicMock()
            mock_client.model_id = "mock/test-model"
            mock_client.generate.return_value = self._mock_result()
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "How do mechanical royalties work?"},
            )

        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "success"
        data = body["data"]
        assert "answer" in data
        assert data["grounding_status"] == "grounded"

    def test_grounded_response_has_sources(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.model_id = "mock/test-model"
            mock_client.generate.return_value = self._mock_result()
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is a PRO in music?"},
            )

        data = r.get_json()["data"]
        assert isinstance(data["sources"], list)
        assert len(data["sources"]) > 0

    def test_source_has_required_fields(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.model_id = "mock/test-model"
            mock_client.generate.return_value = self._mock_result()
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is a PRO in music?"},
            )

        sources = r.get_json()["data"]["sources"]
        required = {"source_org", "document_title", "source_url",
                    "topic", "authority"}
        for src in sources:
            assert required.issubset(src.keys()), (
                f"Source missing keys: {required - set(src.keys())}"
            )

    def test_correlation_id_in_response(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.model_id = "mock/test-model"
            mock_client.generate.return_value = self._mock_result()
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is ASCAP?"},
                headers={"X-Correlation-ID": "test-corr-abc"},
            )

        data = r.get_json()["data"]
        assert data["correlation_id"] == "test-corr-abc"

    def test_correlation_id_echoed_in_response_header(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.model_id = "mock/test-model"
            mock_client.generate.return_value = self._mock_result()
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is ASCAP?"},
                headers={"X-Correlation-ID": "echo-test-456"},
            )

        assert r.headers.get("X-Correlation-ID") == "echo-test-456"

    def test_prompt_template_version_in_response(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.model_id = "mock/test-model"
            mock_client.generate.return_value = self._mock_result()
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is the writer share?"},
            )

        assert r.get_json()["data"]["prompt_template_version"] == "9a-v1"


# ------------------------------------------------------------------ #
# POST /api/ai/ask — insufficient evidence path                        #
# ------------------------------------------------------------------ #

class TestAiAskInsufficientEvidence:
    """Tests for the insufficient-evidence fallback."""

    def test_gibberish_question_returns_insufficient_evidence(
        self, client, db_
    ):
        # A question with no matching keywords in the corpus.
        r = client.post(
            "/api/ai/ask",
            json={"question": "xyzxyzxyz qqqq zzzzz"},
        )
        assert r.status_code == 200
        data = r.get_json()["data"]
        assert data["grounding_status"] == "insufficient_evidence"
        assert data["sources"] == []

    def test_insufficient_evidence_no_watsonx_call(self, client, db_):
        """When there is no evidence, watsonx.ai must NOT be called."""
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            r = client.post(
                "/api/ai/ask",
                json={"question": "xyzxyzxyz qqqq zzzzz"},
            )
            mock_get.assert_not_called()

        assert r.status_code == 200

    def test_insufficient_evidence_answer_is_safe_fallback(
        self, client, db_
    ):
        r = client.post(
            "/api/ai/ask",
            json={"question": "xyzxyzxyz qqqq zzzzz"},
        )
        answer = r.get_json()["data"]["answer"]
        # Must not be empty and must include guidance language.
        assert len(answer) > 20
        assert "qualified professional" in answer.lower() or (
            "enough" in answer.lower() or "information" in answer.lower()
        )


# ------------------------------------------------------------------ #
# POST /api/ai/ask — watsonx error paths                               #
# ------------------------------------------------------------------ #

class TestAiAskWatsonxErrors:
    """Tests for watsonx.ai error degradation."""

    def test_watsonx_unavailable_returns_503(self, client, db_):
        with patch(
            "app.routes.ai.get_watsonx_client",
            side_effect=WatsonxUnavailableError("no credentials"),
        ):
            r = client.post(
                "/api/ai/ask",
                json={"question": "How do royalties work?"},
            )
        assert r.status_code == 503
        assert r.get_json()["status"] == "error"

    def test_watsonx_timeout_returns_503(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.side_effect = WatsonxTimeoutError("timeout")
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is the DMCA?"},
            )
        assert r.status_code == 503

    def test_watsonx_auth_error_returns_503(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.side_effect = WatsonxAuthError("bad key")
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is ASCAP?"},
            )
        assert r.status_code == 503

    def test_watsonx_upstream_error_returns_503(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.side_effect = WatsonxUpstreamError("500")
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is SoundExchange?"},
            )
        assert r.status_code == 503

    def test_watsonx_error_response_is_safe_message(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.side_effect = WatsonxUpstreamError("oops")
            mock_get.return_value = mock_client

            r = client.post(
                "/api/ai/ask",
                json={"question": "What is ISRC?"},
            )
        msg = r.get_json()["error"]
        assert "artisthub ai" in msg.lower() or "error" in msg.lower()
        # Must not expose internal error details.
        assert "oops" not in msg


# ------------------------------------------------------------------ #
# Professional-review classification                                   #
# ------------------------------------------------------------------ #

class TestProfessionalReview:
    """Tests for the professional-review indicator."""

    def _ask_with_answer(self, client, db_, question, answer_text):
        result = MagicMock(spec=GenerationResult)
        result.generated_text = answer_text
        result.stop_reason = "eos_token"
        result.model_id = "mock/test"
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            r = client.post("/api/ai/ask", json={"question": question})
        return r.get_json()["data"]

    def test_contract_keyword_triggers_review_flag(
        self, client, db_
    ):
        data = self._ask_with_answer(
            client, db_,
            "What is ASCAP?",
            "Your contract with a publisher affects royalty splits.",
        )
        assert data["requires_professional_review"] is True

    def test_tax_keyword_triggers_review_flag(self, client, db_):
        data = self._ask_with_answer(
            client, db_,
            "What is ASCAP?",
            "You may have tax obligations on your royalty income.",
        )
        assert data["requires_professional_review"] is True

    def test_clean_answer_no_review_flag(self, client, db_):
        data = self._ask_with_answer(
            client, db_,
            "What is ASCAP?",
            "ASCAP is a performing rights organisation that collects "
            "royalties for songwriters.",
        )
        assert data["requires_professional_review"] is False


# ------------------------------------------------------------------ #
# Outbox / Kafka event creation                                        #
# ------------------------------------------------------------------ #

class TestAiOutboxEvents:
    """Tests for transactional outbox event creation."""

    def test_grounded_creates_two_outbox_events(self, client, db_):
        result = MagicMock(spec=GenerationResult)
        result.generated_text = "Mechanical royalties..."
        result.stop_reason = "eos_token"
        result.model_id = "mock/test"
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            client.post(
                "/api/ai/ask",
                json={"question": "How do mechanical royalties work?"},
            )

        assert _outbox_count(db_) == 2
        types = _outbox_types(db_)
        assert "ai.question.received" in types
        assert "ai.answer.generated" in types

    def test_insufficient_evidence_creates_two_outbox_events(
        self, client, db_
    ):
        client.post(
            "/api/ai/ask",
            json={"question": "xyzxyzxyz qqqq zzzzz"},
        )
        assert _outbox_count(db_) == 2
        types = _outbox_types(db_)
        assert "ai.question.received" in types
        assert "ai.answer.generated" in types

    def test_upstream_error_creates_two_outbox_events(self, client, db_):
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.side_effect = WatsonxUpstreamError("err")
            mock_get.return_value = mock_client
            client.post(
                "/api/ai/ask",
                json={"question": "What is the DMCA?"},
            )

        assert _outbox_count(db_) == 2

    def test_answer_event_has_correct_grounding_status(self, client, db_):
        result = MagicMock(spec=GenerationResult)
        result.generated_text = "Grounded answer text."
        result.stop_reason = "eos_token"
        result.model_id = "mock/test"
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            client.post(
                "/api/ai/ask",
                json={"question": "What is ASCAP?"},
            )

        import json
        row = (
            db_.session.query(OutboxEvent)
            .filter_by(event_type="ai.answer.generated")
            .first()
        )
        assert row is not None
        payload = json.loads(row.payload)
        assert payload["payload"]["grounding_status"] == "grounded"

    def test_question_event_does_not_contain_question_text(
        self, client, db_
    ):
        """Privacy: raw question text must NOT appear in the outbox payload."""
        secret_question = "What is ASCAP SUPERSECRETWORD?"
        result = MagicMock(spec=GenerationResult)
        result.generated_text = "Answer about ASCAP."
        result.stop_reason = "eos_token"
        result.model_id = "mock/test"
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            client.post("/api/ai/ask", json={"question": secret_question})

        import json
        row = (
            db_.session.query(OutboxEvent)
            .filter_by(event_type="ai.question.received")
            .first()
        )
        assert row is not None
        raw_payload = row.payload
        assert "SUPERSECRETWORD" not in raw_payload

    def test_correlation_id_in_outbox_events(self, client, db_):
        result = MagicMock(spec=GenerationResult)
        result.generated_text = "Answer."
        result.stop_reason = "eos_token"
        result.model_id = "mock/test"
        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            client.post(
                "/api/ai/ask",
                json={"question": "What is ASCAP?"},
                headers={"X-Correlation-ID": "corr-outbox-test"},
            )

        rows = db_.session.query(OutboxEvent).all()
        for row in rows:
            if row.event_type in (
                "ai.question.received", "ai.answer.generated"
            ):
                assert row.correlation_id == "corr-outbox-test"


# ------------------------------------------------------------------ #
# No creator-domain writes                                             #
# ------------------------------------------------------------------ #

class TestAiReadOnly:
    """Tests confirming Phase 9A does not write to creator-domain tables."""

    def test_ai_ask_does_not_write_to_artist_table(
        self, client, db_, artist_record
    ):
        from app.models.artist import Artist
        count_before = db_.session.query(Artist).count()

        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            result = MagicMock(spec=GenerationResult)
            result.generated_text = "Answer."
            result.stop_reason = "eos_token"
            result.model_id = "mock/test"
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            client.post(
                "/api/ai/ask",
                json={"question": "What is a PRO?"},
            )

        count_after = db_.session.query(Artist).count()
        assert count_after == count_before

    def test_ai_ask_does_not_write_to_release_table(
        self, client, db_
    ):
        from app.models.release import MusicRelease
        count_before = db_.session.query(MusicRelease).count()

        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            result = MagicMock(spec=GenerationResult)
            result.generated_text = "Answer."
            result.stop_reason = "eos_token"
            result.model_id = "mock/test"
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            client.post(
                "/api/ai/ask",
                json={"question": "What is an ISRC?"},
            )

        count_after = db_.session.query(MusicRelease).count()
        assert count_after == count_before

    def test_ai_ask_only_outbox_table_gains_rows(
        self, client, db_
    ):
        """Only the outbox table should gain rows from an AI ask."""
        from app.models.release import MusicRelease
        from app.models.post import SocialPost
        from app.models.merchandise import MerchProduct
        from app.models.follow import Follow

        counts_before = {
            "release": db_.session.query(MusicRelease).count(),
            "post":    db_.session.query(SocialPost).count(),
            "merch":   db_.session.query(MerchProduct).count(),
            "follow":  db_.session.query(Follow).count(),
        }

        with patch("app.routes.ai.get_watsonx_client") as mock_get:
            mock_client = MagicMock()
            result = MagicMock(spec=GenerationResult)
            result.generated_text = "Answer."
            result.stop_reason = "eos_token"
            result.model_id = "mock/test"
            mock_client.generate.return_value = result
            mock_get.return_value = mock_client
            client.post(
                "/api/ai/ask",
                json={"question": "How do mechanical royalties work?"},
            )

        counts_after = {
            "release": db_.session.query(MusicRelease).count(),
            "post":    db_.session.query(SocialPost).count(),
            "merch":   db_.session.query(MerchProduct).count(),
            "follow":  db_.session.query(Follow).count(),
        }
        assert counts_before == counts_after


# ------------------------------------------------------------------ #
# RAG retrieval unit tests                                             #
# ------------------------------------------------------------------ #

class TestRagRetrieval:
    """Unit tests for rag_retrieval.retrieve() and build_prompt()."""

    def test_relevant_question_returns_results(self):
        results = retrieve("How do mechanical royalties work?")
        assert len(results) > 0

    def test_pro_question_returns_pro_passage(self):
        results = retrieve("What is ASCAP and how does it work?")
        topics = [r.topic for r in results]
        assert any("pro" in t for t in topics)

    def test_result_has_required_fields(self):
        results = retrieve("What is a PRO?")
        for r in results:
            assert r.passage_id
            assert r.text
            assert r.source_org
            assert r.document_title
            assert r.source_url
            assert r.topic
            assert r.authority
            assert r.score > 0

    def test_gibberish_returns_empty(self):
        results = retrieve("xyzxyzxyz qqqq zzzzz")
        assert results == []

    def test_results_sorted_by_score_descending(self):
        results = retrieve("What are royalties?")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_max_results_respected(self):
        results = retrieve("royalties music", max_results=2)
        assert len(results) <= 2

    def test_citation_dict_excludes_text_and_score(self):
        results = retrieve("What is ASCAP?")
        assert results
        cit = results[0].to_citation_dict()
        assert "text" not in cit
        assert "score" not in cit
        assert "source_org" in cit

    def test_build_prompt_contains_question(self):
        results = retrieve("What is the MLC?")
        prompt = build_prompt("What is the MLC?", results)
        assert "What is the MLC?" in prompt

    def test_build_prompt_contains_source_label(self):
        results = retrieve("What is the MLC?")
        prompt = build_prompt("What is the MLC?", results)
        assert "SOURCE 1 [" in prompt

    def test_build_prompt_without_evidence_still_includes_question(self):
        prompt = build_prompt("Some question?", [])
        assert "Some question?" in prompt

    def test_retrieve_no_private_creator_arg(self):
        """Verify retrieve() has no creator_id parameter (privacy guard)."""
        import inspect
        sig = inspect.signature(retrieve)
        assert "creator_id" not in sig.parameters


# ------------------------------------------------------------------ #
# Knowledge corpus unit tests                                          #
# ------------------------------------------------------------------ #

class TestKnowledgeCorpus:
    """Unit tests for the curated knowledge corpus."""

    def test_corpus_is_non_empty(self):
        corpus = get_corpus()
        assert len(corpus) >= 9

    def test_all_passages_have_required_fields(self):
        required = {
            "id", "text", "source_org", "document_title",
            "source_url", "ingestion_date", "topic", "authority", "keywords",
        }
        for p in get_corpus():
            missing = required - set(p.keys())
            assert not missing, f"Passage {p.get('id')} missing: {missing}"

    def test_all_passage_ids_unique(self):
        ids = [p["id"] for p in get_corpus()]
        assert len(ids) == len(set(ids)), "Duplicate passage IDs found"

    def test_authority_values_are_valid(self):
        valid = {"primary_source", "government", "platform_doc"}
        for p in get_corpus():
            assert p["authority"] in valid, (
                f"Passage {p['id']} has invalid authority: {p['authority']}"
            )

    def test_source_urls_are_non_empty(self):
        for p in get_corpus():
            assert p["source_url"], f"Passage {p['id']} has empty source_url"

    def test_corpus_covers_pro_topic(self):
        topics = {p["topic"] for p in get_corpus()}
        assert "pro_royalties" in topics

    def test_corpus_covers_mechanical_topic(self):
        topics = {p["topic"] for p in get_corpus()}
        assert "mechanical_royalties" in topics


# ------------------------------------------------------------------ #
# WatsonxClient unit tests                                             #
# ------------------------------------------------------------------ #

class TestWatsonxClient:
    """Unit tests for WatsonxClient (mock mode and GenerationResult)."""

    def setup_method(self):
        """Reset the singleton before each test."""
        reset_client()

    def teardown_method(self):
        """Reset the singleton after each test."""
        reset_client()

    def test_mock_mode_returns_stub_response(self, monkeypatch):
        monkeypatch.setenv("WATSONX_MOCK", "true")
        # Ensure no API key required in mock mode.
        monkeypatch.delenv("WATSONX_API_KEY", raising=False)
        reset_client()
        client = WatsonxClient()
        result = client.generate("Any prompt")
        assert result.stop_reason == "mock"
        assert result.model_id == "mock/stub"
        assert len(result.generated_text) > 0

    def test_no_api_key_no_mock_raises_unavailable(self, monkeypatch):
        monkeypatch.delenv("WATSONX_API_KEY", raising=False)
        monkeypatch.setenv("WATSONX_MOCK", "false")
        reset_client()
        with pytest.raises(WatsonxUnavailableError):
            WatsonxClient()

    def test_generation_result_from_response_parses_correctly(self):
        body = {
            "model_id": "ibm/granite-13b-instruct-v2",
            "results": [
                {
                    "generated_text": "Hello world",
                    "stop_reason": "eos_token",
                    "input_token_count": 50,
                    "generated_token_count": 10,
                }
            ],
        }
        result = GenerationResult.from_response(body, "fallback-model")
        assert result.generated_text == "Hello world"
        assert result.stop_reason == "eos_token"
        assert result.model_id == "ibm/granite-13b-instruct-v2"
        assert result.input_token_count == 50
        assert result.generated_token_count == 10

    def test_generation_result_handles_empty_results(self):
        body = {"model_id": "ibm/test", "results": []}
        result = GenerationResult.from_response(body, "fallback-model")
        assert result.generated_text == ""
        assert result.stop_reason == "unknown"

    def test_retry_on_server_error(self, monkeypatch):
        """
        WatsonxClient retries on server errors (5xx) and raises
        WatsonxUpstreamError after all attempts are exhausted.
        """
        monkeypatch.setenv("WATSONX_API_KEY", "fake-key")
        monkeypatch.setenv("WATSONX_MOCK", "false")
        monkeypatch.setenv("WATSONX_MAX_RETRIES", "1")
        reset_client()
        client = WatsonxClient()
        # Patch _refresh_token_if_needed to skip IAM.
        client._access_token = "fake-token"
        client._token_expiry = float("inf")

        import requests as req_lib
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        http_err = req_lib.exceptions.HTTPError(response=mock_resp)

        with patch("app.services.watsonx_client._requests") as mock_req:
            mock_req.exceptions.Timeout = req_lib.exceptions.Timeout
            mock_req.exceptions.HTTPError = req_lib.exceptions.HTTPError
            mock_req.exceptions.RequestException = (
                req_lib.exceptions.RequestException
            )
            mock_req.post.side_effect = http_err

            with pytest.raises(WatsonxUpstreamError):
                with patch("time.sleep"):  # skip retry delay
                    client.generate("test prompt")

        # MAX_RETRIES=1 → 2 total attempts.
        assert mock_req.post.call_count == 2


# ------------------------------------------------------------------ #
# Avro schema validation for AI events                                 #
# ------------------------------------------------------------------ #

class TestAiAvroSchemas:
    """Validate that the new AI Avro schemas load and encode correctly."""

    def test_ai_question_received_schema_loads(self):
        from app.services.avro_utils import load_schema
        schema = load_schema("AiQuestionReceived")
        assert schema is not None

    def test_ai_answer_generated_schema_loads(self):
        from app.services.avro_utils import load_schema
        schema = load_schema("AiAnswerGenerated")
        assert schema is not None

    def test_ai_question_received_event_type_mapped(self):
        from app.services.avro_utils import record_name_for_event_type
        assert record_name_for_event_type("ai.question.received") == (
            "AiQuestionReceived"
        )

    def test_ai_answer_generated_event_type_mapped(self):
        from app.services.avro_utils import record_name_for_event_type
        assert record_name_for_event_type("ai.answer.generated") == (
            "AiAnswerGenerated"
        )

    def test_ai_question_received_schema_encodes(self):
        """End-to-end: build event + encode to Avro bytes (schema_id=1 stub)."""
        from app.services.avro_utils import encode
        event = {
            "event_id": "test-uuid-1",
            "event_type": "ai.question.received",
            "event_version": "1",
            "occurred_at": "2025-01-15T10:00:00Z",
            "producer": "artisthub-api",
            "correlation_id": "corr-123",
            "payload": {
                "question_length": 42,
                "session_type": "artist",
            },
        }
        raw = encode("ai.question.received", event, schema_id=1)
        # Must start with Confluent magic byte + 4-byte schema_id.
        assert raw[0:1] == b"\x00"
        assert len(raw) > 5

    def test_ai_answer_generated_schema_encodes(self):
        from app.services.avro_utils import encode
        event = {
            "event_id": "test-uuid-2",
            "event_type": "ai.answer.generated",
            "event_version": "1",
            "occurred_at": "2025-01-15T10:00:00Z",
            "producer": "artisthub-api",
            "correlation_id": "corr-456",
            "payload": {
                "grounding_status": "grounded",
                "sources_count": 3,
                "model_id": "ibm/granite-13b-instruct-v2",
                "latency_ms": 1250,
                "requires_professional_review": False,
                "prompt_template_version": "9a-v1",
            },
        }
        raw = encode("ai.answer.generated", event, schema_id=1)
        assert raw[0:1] == b"\x00"
        assert len(raw) > 5


# ------------------------------------------------------------------ #
# Event factory unit tests                                             #
# ------------------------------------------------------------------ #

class TestAiEventFactory:
    """Unit tests for the AI event builder functions."""

    def test_build_ai_question_received_creates_outbox_row(self):
        from app.services.event_factory import build_ai_question_received
        row = build_ai_question_received(
            question_length=100,
            session_type="artist",
            correlation_id="corr-factory-test",
        )
        assert row.event_type == "ai.question.received"
        assert row.topic == "artisthub.ai"
        assert row.correlation_id == "corr-factory-test"
        payload = row.payload_dict()
        assert payload["payload"]["question_length"] == 100
        assert payload["payload"]["session_type"] == "artist"
        # Privacy: only question_length (int) should be present,
        # NOT the raw question text (which would be a long string value).
        assert isinstance(payload["payload"]["question_length"], int)
        assert len(payload["payload"]) == 2  # exactly question_length + session_type

    def test_build_ai_answer_generated_creates_outbox_row(self):
        from app.services.event_factory import build_ai_answer_generated
        row = build_ai_answer_generated(
            grounding_status="grounded",
            sources_count=3,
            model_id="ibm/granite-13b-instruct-v2",
            latency_ms=800,
            requires_professional_review=False,
            correlation_id="corr-answer-test",
        )
        assert row.event_type == "ai.answer.generated"
        assert row.topic == "artisthub.ai"
        assert row.correlation_id == "corr-answer-test"
        payload = row.payload_dict()
        assert payload["payload"]["grounding_status"] == "grounded"
        assert payload["payload"]["sources_count"] == 3
        assert payload["payload"]["latency_ms"] == 800
        assert payload["payload"]["prompt_template_version"] == "9a-v1"
