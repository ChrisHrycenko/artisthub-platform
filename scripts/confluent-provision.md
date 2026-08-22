# Confluent Cloud Provisioning Guide — ArtistHub Phase 8D

This document gives the exact manual steps needed to provision the
Confluent Cloud resources that ArtistHub requires, and populate the
`openshift/secrets/` credentials before running `validate-phase-8d.sh`.

No credentials are stored in this document. All values must be obtained
from the Confluent Cloud console or CLI and stored only in:
- `.env` (local development — gitignored)
- `openshift/secrets/artisthub-secrets.yaml` (cluster — gitignored)

---

## Prerequisites

1. A Confluent Cloud account (https://confluent.cloud/)
2. Confluent CLI installed: https://docs.confluent.io/confluent-cli/current/install.html
   ```bash
   # macOS
   brew install confluentinc/tap/cli
   # Verify
   confluent version
   ```
3. Login:
   ```bash
   confluent login
   ```

---

## Step 1 — Create or Identify Environment

```bash
# List existing environments
confluent environment list

# Create a new environment (if needed)
confluent environment create artisthub-production
# → note the environment ID: env-XXXXXX

# Select it
confluent environment use env-XXXXXX
```

---

## Step 2 — Create Kafka Cluster

```bash
# Create a Basic cluster (MVP tier — cost-effective)
# cloud: aws | gcp | azure   region: pick closest to your OCP cluster
confluent kafka cluster create artisthub \
  --cloud aws \
  --region us-east-1 \
  --type basic

# → note the cluster ID: lkc-XXXXXX
# Wait ~2 min for provisioning.

# Select it
confluent kafka cluster use lkc-XXXXXX

# Get the bootstrap endpoint
confluent kafka cluster describe lkc-XXXXXX | grep Endpoint
# Example: pkc-XXXXX.us-east-1.aws.confluent.cloud:9092
```

**Record for `.env`:**
```
KAFKA_BOOTSTRAP_SERVERS=pkc-XXXXX.us-east-1.aws.confluent.cloud:9092
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISMS=PLAIN
```

---

## Step 3 — Create Topics

ArtistHub requires exactly these four topics.
Do NOT change partition counts — they match the consumer-group scaling limits.

```bash
# social — 6 partitions, 7-day retention
confluent kafka topic create artisthub.social \
  --partitions 6 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete

# catalog — 6 partitions, 7-day retention
confluent kafka topic create artisthub.catalog \
  --partitions 6 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete

# identity — 3 partitions, 7-day retention
confluent kafka topic create artisthub.identity \
  --partitions 3 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete

# deadletter — 3 partitions, 30-day retention
confluent kafka topic create artisthub.deadletter \
  --partitions 3 \
  --config retention.ms=2592000000 \
  --config cleanup.policy=delete

# Verify
confluent kafka topic list
confluent kafka topic describe artisthub.social
```

---

## Step 4 — Create Least-Privilege API Keys

### 4a. Producer API key (outbox relay)

The relay needs WRITE access to social, catalog, identity, deadletter.

```bash
# Create a service account for the relay
confluent iam service-account create artisthub-relay \
  --description "ArtistHub outbox relay producer"
# → note SA ID: sa-XXXXXX

# Grant WRITE ACLs
for topic in artisthub.social artisthub.catalog artisthub.identity artisthub.deadletter; do
  confluent kafka acl create \
    --allow \
    --service-account sa-XXXXXX \
    --operation WRITE \
    --topic "$topic"
done

# Also need CREATE (for idempotent producer) and DESCRIBE
confluent kafka acl create \
  --allow \
  --service-account sa-XXXXXX \
  --operation IDEMPOTENT-WRITE \
  --cluster-scope

# Create API key for relay SA
confluent api-key create \
  --resource lkc-XXXXXX \
  --service-account sa-XXXXXX \
  --description "artisthub-relay-producer"
# → note: Key and Secret (Secret shown ONCE — save immediately)
```

### 4b. Analytics consumer API key

Needs READ on social, catalog, identity; WRITE on deadletter; consumer group access.

```bash
confluent iam service-account create artisthub-analytics \
  --description "ArtistHub analytics consumer"
# → SA ID: sa-YYYYYY

for topic in artisthub.social artisthub.catalog artisthub.identity; do
  confluent kafka acl create \
    --allow \
    --service-account sa-YYYYYY \
    --operation READ \
    --topic "$topic"
done

# Dead-letter write
confluent kafka acl create \
  --allow \
  --service-account sa-YYYYYY \
  --operation WRITE \
  --topic artisthub.deadletter

# Consumer group
confluent kafka acl create \
  --allow \
  --service-account sa-YYYYYY \
  --operation READ \
  --consumer-group "artisthub.analytics.v1"

confluent api-key create \
  --resource lkc-XXXXXX \
  --service-account sa-YYYYYY \
  --description "artisthub-analytics-consumer"
```

### 4c. Notification consumer API key

Needs READ on catalog; WRITE on deadletter; consumer group access.

```bash
confluent iam service-account create artisthub-notification \
  --description "ArtistHub notification consumer"
# → SA ID: sa-ZZZZZZ

confluent kafka acl create \
  --allow \
  --service-account sa-ZZZZZZ \
  --operation READ \
  --topic artisthub.catalog

confluent kafka acl create \
  --allow \
  --service-account sa-ZZZZZZ \
  --operation WRITE \
  --topic artisthub.deadletter

confluent kafka acl create \
  --allow \
  --service-account sa-ZZZZZZ \
  --operation READ \
  --consumer-group "artisthub.notifications.v1"

confluent api-key create \
  --resource lkc-XXXXXX \
  --service-account sa-ZZZZZZ \
  --description "artisthub-notification-consumer"
```

> **MVP simplification:** For the Phase 8D smoke test you may create a
> single API key with access to all topics and both consumer groups.
> Separate per-workload keys are required before Phase 8E deployment
> to honour least-privilege principle.

### Shared single API key (smoke test only):

```bash
# One key with full access — use ONLY for Phase 8D validation
confluent api-key create \
  --resource lkc-XXXXXX \
  --description "artisthub-phase8d-smoketest"
# → Key and Secret
```

---

## Step 5 — Schema Registry

The Confluent Cloud environment includes a managed Schema Registry.

```bash
# Get Schema Registry endpoint
confluent schema-registry cluster describe
# → Endpoint URL: https://psrc-XXXXX.us-east-1.aws.confluent.cloud

# Create Schema Registry API key
confluent api-key create \
  --resource <SR-cluster-id> \
  --description "artisthub-schema-registry"
# → SR Key and SR Secret
```

**Record for `.env`:**
```
SCHEMA_REGISTRY_URL=https://psrc-XXXXX.us-east-1.aws.confluent.cloud
SCHEMA_REGISTRY_API_KEY=<sr-key>
SCHEMA_REGISTRY_API_SECRET=<sr-secret>
```

---

## Step 6 — Set Compatibility Mode

ArtistHub uses BACKWARD compatibility on all subjects.
Confluent Cloud Schema Registry default is BACKWARD — verify and confirm:

```bash
# Check global default
curl -u "$SCHEMA_REGISTRY_API_KEY:$SCHEMA_REGISTRY_API_SECRET" \
  "$SCHEMA_REGISTRY_URL/config" | python3 -m json.tool
# Expected: {"compatibilityLevel":"BACKWARD"}

# If it shows NONE or FULL, set it:
curl -u "$SCHEMA_REGISTRY_API_KEY:$SCHEMA_REGISTRY_API_SECRET" \
  -X PUT "$SCHEMA_REGISTRY_URL/config" \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"compatibility":"BACKWARD"}'
```

---

## Step 7 — Populate .env

Add to `.env` (NEVER commit):

```bash
# Phase 8D — Confluent Cloud
KAFKA_BOOTSTRAP_SERVERS=pkc-XXXXX.us-east-1.aws.confluent.cloud:9092
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISMS=PLAIN
CONFLUENT_API_KEY=<kafka-api-key>
CONFLUENT_API_SECRET=<kafka-api-secret>
SCHEMA_REGISTRY_URL=https://psrc-XXXXX.us-east-1.aws.confluent.cloud
SCHEMA_REGISTRY_API_KEY=<sr-api-key>
SCHEMA_REGISTRY_API_SECRET=<sr-api-secret>
```

---

## Step 8 — Run Validation

```bash
# Source credentials
set -a; source .env; set +a

# Run Phase 8D validation suite
bash scripts/validate-phase-8d.sh
```

---

## ACL Summary (least-privilege)

| Workload | Topics | ACL | Consumer Group |
|---|---|---|---|
| outbox-relay | social, catalog, identity, deadletter | WRITE | — |
| analytics-consumer | social, catalog, identity | READ | artisthub.analytics.v1 |
| analytics-consumer | deadletter | WRITE | — |
| notification-consumer | catalog | READ | artisthub.notifications.v1 |
| notification-consumer | deadletter | WRITE | — |
| schema-registry clients (all) | — | — | SR API key covers all subjects |

---

## NetworkPolicy Egress Notes

Confluent Cloud uses dynamic infrastructure. Static CIDR-based NetworkPolicies
are not officially supported or recommended by Confluent for Kafka clients:

- Kafka broker endpoints (`pkc-*.confluent.cloud:9092`) resolve via DNS to
  elastic cloud infrastructure IPs that may change.
- Schema Registry endpoints (`psrc-*.confluent.cloud:443`) similarly use
  managed DNS.
- Confluent does NOT publish a stable list of CIDR blocks for general-purpose
  Kafka cluster access.

**Recommendation:** Keep the Phase 8C broad egress policies (ports 9092 + 443
to `0.0.0.0/0`) unless using Confluent Cloud with private networking
(AWS PrivateLink / GCP Private Service Connect). With private networking,
static CIDRs become available and the NetworkPolicies can be tightened.

See Phase 8D NetworkPolicy notes in `openshift/base/networkpolicies.yaml`.
