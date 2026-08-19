"""
services/avro_utils.py

Avro serialization and Schema Registry utilities for ArtistHub — Phase 7F.

This module provides:
  - Schema loading from the kafka/schemas/ directory
  - Confluent wire-format encode / decode  (5-byte magic header + schema_id)
  - Schema Registry HTTP client (register, fetch, compatibility check)
  - RecordNameStrategy subject derivation
  - Event-type → Avro record-name mapping for all 12 Phase 7B schemas

Subject naming — RecordNameStrategy
-------------------------------------
Confluent Schema Registry supports three subject-naming strategies.
ArtistHub uses **RecordNameStrategy**:

    subject = "<namespace>.<record_name>"

For our schemas the namespace is always ``io.artisthub.events`` so every
subject is:

    io.artisthub.events.FanFollowedArtist
    io.artisthub.events.FanUnfollowedArtist
    io.artisthub.events.ArtistPostCreated
    io.artisthub.events.ArtistPostDeleted
    io.artisthub.events.ArtistReleaseCreated
    io.artisthub.events.ArtistReleaseUpdated
    io.artisthub.events.ArtistReleaseDeleted
    io.artisthub.events.ArtistMerchCreated
    io.artisthub.events.ArtistMerchUpdated
    io.artisthub.events.ArtistMerchDeleted
    io.artisthub.events.ArtistRegistered
    io.artisthub.events.ArtistProfileUpdated

These 12 subjects match the Phase 7B schema files exactly.

Confluent wire format
----------------------
Each message begins with a 5-byte framing header:

    Byte 0       : 0x00  (magic byte — signals Confluent encoding)
    Bytes 1–4    : big-endian int32 schema_id assigned by Schema Registry

The Avro binary payload follows the 5 bytes.

This format is consumed natively by all Confluent clients and the
Redpanda Schema Registry.

Schema Registry
---------------
The client uses plain HTTP (requests library) to:
  - POST /subjects/{subject}/versions   — register or fetch existing schema
  - GET  /schemas/ids/{id}              — fetch schema by id (for decode)
  - POST /compatibility/subjects/{subject}/versions/latest
                                        — check BACKWARD compatibility

Configuration (environment variables)
--------------------------------------
SCHEMA_REGISTRY_URL        Base URL (default: http://localhost:8081)
SCHEMA_REGISTRY_API_KEY    Basic-auth username (Confluent Cloud; optional)
SCHEMA_REGISTRY_API_SECRET Basic-auth password (Confluent Cloud; optional)

Dependencies
------------
  fastavro  — Avro schema parsing, binary encode/decode
  requests  — Schema Registry HTTP calls
"""

import io
import json
import logging
import os
import struct
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import fastavro
import fastavro.schema
import fastavro.write
import fastavro.read
import requests as _requests

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Event-type to Avro record name mapping                              #
# (12 approved Phase 7B events)                                       #
# ------------------------------------------------------------------ #

#: Maps every known event_type string to its canonical Avro record name.
#: RecordNameStrategy subject = "<namespace>.<record_name>".
EVENT_TYPE_TO_RECORD_NAME: Dict[str, str] = {
    "fan.followed.artist":     "FanFollowedArtist",
    "fan.unfollowed.artist":   "FanUnfollowedArtist",
    "artist.post.created":     "ArtistPostCreated",
    "artist.post.deleted":     "ArtistPostDeleted",
    "artist.release.created":  "ArtistReleaseCreated",
    "artist.release.updated":  "ArtistReleaseUpdated",
    "artist.release.deleted":  "ArtistReleaseDeleted",
    "artist.merch.created":    "ArtistMerchCreated",
    "artist.merch.updated":    "ArtistMerchUpdated",
    "artist.merch.deleted":    "ArtistMerchDeleted",
    "artist.registered":       "ArtistRegistered",
    "artist.profile.updated":  "ArtistProfileUpdated",
}

#: Avro namespace shared by all 12 schemas.
_NAMESPACE = "io.artisthub.events"

#: Maps record name to .avsc file path relative to the repo root.
_RECORD_NAME_TO_FILE: Dict[str, str] = {
    "FanFollowedArtist": (
        "kafka/schemas/social/fan_followed_artist.avsc"
    ),
    "FanUnfollowedArtist": (
        "kafka/schemas/social/fan_unfollowed_artist.avsc"
    ),
    "ArtistPostCreated": (
        "kafka/schemas/social/artist_post_created.avsc"
    ),
    "ArtistPostDeleted": (
        "kafka/schemas/social/artist_post_deleted.avsc"
    ),
    "ArtistReleaseCreated": (
        "kafka/schemas/catalog/artist_release_created.avsc"
    ),
    "ArtistReleaseUpdated": (
        "kafka/schemas/catalog/artist_release_updated.avsc"
    ),
    "ArtistReleaseDeleted": (
        "kafka/schemas/catalog/artist_release_deleted.avsc"
    ),
    "ArtistMerchCreated": (
        "kafka/schemas/catalog/artist_merch_created.avsc"
    ),
    "ArtistMerchUpdated": (
        "kafka/schemas/catalog/artist_merch_updated.avsc"
    ),
    "ArtistMerchDeleted": (
        "kafka/schemas/catalog/artist_merch_deleted.avsc"
    ),
    "ArtistRegistered": (
        "kafka/schemas/identity/artist_registered.avsc"
    ),
    "ArtistProfileUpdated": (
        "kafka/schemas/identity/artist_profile_updated.avsc"
    ),
}

#: Confluent wire-format magic byte.
_MAGIC_BYTE = b"\x00"


# ------------------------------------------------------------------ #
# Schema path resolution                                               #
# ------------------------------------------------------------------ #

def _schemas_base() -> Path:
    """
    Return the base directory that contains the ``kafka/schemas/`` tree.

    Resolution order (first match wins):

    1. ``KAFKA_SCHEMAS_DIR`` environment variable — set inside Docker to
       ``/app/kafka/schemas`` so the container-copied schema files are
       found regardless of where this module sits in the image.
    2. Path traversal from this file's location — valid for local
       development where the repo structure is intact:
       ``backend/app/services/avro_utils.py`` → 3 parents → repo root.
       The paths in ``_RECORD_NAME_TO_FILE`` (e.g.
       ``kafka/schemas/social/fan_followed_artist.avsc``) are then
       appended to that repo root.

    The env-var path is the *schemas* directory itself, so it is returned
    directly and the ``kafka/schemas/`` prefix in each ``_RECORD_NAME_TO_FILE``
    value is stripped when constructing the final path (see ``_schema_path``).
    """
    env_dir = os.environ.get("KAFKA_SCHEMAS_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    # Local dev: parents[3] is the repo root.
    return Path(__file__).resolve().parents[3]


def _schema_path(record_name: str) -> Path:
    """Return the absolute path to the .avsc file for record_name."""
    rel = _RECORD_NAME_TO_FILE.get(record_name)
    if rel is None:
        raise KeyError(
            f"No schema file registered for record '{record_name}'. "
            f"Known records: {sorted(_RECORD_NAME_TO_FILE)}"
        )
    base = _schemas_base()
    env_dir = os.environ.get("KAFKA_SCHEMAS_DIR", "").strip()
    if env_dir:
        # env_dir IS the kafka/schemas/ directory.
        # _RECORD_NAME_TO_FILE paths start with "kafka/schemas/"; strip that
        # prefix so the final path is: /app/kafka/schemas/<subdir>/<file>.avsc
        prefix = "kafka/schemas/"
        rel_stripped = rel[len(prefix):] if rel.startswith(prefix) else rel
        return base / rel_stripped
    return base / rel


# ------------------------------------------------------------------ #
# Schema loading and caching                                           #
# ------------------------------------------------------------------ #

_schema_cache: Dict[str, Any] = {}


def load_schema(record_name: str) -> Any:
    """
    Load and parse the fastavro schema for record_name.

    Schemas are parsed once and cached in memory for the process lifetime.
    Thread-safety is not required because the relay and consumers are
    single-threaded.

    Args:
        record_name: Avro record name, e.g. ``"FanFollowedArtist"``.

    Returns:
        A fastavro parsed schema object.

    Raises:
        KeyError:   if record_name has no registered schema file.
        FileNotFoundError: if the .avsc file does not exist on disk.
    """
    if record_name in _schema_cache:
        return _schema_cache[record_name]

    path = _schema_path(record_name)
    if not path.exists():
        raise FileNotFoundError(
            f"Avro schema file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as fh:
        schema_dict = json.load(fh)

    parsed = fastavro.schema.parse_schema(schema_dict)
    _schema_cache[record_name] = parsed
    logger.debug("Avro schema loaded | record=%s path=%s", record_name, path)
    return parsed


def schema_str(record_name: str) -> str:
    """
    Return the raw JSON schema string for record_name.

    Used when registering a schema with Schema Registry.
    """
    path = _schema_path(record_name)
    with path.open("r", encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------------ #
# Subject naming — RecordNameStrategy                                  #
# ------------------------------------------------------------------ #

def record_name_for_event_type(event_type: str) -> str:
    """
    Return the Avro record name for an event_type string.

    Raises:
        ValueError: if event_type is not one of the 12 known types.
    """
    name = EVENT_TYPE_TO_RECORD_NAME.get(event_type)
    if name is None:
        raise ValueError(
            f"Unknown event_type '{event_type}'. "
            f"Known types: {sorted(EVENT_TYPE_TO_RECORD_NAME)}"
        )
    return name


def subject_for_record(record_name: str) -> str:
    """
    Return the Schema Registry subject for record_name.

    Strategy: RecordNameStrategy
    Formula:  "<namespace>.<record_name>"
    Example:  "io.artisthub.events.FanFollowedArtist"

    This is the subject name used when registering a schema or fetching
    the schema_id by subject.
    """
    return f"{_NAMESPACE}.{record_name}"


def subject_for_event_type(event_type: str) -> str:
    """
    Return the Schema Registry subject for an event_type string.

    Combines record_name_for_event_type and subject_for_record.

    Example:
        subject_for_event_type("fan.followed.artist")
        → "io.artisthub.events.FanFollowedArtist"
    """
    return subject_for_record(record_name_for_event_type(event_type))


# ------------------------------------------------------------------ #
# Schema Registry HTTP client                                          #
# ------------------------------------------------------------------ #

def _sr_config() -> Tuple[str, Optional[Tuple[str, str]]]:
    """
    Return (base_url, auth_tuple) from environment variables.

    auth_tuple is (api_key, api_secret) for Confluent Cloud basic auth,
    or None for unauthenticated local development.

    No credentials are hardcoded.
    """
    url = os.environ.get(
        "SCHEMA_REGISTRY_URL", "http://localhost:8081"
    ).rstrip("/")
    api_key = os.environ.get("SCHEMA_REGISTRY_API_KEY", "")
    api_secret = os.environ.get("SCHEMA_REGISTRY_API_SECRET", "")
    auth = (api_key, api_secret) if api_key else None
    return url, auth


def register_schema(record_name: str) -> int:
    """
    Register (or retrieve the existing id of) a schema in Schema Registry.

    Uses the RecordNameStrategy subject name.
    If the schema already exists and is compatible, returns the existing id.

    Args:
        record_name: Avro record name, e.g. "FanFollowedArtist".

    Returns:
        Integer schema_id assigned by Schema Registry.

    Raises:
        requests.HTTPError: on unexpected HTTP errors.
        ConnectionError:    if the registry is unreachable.
    """
    url, auth = _sr_config()
    subject = subject_for_record(record_name)
    raw_schema = schema_str(record_name)

    resp = _requests.post(
        f"{url}/subjects/{subject}/versions",
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
        json={"schema": raw_schema, "schemaType": "AVRO"},
        auth=auth,
        timeout=10,
    )
    resp.raise_for_status()
    schema_id: int = resp.json()["id"]
    logger.info(
        "Schema registered | record=%s subject=%s schema_id=%d",
        record_name, subject, schema_id,
    )
    return schema_id


def get_schema_id(record_name: str) -> int:
    """
    Fetch the schema_id for record_name from Schema Registry.

    Calls POST /subjects/{subject} (lookup without registering).

    Raises:
        requests.HTTPError: on unexpected HTTP errors (including 404 if
            the schema has never been registered).
    """
    url, auth = _sr_config()
    subject = subject_for_record(record_name)
    raw_schema = schema_str(record_name)

    resp = _requests.post(
        f"{url}/subjects/{subject}",
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
        json={"schema": raw_schema, "schemaType": "AVRO"},
        auth=auth,
        timeout=10,
    )
    resp.raise_for_status()
    return int(resp.json()["id"])


def check_compatibility(
    record_name: str,
    schema_json_str: str,
) -> bool:
    """
    Check BACKWARD compatibility of schema_json_str against the latest
    registered version for record_name.

    Returns True if compatible, False if not.

    Raises:
        requests.HTTPError: on HTTP errors (e.g., no existing version).
    """
    url, auth = _sr_config()
    subject = subject_for_record(record_name)

    resp = _requests.post(
        f"{url}/compatibility/subjects/{subject}/versions/latest",
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
        json={"schema": schema_json_str, "schemaType": "AVRO"},
        auth=auth,
        timeout=10,
    )
    resp.raise_for_status()
    return bool(resp.json().get("is_compatible", False))


# ------------------------------------------------------------------ #
# Schema_id cache (producer-side)                                      #
# ------------------------------------------------------------------ #

_schema_id_cache: Dict[str, int] = {}


def get_or_register_schema_id(record_name: str) -> int:
    """
    Return the cached schema_id for record_name, registering first if needed.

    The id is cached per-process to avoid repeated Registry calls on every
    message. The cache is invalidated only on process restart.
    """
    if record_name not in _schema_id_cache:
        _schema_id_cache[record_name] = register_schema(record_name)
    return _schema_id_cache[record_name]


# ------------------------------------------------------------------ #
# Confluent wire-format encode / decode                                #
# ------------------------------------------------------------------ #

def encode(event_type: str, record: dict, schema_id: int) -> bytes:
    """
    Serialize record to Confluent Avro wire format.

    Wire format:
        [0x00][schema_id: 4 bytes big-endian][Avro binary payload]

    Args:
        event_type: e.g. "fan.followed.artist" — selects the schema.
        record:     Full event dict (envelope + payload).
        schema_id:  Schema id assigned by Schema Registry for this schema.

    Returns:
        Bytes ready to pass as the Kafka message ``value``.

    Raises:
        ValueError:          if event_type is unknown.
        fastavro.write.ValidationError (or similar): if record violates schema.
    """
    record_name = record_name_for_event_type(event_type)
    parsed = load_schema(record_name)

    buf = io.BytesIO()
    # 5-byte Confluent header.
    buf.write(_MAGIC_BYTE)
    buf.write(struct.pack(">I", schema_id))
    # Avro binary payload (schemaless — schema is identified by the header id).
    fastavro.write.schemaless_writer(buf, parsed, record)
    return buf.getvalue()


def decode(raw: bytes) -> dict:
    """
    Deserialize a Confluent Avro wire-format message.

    Reads the 5-byte header to extract schema_id, then fetches the schema
    from Schema Registry (cached after first fetch), and deserializes the
    Avro binary payload.

    Args:
        raw: Raw bytes from Kafka message value.

    Returns:
        Deserialized event dict.

    Raises:
        ValueError:  if raw is None, too short, or has wrong magic byte.
        KeyError:    if the schema_id is not a known ArtistHub schema.
        requests.HTTPError: if Schema Registry lookup fails.
    """
    if not raw or len(raw) < 5:
        raise ValueError(
            f"Message too short for Confluent wire format: "
            f"got {len(raw) if raw else 0} bytes, expected ≥ 5"
        )
    if raw[0:1] != _MAGIC_BYTE:
        raise ValueError(
            f"Invalid Confluent magic byte: "
            f"expected 0x00, got {raw[0]:02x}"
        )

    schema_id = struct.unpack(">I", raw[1:5])[0]
    avro_bytes = raw[5:]

    parsed = _fetch_reader_schema(schema_id)

    buf = io.BytesIO(avro_bytes)
    return fastavro.read.schemaless_reader(buf, parsed)


# Per-process cache: schema_id (int) → fastavro parsed schema.
_reader_schema_cache: Dict[int, Any] = {}


def _fetch_reader_schema(schema_id: int) -> Any:
    """
    Return the fastavro parsed schema for schema_id.

    Fetches the raw schema JSON from Schema Registry on first call,
    parses it, and caches it.

    Raises:
        requests.HTTPError: if the schema_id is not found in the registry.
        KeyError:           if the returned schema's record name is unknown.
    """
    if schema_id in _reader_schema_cache:
        return _reader_schema_cache[schema_id]

    url, auth = _sr_config()
    resp = _requests.get(
        f"{url}/schemas/ids/{schema_id}",
        auth=auth,
        timeout=10,
    )
    resp.raise_for_status()
    schema_dict = json.loads(resp.json()["schema"])
    parsed = fastavro.schema.parse_schema(schema_dict)
    _reader_schema_cache[schema_id] = parsed
    logger.debug("Reader schema cached | schema_id=%d", schema_id)
    return parsed


# ------------------------------------------------------------------ #
# Convenience: encode from outbox payload dict                        #
# ------------------------------------------------------------------ #

def encode_outbox_payload(
    event_type: str,
    payload_dict: dict,
    schema_id: int,
) -> bytes:
    """
    Serialize a full outbox payload dict to Confluent Avro wire format.

    The outbox stores the complete event envelope + payload as a JSON
    string. This function accepts that decoded dict directly and passes
    it to encode().

    The dict must already match the Avro schema field layout (envelope
    fields at the top level, domain payload under "payload").

    Args:
        event_type:   Event type string for schema selection.
        payload_dict: Full event dict (already decoded from outbox JSON).
        schema_id:    Schema id from Schema Registry.

    Returns:
        Confluent Avro wire-format bytes.
    """
    return encode(event_type, payload_dict, schema_id)
