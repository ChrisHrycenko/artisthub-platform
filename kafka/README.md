# ArtistHub — Kafka / Schema Registry

This directory contains the Avro event schema contracts and tooling for
ArtistHub's event-driven extension layer (Phase 7B).

---

## Contents

```
kafka/
├── schemas/
│   ├── common/
│   │   └── envelope.avsc          ← Reference doc: standard 6-field envelope
│   ├── social/
│   │   ├── fan_followed_artist.avsc
│   │   ├── fan_unfollowed_artist.avsc
│   │   ├── artist_post_created.avsc
│   │   └── artist_post_deleted.avsc
│   ├── catalog/
│   │   ├── artist_release_created.avsc
│   │   ├── artist_release_updated.avsc
│   │   ├── artist_release_deleted.avsc
│   │   ├── artist_merch_created.avsc
│   │   ├── artist_merch_updated.avsc
│   │   └── artist_merch_deleted.avsc
│   └── identity/
│       ├── artist_registered.avsc
│       └── artist_profile_updated.avsc
├── register_schemas.py            ← Registers all schemas with Schema Registry
├── validate_schemas.py            ← Validates registration + sample events
└── README.md                      ← This file
```

---

## Why Avro?

| Concern | JSON | Avro |
|---|---|---|
| Schema enforcement | None at runtime | Enforced on read/write |
| Evolution contract | Ad-hoc | Explicit via Schema Registry |
| Payload size | Verbose | Compact binary |
| Consumer safety | Any change can break consumers | Registry rejects incompatible changes |

ArtistHub events are published by a single Flask backend and consumed by
future analytics, search-index, and notification services.  Avro + Schema
Registry gives consumers a guaranteed contract: if a producer registers a
new schema version, the registry validates it is compatible before accepting
it.  Incompatible changes fail at registration time, not at consumer runtime.

---

## Schema structure

Every event schema follows the same two-layer pattern:

```
RecordName                       ← top-level record (e.g. FanFollowedArtist)
├── event_id        string       ← UUID v4 for this event instance
├── event_type      string       ← Human-readable type tag
├── event_version   string       ← Schema version ("1.0")
├── occurred_at     string       ← ISO 8601 UTC when the event happened
├── producer        string       ← "artisthub-backend"
├── correlation_id  null|string  ← Optional request trace ID
└── payload         record       ← Domain-specific business data
    └── ...                      ← Fields from the relevant model.to_dict()
```

The envelope fields are **repeated verbatim** in each schema.  Schema Registry
stores schemas as standalone documents and does not support `$ref` includes.
The `common/envelope.avsc` file is a human-readable reference only.

---

## Subject naming strategy: RecordNameStrategy

Subjects are named `<namespace>.<RecordName>`, for example:

```
io.artisthub.events.FanFollowedArtist
io.artisthub.events.ArtistReleaseCreated
```

**Why RecordNameStrategy, not TopicRecordNameStrategy?**

TopicRecordNameStrategy subjects are `<topic>-<namespace>.<name>`, which
binds a schema to a specific topic.  If an event ever moves to a different
topic, all subject names change and consumers break.  RecordNameStrategy
decouples the schema identity from the topic name: producers and consumers
look up a subject by record name only, regardless of which topic carries it.

---

## Compatibility mode: BACKWARD

Each subject is configured with `BACKWARD` compatibility.  This means:

* A **new** schema version can always be read by a consumer using the
  **previous** schema version.
* Producers can add optional fields (with `null` union + `default: null`).
* Producers **cannot** remove required fields or change field types.

**Allowed under BACKWARD:**
- Add an optional field: `["null", "string"]` with `"default": null`
- Change a field's doc string

**Not allowed under BACKWARD (registry will reject):**
- Remove any field
- Add a required field (no default)
- Change a field's type (e.g. `int` → `string`)
- Rename a field

---

## Schema evolution guide

To add a new field to an existing event schema:

1. Open the relevant `.avsc` file.
2. Add the new field with a `null` union type and `null` default:
   ```json
   {
     "name": "my_new_field",
     "type": ["null", "string"],
     "doc": "Description of the new field.",
     "default": null
   }
   ```
3. Re-run `register_schemas.py` — the registry will validate and assign
   a new version id.
4. Re-run `validate_schemas.py` to confirm the update is reflected in SR.

To add a **new event schema**:

1. Create a new `.avsc` file in the appropriate domain subdirectory.
2. Follow the envelope + payload pattern (copy an existing schema).
3. Add a sample event to `validate_schemas.py`'s `SAMPLE_EVENTS` dict.
4. Run `register_schemas.py` then `validate_schemas.py`.

---

## Running the scripts

### Prerequisites

- Python 3.8+ (stdlib only — no pip installs required)
- Redpanda (or any Confluent-compatible broker) running with Schema Registry
  exposed on port `8081`

Start the full Kafka stack (from the project root):

```bash
docker-compose -f docker/docker-compose.yml \
               -f docker/docker-compose.kafka.yml up -d
```

### Register all schemas

```bash
python3 kafka/register_schemas.py
```

Override the SR URL:

```bash
python3 kafka/register_schemas.py --sr-url http://localhost:8081
# or
SCHEMA_REGISTRY_URL=http://redpanda:8081 python3 kafka/register_schemas.py
```

Dry-run (discover subjects without registering):

```bash
python3 kafka/register_schemas.py --dry-run
```

The script is **idempotent**: re-running it when schemas are unchanged
prints `[SKIP]` for each subject and exits 0.

### Validate schemas

```bash
# Validate against a running Schema Registry
python3 kafka/validate_schemas.py

# Validate local .avsc structure + sample events only (no SR required)
python3 kafka/validate_schemas.py --local-only
```

`validate_schemas.py` runs four check groups:

| Group | What it checks |
|---|---|
| A | Local .avsc structure + sample event conformance |
| B | All 12 subjects exist in SR |
| C | Each subject's compatibility = BACKWARD |
| D | SR-stored schema matches local .avsc exactly |

---

## Topics

| Topic | Partitions | Retention | Events |
|---|---|---|---|
| `artisthub.social` | 6 | 7 days | FanFollowedArtist, FanUnfollowedArtist, ArtistPostCreated, ArtistPostDeleted |
| `artisthub.catalog` | 6 | 30 days | ArtistRelease{Created,Updated,Deleted}, ArtistMerch{Created,Updated,Deleted} |
| `artisthub.identity` | 3 | 90 days | ArtistRegistered, ArtistProfileUpdated |
| `artisthub.deadletter` | 3 | 14 days | Undeliverable events |

---

## PII boundaries

`email` appears **only** in `ArtistRegistered` (identity topic, 90-day retention).
No email or PII fields are present in social or catalog events.  This keeps
the short-lived social/catalog topics safe for broader consumer access.
