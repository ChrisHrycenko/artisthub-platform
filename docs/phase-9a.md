# ArtistHub AI — Phase 9A Architecture

> **Status:** Implemented  
> **Release:** Phase 9A — Read-Only RAG Assistant  
> **Date:** 2025-01

---

## 1. Overview

Phase 9A delivers the first production-oriented version of ArtistHub AI —
a unified, read-only educational assistant that answers music-business
questions using authoritative sourced knowledge.

**ArtistHub AI presents to users as one assistant.** Specialized agents and
multi-agent orchestration belong to later phases (9B+). Phase 9A
is strictly:

> Read-only + global authoritative knowledge + RAG + citations +
> watsonx.ai + governance/auditability

---

## 2. Request Flow

```
Creator
  │
  ▼
POST /api/ai/ask
  │
  ▼
1. Marshmallow validation (AiAskSchema)
     │ 400 on invalid request
  ▼
2. RAG retrieval (rag_retrieval.retrieve)
     │ Global knowledge corpus only
     │ No private creator documents
     │ BM25-style keyword retrieval
  ▼
3a. No evidence found?
     │ → return insufficient_evidence fallback
     │ → emit outbox events
     │ → 200 response
  ▼
3b. Evidence retrieved → build_prompt()
     │ [SYSTEM] instruction + [SOURCES] + [QUESTION] + [ANSWER]
  ▼
4. watsonx.ai generate (WatsonxClient.generate)
     │ IBM Granite 13B Instruct v2 (configurable)
     │ Bounded retry: max 2 retries, 1s/2s backoff
     │ Timeout: 30 seconds
  ▼
5. Post-processing
     │ Strip leading/trailing whitespace
     │ Professional-review keyword scan
     │ Attach citations from evidence
  ▼
6. Emit Outbox events (ai.question.received + ai.answer.generated)
     │ Transactional Outbox Pattern
     │ Relay → Kafka artisthub.ai topic
     │ Non-blocking: failure logged, answer still returned
  ▼
7. Return 200 success response

Parallel branches (not on synchronous answer path):
  Outbox Relay → Kafka (async)
  Governance/audit metadata recorded in outbox event payloads
```

---

## 3. API Contract

### `POST /api/ai/ask`

**Request body:**
```json
{
  "question": "<string, 3–1000 characters>"
}
```

**Success response (200):**
```json
{
  "status": "success",
  "data": {
    "answer": "<string>",
    "grounding_status": "grounded | insufficient_evidence",
    "sources": [
      {
        "source_org":     "<authoritative organisation name>",
        "document_title": "<document or page title>",
        "source_url":     "<canonical public URL>",
        "topic":          "<domain topic tag>",
        "authority":      "primary_source | government | platform_doc"
      }
    ],
    "requires_professional_review": false,
    "model_id": "ibm/granite-13b-instruct-v2",
    "correlation_id": "<UUID>",
    "prompt_template_version": "9a-v1"
  }
}
```

**Error responses:**

| Status | Condition |
|--------|-----------|
| 400    | Missing/invalid `question` field |
| 503    | watsonx.ai unavailable, timeout, or auth failure |

**Phase 9A guarantees:**
- Never modifies any creator-domain table (Artist, Fan, Release, Post, Merch, Follow)
- Never reads or includes private creator documents
- Never forwards raw question text to Kafka events (only `question_length`)
- Always returns `correlation_id` matching `X-Correlation-ID` header

---

## 4. watsonx.ai Integration

### Module: `backend/app/services/watsonx_client.py`

**Authentication:** IBM Cloud IAM token exchange (`/identity/token`).
Token is cached and refreshed proactively 5 minutes before expiry.

**Generation endpoint:** `POST /ml/v1/text/generation?version=2023-05-29`

**Model:** `ibm/granite-13b-instruct-v2` (configurable via `WATSONX_MODEL_ID`)

**Parameters:**
- `decoding_method`: `greedy` (deterministic output for factual Q&A)
- `max_new_tokens`: 512 (configurable)
- `temperature`: 0.1 (near-deterministic)

**Retry policy:**
- `WATSONX_MAX_RETRIES=2` — up to 3 total attempts (initial + 2 retries)
- Backoff: 1s → 2s
- 4xx errors are NOT retried (client errors)

**Mock mode:** Set `WATSONX_MOCK=true` to bypass IBM credentials for local
development and CI. Returns a clearly labelled stub response.

**Error taxonomy:**

| Exception | Cause | HTTP returned |
|-----------|-------|---------------|
| `WatsonxUnavailableError` | No `WATSONX_API_KEY` and not mock | 503 |
| `WatsonxAuthError` | IAM token exchange failed | 503 |
| `WatsonxTimeoutError` | Request exceeded timeout | 503 |
| `WatsonxUpstreamError` | 5xx or network error from IBM | 503 |

---

## 5. RAG Architecture

### Module: `backend/app/services/rag_retrieval.py`

**Phase 9A implementation:** Keyword/BM25 retrieval against the in-process
global knowledge corpus. Forward-compatible with Phase 9B upgrade to IBM
watsonx.data vector index.

**Retrieval parameters:**
- `MAX_RESULTS = 4` passages per query
- `MIN_SCORE_THRESHOLD = 0.05` (minimum BM25 relevance to include)

**Prompt construction (`build_prompt`):**
```
[SYSTEM]
<system instruction — instructs model not to invent sources>

[SOURCES]
SOURCE 1 [<org> — <title>]
<passage text>
---
...

[QUESTION]
<creator question>

[ANSWER]
```

**Privacy guarantee:** `retrieve()` has no `creator_id` parameter and
makes no connection to any private document store. This is an
architectural constraint, not just policy.

---

## 6. Knowledge Corpus

### Module: `backend/app/services/knowledge_corpus.py`

**Phase 9A domains and sources:**

| Domain | Source Organisation | Authority |
|--------|--------------------|-----------| 
| PROs / Performance Rights | ASCAP, BMI | primary_source |
| PRO comparison (ASCAP/BMI/SESAC) | BMI | primary_source |
| Mechanical royalties / The MLC | The MLC | primary_source |
| SoundExchange / neighbouring rights | SoundExchange | primary_source |
| Master vs. composition rights | US Copyright Office | government |
| Writer/publisher share | ASCAP | primary_source |
| ISRC identifiers | IFPI / RIAA | primary_source |
| ISWC identifiers | CISAC / ISWC Agency | primary_source |
| Copyright basics | US Copyright Office (Circular 1) | government |
| DMCA fundamentals | US Copyright Office | government |
| Release preparation checklist | ArtistHub Documentation | platform_doc |
| ArtistHub platform help | ArtistHub Documentation | platform_doc |

Each passage carries: `id`, `text`, `source_org`, `document_title`,
`source_url`, `ingestion_date`, `topic`, `authority`, `keywords`.

**Corpus policy:**
- Authoritative first-party and government sources only
- No web-scraped blog posts or community forums
- Ingestion is separated from runtime (static for Phase 9A)
- Phase 9B will move to a runtime-ingested vector store

---

## 7. Citation / Provenance Behaviour

Every grounded answer includes a `sources` array. Each source contains:
- `source_org` — authoritative organisation
- `document_title` — document or page title
- `source_url` — canonical public URL
- `topic` — domain topic tag
- `authority` — authority tier

The system instruction in `build_prompt()` explicitly instructs the model:
- Do NOT invent sources, royalty rates, or legal facts
- If passages do not support a claim, omit it or label it clearly
- Recommend professional consultation for individualised advice

---

## 8. Grounding / Fallback Behaviour

| Scenario | `grounding_status` | `sources` | Answer |
|----------|--------------------|-----------|--------|
| Evidence retrieved, generation succeeded | `grounded` | 1–4 items | Model-generated, source-bounded |
| No evidence meets MIN_SCORE_THRESHOLD | `insufficient_evidence` | `[]` | Safe fallback text explaining limitations |
| watsonx.ai unavailable / timeout / error | N/A (503 error response) | — | Error message to try again |

**Insufficient-evidence fallback text** acknowledges the limitation,
lists the domains ArtistHub AI can help with, and recommends a
professional for out-of-scope questions.

---

## 9. Professional-Review Behaviour

When the generated answer text contains any of the following keyword
groups, `requires_professional_review` is set to `true`:

- `contract`, `agreement`, `lawsuit`, `sue`, `litigation`
- `tax`, `income`, `deduction`, `accounting`, `audit`
- `legal advice`, `attorney`, `lawyer`, `liable`, `liability`
- `investment`, `securities`, `financial advice`
- `specific to my`, `in my situation`, `my case`

The frontend renders a prominent blue notice box when this flag is `true`.

---

## 10. Safety Boundaries

ArtistHub AI in Phase 9A:

✅ **Can do:**
- Educate about royalties, PROs, copyright, ISRC/ISWC, release prep
- Explain concepts from authoritative sources with citations
- Recommend professional consultation when appropriate
- Answer questions about the ArtistHub platform

❌ **Cannot do:**
- Give definitive individualised legal, tax, or financial advice
- Modify any creator/platform data
- Sign or accept contracts
- File registrations (PRO, MLC, SoundExchange, Copyright Office)
- Transfer ownership or rights
- Distribute music or contact third parties
- Access private creator documents

---

## 11. Governance / Audit Implementation

Every POST /api/ai/ask interaction writes two Outbox events to the
`event_outbox` table within the HTTP request:

**`ai.question.received`** (topic: `artisthub.ai`)
```json
{
  "event_type": "ai.question.received",
  "payload": {
    "question_length": 42,
    "session_type": "artist | fan | anonymous"
  }
}
```
*Privacy note: raw question text is NEVER stored here.*

**`ai.answer.generated`** (topic: `artisthub.ai`)
```json
{
  "event_type": "ai.answer.generated",
  "payload": {
    "grounding_status": "grounded | insufficient_evidence | upstream_error",
    "sources_count": 3,
    "model_id": "ibm/granite-13b-instruct-v2",
    "latency_ms": 1250,
    "requires_professional_review": false,
    "prompt_template_version": "9a-v1"
  }
}
```

Both events carry the full envelope: `event_id`, `occurred_at`, `producer`,
`correlation_id`. Reconstructible audit record for every interaction.

---

## 12. Kafka Events

**New topic:** `artisthub.ai`

**New event types:**

| Event | Schema | Message key |
|-------|--------|-------------|
| `ai.question.received` | `kafka/schemas/ai/ai_question_received.avsc` | correlation_id |
| `ai.answer.generated` | `kafka/schemas/ai/ai_answer_generated.avsc` | correlation_id |

**Schema strategy:** RecordNameStrategy  
**Namespace:** `io.artisthub.events`  
**Record names:** `AiQuestionReceived`, `AiAnswerGenerated`

Events flow through the existing Transactional Outbox → Outbox Relay →
Kafka pipeline. No new consumers are required in Phase 9A.

---

## 13. Privacy / IP Controls

Phase 9A enforces the following controls by design:

1. **No private document retrieval:** `rag_retrieval.retrieve()` has no
   `creator_id` parameter and no connection to private storage.
2. **No question text in events:** Outbox events record only
   `question_length` (integer), not the question content.
3. **Global corpus only:** All 12 corpus passages are sourced from public
   authoritative organisations.
4. **No creator-domain writes:** The route is read-only except for the
   `event_outbox` table (which records governance metadata only).

---

## 14. Frontend

**New page:** `frontend/ask-ai.html`  
**New script:** `frontend/js/ask-ai.js`

Features:
- Question textarea (3–1000 characters, Cmd/Ctrl+Enter shortcut)
- Suggested topic chips for common questions
- Loading spinner during request
- Answer display with grounding badge (green = grounded, yellow = limited)
- Professional-review notice (blue info box)
- Source/citation list with org, title, and URL
- Upstream-error state with user-friendly message
- Correlation ID reference displayed for support

Navigation link added to `frontend/index.html`.

---

## 15. Observability

Extends Phase 8H architecture:

- Structured JSON log lines on every ask: `question_len`, `session`,
  `grounding_status`, `sources_count`, `model_id`, `latency_ms`, `corr_id`
- Kafka events carry governance metadata for downstream dashboards
- All log records carry `correlation_id` for cross-service tracing
- Prometheus scrape on `/metrics` includes auto-instrumented
  `flask_http_request_duration_seconds` for `/api/ai/ask`

---

## 16. Environment Variables

### Non-secret (ConfigMap)

| Variable | Default | Description |
|----------|---------|-------------|
| `WATSONX_API_URL` | `https://us-south.ml.cloud.ibm.com` | watsonx.ai base URL |
| `WATSONX_MODEL_ID` | `ibm/granite-13b-instruct-v2` | Foundation model |
| `WATSONX_MAX_TOKENS` | `512` | Max new tokens |
| `WATSONX_TEMPERATURE` | `0.1` | Sampling temperature |
| `WATSONX_TIMEOUT` | `30` | HTTP timeout (seconds) |
| `WATSONX_MAX_RETRIES` | `2` | Max retry attempts |
| `WATSONX_MOCK` | `false` | Stub mode for local dev |

### Secret (Secret object — never in ConfigMap/git)

| Variable | Description |
|----------|-------------|
| `WATSONX_API_KEY` | IBM Cloud IAM API key |
| `WATSONX_PROJECT_ID` | watsonx.ai project or space ID |

---

## 17. Local Testing

**Without IBM credentials (recommended for development):**
```bash
export WATSONX_MOCK=true
cd backend && python run.py
# POST /api/ai/ask will return stub responses
```

**With real IBM credentials:**
```bash
export WATSONX_API_KEY=<your-key>
export WATSONX_PROJECT_ID=<your-project-id>
export WATSONX_MOCK=false
cd backend && python run.py
```

**Run Phase 9A test suite:**
```bash
cd backend
pytest tests/test_ai.py -v
```

**Run full suite:**
```bash
cd backend
pytest tests/ --cov=app
```

---

## 18. OpenShift Considerations

**NetworkPolicy:** The existing `allow-backend-egress` policy already
permits outbound HTTPS (port 443) to `0.0.0.0/0` for Confluent Cloud.
IBM watsonx.ai (`*.us-south.ml.cloud.ibm.com`) and IAM
(`iam.cloud.ibm.com`) are also HTTPS — **no additional NetworkPolicy
change is required.**

**Secrets:** `WATSONX_API_KEY` and `WATSONX_PROJECT_ID` are in the
`artisthub-secrets` Secret object (see `openshift/base/secrets.example.yaml`).
They are loaded via `envFrom: secretRef` on the backend Deployment — no
manifest changes to `deployments/backend.yaml` required since it already
consumes the entire secret via `envFrom`.

**Resource limits:** The AI route adds a synchronous HTTP call to IBM.
The existing backend resource limits (500m CPU / 512Mi memory) are
sufficient for Phase 9A given the small model (Granite 13B) and the
30-second timeout. Monitor under load; tune `resources.limits` in the
production overlay if needed.

---

## 19. Known Limitations

1. **Phase 9A retrieval is keyword/BM25 only.** A production vector index
   (Phase 9B) will improve recall for semantic queries that don't match
   exact corpus keywords.

2. **Corpus is static.** The 12 passages are curated in code. Phase 9B
   will introduce runtime ingestion with version tracking.

3. **No streaming responses.** Phase 9A returns the complete answer in one
   HTTP response. Streaming (SSE) may be added in a later phase.

4. **watsonx.governance registration is a local audit representation.**
   If live governance API credentials are unavailable, governance metadata
   is recorded through the outbox events rather than a live governance
   registry call.

5. **English only.** The knowledge corpus and system instruction are English.
   Multilingual support is not in Phase 9A scope.

---

## 20. Phase 9B Recommended Scope

1. IBM watsonx.data vector index to replace BM25 retrieval
2. IBM embedding models for semantic passage matching
3. Private creator document ingestion pipeline (royalty statements,
   contracts — per creator, tenant-isolated)
4. watsonx.governance live model inventory registration
5. Expanded knowledge corpus with more passages and topics
6. Streaming response support (SSE)
7. Response caching for common questions
