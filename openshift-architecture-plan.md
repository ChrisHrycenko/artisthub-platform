# ArtistHub Phase 8 — Red Hat OpenShift Production Deployment
## Architecture Design Document (Design Only — No Implementation)

**Base:** v0.2.0 tag  
**Scope:** Design only. No files are created or modified beyond this document.  
**Status:** Pending approval before implementation begins.

---

## 1. Current Docker Architecture → Target OpenShift Architecture

### Current (v0.2.0 Docker Compose)

| Container | Image | Role | State |
|---|---|---|---|
| `backend` | `python:3.11-slim` + gunicorn | Flask API (2 workers) | Stateful (SQLite vol) |
| `web` | `nginx:1.25-alpine` | Static SPA + API proxy | Stateless |
| `redpanda` | `redpandadata/redpanda:v24.1.13` | Kafka broker + Schema Registry | Stateful (volume) |
| `redpanda-console` | `redpandadata/console:v2.7.2` | Broker UI | Stateless |
| `kafka-init` | `redpandadata/redpanda` | One-shot topic creator | Job |
| `outbox-relay` | same as backend | Transactional outbox poller | Stateless (needs DB) |
| `analytics-consumer` | same as backend | Kafka consumer group v1 | Stateless (needs DB) |
| `notification-consumer` | same as backend | Kafka consumer group v1 | Stateless (needs DB) |

**Key constraints of current architecture:**
- SQLite stored in a Docker named volume — single-host only, not scalable
- All services share the same Docker image (no separation of concerns in registry)
- `pgrep`-based health checks for worker processes — not OCP-compatible
- Redpanda embedded Schema Registry on port 8081 — requires persistent storage
- nginx serves static SPA — needs an OCP Route with TLS termination

### Target (OpenShift)

| Workload | Kind | Replicas | Stateful? |
|---|---|---|---|
| `backend` | Deployment | 2–4 | No (PostgreSQL external) |
| `frontend` (nginx) | Deployment | 2 | No |
| `outbox-relay` | Deployment | 1 (leader-elected) | No |
| `analytics-consumer` | Deployment | 1–6 (≤ partition count) | No |
| `notification-consumer` | Deployment | 1–6 (≤ partition count) | No |
| `postgresql` | StatefulSet or RHEL managed service | 1 primary + 1 replica | Yes |
| `kafka` | Confluent Cloud (recommended) or AMQ Streams | External / Operator | Yes |
| `schema-registry` | Confluent Cloud (bundled) or Apicurio | External / Deployment | Semi-stateful |

---

## 2. OpenShift Deployments, Services, Routes, ConfigMaps and Secrets

### Deployments

**`backend` Deployment**
```yaml
replicas: 2
image: image-registry.openshift-image-registry.svc:5000/artisthub/backend:v0.2.0
command: ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "--timeout", "60",
          "--access-logfile", "-", "--error-logfile", "-", "run:app"]
resources:
  requests: {cpu: 100m, memory: 256Mi}
  limits:   {cpu: 500m, memory: 512Mi}
securityContext: {runAsNonRoot: true, runAsUser: 1000680000}  # OCP arbitrary UID
```

**`frontend` Deployment**
```yaml
replicas: 2
image: image-registry.openshift-image-registry.svc:5000/artisthub/frontend:v0.2.0
# nginx serving /usr/share/nginx/html on port 8080 (non-root)
```

**`outbox-relay` Deployment**
```yaml
replicas: 1
command: ["python", "run_relay.py"]
# Only 1 replica to avoid concurrent outbox polling on the same DB
# If horizontal scale needed: add DB-level row locking (SELECT FOR UPDATE SKIP LOCKED)
```

**`analytics-consumer` Deployment**
```yaml
replicas: 1  # scale up to min(6, partition_count) for social/catalog/identity
command: ["python", "run_analytics_consumer.py"]
```

**`notification-consumer` Deployment**
```yaml
replicas: 1  # scale up to min(6, catalog_partitions)
command: ["python", "run_notification_consumer.py"]
```

### Services

| Service | Type | Port | Selector |
|---|---|---|---|
| `backend-svc` | ClusterIP | 5000 | `app=backend` |
| `frontend-svc` | ClusterIP | 8080 | `app=frontend` |
| `postgresql-svc` | ClusterIP | 5432 | `app=postgresql` |

### Routes

| Route | Host | TLS | Backend Service |
|---|---|---|---|
| `artisthub-route` | `artisthub.apps.<cluster>` | Edge (OCP-managed cert) | `frontend-svc:8080` |
| `api-route` | `artisthub.apps.<cluster>/api/*` | Edge (same cert) | `backend-svc:5000` |

**Note:** A single Route with path-based routing is preferred over two Routes to avoid cookie domain issues with Flask-Login sessions.

### ConfigMaps

**`artisthub-config`**
```yaml
FLASK_ENV: production
OUTBOX_POLL_INTERVAL: "5"
OUTBOX_BATCH_SIZE: "100"
ANALYTICS_MAX_RETRIES: "3"
ANALYTICS_RETRY_BACKOFF: "1.0"
NOTIF_MAX_RETRIES: "3"
NOTIF_RETRY_BACKOFF: "1.0"
KAFKA_CONSUMER_GROUP: artisthub.analytics.v1
NOTIF_CONSUMER_GROUP: artisthub.notifications.v1
KAFKA_SECURITY_PROTOCOL: SASL_SSL
KAFKA_SASL_MECHANISM: PLAIN
KAFKA_SCHEMAS_DIR: /app/kafka/schemas
CORS_ORIGINS: https://artisthub.apps.<cluster>
```

### Secrets

**`artisthub-secrets`** (type: Opaque, sourced from Vault or OCP Secrets)
```
SECRET_KEY
DATABASE_URL          (postgresql://user:pass@postgresql-svc:5432/artisthub)
CONFLUENT_API_KEY
CONFLUENT_API_SECRET
SCHEMA_REGISTRY_URL
SCHEMA_REGISTRY_API_KEY
SCHEMA_REGISTRY_API_SECRET
POSTGRESQL_PASSWORD   (also consumed by postgresql StatefulSet)
```

All Secrets are mounted as environment variables via `envFrom.secretRef`, not baked into the image.

---

## 3. PostgreSQL Production Migration Strategy

### SQLite → PostgreSQL Migration

**Phase 8A prerequisite:** The current codebase already supports PostgreSQL via `DATABASE_URL` — `config.py` falls back to SQLite only when the variable is absent. No application code changes are required for the switch.

**Migration steps (ordered):**

1. **Provision PostgreSQL** — Use `crunchy-postgres-operator` (Red Hat certified) or `postgresql` StatefulSet with PVC backed by OCP storage class.
2. **Export SQLite data** — `sqlite3 artisthub.db .dump > artisthub_dump.sql`; convert SQLite-specific SQL to PostgreSQL-compatible SQL (data types, autoincrement → sequences).
3. **Run Flask-Migrate** — `flask db upgrade` against the PostgreSQL URI to apply all migration files under `backend/migrations/versions/` (phase7c_outbox, phase7d_analytics, phase7e_notification).
4. **Import data** — Load converted dump into PostgreSQL using `psql`.
5. **Validate** — Run `pytest --cov=app` against the PostgreSQL `DATABASE_URL`; run smoke tests against the staging Route.
6. **Cut over** — Update `DATABASE_URL` Secret; rolling-restart Deployments.

**Connection pooling:** Add `psycopg2-binary` (already compatible with Flask-SQLAlchemy) and configure `SQLALCHEMY_POOL_SIZE=10`, `SQLALCHEMY_MAX_OVERFLOW=20` in `ProductionConfig` to handle multi-replica backend load.

**High availability:** Primary + 1 standby replica with streaming replication. Crunchy Postgres Operator handles automatic failover. Connection string uses the operator's service endpoint (automatic failover transparent to app).

---

## 4. Confluent Cloud vs Kafka on OpenShift — Recommendation

### Option A: Confluent Cloud (Recommended ✅)

| Aspect | Detail |
|---|---|
| Kafka brokers | Fully managed, multi-AZ |
| Schema Registry | Confluent Cloud SR (already coded: `SCHEMA_REGISTRY_API_KEY/SECRET`) |
| Operations | Zero broker management |
| Cost | Pay-per-GB; suitable for MVP and growth |
| Security | SASL_SSL (already wired in both consumers and producer) |
| Latency | ~5–10ms additional vs in-cluster; acceptable for ArtistHub workloads |
| Migration | Zero code changes — consumers already support `SASL_SSL` + `CONFLUENT_API_KEY` |

### Option B: AMQ Streams (Kafka Operator on OCP)

| Aspect | Detail |
|---|---|
| Kafka brokers | KRaft-mode pods, OCP-managed |
| Schema Registry | Apicurio Registry (separate Deployment) |
| Operations | High — requires ZooKeeper/KRaft tuning, storage provisioning, cert rotation |
| Cost | Infrastructure cost absorbed into OCP cluster |
| Benefit | Full in-cluster; no egress charges |
| Risk | Operational overhead disproportionate to MVP scale |

### Recommendation: **Confluent Cloud**

The application already has complete SASL_SSL consumer/producer config paths. Confluent Cloud eliminates broker operations, provides built-in Schema Registry with the existing API key/secret pattern, and requires zero application code changes. The 5–10ms latency overhead is irrelevant for the outbox relay's 5-second poll interval and consumer workloads. Revisit AMQ Streams only if monthly Confluent egress costs exceed ~$500/month at scale.

---

## 5. Existing Container Compatibility with OpenShift's Non-Root Security Model

### Current state (favorable)

The existing [`docker/Dockerfile`](docker/Dockerfile) already:
- Creates a non-root system user `artisthub` (UID not pinned — inherits from `adduser --system`)
- Switches to `USER artisthub` before the final `CMD`
- Sets `/app/instance` ownership to `artisthub:artisthub`

### OpenShift SCC requirement

OCP's `restricted` SCC assigns an arbitrary UID from the project's UID range (e.g., `1000680000`). The container **must not** specify a fixed numeric UID in the Dockerfile, or it must be within the range.

**Required change (Phase 8B):** Remove the fixed `adduser --system` call and instead set permissions via group (GID 0 pattern — OpenShift convention):
```dockerfile
RUN chown -R 0:0 /app && chmod -R g=u /app
USER 1001
```
This makes the container runnable under any arbitrary OCP UID while the GID remains 0 (root group, non-privileged). This is the standard OpenShift image guideline.

**nginx frontend:** The stock `nginx:1.25-alpine` image binds port 80 as root. Replace with `nginxinc/nginx-unprivileged:1.25-alpine` which binds port 8080 and runs as UID 101 — fully compatible with OCP `restricted` SCC.

**Worker processes (relay, consumers):** No HTTP port binding; they only need filesystem and network. No SCC changes beyond the GID 0 fix above.

---

## 6. Health/Readiness/Startup Probes

### `backend` (gunicorn — HTTP available)

```yaml
startupProbe:
  httpGet: {path: /api/health, port: 5000}
  failureThreshold: 12     # allows 60s for slow DB init
  periodSeconds: 5
livenessProbe:
  httpGet: {path: /api/health, port: 5000}
  initialDelaySeconds: 0
  periodSeconds: 30
  failureThreshold: 3
readinessProbe:
  httpGet: {path: /api/health, port: 5000}
  periodSeconds: 10
  failureThreshold: 3
```

The existing `/api/health` endpoint executes `SELECT 1` — it is already a meaningful readiness probe that gates traffic until the DB is reachable.

### `frontend` (nginx — HTTP available)

```yaml
livenessProbe:
  httpGet: {path: /, port: 8080}
  periodSeconds: 30
readinessProbe:
  httpGet: {path: /, port: 8080}
  periodSeconds: 10
```

### `outbox-relay`, `analytics-consumer`, `notification-consumer` (no HTTP)

These are long-running Python processes with no HTTP server. The `pgrep` healthcheck used in Docker Compose is not available in OCP probes (which require exec, httpGet, or tcpSocket).

**Solution — exec probe against a sentinel file:**

Each worker writes a heartbeat file (`/tmp/worker_alive`) updated every poll cycle. The probe checks file freshness:

```yaml
livenessProbe:
  exec:
    command: ["python", "-c",
      "import os,time; s=os.stat('/tmp/worker_alive'); assert time.time()-s.st_mtime<60"]
  periodSeconds: 30
  failureThreshold: 3
startupProbe:
  exec:
    command: ["test", "-f", "/tmp/worker_alive"]
  failureThreshold: 6
  periodSeconds: 10
```

**Required code change (Phase 8B):** Add a `Path('/tmp/worker_alive').touch()` call inside the main loop of `run_relay.py`, `run_analytics_consumer.py`, and `run_notification_consumer.py`. This is a 1–2 line change per file.

No readinessProbe is needed for workers — they are not behind a Service/Route.

---

## 7. Horizontal Scaling and Kafka Partition/Consumer-Group Limits

### `backend` — stateless, freely scalable

```
minReplicas: 2  maxReplicas: 8
HPA metric: cpu utilization > 70%
```

Gunicorn workers are independent; the PostgreSQL connection pool (`SQLALCHEMY_POOL_SIZE`) must be sized as `pool_size × backend_replicas ≤ PostgreSQL max_connections`.

### `outbox-relay` — **max 1 replica**

The relay polls `event_outbox WHERE published_at IS NULL`. Multiple replicas cause duplicate publishes and race conditions on row updates. Kept at `replicas: 1`. To achieve HA, implement `SELECT FOR UPDATE SKIP LOCKED` in `outbox_relay.py` (Phase 8 scope item) and then allow `replicas: 2`.

### `analytics-consumer` — limited by partition count

Topic subscriptions: `artisthub.social` (6 partitions), `artisthub.catalog` (6 partitions), `artisthub.identity` (3 partitions).

Consumer group `artisthub.analytics.v1` subscribes to all three topics. The Kafka rebalancer assigns partitions across all active consumers. **Maximum useful replicas = max partition count of any subscribed topic = 6.** Additional replicas beyond 6 receive no partition assignment and are idle. Recommended: `minReplicas: 1, maxReplicas: 6` with Kafka consumer lag as the HPA metric (via KEDA).

### `notification-consumer` — limited by catalog partition count

Subscribes to `artisthub.catalog` only (6 partitions). **Maximum useful replicas = 6.** Same KEDA-based HPA approach.

### KEDA (Kubernetes Event-Driven Autoscaler)

OCP supports KEDA via the KEDA operator. A `ScaledObject` targeting Confluent Cloud lag metric scales consumer Deployments automatically:

```yaml
kind: ScaledObject
spec:
  scaleTargetRef: {name: analytics-consumer}
  minReplicaCount: 1
  maxReplicaCount: 6
  triggers:
  - type: kafka
    metadata:
      bootstrapServers: pkc-xxxxx.confluent.cloud:9092
      consumerGroup: artisthub.analytics.v1
      topic: artisthub.social
      lagThreshold: "50"
```

---

## 8. Persistent vs Stateless Components

| Component | Persistent? | Storage | Notes |
|---|---|---|---|
| `backend` | ❌ Stateless | None | All state in PostgreSQL |
| `frontend` | ❌ Stateless | None | Read-only config at startup |
| `outbox-relay` | ❌ Stateless | None | Reads/writes PostgreSQL |
| `analytics-consumer` | ❌ Stateless | None | Reads/writes PostgreSQL; offsets in Kafka |
| `notification-consumer` | ❌ Stateless | None | Reads/writes PostgreSQL; offsets in Kafka |
| `postgresql` | ✅ Persistent | PVC (block storage, RWO) | Primary + replica StatefulSet |
| `kafka` (Confluent Cloud) | ✅ Managed | Confluent-managed | No OCP PVC needed |
| `schema-registry` (Confluent) | ✅ Managed | Confluent-managed | No OCP PVC needed |

**PostgreSQL PVC sizing (initial):** 20 GiB gp3 block storage. The `event_outbox` and `processed_event` tables grow unboundedly without archival — add a cron job to purge `published_at IS NOT NULL AND published_at < NOW() - INTERVAL '30 days'` rows.

---

## 9. RBAC, ServiceAccounts, NetworkPolicies, TLS and Secrets Management

### ServiceAccounts

Each Deployment gets a dedicated ServiceAccount with no cluster-level permissions:
- `sa-backend`
- `sa-frontend`
- `sa-outbox-relay`
- `sa-analytics-consumer`
- `sa-notification-consumer`

No `automountServiceAccountToken: true` unless a specific service needs OCP API access.

### RBAC

No cluster roles needed. Project-scoped Role allowing workers to read their own ConfigMaps/Secrets:
```yaml
rules:
- apiGroups: [""]
  resources: ["configmaps", "secrets"]
  verbs: ["get"]
```

### NetworkPolicies

```yaml
# Default deny all ingress/egress
kind: NetworkPolicy
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
---
# Allow frontend → backend on 5000
# Allow backend → postgresql on 5432
# Allow outbox-relay, consumers → postgresql on 5432
# Allow all workers → Confluent Cloud egress on 9092, 443
# Allow OCP monitoring → all pods on metrics port
# Allow ingress controller → frontend on 8080, backend on 5000
```

### TLS

- **Ingress TLS:** OCP Router handles TLS termination (Edge mode) using a Let's Encrypt cert via `cert-manager` operator. Pods communicate over plain HTTP internally.
- **Database TLS:** Enforce `sslmode=require` in `DATABASE_URL`.
- **Kafka TLS:** `KAFKA_SECURITY_PROTOCOL=SASL_SSL` already in place — Confluent Cloud endpoint is TLS-only.
- **Schema Registry TLS:** Confluent Cloud SR endpoint is HTTPS-only.

### Secrets Management

**Preferred approach: HashiCorp Vault + Vault Agent Injector (OCP-native)**
- Secrets never touch OCP etcd
- Vault Agent sidecar injects secrets as environment variables or files at pod startup
- Secret rotation triggers a rolling restart without image rebuild

**Fallback: OCP Secrets + Sealed Secrets (Bitnami)**
- `SealedSecret` resources are safe to commit to git — decrypted only in-cluster
- Simpler operational model for small teams

**Mandatory:** No secrets in `ConfigMap`, no secrets in container environment literals in Deployment manifests.

---

## 10. GitHub Actions → Image Registry → OpenShift CI/CD Architecture

### Current CI (`.github/workflows/ci.yml`)

Two parallel jobs: `lint` (flake8) and `test` (pytest + coverage ≥ 80%). No build or push steps.

### Target CI/CD Pipeline

```
GitHub Push to main
  │
  ├── Job: lint        (flake8 app/)
  ├── Job: test        (pytest --cov=app --cov-fail-under=80)
  │
  └── Job: build-push  (requires lint + test)
        ├── docker build -f docker/Dockerfile  → backend image
        ├── docker build -f docker/Dockerfile.frontend  → frontend image
        ├── docker push  → OCP Internal Registry  OR  Quay.io
        └── Job: deploy (requires build-push, on main branch only)
              └── oc rollout latest backend  (triggers rolling restart)
                  oc rollout latest frontend
                  oc rollout latest outbox-relay
                  oc rollout latest analytics-consumer
                  oc rollout latest notification-consumer
```

### GitHub Actions Secrets Required

```
OCP_SERVER          (https://api.<cluster>:6443)
OCP_TOKEN           (service account token with edit role in artisthub namespace)
OCP_NAMESPACE       (artisthub)
QUAY_USERNAME       (if using Quay.io registry)
QUAY_PASSWORD
```

### Image Tagging Strategy

- PR builds: `backend:pr-<number>-<sha7>` — pushed to staging namespace
- `main` merges: `backend:<semver>` + `backend:latest`
- Image digest pinned in Deployment (not floating `latest` tag) for reproducibility

### Rollout Strategy

All Deployments use `RollingUpdate` with:
```yaml
maxSurge: 1
maxUnavailable: 0
```
This ensures zero downtime. The `backend` readinessProbe gates traffic until the new pod passes `/api/health` (DB connectivity confirmed).

---

## 11. Logs, Metrics, Kafka Lag, DLQ Monitoring and Alerting

### Logging

All containers write structured logs to stdout/stderr (already the case — gunicorn `--access-logfile -` and Python `logging.basicConfig`). OCP's built-in Loki stack (Logging Operator) aggregates these.

**Recommended log format upgrade (Phase 8):** Switch to JSON structured logging using `python-json-logger` so log entries are queryable in Grafana/Loki without regex parsing.

### Metrics

- **`backend`:** Expose `/api/metrics` via `prometheus_flask_exporter` (to be added) — request rate, latency histograms, DB pool utilization.
- **Workers:** Export a Prometheus `Counter` and `Gauge` via `prometheus_client` HTTP server on port `8001` (separate from main process or via `multiprocess_mode`).
- **PostgreSQL:** `postgres_exporter` sidecar in the PostgreSQL pod.
- OCP Monitoring Stack (Prometheus + Grafana) scrapes all `ServiceMonitor` resources.

### Kafka Lag Monitoring

- **Confluent Cloud:** Native Confluent Cloud metrics API exposes consumer lag per group/topic/partition. Import via Prometheus remote_write or the Confluent Cloud Metrics API exporter.
- **Alert thresholds:**
  - `analyticsConsumerLag > 1000 for 5m` → Warning
  - `analyticsConsumerLag > 5000 for 2m` → Critical (page)
  - `notificationConsumerLag > 500 for 5m` → Warning

### DLQ Monitoring

- A Grafana panel counts messages in `artisthub.deadletter` (topic offset delta over time).
- Alert: `deadletterMessageRate > 0 for 10m` → Slack/PagerDuty notification.
- A dedicated `dlq-inspector` CronJob (or Redpanda Console equivalent) runs weekly to summarize DLQ contents and email the ops team.

### Alerting Stack

```
Prometheus AlertManager → PagerDuty (Critical)
                       → Slack #artisthub-alerts (Warning)
```

Key alert rules:
| Alert | Condition | Severity |
|---|---|---|
| BackendDown | No healthy backend pods | Critical |
| OutboxRelayDown | No healthy relay pods | Warning |
| OutboxBacklog | `published_at IS NULL` rows > 1000 | Warning |
| DatabaseDown | `pg_up == 0` | Critical |
| KafkaConsumerLagHigh | any group lag > 5000 | Critical |
| DLQMessageReceived | DLQ offset increase | Warning |
| CertExpiry | TLS cert expiry < 14d | Warning |

---

## 12. Failure and Recovery Scenarios

| Scenario | Impact | Automatic Recovery | Manual Action |
|---|---|---|---|
| Backend pod crash | Requests fail until rolling restart | OCP restarts pod; readinessProbe gates traffic | None if < 1 min |
| All backend pods down | 503 from Route | OCP restarts; HPA ensures min 2 | Check DB connectivity |
| PostgreSQL primary failure | All writes fail | Crunchy Operator promotes standby (~30s) | Validate connection string |
| Outbox relay crash | Events queue in `event_outbox` | OCP restarts pod; `published_at IS NULL` rows are retried on restart | Monitor outbox backlog |
| Kafka consumer crash | Lag accumulates; no data loss | OCP restarts pod; consumer resumes from last committed offset | Monitor lag alert |
| Confluent Cloud outage | Events queue in outbox; consumers paused | Outbox retains all events; consumers resume when CC recovers | None — inherent resilience |
| Schema Registry unavailable | `produce_avro()` fails; relay records `last_error` | Relay retries on next poll cycle | Check Schema Registry endpoint |
| DLQ message storm | Analytics/notification counters may be inconsistent | Manual DLQ replay | Inspect `artisthub.deadletter`, fix root cause, replay |
| PostgreSQL storage full | Writes fail | — | Add PVC capacity; purge old outbox/processed_event rows |
| Certificate expiry | HTTPS broken | cert-manager auto-renews 30d before expiry | Monitor cert-expiry alert |
| Image pull failure | Pod stuck in `ImagePullBackOff` | Retry backoff | Check registry credentials |

---

## 13. Proposed `openshift/` Manifest Structure

```
openshift/
├── namespace.yaml                    # Project / Namespace definition
├── rbac/
│   ├── serviceaccounts.yaml          # sa-backend, sa-frontend, sa-relay, sa-consumers
│   └── roles.yaml                    # Read-only ConfigMap/Secret roles
├── config/
│   ├── configmap.yaml                # artisthub-config (non-secret env vars)
│   └── sealed-secrets.yaml           # SealedSecret for artisthub-secrets (git-safe)
├── deployments/
│   ├── backend.yaml                  # Deployment + HPA
│   ├── frontend.yaml                 # Deployment + HPA
│   ├── outbox-relay.yaml             # Deployment (replicas: 1)
│   ├── analytics-consumer.yaml       # Deployment + ScaledObject (KEDA)
│   └── notification-consumer.yaml    # Deployment + ScaledObject (KEDA)
├── statefulsets/
│   └── postgresql.yaml               # StatefulSet + PVC (or Crunchy CRD)
├── services/
│   ├── backend-svc.yaml
│   ├── frontend-svc.yaml
│   └── postgresql-svc.yaml
├── routes/
│   └── artisthub-route.yaml          # Edge TLS Route → frontend (with /api path rewrite)
├── network-policies/
│   ├── default-deny.yaml
│   ├── allow-frontend-backend.yaml
│   ├── allow-backend-postgres.yaml
│   ├── allow-workers-postgres.yaml
│   ├── allow-workers-kafka-egress.yaml
│   └── allow-ingress-controller.yaml
├── monitoring/
│   ├── service-monitors.yaml         # Prometheus ServiceMonitor resources
│   ├── alert-rules.yaml              # PrometheusRule for all alerts
│   └── grafana-dashboards/
│       ├── artisthub-overview.json
│       └── kafka-lag.json
├── jobs/
│   └── db-migrate-job.yaml           # Pre-deployment Flask-Migrate Job
└── keda/
    ├── analytics-scaledobject.yaml
    └── notification-scaledobject.yaml
```

---

## 14. Implementation Phases 8A–8J

### Phase 8A — PostgreSQL Migration
**Goal:** Replace SQLite with PostgreSQL in a staging environment.  
**Work:** Provision Crunchy PostgreSQL operator; run `flask db upgrade`; data migration script; update `DATABASE_URL` Secret.  
**Validation:** Full pytest suite passes against PostgreSQL URI; `/api/health` returns `"database": "ok"`.  
**Rollback:** Revert `DATABASE_URL` Secret to SQLite path; no code changes needed.

### Phase 8B — Image Hardening for OCP
**Goal:** Images runnable under OCP `restricted` SCC.  
**Work:** Apply GID 0 Dockerfile pattern; switch nginx to `nginx-unprivileged`; add heartbeat file writes to workers; build Dockerfile.frontend.  
**Validation:** `oc adm policy who-can use scc restricted` confirms no privileged SCC needed; pods start without SCC escalation.  
**Rollback:** Revert Dockerfile changes; re-tag image.

### Phase 8C — Namespace, RBAC, NetworkPolicies, Secrets
**Goal:** Baseline OCP project security posture.  
**Work:** Apply `openshift/namespace.yaml`, `rbac/`, `network-policies/`, `config/`.  
**Validation:** `oc auth can-i` checks; NetworkPolicy egress test (confirm workers reach Confluent Cloud, cannot reach other namespaces).  
**Rollback:** `oc delete` the applied resources.

### Phase 8D — Confluent Cloud Integration
**Goal:** Switch from local Redpanda to Confluent Cloud.  
**Work:** Provision Confluent Cloud cluster; create topics (same names/partitions as current); register Avro schemas via `kafka/register_schemas.py`; update `KAFKA_BOOTSTRAP_SERVERS`, `CONFLUENT_API_KEY/SECRET`, `SCHEMA_REGISTRY_URL/API_KEY/SECRET` Secrets.  
**Validation:** Run `kafka/validate_schemas.py`; produce a test event; relay polls and publishes; consumer group shows committed offsets.  
**Rollback:** Revert Secret values to Redpanda endpoint (or keep both and switch via ConfigMap).

### Phase 8E — Deploy Stateless Workloads
**Goal:** Backend, frontend, outbox-relay, and consumers running on OCP.  
**Work:** Apply all Deployments and Services; configure probes (HTTP for backend/frontend, exec for workers).  
**Validation:** `oc rollout status deployment/backend`; Route returns 200; `/api/health` returns `"database": "ok"` and `"status": "ok"`; relay logs show published events; consumers show decreasing lag.  
**Rollback:** `oc scale deployment --replicas=0`; revert Docker Compose for continuity.

### Phase 8F — Routes and TLS
**Goal:** Public HTTPS endpoint with valid certificate.  
**Work:** Apply Route with Edge TLS; configure `cert-manager` for Let's Encrypt; validate session cookie `Secure` flag and SameSite behavior.  
**Validation:** `curl -I https://artisthub.apps.<cluster>/api/health` returns 200 with valid cert; Flask-Login session works across SPA pages.  
**Rollback:** Update Route to HTTP (non-TLS) temporarily.

### Phase 8G — CI/CD Pipeline
**Goal:** GitHub Actions builds and deploys on merge to `main`.  
**Work:** Add `build-push` and `deploy` jobs to `ci.yml`; create `sa-ci` ServiceAccount with `edit` role; store OCP credentials in GitHub Secrets.  
**Validation:** Merge a trivial PR; verify pipeline stages pass; verify `oc rollout status` succeeds.  
**Rollback:** Disable the deploy job; revert to manual `oc rollout`.

### Phase 8H — Monitoring and Alerting
**Goal:** Full observability stack active.  
**Work:** Add `prometheus_flask_exporter` to backend; add Prometheus metrics to workers; apply ServiceMonitors and PrometheusRules; import Grafana dashboards; configure AlertManager routes.  
**Validation:** Grafana shows real-time request rate and consumer lag; fire a test alert; confirm PagerDuty/Slack delivery.  
**Rollback:** Remove ServiceMonitors (no impact on application traffic).

### Phase 8I — KEDA Autoscaling
**Goal:** Consumer Deployments scale with Kafka lag.  
**Work:** Install KEDA operator; apply ScaledObjects for analytics and notification consumers; test scale-up by producing a burst of events.  
**Validation:** At lag > 50, OCP scales consumers from 1 → N; at lag = 0, scales back to 1.  
**Rollback:** Delete ScaledObjects; manually set `replicas`.

### Phase 8J — Production Hardening and Load Test
**Goal:** Validate SLA under realistic load.  
**Work:** Run `k6` or `locust` load test (target: 100 concurrent users); confirm no OOM kills; confirm no DB connection pool exhaustion; validate outbox relay keeps pace; validate DLQ remains empty.  
**Validation:** p99 API latency < 500ms; zero pod restarts during 10-minute soak; consumer lag < 100 throughout.  
**Rollback:** n/a (test environment only; findings fed back into resource limits).

---

## 15. Architecture Diagram

```mermaid
graph TD
    subgraph Internet
        User([Browser / Fan / Artist])
    end

    subgraph OpenShift Cluster ["OpenShift Cluster (artisthub namespace)"]
        Route["Route (Edge TLS)\nartisthub.apps.<cluster>"]
        
        subgraph Stateless Tier
            FE["frontend\nDeployment × 2\nnginx-unprivileged:1.25\nport 8080"]
            BE["backend\nDeployment × 2–4\ngunicorn 4w\nport 5000"]
            Relay["outbox-relay\nDeployment × 1\npython run_relay.py"]
            AC["analytics-consumer\nDeployment × 1–6\ngroup: analytics.v1"]
            NC["notification-consumer\nDeployment × 1–6\ngroup: notifications.v1"]
        end

        subgraph Persistent Tier
            PG[("PostgreSQL\nStatefulSet × 2\nPrimary + Standby\nPVC: 20Gi gp3")]
        end

        subgraph Security
            CM["ConfigMap\nartisthub-config"]
            SEC["Secret\nartisthub-secrets"]
            NP["NetworkPolicies\ndefault-deny +\nscoped allow rules"]
            SA["ServiceAccounts\nsa-backend / sa-relay\nsa-consumers"]
        end

        subgraph Observability
            PROM["Prometheus\n+ AlertManager"]
            GRAF["Grafana\nDashboards"]
            LOKI["Loki\nLog Aggregation"]
        end
    end

    subgraph External Services
        CC["Confluent Cloud\nKafka Broker\nSASL_SSL :9092\n\nTopics:\n• artisthub.social\n• artisthub.catalog\n• artisthub.identity\n• artisthub.deadletter"]
        SR["Confluent\nSchema Registry\nHTTPS :443"]
        GH["GitHub Actions\nCI/CD\nbuild → push → deploy"]
        REG["OCP Image Registry\nor Quay.io"]
    end

    User -->|HTTPS 443| Route
    Route -->|HTTP 8080| FE
    Route -->|HTTP 5000 /api/*| BE
    FE -->|static assets| FE
    BE -->|SQLAlchemy| PG
    Relay -->|SQLAlchemy poll outbox| PG
    Relay -->|produce Avro SASL_SSL| CC
    Relay -->|schema lookup| SR
    AC -->|consume SASL_SSL| CC
    AC -->|Avro decode| SR
    AC -->|write analytics_state| PG
    NC -->|consume SASL_SSL| CC
    NC -->|Avro decode| SR
    NC -->|write notifications| PG
    BE -->|write outbox rows| PG

    GH -->|docker push| REG
    GH -->|oc rollout| OpenShift Cluster
    REG -->|image pull| Stateless Tier

    PROM -->|scrape /metrics| BE
    PROM -->|scrape /metrics| Relay
    PROM -->|scrape /metrics| AC
    PROM -->|scrape /metrics| NC
    LOKI -->|log stream| Stateless Tier
    GRAF --> PROM
    GRAF --> LOKI

    style CC fill:#1565C0,color:#fff
    style SR fill:#1565C0,color:#fff
    style PG fill:#2E7D32,color:#fff
    style GH fill:#333,color:#fff
    style REG fill:#444,color:#fff
```

---

## 16. Executive and Technical Summaries

### 30-Second Executive Summary

ArtistHub v0.2.0 is a containerized music platform with real-time event streaming. Phase 8 moves it from a single-server Docker Compose setup to Red Hat OpenShift — a hardened, enterprise Kubernetes platform. The result is auto-healing deployments, encrypted HTTPS traffic, PostgreSQL for production data durability, Confluent Cloud for managed event streaming, and a GitHub Actions pipeline that ships tested code to production automatically. The platform will handle traffic spikes without manual intervention and page the team if anything breaks.

---

### 90-Second Solutions Sales Engineer Summary

ArtistHub v0.2.0 ships five containerized workloads: a Flask/gunicorn API, an nginx frontend, a transactional outbox relay, and two Kafka consumers for analytics and fan notifications. All event messages use Confluent Avro wire format with Schema Registry for schema evolution.

Phase 8 lifts this architecture onto OpenShift with zero code rewrites for the business logic. The primary changes are: (1) switching from SQLite to PostgreSQL using Flask-Migrate — one config variable; (2) pointing Kafka clients at Confluent Cloud, which the code already supports via `SASL_SSL` environment variables; (3) hardening the Docker images for OpenShift's non-root security model; and (4) adding HTTP and exec-based health probes.

The outcome: a GitOps-driven deployment pipeline where a `git push` to `main` triggers lint, tests, image build, and a rolling OCP deployment. Consumer groups autoscale via KEDA based on Kafka lag. Alerts fire to Slack and PagerDuty. PostgreSQL has automatic failover. The entire architecture requires zero privileged containers and satisfies OpenShift's default restricted SCC — no security exceptions needed.

---

### 3-Minute Technical Summary

**Current state:** v0.2.0 runs eight Docker Compose services on a single host. The backend uses SQLite via a named volume — not horizontally scalable. The Dockerfile already follows non-root best practices (`adduser --system`, `USER artisthub`) but uses a fixed system UID that conflicts with OpenShift's arbitrary UID assignment. Worker process health is checked via `pgrep` — not compatible with OCP probes. Both consumers already handle `SASL_SSL` with `CONFLUENT_API_KEY/SECRET`, so the Kafka migration is purely configuration.

**Phase 8 technical path:** Phase 8A migrates to PostgreSQL by provisioning the Crunchy Postgres Operator, running the existing Alembic migrations, and updating `DATABASE_URL`. No model changes are needed. Phase 8B applies the GID 0 image pattern (OCP standard), switches nginx to `nginx-unprivileged`, and adds a `/tmp/worker_alive` heartbeat file to worker processes so `exec` probes can verify liveness. Phase 8C establishes namespace RBAC with per-component ServiceAccounts, default-deny NetworkPolicies, and Sealed Secrets for GitOps-safe secret management.

Phase 8D connects to Confluent Cloud — registering the 13 existing Avro schemas and updating bootstrap/SR credentials. Phase 8E deploys all five Deployments with correctly tuned resource requests/limits and the new probes. Phase 8F adds an Edge TLS Route with `cert-manager` for automatic Let's Encrypt renewal.

Phase 8G extends `ci.yml` with a `build-push` job that tags images by semver and a `deploy` job that calls `oc rollout` via a scoped service account token. Phase 8H adds `prometheus_flask_exporter` to the backend and `prometheus_client` HTTP servers to workers, wired to ServiceMonitors and a PrometheusRule manifest with the alert inventory defined above. Phase 8I deploys KEDA ScaledObjects that autoscale analytics and notification consumers between 1 and 6 replicas based on Confluent consumer group lag, with a 50-message lag threshold.

Phase 8J is a load test gate: 100 concurrent users, 10-minute soak, p99 < 500ms, zero pod restarts, consumer lag < 100. This validates resource limits are correctly set before traffic is migrated from the existing environment.

**Key architectural constraints:** The outbox relay is capped at 1 replica to prevent duplicate publishes — to scale beyond 1, `SELECT FOR UPDATE SKIP LOCKED` must be added to `poll_and_publish()`. Consumer replicas are hard-capped at the maximum partition count of their subscribed topics (6 for analytics, 6 for notifications). The PostgreSQL connection pool must be sized as `pool_size × backend_replicas ≤ max_connections` — at 4 replicas with pool size 10, that is 40 connections, well within PostgreSQL's default 100.

The architecture is designed for zero production downtime at each phase boundary: every Deployment uses `RollingUpdate` with `maxUnavailable: 0`, the readinessProbe on `/api/health` gates traffic until both gunicorn and the DB are ready, and all Kafka consumers resume from their last committed offset after a restart with no event loss.
