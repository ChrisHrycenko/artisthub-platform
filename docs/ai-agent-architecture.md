# ArtistHub AI Agent Architecture

**Phase 9A Planning Document** — IBM watsonx.ai · watsonx.data · watsonx.governance

> This document covers Sections 1–5 only. Implementation sections (data pipelines,
> agent implementation, deployment manifests, testing strategy) will be added in
> subsequent planning iterations.

---

## 1. Executive Summary

ArtistHub has matured through eight phases of development: a Flask REST API backed
by SQLite, a static SPA frontend, Docker containerisation, OpenShift deployment,
Confluent Cloud event streaming, a CI/CD pipeline with Trivy scanning, and
Prometheus/Grafana observability. The platform now has a reliable operational
foundation from which to layer intelligent, data-driven capabilities.

Phase 9A introduces an **AI agent layer** built on IBM watsonx. Three distinct
agents address the highest-value opportunities identified in the existing roadmap:

| Agent | Capability | Primary Watson Product |
|---|---|---|
| **BioWriter** | Generates polished artist biography text from structured keywords | watsonx.ai (granite) |
| **RecommendationEngine** | Surfaces personalised release and merch recommendations for fans | watsonx.data + watsonx.ai |
| **ContentModerator** | Screens posts and merch descriptions for policy violations before publish | watsonx.ai (classification) |

These agents are introduced entirely behind the existing REST API contract. No
frontend page structure changes and no new database tables are required in Phase
9A. Each agent is encapsulated in a dedicated service module under
`backend/app/services/` and called from the relevant existing route. This
preserves the ownership check, marshmallow validation, and `success()` / `error()`
envelope patterns that all current routes enforce.

IBM watsonx.governance wraps all three agents with a shared model evaluation,
bias detection, drift monitoring, and audit-trail policy — ensuring every
AI-generated output is tracked and reviewable.

---

## 2. Current ArtistHub Architecture

### 2.1 System Topology

```
┌─────────────────────────┐        ┌─────────────────────────────┐
│   Static Frontend SPA   │◄──────►│   Flask REST API (JSON)     │
│  HTML / CSS / JS        │  HTTP  │  Python 3.11 · Flask 3.x    │
│  served by nginx         │        │  Blueprints · Flask-Login   │
│                          │        │  marshmallow · flask-bcrypt │
└─────────────────────────┘        └──────────────┬──────────────┘
                                                   │ SQLAlchemy ORM
                                            ┌──────▼──────┐
                                            │  SQLite DB  │
                                            │ artisthub.db│
                                            └─────────────┘
                                                   │
                                    ┌──────────────▼──────────────┐
                                    │  Confluent Cloud (Kafka)     │
                                    │  fan.followed                │
                                    │  orders.created              │
                                    │  artist.post_published       │
                                    │  analytics.browse_event      │
                                    └─────────────────────────────┘
```

The full stack runs in an **OpenShift cluster** with:
- `backend` Deployment (Flask/gunicorn, port 5000)
- `frontend` Deployment (nginx static, port 8080)
- `outbox-relay` Deployment (Kafka outbox publisher)
- `analytics-consumer` Deployment (Kafka consumer → metrics)
- `notification-consumer` Deployment (Kafka consumer → fan alerts)
- Prometheus User Workload Monitoring with Grafana dashboards
- GitHub Actions CI/CD with Trivy image scanning and cosign signing

### 2.2 Data Models in Scope for AI

| Model | AI relevance |
|---|---|
| `Artist` | `bio`, `genre`, `location` — input signals for BioWriter |
| `MusicRelease` | `title`, `genre`, `description`, `price` — recommendation features |
| `MerchProduct` | `name`, `description` — moderation target; recommendation feature |
| `SocialPost` | `body` — moderation target; sentiment signal |
| `Follow` | Fan → Artist relationship — collaborative filtering signal |
| `Order` | Fan purchase history — collaborative filtering signal |

### 2.3 Existing Extension Points

The `artisthub-plan.md` roadmap already identifies an `services/watsonx.py`
integration path. The Kafka analytics pipeline (`analytics.browse_event` topic)
produces a continuous stream of fan interaction events that feeds the
RecommendationEngine without requiring new instrumentation.

---

## 3. Proposed AI Architecture

### 3.1 High-Level Data Flow

```
Fan / Artist request
        │
        ▼
Flask Route (existing Blueprint)
        │
        ├──► marshmallow validation  (unchanged)
        │
        ├──► ownership / auth check  (unchanged)
        │
        ├──► DB access via SQLAlchemy (unchanged)
        │
        ├──► AI Service call (new)
        │         │
        │         ├── BioWriter ────────► watsonx.ai  (text generation)
        │         ├── RecommendEngine ──► watsonx.data feature store
        │         │                       + watsonx.ai inference
        │         └── ContentModerator ─► watsonx.ai  (classification)
        │                   │
        │                   └── watsonx.governance audit log
        │
        └──► success() / error() response envelope (unchanged)
```

### 3.2 Service Module Layout

```
backend/app/services/
├── __init__.py
├── watsonx_client.py          # Shared IBM watsonx REST session, auth token refresh
├── bio_writer.py              # BioWriter agent — prompt construction + generation call
├── recommendation_engine.py   # RecommendationEngine — feature retrieval + inference
└── content_moderator.py       # ContentModerator — classification call + policy decision
```

All three service modules are pure Python classes with no Flask application context
dependency. They are instantiated once at app startup (via `create_app()`) and
injected into the routes that need them, keeping the app factory as the single
initialisation point.

### 3.3 Agent Invocation Points

| Route | Agent | Trigger |
|---|---|---|
| `PUT /api/artists/:id` | BioWriter | Request body includes `bio_keywords` field |
| `GET /api/releases` | RecommendationEngine | Authenticated fan — returns ranked list |
| `GET /api/merch` | RecommendationEngine | Authenticated fan — returns ranked list |
| `POST /api/posts` | ContentModerator | Pre-publish screening; 422 if flagged |
| `POST /api/merch` | ContentModerator | Pre-publish screening; 422 if flagged |
| `PUT /api/merch/:id` | ContentModerator | Pre-publish screening on description update |

Agents are **opt-in and non-blocking where practical**: BioWriter only fires when
`bio_keywords` is present; RecommendationEngine degrades gracefully to unranked
results when the feature store is unavailable; ContentModerator failures default
to `allow` with a governance flag rather than blocking a publish.

---

## 4. IBM watsonx Responsibility Matrix

### 4.1 Product Mapping

| Capability | watsonx.ai | watsonx.data | watsonx.governance |
|---|:---:|:---:|:---:|
| Granite LLM text generation (BioWriter) | ✓ | | |
| NLP classification (ContentModerator) | ✓ | | |
| Artist genre / keyword embeddings | ✓ | | |
| Fan interaction feature store | | ✓ | |
| Collaborative filtering model training data | | ✓ | |
| Recommendation inference endpoint | ✓ | | |
| Model evaluation and accuracy tracking | | | ✓ |
| Bias and fairness monitoring | | | ✓ |
| Drift detection (recommendation staleness) | | | ✓ |
| Audit trail for all AI-generated content | | | ✓ |
| Policy enforcement (content moderation threshold) | | | ✓ |
| Explainability reports for artist recommendations | | | ✓ |

### 4.2 watsonx.ai Responsibilities

- **Foundation model hosting** — IBM Granite series (`granite-13b-chat-v2` for
  BioWriter; `granite-13b-instruct-v2` for ContentModerator classification).
- **Prompt management** — prompt templates for BioWriter are versioned in the
  watsonx.ai Prompt Lab and referenced by ID in `bio_writer.py`, enabling
  non-code prompt iteration.
- **Inference API** — all agent calls go through the watsonx.ai REST
  `/ml/v1/text/generation` endpoint; no on-premises model serving is required for
  Phase 9A.
- **Embedding generation** — semantic embeddings for release descriptions used as
  recommendation features are produced via the watsonx.ai embeddings endpoint.

### 4.3 watsonx.data Responsibilities

- **Feature store** — fan interaction vectors (follow graph, order history,
  browse events from the Kafka analytics topic) are materialised into watsonx.data
  tables. The RecommendationEngine queries these at inference time.
- **Historical training data** — anonymised aggregations of fan/artist interaction
  data (no PII) stored in watsonx.data Iceberg tables, available for periodic
  model fine-tuning.
- **Presto query layer** — the RecommendationEngine service executes a lightweight
  collaborative filtering query against watsonx.data via its Presto-compatible
  endpoint, returning a ranked `item_id` list that is then hydrated from the
  ArtistHub SQLite/PostgreSQL database.

### 4.4 watsonx.governance Responsibilities

- **AI use-case registry** — all three agents are registered as governed use cases
  with owner, purpose, data lineage, and risk classification metadata.
- **Model lifecycle management** — tracks which Granite model version is in
  production for each agent; gate approvals required before version promotion.
- **Continuous evaluation** — automated evaluation jobs run weekly against a
  held-out evaluation dataset to measure generation quality (BioWriter ROUGE
  scores) and classification accuracy (ContentModerator F1).
- **Bias detection** — ContentModerator outputs are monitored for systematic
  disparities across artist genre and fan demographic segments.
- **Drift monitoring** — RecommendationEngine click-through rate and follow
  conversion metrics are tracked; governance alerts fire when drift crosses
  configured thresholds.
- **Audit trail** — every AI-generated or AI-influenced response is logged with
  model version, prompt hash, input fingerprint, and output confidence score. Logs
  are immutable and queryable for compliance review.

---

## 5. High-Level Agent Architecture

### 5.1 BioWriter Agent

```
Artist fills in bio_keywords
           │
           ▼
PUT /api/artists/:id  ──► marshmallow validation
           │
           ▼
BioWriterService.generate(keywords, genre, location)
           │
           ├── build structured prompt from Prompt Lab template ID
           │
           ├── POST watsonx.ai /ml/v1/text/generation
           │         model: granite-13b-chat-v2
           │         max_new_tokens: 300
           │
           ├── validate output length and strip unsafe content
           │
           ├── watsonx.governance: log(agent=bio_writer, input_hash, output_hash)
           │
           └── return generated_bio string
                      │
                      ▼
           artist.bio = generated_bio  (saved to DB)
           success({"bio": generated_bio})
```

**Fallback:** if the watsonx.ai call fails or times out (5 s), the route saves the
raw `bio_keywords` string as the bio and returns `200` with a
`"ai_generation_skipped": true` flag in the response envelope. No user-facing
error is surfaced.

### 5.2 RecommendationEngine Agent

```
GET /api/releases?page=1  (authenticated Fan)
           │
           ▼
RecommendationService.rank(fan_id, candidate_ids)
           │
           ├── query watsonx.data (Presto):
           │     SELECT item_id, score
           │     FROM fan_item_affinity
           │     WHERE fan_id = ?  AND item_type = 'release'
           │     ORDER BY score DESC
           │
           ├── merge ranked ids with paginated SQLAlchemy result set
           │
           ├── watsonx.governance: log(fan_id_hash, model_version, ranked_ids)
           │
           └── return ordered Release list
                      │
                      ▼
           success({"releases": [...]})
```

**Fallback:** if watsonx.data is unreachable, the route returns the standard
unranked paginated result set. No error is surfaced to the fan.

### 5.3 ContentModerator Agent

```
POST /api/posts  (authenticated Artist)
           │
           ▼
ContentModeratorService.screen(text)
           │
           ├── POST watsonx.ai /ml/v1/text/generation
           │         model: granite-13b-instruct-v2
           │         prompt: policy classification template
           │
           ├── parse classification: {label, confidence}
           │
           ├── watsonx.governance: log(artist_id_hash, label, confidence, policy_version)
           │
           ├── if label == VIOLATION and confidence >= THRESHOLD:
           │       return error("Content policy violation", 422)
           │
           └── if watsonx.ai unavailable:
                   allow publish + governance flag = UNSCREENED
                      │
                      ▼
           post saved to DB, Kafka event published
           success({"post": post.to_dict()})
```

**Threshold configuration** — `CONTENT_MODERATION_THRESHOLD` is an environment
variable (default `0.85`) loaded via `config.py`. Governance policy version is
also injected via environment variable so threshold changes are tracked without
code deploys.

### 5.4 Shared Infrastructure

| Component | Detail |
|---|---|
| `watsonx_client.py` | Manages IAM token refresh (watsonx tokens expire every 60 min); single shared `requests.Session` |
| Timeout | All watsonx calls have a 5 s connect / 10 s read timeout — never blocks a user request indefinitely |
| Circuit breaker | After 3 consecutive failures, the client opens the circuit for 60 s and all agents fall back immediately without attempting a call |
| OpenShift Secrets | `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_DATA_ENDPOINT`, `CONTENT_MODERATION_THRESHOLD` injected as env vars |
| Governance SDK | `ibm-aigov-facts-client` records facts to watsonx.governance; imported only in `watsonx_client.py` |

---

## 6. Specialized ArtistHub Agents

The eight agents below operate within the Orchestrator model described in Section 8.
BioWriter, RecommendationEngine, and ContentModerator (Section 5) are **sub-capabilities
or tools** available to these agents — not replacements for them. Each agent owns a
distinct business domain and communicates with others only through the Orchestrator
or through Kafka events.

---

### 6.1 ArtistHub Orchestrator Agent

**Business purpose:** Single point of entry for all multi-step creator goals.
Decomposes a natural-language intent into a sequenced plan, delegates to
specialized agents, collects results, and assembles a unified response.

**Creator problem solved:** Artists should not need to know which system handles
royalty registration vs. marketing scheduling vs. metadata submission — they state
a goal and the Orchestrator routes it correctly.

**Allowed data:** All data accessible to the authenticated artist (own releases,
posts, orders, follower counts, aggregated analytics). No cross-artist data.

**Prohibited data/actions:** May not read another artist's private data; may not
trigger any External/high-impact action (approval level 3) without an explicit
human confirmation token present in the session.

**Tools/APIs:** Internal agent dispatcher; `GET /api/auth/me`; Kafka producer
(`agent.orchestrator.*` topics); watsonx.ai intent classification endpoint.

**Kafka events consumed:** `agent.task.completed`, `agent.approval.response`

**Kafka events produced:** `agent.task.dispatched`, `agent.plan.created`,
`agent.plan.completed`, `agent.approval.requested`

**watsonx.ai role:** Intent classification — maps free-text creator input to a
structured goal type (`RELEASE_LAUNCH`, `ROYALTY_QUERY`, `MARKETING_CAMPAIGN`,
etc.) using a Granite instruct model.

**watsonx.data role:** Reads the artist's historical goal outcomes to personalise
plan templates (e.g., a first-time releaser gets more explanatory steps).

**watsonx.governance role:** Registers every plan instance as a governed workflow
run; records which agents were invoked and which approvals were granted or denied.

**Human approval requirements:** Required before executing any plan that includes
an approval-level-3 action. The Orchestrator pauses the plan, emits
`agent.approval.requested`, and resumes only when the artist confirms.

**Fallback/error behavior:** If intent classification fails or confidence is below
0.70, the Orchestrator asks the artist for clarification rather than guessing.

**Audit requirements:** Every plan (goal text, intent classification, agent
sequence, approval tokens, final status) is logged immutably in watsonx.governance.

**Concrete ArtistHub example:** Artist types "Help me release my EP next month."
The Orchestrator classifies intent as `RELEASE_LAUNCH`, builds a 5-step plan,
and dispatches tasks to Release Agent → Publishing & Royalties Agent →
Rights & Protection Agent → Marketing Agent → Analytics Agent.

---

### 6.2 Release Agent

**Business purpose:** Guides an artist through preparing and publishing a music
release — metadata, artwork, streaming links, pricing, and distribution readiness.

**Creator problem solved:** Eliminates the checklist burden of getting a release
right: complete metadata, correct artwork dimensions, genre tags, and ISRC codes
before a release goes live.

**Allowed data:** Own `MusicRelease` records, own artist profile, DSP metadata
standards knowledge base.

**Prohibited data/actions:** Cannot submit to any external distribution service
autonomously (approval level 3); cannot set price below zero.

**Tools/APIs:** `POST /api/releases`, `PUT /api/releases/:id`; BioWriter
(sub-capability for description generation); ContentModerator (sub-capability for
description screening); DSP metadata validation rules (internal knowledge base).

**Kafka events consumed:** `agent.task.dispatched` (type=RELEASE_PREPARE)

**Kafka events produced:** `release.metadata_complete`, `release.ready_for_distribution`

**watsonx.ai role:** Suggests missing metadata fields (title variants, genre tags,
mood tags) and generates the release description using the BioWriter sub-capability.

**watsonx.data role:** Queries historical release performance by genre and release
day to recommend optimal release timing.

**watsonx.governance role:** Validates that the release metadata meets the
registered completeness policy before marking the release ready.

**Human approval requirements:** Level 2 — the agent proposes metadata changes;
the artist reviews and approves before any `PUT /api/releases/:id` write.

**Fallback/error behavior:** If metadata suggestions are unavailable, the agent
returns a plain checklist of required fields with no AI-generated content.

**Audit requirements:** All metadata suggestions and the artist's accept/reject
decisions are logged per release.

**Concrete ArtistHub example:** Artist creates a release with only `title` filled
in. Release Agent detects missing `genre`, `description`, and `streaming_link`,
proposes values using Granite, and presents a diff for approval before saving.

---

### 6.3 Publishing & Royalties Agent

**Business purpose:** Educates artists on music publishing structures, mechanical
and performance royalty collection, PRO (Performance Rights Organisation)
registration, and revenue splits.

**Creator problem solved:** Most independent artists do not register their
compositions with a PRO or understand mechanical vs. performance royalties, leaving
money uncollected.

**Allowed data:** Own release and order data (to estimate royalty potential);
public PRO registration information; royalty rate knowledge base.

**Prohibited data/actions:** Cannot register a composition with any PRO
autonomously; cannot submit split sheets or contracts; cannot quote specific legal
advice.

**Tools/APIs:** Internal royalty knowledge base; `GET /api/releases/:id`;
`GET /api/orders` (to estimate units sold).

**Kafka events consumed:** `release.ready_for_distribution`, `agent.task.dispatched`
(type=ROYALTY_GUIDANCE)

**Kafka events produced:** `agent.royalty.guidance_delivered`,
`agent.approval.requested` (for PRO registration initiation)

**watsonx.ai role:** Generates plain-language royalty education summaries tailored
to the artist's specific release (e.g., "Your EP has 4 tracks — here is how
performance royalties work for a release this size").

**watsonx.data role:** Aggregates order history to estimate mechanical royalty
potential; surfaces peer benchmarks (anonymised).

**watsonx.governance role:** Ensures all royalty estimates are labelled as
illustrative only; bias checks confirm advice is not skewed by genre or artist size.

**Human approval requirements:** Level 1 for education; Level 2 before the agent
initiates any external PRO registration workflow on the artist's behalf.

**Fallback/error behavior:** If watsonx.ai is unavailable, returns a static
royalty FAQ and links to relevant PRO registration pages.

**Audit requirements:** Every piece of royalty guidance delivered, including the
model version and any estimates shown, is logged for compliance review.

**Concrete ArtistHub example:** After a release goes live, the agent surfaces
"You have not registered this composition with a PRO. Estimated uncollected
performance royalties for a release of this size in your genre: $X–$Y/year.
Would you like a step-by-step PRO registration guide?"

---

### 6.4 Rights & Protection Agent

**Business purpose:** Helps artists understand their copyright position, flag
potential infringement risks, and initiate takedown or registration workflows.

**Creator problem solved:** Independent artists rarely know when their work is
being used without permission or how to assert their rights without legal support.

**Prohibited data/actions:** Cannot file a legal claim or sign any document
autonomously; cannot access third-party platform APIs to search for infringement
without explicit artist authorisation (approval level 3).

**Allowed data:** Own release metadata; copyright knowledge base; artist's stated
registration status.

**Tools/APIs:** Internal copyright knowledge base; ContentModerator
(sub-capability — screens incoming licensing requests for red flags).

**Kafka events consumed:** `release.ready_for_distribution`,
`agent.task.dispatched` (type=RIGHTS_CHECK)

**Kafka events produced:** `agent.rights.assessment_complete`,
`agent.approval.requested` (for any external filing action)

**watsonx.ai role:** Explains copyright concepts in plain language tailored to the
release; flags common risk patterns in release metadata (e.g., uncleared samples).

**watsonx.data role:** Stores the artist's rights registration history; enables
the agent to detect gaps (registered on PRO but not with Copyright Office).

**watsonx.governance role:** All rights assessments are immutably logged; the
agent's outputs are explicitly labelled as informational and not legal advice.

**Human approval requirements:** Level 1 for education; Level 3 for any action
that initiates a filing with a government body or platform takedown system.

**Fallback/error behavior:** If assessment cannot be completed, the agent returns
a generic rights checklist and recommends consulting an entertainment lawyer.

**Audit requirements:** All assessments logged with the model version,
release ID, and the specific risk flags raised.

**Concrete ArtistHub example:** After release preparation, the agent detects the
description mentions "interpolates a classic riff" and surfaces: "Uncleared
interpolations may create copyright liability. Here is what to verify before
distributing."

---

### 6.5 Marketing Agent

**Business purpose:** Plans and proposes promotional campaigns for releases and
merch — social posting schedules, audience targeting guidance, and content ideas.

**Creator problem solved:** Independent artists have limited marketing budgets and
often release music with no promotional plan, resulting in low initial discovery.

**Allowed data:** Own release and merch data; own follower count and growth trend;
social post history; genre-level benchmark data (anonymised, from watsonx.data).

**Prohibited data/actions:** Cannot post to any external social platform
autonomously; cannot spend any budget; cannot create paid advertising campaigns.

**Tools/APIs:** `POST /api/posts` (via artist approval); ContentModerator
(sub-capability); `GET /api/artists/:id/followers`.

**Kafka events consumed:** `release.metadata_complete`,
`agent.task.dispatched` (type=MARKETING_PLAN)

**Kafka events produced:** `agent.marketing.plan_proposed`,
`artist.post_published` (only after artist approval)

**watsonx.ai role:** Generates social post copy, release announcement text, and
promotional caption variants for artist review using Granite chat model.

**watsonx.data role:** Queries genre-level engagement benchmarks to recommend
posting cadence and timing; feeds the RecommendationEngine with campaign signals.

**watsonx.governance role:** Tracks which generated copy was approved vs.
rejected; monitors for brand-inconsistent or misleading promotional language.

**Human approval requirements:** Level 2 for all generated copy before any post
is created via `POST /api/posts`; Level 3 before initiating any paid promotion.

**Fallback/error behavior:** If content generation fails, returns a structured
promotional brief template the artist can fill in manually.

**Audit requirements:** All generated copy proposals and the artist's decisions
(approved/edited/rejected) are logged per campaign.

**Concrete ArtistHub example:** Artist approves the release plan. Marketing Agent
proposes a 4-week drip campaign: teaser post (week −2), pre-save link post
(week −1), release day announcement, and follow-up engagement post. Artist reviews
and approves each draft before it is published.

---

### 6.6 Analytics Agent

**Business purpose:** Delivers plain-language interpretation of the artist's
performance data — follower growth, release sales, merch revenue, and fan
engagement trends.

**Creator problem solved:** Raw numbers in a dashboard are hard to act on. Artists
need context: "Is 42 new followers this month good for my genre and size?"

**Allowed data:** Own follower count, order history, release and merch data,
Kafka analytics events (`analytics.browse_event`).

**Prohibited data/actions:** Read-only. May not write to any model or trigger any
action. May not expose individual fan PII.

**Tools/APIs:** `GET /api/orders`; `GET /api/artists/:id/followers`; watsonx.data
query layer (own aggregated events only).

**Kafka events consumed:** `analytics.browse_event`, `orders.created`,
`fan.followed`, `agent.task.dispatched` (type=ANALYTICS_SUMMARY)

**Kafka events produced:** `agent.analytics.summary_delivered`

**watsonx.ai role:** Translates aggregated metrics into narrative summaries and
surfaces anomalies ("Your merch sales spiked 3× after last week's post — here is
why that may have happened").

**watsonx.data role:** Primary data source — all event aggregations, trend
computations, and peer benchmarks live here.

**watsonx.governance role:** Ensures analytics outputs do not infer or expose
individual fan identity; drift monitoring alerts if model interpretations diverge
from actual metric changes.

**Human approval requirements:** None (informational only, approval level 1).

**Fallback/error behavior:** If watsonx.data is unavailable, returns raw counts
from the ArtistHub SQLite/PostgreSQL database with no narrative interpretation.

**Audit requirements:** All summaries delivered and the underlying data snapshot
used to generate them are logged with a timestamp and model version.

**Concrete ArtistHub example:** One week after release, Analytics Agent surfaces:
"Your new single has 87 streams via the browse page, 14 purchases, and drove 31
new follows — outperforming your previous release by 40% in week-one orders."

---

### 6.7 Fan Intelligence / Recommendation Agent

**Business purpose:** Personalises the fan-facing discovery experience and helps
artists understand who their fans are without exposing individual fan data.

**Creator problem solved (fan side):** Fans discover music randomly; personalised
ranking surfaces releases and merch that match their taste.
**Creator problem solved (artist side):** Artists cannot see which fan segments
engage most with their releases, making targeting guesswork.

**Allowed data (fan):** Own follow history, order history, browse events
(own session only). **Allowed data (artist):** Aggregated, anonymised fan segment
summaries — no individual fan identity.

**Prohibited data/actions:** May never expose one fan's data to another fan or to
the artist at the individual level.

**Tools/APIs:** RecommendationEngine (sub-capability); `GET /api/releases`;
`GET /api/merch`; `GET /api/artists/:id/followers`.

**Kafka events consumed:** `analytics.browse_event`, `fan.followed`,
`orders.created`

**Kafka events produced:** `recommendation.served`, `agent.fan_segment.updated`

**watsonx.ai role:** Generates personalised "Because you follow X" explanations
for recommended releases; produces anonymised fan segment narratives for artists.

**watsonx.data role:** Hosts the fan–item affinity feature store and the
collaborative filtering model training data; all fan data stored pseudonymously.

**watsonx.governance role:** Primary privacy enforcement point — bias monitoring
ensures recommendations do not systematically disadvantage genres or artist sizes;
all recommendation decisions are explainable and logged.

**Human approval requirements:** None for fan-facing recommendations. Artist
segment summaries are level 1 (informational).

**Fallback/error behavior:** Degrades to unranked chronological listing; no error
surface to the fan.

**Audit requirements:** Every recommendation set served is logged with the model
version, fan pseudonym hash, and ranked item IDs.

**Concrete ArtistHub example:** Fan who follows three jazz artists and has
purchased two jazz releases is shown jazz-tagged new releases at the top of
`browse-releases.html` with the label "Recommended for you."

---

### 6.8 Creator Business Agent

**Business purpose:** Acts as a holistic business advisor — surfaces revenue
gaps, suggests pricing adjustments, flags uncollected income streams, and helps
artists make informed commercial decisions.

**Creator problem solved:** Independent artists often undervalue their work,
miss revenue streams (sync licensing, merch bundles), or make pricing decisions
without any market context.

**Allowed data:** Own release prices, merch prices, order history, follower count,
royalty guidance history; genre-level pricing benchmarks (anonymised).

**Prohibited data/actions:** Cannot change any price autonomously; cannot accept
any licensing deal or contract; cannot move or recommend movement of real money.

**Tools/APIs:** `GET /api/releases`; `GET /api/merch`; `GET /api/orders`;
Publishing & Royalties Agent (for royalty gap signals).

**Kafka events consumed:** `agent.royalty.guidance_delivered`,
`orders.created`, `agent.task.dispatched` (type=BUSINESS_REVIEW)

**Kafka events produced:** `agent.business.recommendation_proposed`,
`agent.approval.requested` (for any pricing change proposal)

**watsonx.ai role:** Generates a plain-language business health summary with
ranked revenue opportunities ("Your merch is priced 30% below comparable
artists in your genre; raising it by $5 could add $X/month at current sales pace").

**watsonx.data role:** Stores genre-level pricing, sales velocity, and revenue
benchmarks used to contextualise the artist's own numbers.

**watsonx.governance role:** All business recommendations are logged with the
model version; bias detection ensures advice is not skewed by artist size or genre.

**Human approval requirements:** Level 1 for all analysis and recommendations;
Level 2 before the agent drafts a price-change request for `PUT /api/releases/:id`
or `PUT /api/merch/:id`; Level 3 for any contract- or licensing-related guidance
that involves a third party.

**Fallback/error behavior:** If benchmark data is unavailable, the agent returns
the artist's own historical trends only with a note that peer benchmarks are
temporarily unavailable.

**Audit requirements:** All recommendations, the benchmark data snapshot used,
and the artist's response (acted/dismissed) are logged per session.

**Concrete ArtistHub example:** Creator Business Agent notices the artist's merch
has had zero sales in 60 days despite 200 followers. It surfaces: "Consider
dropping the price by $3 for 2 weeks — artists in your genre see a 2× conversion
lift from time-limited discounts. Approve a temporary price change?"

---

## 7. AI Action Approval Framework

All AI agent actions are classified into one of three approval levels before
execution. No agent may escalate its own action to a lower level; the Orchestrator
enforces level assignment based on a static action-type registry.

---

### Level 1 — Informational

The AI answers, explains, or summarises without writing to any data store or
initiating any external workflow. No artist confirmation is required.

The response envelope includes `"ai_action_level": 1` and the watsonx.governance
fact is recorded automatically.

| Action type | Example |
|---|---|
| Royalty education | "Here is how mechanical royalties work for digital releases" |
| Analytics summary | "Your release has 87 streams and 14 purchases this week" |
| Rights explanation | "Copyright in a composition lasts for the life of the author plus 70 years" |
| Benchmark context | "Artists in your genre typically price EPs between $5 and $10" |
| FAQ / how-to | "Here is how to register with ASCAP in three steps" |

---

### Level 2 — Recommended Action

The AI proposes a specific action against ArtistHub data. The artist sees the
proposed change as a diff or summary and must explicitly confirm before any write
is executed. The confirmation is a button click in the UI that sends an approval
token back to the Orchestrator.

The response envelope includes `"ai_action_level": 2` and
`"pending_approval": true`. The plan is paused and `agent.approval.requested`
is emitted to Kafka. No write occurs until `agent.approval.response` is received
with `status: approved`.

| Action type | Example |
|---|---|
| Publishing guidance | Agent proposes a draft PRO registration checklist for artist to initiate |
| Marketing recommendations | Agent proposes social post copy for artist to approve before `POST /api/posts` |
| Metadata changes | Agent proposes updated `genre` and `description` fields for a release |
| Price change proposal | Creator Business Agent proposes a temporary merch price reduction |
| Release preparation | Release Agent proposes a complete metadata set before `PUT /api/releases/:id` |

---

### Level 3 — External / High-Impact Action

The AI proposes an action that touches an external system, involves a third party,
or has financial, legal, or reputational consequences that cannot be reversed
easily. In addition to explicit artist confirmation, the Orchestrator surfaces a
prominent advisory that professional or human review is recommended before
proceeding.

The response envelope includes `"ai_action_level": 3`,
`"professional_review_advised": true`, and a plain-language risk summary. The
Orchestrator will not proceed until a two-step confirmation sequence is completed:
(1) artist acknowledges the risk advisory, (2) artist confirms the action.

| Action type | Example |
|---|---|
| Distribution changes | Initiating a DSP distribution submission or takedown on an external platform |
| Rights transfers | Any assignment or exclusive licensing of a composition or master |
| Contract acceptance | Accepting terms with a third-party distributor, label, or sync library |
| Money movement | Any action that triggers a real payment, withdrawal, or revenue split |
| PRO registration filing | Submitting a composition directly to a PRO on the artist's behalf |
| Legal filing | Initiating a DMCA takedown or copyright registration with a government body |

---

### Cross-Cutting Approval Rules

1. **No level downgrade** — an agent may never reclassify its own action to a
   lower level. Level is determined by the action-type registry, not the agent.
2. **Approval expiry** — a level-2 or level-3 approval token is valid for 15
   minutes. Expired tokens require re-confirmation.
3. **Denial is final in session** — if an artist denies a level-3 action, the
   Orchestrator does not retry it in the same session.
4. **Governance log on every level** — all three levels produce an immutable
   watsonx.governance audit record including the action type, level, model version,
   and artist decision.

---

## 8. Agent Orchestration Model

### 8.1 Orchestrator Responsibilities

The ArtistHub Orchestrator Agent is the sole entry point for multi-agent workflows.
No specialized agent calls another specialized agent directly; all inter-agent
coordination is mediated by the Orchestrator.

```
Creator goal (natural language)
           │
           ▼
Orchestrator Agent
    ├── 1. Intent classification (watsonx.ai, Granite instruct)
    │         goal_type, confidence, required_agents[]
    │
    ├── 2. Plan construction
    │         ordered list of agent tasks with input/output contracts
    │         approval gates identified from action-type registry
    │
    ├── 3. Task dispatch loop
    │         for each task in plan:
    │           publish agent.task.dispatched → Kafka
    │           await agent.task.completed (or timeout)
    │           if approval_required:
    │             publish agent.approval.requested → Kafka
    │             pause plan, await agent.approval.response
    │             if denied: abort plan, log, notify artist
    │
    ├── 4. Result assembly
    │         collect outputs from all completed tasks
    │         compose unified response for artist
    │
    ├── 5. Audit finalisation
    │         watsonx.governance: mark plan run COMPLETE or ABORTED
    │         record all agent outputs, approval tokens, timestamps
    │
    └── 6. Response delivery
              success({"plan_id": ..., "summary": ..., "next_actions": [...]})
```

### 8.2 Preventing Unauthorized Autonomous Actions

- **Action-type registry** — a static configuration (not ML-derived) maps every
  agent action to its approval level. The Orchestrator consults this registry;
  agents do not.
- **No direct external calls from specialized agents** — all external-system
  interactions are mediated through the Orchestrator's approval gate. A specialized
  agent that needs to touch an external system emits an intent event; the
  Orchestrator evaluates the level and either proceeds (level 1) or pauses for
  approval (levels 2–3).
- **Session-scoped approval tokens** — approval tokens are cryptographically tied
  to the artist session and the specific plan step. A token for step 3 cannot
  authorise step 5.
- **Immutable audit trail** — watsonx.governance records every plan step, approval
  decision, and agent output. Post-hoc review can reconstruct exactly what the AI
  did and why, with which model version, at what time.
- **Circuit breaker** — if any specialized agent fails 3 times consecutively, the
  Orchestrator aborts the plan and surfaces a graceful error rather than retrying
  into an unknown state.

### 8.3 Kafka Event Flow in Multi-Agent Plans

```
Orchestrator ──► agent.plan.created
                      │
                      ▼
Orchestrator ──► agent.task.dispatched (Release Agent)
Release Agent ──► release.metadata_complete
                      │
                      ▼ (approval gate, level 2)
Orchestrator ──► agent.approval.requested
Artist ──────────────────────────────────► agent.approval.response (approved)
                      │
                      ▼
Orchestrator ──► agent.task.dispatched (Publishing & Royalties Agent)
P&R Agent    ──► agent.royalty.guidance_delivered
                      │
                      ▼
Orchestrator ──► agent.task.dispatched (Rights & Protection Agent)
Rights Agent ──► agent.rights.assessment_complete
                      │
                      ▼
Orchestrator ──► agent.task.dispatched (Marketing Agent)
Marketing Agent ──► agent.marketing.plan_proposed
                      │
                      ▼ (approval gate, level 2 — copy approval)
Orchestrator ──► agent.approval.requested
Artist ──────────────────────────────────► agent.approval.response (approved)
Marketing Agent ──► artist.post_published
                      │
                      ▼
Orchestrator ──► agent.task.dispatched (Analytics Agent)
Analytics Agent ──► agent.analytics.summary_delivered
                      │
                      ▼
Orchestrator ──► agent.plan.completed
```

### 8.4 Multi-Agent Worked Example

**Creator goal:** *"I want to release my new single next month and make sure I
handle the business side correctly."*

**Step 1 — Intent classification**
The Orchestrator sends this text to watsonx.ai. Granite classifies it as
`RELEASE_LAUNCH` with confidence 0.94. Required agents:
`[Release, Publishing & Royalties, Rights & Protection, Marketing, Analytics]`.
Plan is constructed and persisted in watsonx.governance as run `plan-4821`.

**Step 2 — Release Agent**
Receives `agent.task.dispatched` (type=RELEASE_PREPARE). Discovers the draft
release has no `genre`, `description`, or `streaming_link`. Generates metadata
proposals via watsonx.ai and presents a diff to the artist. Artist approves
(level-2 gate). `release.metadata_complete` is emitted.

**Step 3 — Publishing & Royalties Agent**
Triggered by `release.metadata_complete`. Checks the artist's profile — no PRO
affiliation registered. Generates a plain-language mechanical and performance
royalty explainer tailored to the single format. Surfaces a PRO registration
guide (level 1 — informational). Emits `agent.royalty.guidance_delivered`.

**Step 4 — Rights & Protection Agent**
Triggered by `release.metadata_complete`. Scans the release description for
uncleared-sample risk signals — none found. Provides a standard copyright
checklist confirming no red flags (level 1). Emits
`agent.rights.assessment_complete`.

**Step 5 — Marketing Agent**
Triggered by `agent.rights.assessment_complete`. Proposes a 3-post launch
campaign: teaser (3 weeks out), release-day announcement, and week-2 engagement
post. All copy is presented for review. Artist approves teaser and release posts,
edits the week-2 post. Both approved posts are published via `POST /api/posts`
(level-2 gate per post). `agent.marketing.plan_proposed` and two
`artist.post_published` events emitted.

**Step 6 — Analytics Agent**
Triggered by `agent.plan.completed` (30 days later via a scheduled task).
Aggregates first-month order, follow, and browse data from watsonx.data. Returns:
"Your single generated 210 streams, 34 purchases, and 68 new followers in month 1
— 55% above your previous release." Emits `agent.analytics.summary_delivered`.
No approval required (level 1).

**Step 7 — Plan finalisation**
Orchestrator marks plan `plan-4821` COMPLETE in watsonx.governance. Full audit
record: intent classification confidence, every agent output, both approval tokens
(release metadata + marketing copy), model versions used at each step, timestamps.

---

## 9. ArtistHub Knowledge & RAG Architecture

### 9.1 Knowledge Classification

ArtistHub agents draw from two strictly isolated knowledge tiers:

**Global ArtistHub Knowledge** — shared across all tenants, read-only, managed
by the platform team.

| Corpus | Examples |
|---|---|
| Music-business education | How mechanical royalties work; what a PRO does; sync licensing basics |
| PRO / CMO reference | ASCAP, BMI, SESAC, PRS, SOCAN registration requirements, rate schedules |
| Copyright & rights | US and international copyright basics; fair use; DMCA takedown procedure |
| Distribution requirements | Streaming DSP metadata standards; ISRC/ISWC structure; release date windows |
| ArtistHub documentation | Platform help articles, API contract descriptions, feature guides |

**Private Creator Knowledge** — isolated per creator (tenant), never retrievable
into another creator's AI context.

| Asset type | Sensitivity |
|---|---|
| Unreleased recordings / stems | Critical |
| Lyrics and songwriting ideas | Critical |
| Contracts and term sheets | Critical |
| Royalty statements and split sheets | Critical |
| Ownership percentages | Critical |
| Release metadata (draft, unpublished) | High |
| Financial and business data | High |
| Personal information | High |

### 9.2 Ingestion Pipeline

```
Source document (upload or platform event)
         │
         ▼
1. INGEST — document received, creator_id stamped, SHA-256 fingerprint recorded
         │
         ▼
2. PARSE — extract text (PDF, DOCX, plain text); strip binary artefacts
         │
         ▼
3. CLASSIFY — assign corpus (global vs. private) and sensitivity tier
              global: proceed to shared index pipeline
              private: enforce tenant namespace before any further step
         │
         ▼
4. CHUNK — split into overlapping passages (~400 tokens, 50-token overlap)
           each chunk carries: creator_id, document_id, source_label, page/line ref
         │
         ▼
5. EMBED — watsonx.ai embeddings endpoint → dense vector per chunk
         │
         ▼
6. INDEX — store vector + metadata in watsonx.data vector index
           global chunks → shared namespace
           private chunks → creator-scoped namespace (tenant-isolated)
         │
         ▼
knowledge.document.ingested event → Kafka
```

### 9.3 Retrieval & Context Construction

At inference time the agent issues a retrieval request through `watsonx_client.py`:

```
Agent query (intent + user text)
         │
         ▼
AuthZ check — confirm requesting agent has access to tenant namespace
         │  (fails closed: deny if ambiguous)
         ▼
Vector search — global namespace + creator's private namespace (if authorised)
         │  NEVER merges namespaces across different creator_ids
         ▼
Re-rank top-k passages by relevance + recency
         │
         ▼
Context assembly:
  - sourced passages labelled with [SOURCE: <document_label>, <page/section>]
  - AI-generated interpolation clearly separated
         │
         ▼
Model prompt = system instruction + sourced passages + agent query
         │
         ▼
watsonx.ai generation call
         │
         ▼
Response includes citation block:
  sources_used: [{document, section, retrieval_score}]
  ai_interpretation: true/false per statement
```

**Provenance rule:** any factual claim in a music-business response must either
cite a retrieved passage from the global knowledge base or be explicitly labelled
`[AI interpretation — verify with a qualified professional]`. Agents may not
present AI-generated estimates as sourced facts.

### 9.4 Tenant Isolation

- Every chunk and every vector index entry carries a `creator_id` field.
- Retrieval queries are constructed with a mandatory `creator_id` filter applied
  **before** the vector search executes — not as a post-filter.
- The Orchestrator passes the authenticated artist's `creator_id` in every agent
  task payload; agents may not override this value.
- Cross-creator retrieval is architecturally impossible by namespace design, not
  just by policy.

### 9.5 watsonx.data Participation

**watsonx.data IS used for:**
- Hosting the vector index (both namespaces) via its Presto-queryable Iceberg
  storage layer.
- Storing document metadata (fingerprints, ingestion timestamps, source labels)
  for governance and deduplication.
- Feeding the analytics and recommendation feature store (Section 6.6–6.7).

**watsonx.data is NOT used for:**
- Storing raw creator documents (unstructured originals remain in an encrypted
  object store external to watsonx.data).
- Enforcing row-level security on private creator chunks — that is handled by the
  application-layer `creator_id` filter in the retrieval query.

---

## 10. Creator IP, Data Privacy & Security

### 10.1 Data Classification

| Tier | Assets | Examples |
|---|---|---|
| Critical | Creator IP and financial | Lyrics, stems, contracts, royalty statements, split sheets, ownership % |
| High | Creator identity and relationships | Personal info, fan PII, draft releases |
| Medium | Platform operational | Published releases, post history, follower counts |
| Low | Public | Published artist profile, genre tags |

### 10.2 Isolation, Encryption & Access

**Tenant isolation** — every database row, object-store object, vector chunk, and
Kafka message payload carrying creator-specific data includes `creator_id`. No
query, retrieval, or agent task may cross this boundary.

**Encryption in transit** — all service-to-service communication uses TLS 1.2+.
watsonx API calls and Confluent Cloud connections use SASL_SSL. Internal
OpenShift traffic uses mTLS between pods.

**Encryption at rest** — OpenShift PVCs and the object store holding raw creator
documents use AES-256. watsonx.data Iceberg tables encrypted at rest by the
IBM Cloud storage layer.

**Least privilege** — each agent service has a dedicated OpenShift `ServiceAccount`
with an RBAC policy granting access only to the Kafka topics and Kubernetes Secrets
it needs. No agent reads another agent's secrets.

**Secrets management** — all credentials (`WATSONX_API_KEY`, `OBJECT_STORE_KEY`,
Confluent SASL credentials) are stored in OpenShift Secrets, never in environment
variable literals or source code. Secrets are rotated on a defined schedule.

**Service identity** — agents authenticate to each other via short-lived JWT
tokens signed with the Orchestrator's private key. A token issued for the Release
Agent cannot be used by the Marketing Agent.

### 10.3 Retention and Deletion

- Creator-uploaded documents are retained until the creator deletes them or closes
  their account.
- On account deletion: all private vector chunks, object-store documents, and
  creator rows are purged within 30 days; a deletion audit record is retained.
- Kafka retention for AI events: 90 days for operational events; 7 years for
  governance audit events (immutable, append-only topic).
- Recommendation feature store entries are pseudonymised; a fan's pseudonym can
  be purged on account deletion without re-training the model.

### 10.4 Prompt Injection & Malicious Document Protection

- **Input sanitisation** — all user-supplied text (goal input, uploaded documents)
  is stripped of control characters and common injection patterns before being
  inserted into a prompt.
- **Prompt structure** — system instructions and retrieved context appear in
  separate prompt sections from user input; the model is instructed to treat the
  user section as untrusted data, not as commands.
- **Document parsing isolation** — uploaded documents are parsed in a sandboxed
  worker process. A malformed or malicious document cannot affect the parsing of
  other creators' documents.
- **Output validation** — agent outputs are scanned for instruction-following
  artefacts (e.g., attempts to exfiltrate `creator_id`, override approval levels)
  before being returned to the creator or passed to the next agent.

### 10.5 Model Training Policy

**Default: NO.** Creator-owned private content (documents, lyrics, contracts,
royalty statements, private metadata) is **never used for model training or
fine-tuning** without explicit creator opt-in.

Any future opt-in programme must:
1. Present a plain-language description of exactly what data will be used and how.
2. Require an active affirmative action (not a pre-checked box).
3. Allow revocation at any time, with confirmed deletion of contributed data from
   any training pipeline within 30 days.
4. Be governed by a watsonx.governance policy entry that is auditable.

Global ArtistHub Knowledge corpus content (platform documentation, public music-
business references) may be used for model fine-tuning as it contains no creator
PII or IP.

### 10.6 Audit Logging

Every access to a creator's private namespace — retrieval query, document upload,
document deletion, agent task — produces an immutable log entry in
watsonx.governance containing: `creator_id`, `agent_id`, `action_type`,
`document_ids_accessed`, `timestamp`, `session_id`. These logs are the foundation
for the auditor query defined in Section 11.

---

## 11. watsonx.governance & AI Trust Model

### 11.1 What Is Registered

| Entity | Registration |
|---|---|
| Every Granite model version used in production | Model inventory entry with version, deployment date, evaluation scores |
| Every prompt template | Versioned prompt entry with hash, linked model version, approval status |
| Every agent type | Agent inventory entry with permitted tools, action-type registry snapshot |
| Every knowledge corpus | Document inventory with source label, ingestion date, provenance URL |

### 11.2 Continuous Evaluation

| Metric | Agent | Measurement method |
|---|---|---|
| Generation quality (ROUGE / BERTScore) | BioWriter, P&R Agent, Creator Business | Weekly eval against held-out gold set |
| Classification accuracy (F1) | ContentModerator | Weekly eval against labelled test set |
| Groundedness | All generative agents | Automated check: does response cite a retrieved passage? |
| Hallucination rate | All generative agents | Automated check: claims without citations flagged for human review sample |
| Recommendation CTR / conversion drift | Fan Intelligence | Weekly comparison of live metrics vs. baseline |
| Bias / fairness | ContentModerator, Fan Intelligence | Demographic parity across genre and artist size segments |

### 11.3 Harmful-Output and Policy Monitoring

- ContentModerator outputs are themselves monitored by a governance evaluation job
  for systematic false-positive bias (over-flagging a specific genre or topic).
- All generated text passes through a lightweight harm classifier before delivery.
  Outputs scoring above a harm threshold are held, logged, and routed for human
  review rather than returned to the creator.
- `governance.violation.detected` Kafka event fires whenever a policy threshold is
  breached; a designated platform reviewer is notified.

### 11.4 The Full Auditor Query

An auditor with access to the watsonx.governance audit log can reconstruct any AI
interaction by answering all seven questions:

| Question | Source |
|---|---|
| Who requested the action? | `creator_id`, `session_id` from the `creator.goal.created` event |
| Which agent handled it? | `agent_id` from `agent.task.started` / `agent.task.completed` |
| Which model and prompt version were used? | Model inventory + prompt version recorded in every generation call |
| What information was retrieved? | `sources_used[]` from the retrieval log (document IDs, chunk IDs, scores) |
| What did the AI recommend? | Agent output payload in `ai.recommendation.created` event |
| What did the creator approve? | Approval token record in `ai.action.approved` / `ai.action.rejected` |
| What external action occurred? | `ai.action.executed` event with external system reference |

---

## 12. AI Event Architecture

All events use the existing Transactional Outbox + Confluent Cloud Kafka
infrastructure established in Phases 7–8. No new broker is introduced.
New AI topics are named under an `agent.*` and `ai.*` prefix namespace.
Schemas will be defined in Avro in a later implementation phase.

| Event | Producer | Consumers | Purpose | Message key | Sensitivity |
|---|---|---|---|---|---|
| `creator.goal.created` | Flask API (new `/api/agent/goals` route) | Orchestrator | Creator submits a natural-language goal | `creator_id` | Medium |
| `agent.plan.created` | Orchestrator | Governance recorder, artist notification consumer | Plan constructed from intent classification | `plan_id` | Low |
| `agent.task.started` | Orchestrator | Specialized agent, governance recorder | Dispatch a task to a specialized agent | `plan_id` | Low |
| `agent.task.completed` | Specialized agent | Orchestrator, governance recorder | Agent has finished its task; payload includes output | `plan_id` | Medium |
| `agent.task.failed` | Specialized agent | Orchestrator, alerting consumer | Task failed after retries; includes error detail | `plan_id` | Low |
| `ai.recommendation.created` | Any generative agent | Governance recorder, analytics consumer | AI has produced a recommendation or generated text | `creator_id` | High |
| `ai.action.approval.requested` | Orchestrator | Artist notification consumer, UI push | Approval gate reached; creator must confirm | `plan_id` | Medium |
| `ai.action.approved` | Orchestrator (on receipt of creator confirmation) | Orchestrator (resumes plan), governance recorder | Creator approved the proposed action | `plan_id` | Medium |
| `ai.action.rejected` | Orchestrator | Orchestrator (aborts step), governance recorder | Creator denied the proposed action | `plan_id` | Medium |
| `ai.action.executed` | Orchestrator | Governance recorder, analytics consumer | An approved action was executed (write or external call) | `plan_id` | High |
| `knowledge.document.ingested` | Ingestion worker | RAG index builder, governance recorder | A document was parsed, chunked, and indexed | `creator_id` | High |
| `governance.violation.detected` | Governance evaluation job | Platform reviewer notification consumer | A policy threshold was breached | `violation_id` | High |
| `agent.plan.completed` | Orchestrator | Analytics Agent (scheduled), governance recorder, artist notification consumer | All plan steps finished | `plan_id` | Low |

**Existing events preserved:** `fan.followed`, `orders.created`,
`artist.post_published`, `analytics.browse_event` remain unchanged and are
consumed by the Fan Intelligence / Analytics agents as documented in Section 6.

---

## 13. Phase 9 Implementation Roadmap

### 13.1 Phasing Principle

Agents are introduced in strict order of risk and reversibility:
read-only → recommended (creator-approved writes) → coordinated multi-agent →
approved external actions. No phase begins before its predecessor's exit criteria
are met and governance evaluation passes.

---

### Phase 9A — Read-Only Creator Assistant (RAG + Education)

**Objective:** Deliver a safe, high-value AI assistant that answers music-business
questions using authoritative sourced knowledge. Zero write capability. Zero
external actions.

**Features:**
- Natural-language Q&A: royalty education, PRO/CMO guidance, copyright basics,
  distribution requirements, ArtistHub help
- All answers cite the retrieved knowledge source
- AI interpretation clearly labelled separately from sourced content
- Global ArtistHub Knowledge corpus only (no private creator documents yet)

**watsonx components:** watsonx.ai (Granite instruct, embeddings endpoint);
watsonx.data (global knowledge vector index); watsonx.governance (model inventory,
prompt versioning, groundedness monitoring)

**Kafka additions:** `creator.goal.created` (producer: new `/api/agent/goals`
endpoint, read-only goal type only); `ai.recommendation.created`

**Database additions:** None. No schema changes to ArtistHub DB.

**Security/governance:** Global knowledge namespace only; `creator_id` scope not
yet required (no private data); all responses include `sources_used[]`; prompt
injection sanitisation in place from day one; watsonx.governance registered

**Tests:** Unit tests for `watsonx_client.py` retrieval path; integration tests
confirming no write to DB; tests confirming citation block present on every
response; flake8 passing

**Exit criteria:** ≥90% of test answers cite a retrieved passage; groundedness
score ≥0.80 on held-out eval set; zero DB writes observed in integration test run;
governance model inventory entry confirmed

---

### Phase 9B — Private Creator Knowledge Ingestion

**Objective:** Allow creators to upload private documents (royalty statements,
contracts, release metadata) and ask questions against their own content.

**Features:** Document upload endpoint; ingestion pipeline (parse → chunk →
embed → private namespace index); creator-scoped Q&A against private documents;
answers distinguish global vs. private source citations

**watsonx components:** watsonx.ai (embeddings); watsonx.data (private
creator namespace in vector index)

**Kafka additions:** `knowledge.document.ingested`

**Database additions:** `creator_document` table (document_id, creator_id,
filename, fingerprint, ingestion_status, created_at)

**Security/governance:** Tenant isolation enforced at query construction; `creator_id`
filter mandatory before vector search; document parsing in sandboxed worker;
training opt-out policy active; retention/deletion flow implemented

**Exit criteria:** Integration tests confirm cross-creator retrieval returns zero
results; deletion test confirms purge within 24 hours; governance namespace audit
log populated correctly

---

### Phase 9C — Recommended Action Agents (Level 2)

**Objective:** Introduce Release Agent and Creator Business Agent with Level 2
(propose → approve → write) capability.

**Features:** Release metadata suggestion and diff-for-approval flow; Creator
Business pricing recommendation proposals; approval token mechanism; plan pause/resume

**watsonx components:** watsonx.ai (Granite chat for proposals); watsonx.data
(genre benchmarks for pricing); watsonx.governance (approval decision logging)

**Kafka additions:** `agent.plan.created`, `agent.task.started`,
`agent.task.completed`, `agent.task.failed`, `ai.action.approval.requested`,
`ai.action.approved`, `ai.action.rejected`, `ai.action.executed`

**Database additions:** `agent_plan` table (plan_id, creator_id, goal_text,
intent_type, status, created_at); `agent_approval` table (approval_id, plan_id,
step, level, decision, token_hash, decided_at)

**Security/governance:** Action-type registry deployed; approval token expiry (15
min) enforced; all approvals logged in watsonx.governance; circuit breaker active

**Exit criteria:** Integration tests confirm no write occurs without approval token;
expired token rejection tested; governance log contains approval record for every
executed action

---

### Phase 9D — Content Moderation & Marketing Agent

**Objective:** Add ContentModerator (pre-publish screening) and Marketing Agent
(campaign copy generation and approval).

**Features:** Post and merch description screening before publish; Marketing Agent
campaign proposal and per-post Level 2 approval; BioWriter sub-capability exposed
through Release Agent

**watsonx components:** watsonx.ai (Granite instruct for classification and copy
generation); watsonx.governance (classification threshold policy, copy approval
audit)

**Kafka additions:** `agent.marketing.plan_proposed` (internal); existing
`artist.post_published` consumed by Marketing Agent

**Security/governance:** ContentModerator threshold via environment variable;
governance bias monitoring for over-flagging; all copy approvals and rejections
logged

**Exit criteria:** ContentModerator false-positive rate ≤5% on genre-balanced
eval set; all marketing copy proposals auditable end-to-end in governance log

---

### Phase 9E — Full Orchestrator + Multi-Agent Coordination

**Objective:** Activate the Orchestrator Agent and the remaining specialized agents
(Publishing & Royalties, Rights & Protection, Analytics, Fan Intelligence).
Enable the complete `RELEASE_LAUNCH` multi-agent plan from Section 8.4.

**Features:** Intent classification routing; multi-agent plan construction and
dispatch; Publishing & Royalties education in plan context; Rights assessment in
plan context; Analytics post-release summary; personalised fan recommendation
ranking

**watsonx components:** All components active; full governance workflow coverage

**Kafka additions:** `agent.plan.completed`; Analytics Agent consuming existing
`analytics.browse_event`

**Database additions:** `fan_pseudonym` table (fan_id hash → pseudonym mapping,
for recommendation feature store)

**Security/governance:** Full auditor query (Section 11.4) demonstrated against
a test plan; cross-agent data isolation verified; Fan Intelligence privacy audit
(no individual fan data exposed to artists)

**Exit criteria:** End-to-end plan test passes all seven auditor questions; bias
checks pass for Fan Intelligence across at least three genre segments; no
cross-creator data leakage in integration test suite

---

### Phase 9F — Level 3 External Action Framework (Future)

**Objective:** Enable approved external actions (PRO registration initiation,
DSP distribution submission) under the Level 3 two-step confirmation model.

**Note:** This phase is intentionally scoped out of the Phase 9 immediate roadmap.
It requires additional legal review of platform liability, external API integration
agreements, and a mature governance audit record from Phases 9A–9E before
proceeding.

---

### 13.2 Phase 9A Recommendation

**Phase 9A must build:**

1. `watsonx_client.py` — shared IAM token refresh, circuit breaker, timeout, governance SDK initialisation
2. `services/rag_retrieval.py` — global knowledge namespace query, citation block construction, source labelling
3. Global knowledge corpus — initial set: PRO registration guides (ASCAP, BMI, SESAC), mechanical royalty basics, performance royalty basics, ISRC/ISWC reference, DMCA overview, ArtistHub help content
4. New endpoint `POST /api/agent/goals` — accepts `{"goal_type": "QUESTION", "text": "..."}` from authenticated artist; returns `{"answer": "...", "sources_used": [...], "ai_interpretation_present": bool}`
5. `creator.goal.created` and `ai.recommendation.created` Kafka events (producer only in Phase 9A)
6. watsonx.governance registration — model inventory entry, prompt template version, groundedness monitoring activated

**Phase 9A must NOT build:**

- Any agent that writes to the ArtistHub database
- Private creator document ingestion or private namespace retrieval
- The Orchestrator or any multi-agent coordination
- Approval tokens or approval gate mechanism
- Any connection to an external system (PRO, DSP, royalty aggregator)
- Fan-facing recommendation ranking changes
- ContentModerator integration into existing post/merch routes
- Level 2 or Level 3 action capability of any kind

The value of Phase 9A is establishing a safe, fully observable, fully governed
RAG foundation that creators can trust before any write or external capability is
layered on top.

---

*This document is complete through Section 13. Implementation phases begin with Phase 9A as defined in Section 13.2.*
