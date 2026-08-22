# ArtistHub Confluent Cloud Architecture — Phase 8D

This document describes the Confluent Cloud resource architecture for
ArtistHub and how it maps to the existing application configuration.

---

## Overview

ArtistHub uses Confluent Cloud as the managed Kafka service for all
event streaming. The application code is unchanged from Phase 7 — all
configuration is injected via environment variables / OpenShift Secrets.

**Local development** continues to use Redpanda (configured via
`docker-compose.kafka.yml` with `KAFKA_SECURITY_PROTOCOL=PLAINTEXT`).

**Production / OpenShift** uses Confluent Cloud with
`KAFKA_SECURITY_PROTOCOL=SASL_SSL`.

Both paths share the identical event architecture, schemas, and
consumer semantics.

---

## Confluent Cloud Resource Architecture

```
Confluent Cloud Environment: artisthub-production
│
└── Kafka Cluster: artisthub (type: Basic)
    │
    ├── Topic: artisthub.social      (6 partitions, 7d retention)
    ├── Topic: artisthub.catalog     (6 partitions, 7d retention)
    ├── Topic: artisthub.identity    (3 partitions, 7d retention)
    └── Topic: artisthub.deadletter  (3 partitions, 30d retention)

Confluent Schema Registry (environment-level)
    ├── Subject: io.artisthub.events.FanFollowedArtist       (v1, BACKWARD)
    ├── Subject: io.artisthub.events.FanUnfollowedArtist     (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistPostCreated       (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistPostDeleted       (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistReleaseCreated    (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistReleaseUpdated    (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistReleaseDeleted    (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistMerchCreated      (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistMerchUpdated      (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistMerchDeleted      (v1, BACKWARD)
    ├── Subject: io.artisthub.events.ArtistRegistered        (v1, BACKWARD)
    └── Subject: io.artisthub.events.ArtistProfileUpdated    (v1, BACKWARD)
```

---

## Configuration: Local Redpanda vs Confluent Cloud

| Variable | Local (Redpanda) | Production (Confluent Cloud) |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | `pkc-XXXXX.us-east-1.aws.confluent.cloud:9092` |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `SASL_SSL` |
| `KAFKA_SASL_MECHANISMS` | *(not set)* | `PLAIN` |
| `CONFLUENT_API_KEY` | *(not set)* | `<cluster-api-key>` |
| `CONFLUENT_API_SECRET` | *(not set)* | `<cluster-api-secret>` |
| `SCHEMA_REGISTRY_URL` | `http://localhost:8081` | `https://psrc-XXXXX.us-east-1.aws.confluent.cloud` |
| `SCHEMA_REGISTRY_API_KEY` | *(not set)* | `<sr-api-key>` |
| `SCHEMA_REGISTRY_API_SECRET` | *(not set)* | `<sr-api-secret>` |

No application code changes are required between environments.
The switch is purely a configuration change.

---

## Authentication Model

Confluent Cloud uses **SASL_SSL with PLAIN mechanism**:

- TLS 1.2/1.3 encrypts all traffic to brokers and Schema Registry.
- SASL PLAIN provides username/password authentication where:
  - Username = Confluent API Key
  - Password = Confluent API Secret

This is the industry-standard model for Confluent Cloud. "PLAIN" refers
to the SASL mechanism name, not to the transport layer (which is TLS).

### Producer (outbox relay)

```python
config = {
    "security.protocol": "SASL_SSL",
    "sasl.mechanisms": "PLAIN",
    "sasl.username": os.environ.get("CONFLUENT_API_KEY"),
    "sasl.password": os.environ.get("CONFLUENT_API_SECRET"),
}
```

### Consumers

Identical configuration, plus consumer group settings.

### Schema Registry

Basic auth: `SCHEMA_REGISTRY_API_KEY` / `SCHEMA_REGISTRY_API_SECRET` are
passed as HTTP basic auth credentials on every Schema Registry API call.

---

## ACL Model (Least Privilege)

| Workload | Topic | ACL | Consumer Group |
|---|---|---|---|
| outbox-relay | `artisthub.social` | WRITE | — |
| outbox-relay | `artisthub.catalog` | WRITE | — |
| outbox-relay | `artisthub.identity` | WRITE | — |
| outbox-relay | `artisthub.deadletter` | WRITE | — |
| analytics-consumer | `artisthub.social` | READ | `artisthub.analytics.v1` |
| analytics-consumer | `artisthub.catalog` | READ | `artisthub.analytics.v1` |
| analytics-consumer | `artisthub.identity` | READ | `artisthub.analytics.v1` |
| analytics-consumer | `artisthub.deadletter` | WRITE | — |
| notification-consumer | `artisthub.catalog` | READ | `artisthub.notifications.v1` |
| notification-consumer | `artisthub.deadletter` | WRITE | — |

**Note:** For the Phase 8D smoke test, a single API key with full
topic access is acceptable. Separate per-workload keys are required
before Phase 8E production deployment.

---

## Topic Configuration

| Topic | Partitions | Retention | Cleanup | Purpose |
|---|---|---|---|---|
| `artisthub.social` | 6 | 7 days | delete | Fan-artist social events |
| `artisthub.catalog` | 6 | 7 days | delete | Release/merch catalog events |
| `artisthub.identity` | 3 | 7 days | delete | Artist identity events |
| `artisthub.deadletter` | 3 | 30 days | delete | Unprocessable events |

**Partition rationale:**
- 6 partitions for social/catalog: supports up to 6 consumer replicas per group
- 3 partitions for identity/deadletter: lower throughput, fewer replicas needed
- Do NOT change partition counts without updating the architecture plan

---

## Schema Subject Configuration

- **Strategy:** RecordNameStrategy
- **Subject format:** `<namespace>.<RecordName>`
- **Namespace:** `io.artisthub.events`
- **Compatibility:** BACKWARD (set globally; do not override per-subject)
- **Versions:** v1 for all 12 subjects at Phase 8D

BACKWARD compatibility means:
- New optional fields (with defaults) may be added
- Required fields must never be removed
- Field types may not change

---

## OpenShift Secret Mapping

All Confluent credentials are in the `artisthub-secrets` OpenShift Secret.

| Secret Key | Maps to Env Var | Consumed By |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `KAFKA_BOOTSTRAP_SERVERS` | all workers |
| `CONFLUENT_API_KEY` | `CONFLUENT_API_KEY` | outbox-relay, consumers |
| `CONFLUENT_API_SECRET` | `CONFLUENT_API_SECRET` | outbox-relay, consumers |
| `SCHEMA_REGISTRY_URL` | `SCHEMA_REGISTRY_URL` | outbox-relay, consumers |
| `SCHEMA_REGISTRY_API_KEY` | `SCHEMA_REGISTRY_API_KEY` | outbox-relay, consumers |
| `SCHEMA_REGISTRY_API_SECRET` | `SCHEMA_REGISTRY_API_SECRET` | outbox-relay, consumers |

The ConfigMap `artisthub-config` holds non-secret Kafka config:
- `KAFKA_SECURITY_PROTOCOL=SASL_SSL`
- `KAFKA_SASL_MECHANISMS=PLAIN`
- `KAFKA_CONSUMER_GROUP=artisthub.analytics.v1`
- `NOTIF_CONSUMER_GROUP=artisthub.notifications.v1`

---

## NetworkPolicy Egress — Confluent Cloud

Confluent Cloud broker endpoints resolve to dynamic cloud infrastructure
IPs. Static CIDR-based NetworkPolicy rules are not recommended unless
using Confluent Cloud with private networking (AWS PrivateLink / GCP
Private Service Connect).

**Current Phase 8C/8D policy:** Broad egress on ports 9092 (Kafka)
and 443 (Schema Registry HTTPS) to `0.0.0.0/0`.

**To tighten (with private networking):**
1. Enable AWS PrivateLink or GCP Private Service Connect on your
   Confluent cluster.
2. Obtain the static private endpoint IP(s) or CIDR range from Confluent.
3. Replace the broad `0.0.0.0/0` egress rules in
   `openshift/base/networkpolicies.yaml` with the static CIDRs.

**Why not tighten with public Confluent Cloud:**
Confluent does not publish a stable CIDR block list for general-purpose
Kafka cluster access. Using DNS-resolved endpoints (`pkc-*.confluent.cloud`)
with dynamic IPs makes static CIDR-based NetworkPolicies unreliable and
potentially connection-breaking after infrastructure changes.

---

## Validation Procedure

See `scripts/validate-phase-8d.sh` for the automated validation script.

```bash
# 1. Provision resources (see scripts/confluent-provision.md)

# 2. Add credentials to .env (NEVER commit)
cat >> .env <<'EOF'
KAFKA_BOOTSTRAP_SERVERS=pkc-XXXXX.us-east-1.aws.confluent.cloud:9092
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISMS=PLAIN
CONFLUENT_API_KEY=<key>
CONFLUENT_API_SECRET=<secret>
SCHEMA_REGISTRY_URL=https://psrc-XXXXX.us-east-1.aws.confluent.cloud
SCHEMA_REGISTRY_API_KEY=<sr-key>
SCHEMA_REGISTRY_API_SECRET=<sr-secret>
EOF

# 3. Source and validate
set -a; source .env; set +a
bash scripts/validate-phase-8d.sh

# 4. Run Schema Registry tests
python3 kafka/confluent-sr-test.py

# 5. Run Kafka smoke tests
python3 kafka/confluent-smoke-test.py
```

---

## Troubleshooting

### `Authentication failed` / `SASL authentication error`
- Verify `CONFLUENT_API_KEY` / `CONFLUENT_API_SECRET` are set (not swapped with SR creds).
- Confirm the API key has the required topic ACLs.

### `Topic not found` / `UNKNOWN_TOPIC_OR_PARTITION`
- Run `confluent kafka topic list` to confirm topics exist.
- Verify partition counts match the required values.

### `Schema not found` / `404 from Schema Registry`
- Run `python3 kafka/confluent-sr-test.py` to register/verify all 12 subjects.
- Confirm `SCHEMA_REGISTRY_URL` points to the correct SR endpoint (not the broker).

### `Message not received by consumer`
- Verify consumer group has READ ACL on the subscribed topic.
- Check `auto.offset.reset=earliest` — new group starts from beginning.
- Verify `KAFKA_BOOTSTRAP_SERVERS` is the same endpoint for both producer and consumer.

### `Compatibility check failed`
- Confirm global compatibility mode is `BACKWARD` (not `NONE` or `FULL`).
  ```bash
  curl -u "$SCHEMA_REGISTRY_API_KEY:$SCHEMA_REGISTRY_API_SECRET" \
    "$SCHEMA_REGISTRY_URL/config"
  ```

---

## Phase 8D Validation Status

Run `bash scripts/validate-phase-8d.sh` for the current status.

Live validation gates (sections E, F, G) are **BLOCKED** until
Confluent Cloud resources are provisioned and credentials are added to `.env`.

See `scripts/confluent-provision.md` for exact provisioning steps.
