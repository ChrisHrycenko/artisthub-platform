# ArtistHub OpenShift Manifests

This directory contains Kubernetes/OpenShift manifests for deploying the
ArtistHub platform on Red Hat OpenShift 4.x.

**Phase 8C** establishes the platform foundation — namespace, RBAC, ConfigMaps,
Secret references, NetworkPolicies, and Services.  Workload Deployments are added
in Phase 8E once the PostgreSQL operator and image registry are configured.

---

## Directory Structure

```
openshift/
├── base/                         # Environment-agnostic base manifests
│   ├── kustomization.yaml        # Kustomize base entry point
│   ├── namespace.yaml            # Namespace / OCP Project
│   ├── serviceaccounts.yaml      # One SA per workload class (5 total)
│   ├── rbac.yaml                 # Role + 5 RoleBindings (least-privilege)
│   ├── configmap.yaml            # Non-secret runtime config
│   ├── secrets.example.yaml      # Secret schema template (NO real values)
│   ├── networkpolicies.yaml      # 11 NetworkPolicy objects (default-deny + allows)
│   ├── services.yaml             # 3 ClusterIP Services
│   ├── resource-defaults.yaml    # Resource request/limit reference (ConfigMap)
│   ├── probe-templates.yaml      # Probe strategy reference (ConfigMap)
│   └── security-context.yaml     # securityContext reference (ConfigMap)
├── overlays/
│   ├── local/
│   │   └── kustomization.yaml    # Local dev: PLAINTEXT Kafka, localhost CORS
│   └── production/
│       └── kustomization.yaml    # Production: SASL_SSL, Route hostname, image tags
├── secrets/                      # .gitignored — real secrets go here, never committed
│   └── README.md                 # Instructions for secret injection
└── README.md                     # This file
```

---

## Namespace Strategy

All ArtistHub resources live in the **`artisthub`** namespace (OCP Project).

```
artisthub
├── ServiceAccounts (sa-backend, sa-frontend, sa-outbox-relay, sa-analytics-consumer, sa-notification-consumer)
├── Role: artisthub-workload (GET configmaps/secrets only)
├── RoleBindings (one per SA)
├── ConfigMap: artisthub-config
├── Secret: artisthub-secrets  ← injected separately, never in git
├── Services: backend-svc, frontend-svc, postgresql-svc
├── NetworkPolicies (default-deny + 10 explicit allows)
└── Deployments (Phase 8E): backend, frontend, outbox-relay, analytics-consumer, notification-consumer
```

On OpenShift:
```bash
# Equivalent to applying namespace.yaml:
oc new-project artisthub \
  --description="ArtistHub platform" \
  --display-name="ArtistHub"
```

---

## ServiceAccount / RBAC Model

| ServiceAccount | Used by | Permissions |
|---|---|---|
| `sa-backend` | Flask/gunicorn Deployment | GET `artisthub-config`, GET `artisthub-secrets` |
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

The Role `artisthub-workload` grants `get` on `configmaps` and `secrets`, restricted
by `resourceNames` to `artisthub-config` and `artisthub-secrets` only.

---

## ConfigMap vs Secret Separation

### ConfigMap (`artisthub-config`) — safe in git

| Key | Value | Reason safe |
|---|---|---|
| `FLASK_ENV` | `production` | String literal |
| `KAFKA_SECURITY_PROTOCOL` | `SASL_SSL` | Protocol name |
| `KAFKA_SASL_MECHANISMS` | `PLAIN` | Mechanism name |
| `KAFKA_CONSUMER_GROUP` | `artisthub.analytics.v1` | Group name |
| `NOTIF_CONSUMER_GROUP` | `artisthub.notifications.v1` | Group name |
| `OUTBOX_POLL_INTERVAL` | `5` | Tuning value |
| `OUTBOX_BATCH_SIZE` | `100` | Tuning value |
| `ANALYTICS_MAX_RETRIES` | `3` | Tuning value |
| `ANALYTICS_RETRY_BACKOFF` | `1.0` | Tuning value |
| `NOTIF_MAX_RETRIES` | `3` | Tuning value |
| `NOTIF_RETRY_BACKOFF` | `1.0` | Tuning value |
| `KAFKA_SCHEMAS_DIR` | `/app/kafka/schemas` | File path |
| `DB_POOL_SIZE` | `5` | Tuning value |
| `DB_MAX_OVERFLOW` | `10` | Tuning value |
| `CORS_ORIGINS` | (overlay-specific) | Hostname (public) |

### Secret (`artisthub-secrets`) — NEVER in git

| Key | Description |
|---|---|
| `SECRET_KEY` | Flask session signing key |
| `DATABASE_URL` | Full PostgreSQL DSN including password |
| `KAFKA_BOOTSTRAP_SERVERS` | Confluent Cloud broker endpoint |
| `CONFLUENT_API_KEY` | Confluent Cloud cluster API key |
| `CONFLUENT_API_SECRET` | Confluent Cloud cluster API secret |
| `SCHEMA_REGISTRY_URL` | Confluent Cloud SR endpoint |
| `SCHEMA_REGISTRY_API_KEY` | Confluent Cloud SR API key |
| `SCHEMA_REGISTRY_API_SECRET` | Confluent Cloud SR API secret |

See `openshift/base/secrets.example.yaml` for the full Secret schema.

---

## Secret Injection Strategy

**Preferred: HashiCorp Vault + Vault Agent Injector**
- Secrets are stored in Vault, never touch OCP etcd
- Vault Agent sidecar injects values as environment variables at pod startup
- Secret rotation triggers rolling restart without image rebuild

**Fallback: External Secrets Operator**
- Pulls secrets from Vault, AWS Secrets Manager, GCP Secret Manager, etc.
- Creates and syncs Kubernetes Secret objects automatically

**Fallback: Sealed Secrets (Bitnami kubeseal)**
- Encrypt a Secret with the cluster's public key
- Encrypted `SealedSecret` is safe to commit to git
- Controller decrypts on-cluster
- NOTE: Do not generate SealedSecret manifests without an actual cluster and
  real kubeseal certificate — fabricated encrypted values cannot be decrypted

**For development / staging (manual):**
```bash
# Create secret from environment file
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

## NetworkPolicy Communication Matrix

```
Internet / OCP Router (openshift-ingress namespace)
         ↓ port 8080
      frontend  ──────────────────────────────────────────┐
         ↓ port 5000                                       │
      backend ──────────────→ postgresql:5432              │
         ↓                                                  │
    Confluent Cloud (egress 9092/443)                      │
                                                            │
   outbox-relay ──────────→ postgresql:5432                │
         ↓                                                  │
    Confluent Cloud (egress 9092/443)                      │
                                                            │
   analytics-consumer ────→ postgresql:5432                │
         ↓                                                  │
    Confluent Cloud (egress 9092/443)                      │
                                                            │
   notification-consumer ─→ postgresql:5432                │
         ↓                                                  │
    Confluent Cloud (egress 9092/443)                      │
                                                            │
   OCP monitoring ─────────────────────────────────────────┘
   (openshift-monitoring namespace → all pods metrics port)
```

| Policy | Direction | From | To | Port |
|---|---|---|---|---|
| `default-deny-all` | Ingress+Egress | ALL | ALL | ALL |
| `allow-dns-egress` | Egress | ALL | ANY | 53 UDP+TCP |
| `allow-router-to-frontend` | Ingress | openshift-ingress | frontend | 8080 |
| `allow-frontend-to-backend` | Ingress | frontend | backend | 5000 |
| `allow-backend-egress` | Egress | backend | postgresql, Confluent | 5432, 9092, 443 |
| `allow-workers-to-postgresql` | Ingress | backend, relay, analytics, notif | postgresql | 5432 |
| `allow-outbox-relay-egress` | Egress | outbox-relay | postgresql, Confluent | 5432, 9092, 443 |
| `allow-analytics-consumer-egress` | Egress | analytics-consumer | postgresql, Confluent | 5432, 9092, 443 |
| `allow-notification-consumer-egress` | Egress | notification-consumer | postgresql, Confluent | 5432, 9092, 443 |
| `allow-ocp-monitoring` | Ingress | openshift-monitoring | ALL | 5000, 8080 |
| `allow-kubelet-probes` | Ingress | node IPs (10.0.0.0/8) | ALL | 5000, 8080 |

**Known broad egress (Phase 8D TODO):**
The Confluent Cloud egress rules (`0.0.0.0/0` on ports 9092 and 443) are
intentionally broad until Confluent Cloud IP ranges are confirmed in Phase 8D.
After Phase 8D, restrict to the specific Confluent Cloud CIDR blocks for the
provisioned cluster region.

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

PostgreSQL resource sizing is determined by the Crunchy Data operator configuration (Phase 8E).

---

## Probe Strategy

### HTTP workloads (backend, frontend)

| Workload | Probe | Type | Path | Period | Threshold |
|---|---|---|---|---|---|
| `backend` | startup | httpGet | `/api/health:5000` | 5s | 12 failures (60s) |
| `backend` | liveness | httpGet | `/api/health:5000` | 30s | 3 failures (90s) |
| `backend` | readiness | httpGet | `/api/health:5000` | 10s | 3 failures (30s) |
| `frontend` | liveness | httpGet | `/:8080` | 30s | 3 failures (90s) |
| `frontend` | readiness | httpGet | `/:8080` | 10s | 3 failures (30s) |

The `/api/health` endpoint executes `SELECT 1` — a failing DB connection marks
the pod as not-ready and pulls it from load balancing before crashing.

### Worker processes (outbox-relay, analytics-consumer, notification-consumer)

Workers use exec probes against heartbeat files written by `touch_heartbeat()`.

| Workload | Probe | Command | Period | Threshold |
|---|---|---|---|---|
| `outbox-relay` | startup | `test -f /tmp/relay_alive` | 10s | 12 failures (120s) |
| `outbox-relay` | liveness | `find /tmp -name relay_alive -mmin -2` | 60s | 2 failures (120s) |
| `analytics-consumer` | startup | `test -f /tmp/analytics_alive` | 10s | 12 failures (120s) |
| `analytics-consumer` | liveness | `find /tmp -name analytics_alive -mmin -2` | 60s | 2 failures (120s) |
| `notification-consumer` | startup | `test -f /tmp/notification_alive` | 10s | 12 failures (120s) |
| `notification-consumer` | liveness | `find /tmp -name notification_alive -mmin -2` | 60s | 2 failures (120s) |

The 120s window tolerates transient Kafka broker unavailability (rebalance,
brief network interruption) without triggering a false restart.

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
```

### Production (OpenShift)
```bash
# Create project
oc new-project artisthub

# Validate manifest output
kubectl kustomize openshift/overlays/production

# Apply foundation (Phase 8C — no Deployments yet)
kubectl apply -k openshift/overlays/production

# Inject secrets (choose one method — see Secret Injection Strategy above)
kubectl create secret generic artisthub-secrets \
  --from-literal=SECRET_KEY=<value> \
  ...
  -n artisthub

# Verify
kubectl get all,configmap,secret,networkpolicy,serviceaccount,role,rolebinding \
  -n artisthub
```

---

## Future Phases

| Phase | Scope |
|---|---|
| 8D | Confluent Cloud connectivity — configure bootstrap/SR URLs in secrets, smoke test |
| 8E | Workload Deployments — backend, frontend, workers with probes and securityContext |
| 8F | Routes + TLS — OCP Router edge termination, cert-manager |
| 8G | PostgreSQL on OCP — Crunchy Data operator, PVC, migration, backup |
| 8H | Observability — Prometheus, Grafana, Kafka lag alerting, DLQ dashboards |
| 8I | CI/CD — GitHub Actions → OCP image registry → rolling deploys |
| 8J | Load testing and autoscaling — k6, KEDA, HPA tuning |
