# ArtistHub Observability Architecture

**Phase 8H** — Prometheus metrics, structured logging, alerting rules, Grafana dashboards, SBOM generation, cosign image signing.

---

## Architecture Overview

```
                    ┌─────────────────────────────────────────────────┐
                    │           OpenShift Cluster (artisthub)          │
                    │                                                   │
  ┌─────────────────┴──────────────────────────────────────────────────┴────┐
  │                                                                          │
  │  ┌──────────────────────┐    ┌─────────────────────────────────────┐   │
  │  │   Flask backend       │    │  openshift-monitoring namespace      │   │
  │  │   port 5000           │    │                                     │   │
  │  │   GET /metrics ──────────→│  Prometheus (User Workload           │   │
  │  │   (prometheus_flask_  │    │  Monitoring)                        │   │
  │  │   exporter)           │    │                                     │   │
  │  └──────────────────────┘    │  ServiceMonitor (backend)           │   │
  │                               │  PodMonitor (workers)               │   │
  │  ┌──────────────────────┐    │  PrometheusRule (alerting)          │   │
  │  │  outbox-relay         │    │                                     │   │
  │  │  port 9090 ──────────────→│  AlertManager → Slack/PagerDuty     │   │
  │  │  GET /metrics         │    │                                     │   │
  │  │  (prometheus_client)  │    │  Grafana (platform-health,          │   │
  │  └──────────────────────┘    │           kafka-pipeline,           │   │
  │                               │           business-ops)             │   │
  │  ┌──────────────────────┐    └─────────────────────────────────────┘   │
  │  │  analytics-consumer   │                                              │
  │  │  port 9090 ──────────────→  PodMonitor scrape                       │
  │  │  GET /metrics         │                                              │
  │  └──────────────────────┘                                              │
  │                                                                          │
  │  ┌──────────────────────┐                                              │
  │  │  notification-consumer│                                              │
  │  │  port 9090 ──────────────→  PodMonitor scrape                       │
  │  │  GET /metrics         │                                              │
  │  └──────────────────────┘                                              │
  │                                                                          │
  └──────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### Backend Metrics (`prometheus_flask_exporter`)

**Endpoint:** `GET /metrics` on port 5000  
**Scrape mechanism:** `ServiceMonitor` (`openshift/base/monitoring/servicemonitor-backend.yaml`)  
**Key metrics:**

| Metric | Type | Description |
|---|---|---|
| `flask_http_request_total` | Counter | Total HTTP requests by method, path, status |
| `flask_http_request_duration_seconds` | Histogram | Request latency by endpoint |
| `flask_http_request_exceptions_total` | Counter | 5xx exceptions by endpoint |

**Labels:** `app="artisthub-backend"`, `http_method`, `http_status`, `path`  
**Low-cardinality policy:** Never use user IDs, artist IDs, or any free-form value as a label.

**Correlation ID middleware:**
- Every request receives a UUID correlation ID
- Stored in `flask.g.correlation_id`
- Echoed in `X-Correlation-ID` response header
- Propagated from caller via `X-Correlation-ID` request header

---

### Worker Metrics (`prometheus_client`)

Workers expose metrics on **port 9090** via `prometheus_client.start_http_server()`.  
**Scrape mechanism:** `PodMonitor` (`openshift/base/monitoring/podmonitor-workers.yaml`)

Workers have **no Kubernetes Service** — the PodMonitor scrapes Pod IPs directly.  
The existing `allow-ocp-monitoring` NetworkPolicy (policy #10) is updated to allow port 9090.

#### Outbox Relay Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `artisthub_relay_poll_cycles_total` | Counter | — | Total relay poll loop iterations |
| `artisthub_relay_publish_total` | Counter | `topic` | Events successfully published |
| `artisthub_relay_publish_failures_total` | Counter | `topic` | Events that failed Kafka publish |
| `artisthub_relay_outbox_backlog` | Gauge | — | Current pending outbox row count |
| `artisthub_relay_outbox_oldest_age_seconds` | Gauge | — | Age of oldest pending row |

#### Analytics Consumer Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `artisthub_analytics_messages_processed_total` | Counter | `event_type` | Events processed |
| `artisthub_analytics_messages_skipped_total` | Counter | — | Unknown type / duplicate |
| `artisthub_analytics_messages_deadlettered_total` | Counter | `reason` | Dead-lettered events |
| `artisthub_analytics_db_retries_total` | Counter | — | DB retry attempts |

#### Notification Consumer Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `artisthub_notification_messages_processed_total` | Counter | — | Events processed |
| `artisthub_notification_messages_skipped_total` | Counter | — | Unknown type / duplicate |
| `artisthub_notification_messages_deadlettered_total` | Counter | `reason` | Dead-lettered events |
| `artisthub_notification_notifications_created_total` | Counter | — | Notification rows inserted |
| `artisthub_notification_db_retries_total` | Counter | — | DB retry attempts |

---

## Structured Logging

All ArtistHub processes emit **structured JSON logs** via `app/utils/structured_logging.py`.

### Log Record Format

```json
{
  "timestamp": "2025-01-15T10:23:45.123456Z",
  "level": "INFO",
  "logger": "app.services.outbox_relay",
  "message": "Relay: published 3 event(s)",
  "worker": "relay",
  "correlation_id": ""
}
```

### Fields

| Field | Description |
|---|---|
| `timestamp` | ISO-8601 UTC (always ends with `Z`) |
| `level` | Python log level name |
| `logger` | Logger name (module path) |
| `message` | Human-readable log message |
| `worker` | Static worker identifier: `relay`, `analytics`, `notification`, or `""` for Flask |
| `correlation_id` | Request UUID (Flask backend) or `""` for workers |

### Configuration

- **Environment variable:** `LOG_LEVEL` (default: `INFO`)
- **Flask backend:** `configure_structured_logging()` is called in `create_app()` — not yet; called in worker entry points
- **Workers:** Called at the start of each `run()` function before the main loop

---

## Alerting Rules

Defined in `openshift/base/monitoring/prometheusrule.yaml`.

| Alert | Severity | Condition | For |
|---|---|---|---|
| `BackendHighErrorRate` | warning | 5xx rate > 1% over 5m | 5m |
| `BackendNoHealthyReplicas` | critical | 0 ready backend pods | 2m |
| `OutboxBacklogHigh` | warning | pending events > 500 | 5m |
| `OutboxOldestEventStale` | warning | oldest pending > 10 min | 5m |
| `OutboxPublishFailures` | warning | any publish failures in 15m | 0m |
| `AnalyticsDeadLetterMessagesDetected` | warning | any dead-letter in 15m | 0m |
| `NotificationDeadLetterMessagesDetected` | warning | any dead-letter in 15m | 0m |
| `AnalyticsConsumerDown` | critical | 0 ready analytics pods | 5m |
| `NotificationConsumerDown` | critical | 0 ready notification pods | 5m |
| `OutboxRelayDown` | critical | 0 ready relay pods | 5m |
| `WorkerCrashLoopBackOff` | warning | any pod in CrashLoopBackOff | 5m |

---

## Grafana Dashboards

Defined in `openshift/base/monitoring/grafana-dashboards.yaml` as ConfigMaps.

| Dashboard | UID | Key Panels |
|---|---|---|
| Platform Health | `artisthub-platform-health` | Request rate, error rate, latency p95, replica counts |
| Kafka Event Pipeline | `artisthub-kafka-pipeline` | Outbox backlog, publish rate, consumer processed rate, dead-letter rate |
| Business Operations | `artisthub-business-ops` | Follow/unfollow rate, release/post/merch creation, DB retry rate |

---

## OpenShift User Workload Monitoring

The `artisthub` namespace has `openshift.io/cluster-monitoring: "true"` set in `openshift/base/namespace.yaml`.

This enables OpenShift User Workload Monitoring (UWM), which automatically:
1. Picks up `ServiceMonitor` and `PodMonitor` resources in the namespace
2. Evaluates `PrometheusRule` resources
3. Provides a Grafana instance that can import the dashboard ConfigMaps

**No separate Prometheus stack is needed** — UWM provides the full stack.

---

## SBOM (Software Bill of Materials)

Phase 8H adds **syft** SBOM generation to `release.yml`:

- **Format:** CycloneDX JSON (`.cdx.json`)
- **Tool:** `anchore/sbom-action/download-syft`
- **Trigger:** After each successful image build (parallel to Trivy scan)
- **Storage:** GitHub Actions artifacts, 90-day retention
- **Artifact names:**
  - `sbom-backend-<sha>` → `sbom-backend.cdx.json`
  - `sbom-frontend-<sha>` → `sbom-frontend.cdx.json`

---

## Image Signing (cosign keyless)

Phase 8H adds **cosign keyless** signing to `release.yml`.

### Design

- **Tool:** `sigstore/cosign-installer` + `cosign sign`
- **Method:** Keyless OIDC (GitHub Actions OIDC token as identity)
- **No stored key:** The GitHub Actions OIDC token creates a short-lived certificate via Sigstore
- **Signature storage:** OCI artifact attached to the image in the registry
- **Job:** `sign-images` — runs after `publish`, skipped when no registry is configured

### Verification Command

```bash
cosign verify <image>@<digest> \
  --certificate-identity-regexp="https://github.com/YOUR_ORG/.*" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

**⚠ VERIFICATION STATUS: BLOCKED** — No live registry is connected in the current environment. Signing runs in the workflow; verification cannot be demonstrated without a live registry and active GitHub Actions run.

---

## Confluent Cloud Smoke Test Workflow

`confluent-smoke.yml` is a **manual-only** workflow that runs metadata-only connectivity checks against Confluent Cloud.

### What it checks
- TLS connectivity to bootstrap endpoints
- SASL_SSL authentication
- Topic existence and partition counts
- Schema Registry reachability (metadata query)

### What it does NOT do
- Does NOT produce events to Kafka topics
- Does NOT consume messages
- Does NOT register schemas or modify ACLs
- Does NOT require ArtistHub application to be running

### Mode control
`SMOKE_METADATA_ONLY=1` in `confluent-smoke-test.py` switches the script to metadata-only mode, skipping producer/consumer tests.

---

## actionlint

Phase 8H adds an `actionlint` job to `ci.yml` that validates all GitHub Actions workflow YAML files for syntax and semantic errors on every push/PR to `main`.

```yaml
- uses: rhysd/actionlint@v1.7.7
```

This catches:
- Invalid expression syntax (`${{ }}`)
- Undefined secrets/variables
- Job dependency cycles
- Invalid `uses` references

---

## Known Limitations (Phase 8H)

1. **Kafka consumer lag not directly measurable** — Confluent Cloud consumer lag is available via Confluent metrics API or Prometheus JMX exporter, but neither is integrated in Phase 8H. Lag is inferred from `OutboxBacklogHigh` and `*ConsumerDown` alerts. Phase 8I (KEDA) will add lag-based autoscaling.
2. **Verification of cosign signatures is BLOCKED** — No live registry available. The signing workflow structure is in place.
3. **Alert thresholds are conservative MVP estimates** — All thresholds (1% error rate, 500 pending events, etc.) should be calibrated after Phase 8J load testing.
4. **LOG_LEVEL control per-worker not yet in ConfigMap** — Add `LOG_LEVEL` to `artisthub-config` ConfigMap for per-workload control in Phase 8I.
5. **Grafana Operator not provisioned** — Dashboard ConfigMaps are ready but require Grafana Operator or manual import.
6. **SBOM signing** — SBOM artifacts are unsigned in Phase 8H. Phase 8I/8J may add SBOM attestation via cosign attach.
