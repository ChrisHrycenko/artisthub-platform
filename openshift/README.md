# ArtistHub OpenShift Manifests

This directory contains Kubernetes/OpenShift manifests for deploying the
ArtistHub platform on Red Hat OpenShift 4.x.

**Phase 8C** establishes the platform foundation — namespace, RBAC, ConfigMaps,
Secret references, NetworkPolicies, and Services.

**Phase 8E** adds the five core workload Deployments, the database migration Job,
and the OpenShift-specific nginx configuration. All application behaviour from
Phase 8D (Confluent Cloud Kafka, Avro, Schema Registry) is preserved.

---

## Directory Structure

```
openshift/
├── base/                                  # Environment-agnostic base manifests
│   ├── kustomization.yaml                 # Kustomize base entry point
│   ├── namespace.yaml                     # Namespace / OCP Project
│   ├── serviceaccounts.yaml               # One SA per workload class (5 total)
│   ├── rbac.yaml                          # Role + 5 RoleBindings (least-privilege)
│   ├── configmap.yaml                     # Non-secret runtime config
│   ├── secrets.example.yaml               # Secret schema template (NO real values)
│   ├── networkpolicies.yaml               # 11 NetworkPolicy objects (default-deny + allows)
│   ├── services.yaml                      # 3 ClusterIP Services
│   ├── resource-defaults.yaml             # Resource request/limit reference (ConfigMap)
│   ├── probe-templates.yaml               # Probe strategy reference (ConfigMap)
│   ├── security-context.yaml              # securityContext reference (ConfigMap)
│   ├── deployments/                       # Phase 8E — workload Deployments
│   │   ├── backend.yaml                   # Flask/gunicorn backend (2 replicas)
│   │   ├── frontend.yaml                  # nginx-unprivileged frontend (2 replicas)
│   │   ├── outbox-relay.yaml              # Outbox relay (1 replica — no SKIP LOCKED yet)
│   │   ├── analytics-consumer.yaml        # Analytics consumer (2 replicas, max 15)
│   │   └── notification-consumer.yaml     # Notification consumer (2 replicas, max 6)
│   └── jobs/
│       └── db-migrate.yaml                # One-shot Flask-Migrate Job
├── overlays/
│   ├── local/
│   │   └── kustomization.yaml             # Local dev: PLAINTEXT Kafka, localhost CORS
│   └── production/
│       └── kustomization.yaml             # Production: SASL_SSL, Route hostname, image tags
├── secrets/                               # .gitignored — real secrets go here, never committed
│   └── README.md                          # Instructions for secret injection
└── README.md                              # This file
```

---

## Architecture

```
                 ┌─────────────────────────────────────────┐
                 │  Internet (Phase 8F — Route/TLS, not yet) │
                 └──────────────────┬──────────────────────┘
                                    │ port 8080 (future Route)
                          ┌─────────▼─────────┐
                          │  frontend-svc      │  ClusterIP :8080
                          │  (nginx-unprivileged)│
                          └─────────┬─────────┘
                                    │ /api/* → port 5000
                          ┌─────────▼─────────┐
                          │  backend-svc       │  ClusterIP :5000
                          │  (Flask/gunicorn)  │
                          └──────┬─────────────┘
                                 │ SQL
                    ┌────────────▼────────────┐
                    │  postgresql-svc :5432   │  ClusterIP
                    │  (PostgreSQL)           │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │     event_outbox table  │
                    └────────────┬────────────┘
                                 │ poll (5s)
                    ┌────────────▼────────────┐
                    │   outbox-relay          │  No Service
                    │   (1 replica)           │
                    └────────────┬────────────┘
                                 │ produce
                    ┌────────────▼────────────┐
                    │    Confluent Cloud      │
                    │    Kafka Cluster        │
                    │  social / catalog /     │
                    │  identity / deadletter  │
                    └──────┬─────────────────┘
                           │ consume
               ┌───────────┼──────────────────┐
               │           │                  │
    ┌──────────▼──┐  ┌─────▼──────────┐       │
    │ analytics-  │  │ notification-  │       │
    │ consumer    │  │ consumer       │       │
    │ (2 replicas)│  │ (2 replicas)   │       │
    └──────┬──────┘  └──────┬─────────┘       │
           │                │                 │
           └────────────────┘                 │
                    │ SQL                      │
           ┌────────▼────────────────────────┐│
           │  postgresql-svc :5432           ││
           └─────────────────────────────────┘│
                                               │
  ┌─────────────────────────────────────────  ┘
  │  Phase 8F — public Route + TLS (NOT YET IMPLEMENTED)
  └─────────────────────────────────────────────────────

Note: Public internet access is blocked until Phase 8F adds the OCP Route.
```

---

## Namespace Strategy

All ArtistHub resources live in the **`artisthub`** namespace (OCP Project).

```
artisthub
├── ServiceAccounts (sa-backend, sa-frontend, sa-outbox-relay,
│                   sa-analytics-consumer, sa-notification-consumer)
├── Role: artisthub-workload (GET configmaps/secrets only)
├── RoleBindings (one per SA)
├── ConfigMap: artisthub-config
├── Secret: artisthub-secrets  ← injected separately, never in git
├── Services: backend-svc, frontend-svc, postgresql-svc
├── NetworkPolicies (default-deny + 11 explicit allows)
├── Deployments (Phase 8E):
│   ├── backend           (2 replicas)
│   ├── frontend          (2 replicas)
│   ├── outbox-relay      (1 replica)
│   ├── analytics-consumer (2 replicas)
│   └── notification-consumer (2 replicas)
└── Jobs (Phase 8E):
    └── db-migrate        (runs flask db upgrade before workloads are healthy)
```

---

## Workload Summary

| Workload | Replicas | Service | Image | Command |
|---|---|---|---|---|
| `backend` | 2 | `backend-svc:5000` | `artisthub-backend` | gunicorn default |
| `frontend` | 2 | `frontend-svc:8080` | `artisthub-frontend` | nginx default |
| `outbox-relay` | 1 | none | `artisthub-backend` | `python run_relay.py` |
| `analytics-consumer` | 2 | none | `artisthub-backend` | `python run_analytics_consumer.py` |
| `notification-consumer` | 2 | none | `artisthub-backend` | `python run_notification_consumer.py` |
| `db-migrate` *(Job)* | N/A | none | `artisthub-backend` | `flask db upgrade` |

---

## Replica Strategy

### Backend (2 replicas)

Flask session cookies are server-signed with `SECRET_KEY`.  Any replica can
verify any session token because `SECRET_KEY` is shared via the same Secret
object.  No sticky sessions or shared-memory session store is required.

All persistent state lives in PostgreSQL.  The `readinessProbe` on `/api/health`
ensures traffic is only routed to replicas that have successfully connected to
the database.

**SQLite-era assumption broken at > 1 replica:** `DATABASE_URL` in the Secret
MUST be a PostgreSQL DSN.  SQLite is a file database — two pods would corrupt
it.  The `create_app()` factory gates `db.create_all()` behind a SQLite check;
with a PostgreSQL URL it is skipped and Alembic migrations are used instead.

### Outbox Relay (1 replica — HARD LIMIT)

The relay polls `event_outbox` for `status='pending'` rows using a plain
`SELECT` (no `SELECT FOR UPDATE SKIP LOCKED`).  Running two replicas would
cause both to read the same pending rows in the same polling window, producing
duplicate Kafka events.

Downstream consumers deduplicate via `ProcessedEvent(event_id, consumer_group)`,
so duplicates would not corrupt data — but they are wasteful and non-deterministic.

**Phase 8F+:** Implement `SELECT FOR UPDATE SKIP LOCKED` in
`app/services/outbox_relay.py` to enable safe multi-replica relay operation.

### Analytics Consumer (2 replicas, max 15)

Consumer group: `artisthub.analytics.v1`
Subscribed topics: `artisthub.social` (6p), `artisthub.catalog` (6p),
`artisthub.identity` (3p).  Total partitions = 15.

Kafka assigns partitions exclusively within a consumer group — only one consumer
per partition.  Multiple replicas are safe.  Idempotency is preserved via:
- `ProcessedEvent(event_id, consumer_group)` deduplication table
- Manual offset commits (`enable.auto.commit=false`)
- Offset committed only after DB transaction commits successfully

Phase 8E starts at 2 replicas.  Scale to 6 or 15 in Phase 8I via KEDA based on
consumer lag metrics.

### Notification Consumer (2 replicas, max 6)

Consumer group: `artisthub.notifications.v1`
Subscribed topic: `artisthub.catalog` (6 partitions).  Max parallelism = 6.

Idempotency layers:
1. `ProcessedEvent(event_id, consumer_group)` table — primary deduplication
2. `Notification.UNIQUE(event_id, fan_id)` DB constraint — defence in depth

Phase 8E starts at 2 replicas.  Scale to 6 in Phase 8I via KEDA.

---

## ServiceAccount / RBAC Model

| ServiceAccount | Used by | Permissions |
|---|---|---|
| `sa-backend` | Flask/gunicorn Deployment + db-migrate Job | GET `artisthub-config`, GET `artisthub-secrets` |
| `sa-frontend` | nginx-unprivileged Deployment | GET `artisthub-config`, GET `artisthub-secrets` |
| `sa-outbox-relay` | Outbox relay Deployment | GET `artisthub-config`, GET `artisthub-secrets` |
| `sa-analytics-consumer` | Analytics consumer Deployment | GET `artisthub-config`, GET `artisthub-secrets` |
| `sa-notification-consumer` | Notification consumer Deployment | GET `artisthub-config`, GET `artisthub-secrets` |

**Not granted:**
- `cluster-admin`, `admin`, `edit` at cluster or project scope
- `anyuid` SCC — not needed; Phase 8B GID-0 pattern works under `restricted`
- `privileged` SCC — never required
- `create`/`update`/`delete` on any resource
- `automountServiceAccountToken: true` — none of the workloads call the K8s API

---

## ConfigMap vs Secret Separation

### ConfigMap (`artisthub-config`) — safe in git

| Key | Value | Consumed by |
|---|---|---|
| `FLASK_ENV` | `production` | backend, relay, consumers |
| `KAFKA_SECURITY_PROTOCOL` | `SASL_SSL` | relay, consumers |
| `KAFKA_SASL_MECHANISMS` | `PLAIN` | relay, consumers |
| `KAFKA_CONSUMER_GROUP` | `artisthub.analytics.v1` | analytics-consumer |
| `NOTIF_CONSUMER_GROUP` | `artisthub.notifications.v1` | notification-consumer |
| `OUTBOX_POLL_INTERVAL` | `5` | outbox-relay |
| `OUTBOX_BATCH_SIZE` | `100` | outbox-relay |
| `ANALYTICS_MAX_RETRIES` | `3` | analytics-consumer |
| `ANALYTICS_RETRY_BACKOFF` | `1.0` | analytics-consumer |
| `NOTIF_MAX_RETRIES` | `3` | notification-consumer |
| `NOTIF_RETRY_BACKOFF` | `1.0` | notification-consumer |
| `KAFKA_SCHEMAS_DIR` | `/app/kafka/schemas` | relay, consumers |
| `DB_POOL_SIZE` | `5` | backend |
| `DB_MAX_OVERFLOW` | `10` | backend |
| `CORS_ORIGINS` | (overlay-specific) | backend |

All workloads receive the full ConfigMap via `envFrom: configMapRef`.  Unused
keys are silently ignored by Python.

### Secret (`artisthub-secrets`) — NEVER in git

| Key | Description | Consumed by |
|---|---|---|
| `SECRET_KEY` | Flask session signing key | backend, relay, consumers |
| `DATABASE_URL` | Full PostgreSQL DSN including password | backend, relay, consumers, db-migrate |
| `KAFKA_BOOTSTRAP_SERVERS` | Confluent Cloud broker endpoint | relay, consumers |
| `CONFLUENT_API_KEY` | Confluent Cloud cluster API key | relay, consumers |
| `CONFLUENT_API_SECRET` | Confluent Cloud cluster API secret | relay, consumers |
| `SCHEMA_REGISTRY_URL` | Confluent Cloud SR endpoint | relay, consumers |
| `SCHEMA_REGISTRY_API_KEY` | Confluent Cloud SR API key | consumers |
| `SCHEMA_REGISTRY_API_SECRET` | Confluent Cloud SR API secret | consumers |

See `openshift/base/secrets.example.yaml` for the full Secret schema.

---

## PostgreSQL Connectivity

The backend, relay, and all consumers connect to PostgreSQL using `DATABASE_URL`
from `artisthub-secrets`.  The expected DSN format is:

```
postgresql://artisthub:<password>@postgresql-svc:5432/artisthub?sslmode=require
```

`postgresql-svc` is the ClusterIP Service DNS name within the `artisthub`
namespace (see `openshift/base/services.yaml`).  Replace with the Crunchy Data
operator primary Service name if using the PGO operator.

**Schema migrations** are applied by the `db-migrate` Job before workloads start
serving traffic.  The Job runs `flask db upgrade` (Flask-Migrate / Alembic).
`db.create_all()` is never called in production (gated behind a SQLite check in
`create_app()`).

---

## Database Migration Job

The `db-migrate` Job (`openshift/base/jobs/db-migrate.yaml`) runs
`flask db upgrade` once per deployment to apply all pending Alembic revisions.

```
┌────────────────────────────────────────────────────────────────────┐
│ Deployment ordering (manual — Kubernetes cannot enforce this       │
│ automatically without Argo Rollouts or a pre-sync hook):           │
│                                                                    │
│  1. Apply all manifests: kubectl apply -k openshift/overlays/prod  │
│  2. Wait for migration Job to complete:                            │
│       kubectl wait --for=condition=complete job/db-migrate \       │
│         --timeout=120s -n artisthub                                │
│  3. If Job fails:                                                  │
│       kubectl logs job/db-migrate -n artisthub                    │
│       (fix the migration error, then re-apply)                     │
│  4. Verify backend rollout:                                        │
│       kubectl rollout status deployment/backend -n artisthub       │
│  5. Verify all other Deployments.                                  │
└────────────────────────────────────────────────────────────────────┘
```

The `flask db upgrade` command is idempotent: if the target revision is already
applied, the command exits 0 without changes (Alembic tracks applied revisions
in the `alembic_version` table).

`backoffLimit: 2` — allows 3 total attempts before marking the Job as Failed.
`ttlSecondsAfterFinished: 86400` — cleans up the Job resource after 24 hours.

---

## Safe Rollout Order

```
1. namespace / Project
2. ServiceAccounts
3. RBAC (Role + RoleBindings)
4. ConfigMap (artisthub-config)
5. Secret (artisthub-secrets) — injected externally, never committed
6. NetworkPolicies
7. Services (backend-svc, frontend-svc, postgresql-svc)
8. PostgreSQL dependency available (operator / StatefulSet ready)
9. db-migrate Job — run and WAIT FOR COMPLETION
10. backend Deployment
11. frontend Deployment
12. outbox-relay Deployment
13. analytics-consumer Deployment
14. notification-consumer Deployment
```

Workers (relay, analytics-consumer, notification-consumer) should not be started
before migrations complete.  They will not fail immediately — the migration schema
mismatch would only surface on the first database write — but starting after
migration completion is the safe and correct order.

---

## nginx — OpenShift vs Docker Compose

Two nginx configurations exist:

| File | Resolver | Backend upstream | Used by |
|---|---|---|---|
| `docker/nginx-frontend.conf` | `127.0.0.11` (Docker DNS) | `http://backend:5000` | Docker Compose (`Dockerfile.frontend`) |
| `docker/nginx-openshift.conf` | `172.30.0.10` (CoreDNS) | `http://backend-svc.artisthub.svc.cluster.local:5000` | OpenShift (`Dockerfile.frontend.openshift`) |

The `set $backend_upstream` variable in both configs forces nginx to resolve the
upstream hostname at request time, not at startup.  This prevents nginx from
exiting if the backend Service is temporarily unavailable during rolling restarts.

Build the OpenShift frontend image:
```bash
docker build -f docker/Dockerfile.frontend.openshift -t artisthub-frontend .
```

---

## securityContext Strategy

All containers use the OpenShift `restricted` SCC — the default for new projects.

**Pod-level (`spec.securityContext`):**
```yaml
securityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
```

**Container-level (`spec.containers[].securityContext`):**
```yaml
securityContext:
  allowPrivilegeEscalation: false
  runAsNonRoot: true
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
```

**Deliberately omitted:**
- `runAsUser` — OpenShift assigns an arbitrary UID from the project's range.
  The Phase 8B GID-0 filesystem pattern (`chgrp -R 0 /app && chmod -R g=u /app`)
  ensures the assigned UID can write to `/app` and `/app/instance`.
- `privileged: true` — never set
- `hostNetwork`, `hostPID`, `hostIPC` — never set

---

## Resource Requests / Limits

Initial conservative MVP values (tune from Phase 8H metrics and Phase 8J load test):

| Workload | CPU Request | CPU Limit | Mem Request | Mem Limit |
|---|---|---|---|---|
| `backend` | 100m | 500m | 256Mi | 512Mi |
| `frontend` | 25m | 100m | 64Mi | 128Mi |
| `outbox-relay` | 50m | 200m | 128Mi | 256Mi |
| `analytics-consumer` | 50m | 200m | 128Mi | 256Mi |
| `notification-consumer` | 50m | 200m | 128Mi | 256Mi |
| `db-migrate` *(Job)* | 50m | 200m | 128Mi | 256Mi |

These are initial values and should be tuned in Phase 8H (Prometheus/Grafana)
and Phase 8J (k6 load testing).

---

## Probe Strategy

### HTTP workloads (backend, frontend)

| Workload | Probe | Type | Path | Period | Threshold | Notes |
|---|---|---|---|---|---|---|
| `backend` | startup | httpGet | `/api/health:5000` | 5s | 12 failures (60s) | Allows DB connection init |
| `backend` | liveness | httpGet | `/api/health:5000` | 30s | 3 failures (90s) | Restarts hung pod |
| `backend` | readiness | httpGet | `/api/health:5000` | 10s | 3 failures (30s) | Gates Service traffic |
| `frontend` | startup | httpGet | `/:8080` | 5s | 6 failures (30s) | nginx starts < 2s |
| `frontend` | liveness | httpGet | `/:8080` | 30s | 3 failures (90s) | Restarts hung nginx |
| `frontend` | readiness | httpGet | `/:8080` | 10s | 3 failures (30s) | Gates Service traffic |

The `/api/health` endpoint executes `SELECT 1` — a failing DB connection marks
the backend pod as not-ready before it crashes.

### Worker processes (outbox-relay, analytics-consumer, notification-consumer)

Workers use exec probes against heartbeat files written on every main-loop iteration.

| Workload | Probe | Command | Period | Threshold |
|---|---|---|---|---|
| `outbox-relay` | startup | `test -f /tmp/relay_alive` | 10s | 12 failures (120s) |
| `outbox-relay` | liveness | `find /tmp -name relay_alive -mmin -2` | 60s | 2 failures (120s) |
| `analytics-consumer` | startup | `test -f /tmp/analytics_alive` | 10s | 12 failures (120s) |
| `analytics-consumer` | liveness | `find /tmp -name analytics_alive -mmin -2` | 60s | 2 failures (120s) |
| `notification-consumer` | startup | `test -f /tmp/notification_alive` | 10s | 12 failures (120s) |
| `notification-consumer` | liveness | `find /tmp -name notification_alive -mmin -2` | 60s | 2 failures (120s) |

The 120s liveness window tolerates transient Kafka broker unavailability
(rebalance, brief network interruption) without triggering a false restart.

Workers do not have a `readinessProbe` — they are not behind a Service.

---

## Secret Injection Strategy

**Preferred: HashiCorp Vault + Vault Agent Injector**
- Secrets are stored in Vault, never touch OCP etcd
- Vault Agent sidecar injects values as environment variables at pod startup
- Secret rotation triggers rolling restart without image rebuild

**Fallback: External Secrets Operator**
- Pulls secrets from Vault, AWS Secrets Manager, GCP Secret Manager, etc.
- Creates and syncs Kubernetes Secret objects automatically

**For development / staging (manual):**
```bash
kubectl create secret generic artisthub-secrets \
  --from-literal=SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  --from-literal=DATABASE_URL="postgresql://artisthub:CHANGE_ME@postgresql-svc:5432/artisthub?sslmode=require" \
  --from-literal=KAFKA_BOOTSTRAP_SERVERS="pkc-XXXXX.confluent.cloud:9092" \
  --from-literal=CONFLUENT_API_KEY="your-key" \
  --from-literal=CONFLUENT_API_SECRET="your-secret" \
  --from-literal=SCHEMA_REGISTRY_URL="https://psrc-XXXXX.confluent.cloud" \
  --from-literal=SCHEMA_REGISTRY_API_KEY="your-sr-key" \
  --from-literal=SCHEMA_REGISTRY_API_SECRET="your-sr-secret" \
  -n artisthub
```

---

## Applying Manifests

### Prerequisites
- `kubectl` (≥1.27) or `oc` (OCP 4.12+)
- Cluster access with `edit` rights to the `artisthub` namespace

### Local development
```bash
# Validate (no cluster needed)
kubectl kustomize openshift/overlays/local

# Apply (requires cluster)
kubectl apply -k openshift/overlays/local

# Wait for migration Job
kubectl wait --for=condition=complete job/db-migrate --timeout=120s -n artisthub

# Check Deployments
kubectl rollout status deployment/backend -n artisthub
kubectl rollout status deployment/frontend -n artisthub
```

### Production (OpenShift)
```bash
# Create project
oc new-project artisthub

# Validate manifest output
kubectl kustomize openshift/overlays/production

# Inject secrets (BEFORE apply — the backend startup probe requires DATABASE_URL)
kubectl create secret generic artisthub-secrets \
  --from-literal=SECRET_KEY=<value> \
  --from-literal=DATABASE_URL="postgresql://artisthub:CHANGE_ME@postgresql-svc:5432/artisthub?sslmode=require" \
  --from-literal=KAFKA_BOOTSTRAP_SERVERS="pkc-XXXXX.confluent.cloud:9092" \
  --from-literal=CONFLUENT_API_KEY="your-key" \
  --from-literal=CONFLUENT_API_SECRET="your-secret" \
  --from-literal=SCHEMA_REGISTRY_URL="https://psrc-XXXXX.confluent.cloud" \
  --from-literal=SCHEMA_REGISTRY_API_KEY="your-sr-key" \
  --from-literal=SCHEMA_REGISTRY_API_SECRET="your-sr-secret" \
  -n artisthub

# Apply all manifests (namespace, RBAC, ConfigMap, Services, Deployments, Job)
kubectl apply -k openshift/overlays/production

# Wait for migration Job to complete BEFORE proceeding
kubectl wait --for=condition=complete job/db-migrate --timeout=120s -n artisthub

# Verify all Deployments are healthy
kubectl rollout status deployment/backend -n artisthub
kubectl rollout status deployment/frontend -n artisthub
kubectl rollout status deployment/outbox-relay -n artisthub
kubectl rollout status deployment/analytics-consumer -n artisthub
kubectl rollout status deployment/notification-consumer -n artisthub

# Verify all pods are Running
kubectl get pods -n artisthub
```

---

## NetworkPolicy Communication Matrix

```
Internet / OCP Router (openshift-ingress namespace) — Phase 8F Route (not yet)
         ↓ port 8080
      frontend  ──────────────────────────────────────────────────┐
         ↓ port 5000                                               │
      backend ──────────────→ postgresql:5432                     │
         ↓                                                         │
    Confluent Cloud (egress 9092/443)                             │
                                                                    │
   outbox-relay ──────────→ postgresql:5432                       │
         ↓                                                         │
    Confluent Cloud (egress 9092/443)                             │
                                                                    │
   analytics-consumer ────→ postgresql:5432                       │
         ↓                                                         │
    Confluent Cloud (egress 9092/443)                             │
                                                                    │
   notification-consumer ─→ postgresql:5432                       │
         ↓                                                         │
    Confluent Cloud (egress 9092/443)                             │
                                                                    │
   OCP monitoring ─────────────────────────────────────────────────┘
   (openshift-monitoring namespace → all pods metrics port)
```

---

## Known Limitations (Phase 8E)

| Limitation | Impact | Phase to fix |
|---|---|---|
| Outbox relay: no `SELECT FOR UPDATE SKIP LOCKED` | Maximum 1 relay replica | 8F+ |
| Public Route not created | Frontend/backend not reachable from internet | 8F |
| No TLS certificate | All traffic plain HTTP inside cluster | 8F |
| No CI/CD pipeline | Images must be pushed manually | 8G |
| No Prometheus/Grafana | No metrics or alerting | 8H |
| No KEDA autoscaling | Consumer replicas are static | 8I |
| PostgreSQL: no persistent backup | Data at risk during PVC loss | 8G |
| Image tags hard-coded to `latest` | No pinned immutable releases | 8G |

---

## Future Phases

| Phase | Scope |
|---|---|
| 8F | Routes + TLS — OCP Router edge termination, cert-manager, public hostname |
| 8G | PostgreSQL on OCP — Crunchy Data operator, PVC, migration, backup |
| 8H | Observability — Prometheus, Grafana, Kafka lag alerting, DLQ dashboards |
| 8I | KEDA autoscaling — scale consumers by Kafka lag; HPA for backend |
| 8J | CI/CD — GitHub Actions → OCP image registry → rolling deploys |
| 8K | Load testing — k6, resource limit tuning |
