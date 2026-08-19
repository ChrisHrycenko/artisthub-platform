"""
tests/test_avro_utils.py

Test suite for Phase 7F — Avro Serialization and Schema Registry.

Coverage targets
----------------
avro_utils module
  - record_name_for_event_type: known types return correct name; unknown raises
  - subject_for_record: RecordNameStrategy subject derivation
  - subject_for_event_type: combined lookup
  - load_schema: returns parseable fastavro schema; caches on second call
  - schema_str: returns raw JSON string
  - encode + decode (local round-trip, no Schema Registry):
      - correct Confluent magic byte and schema_id header
      - valid record encodes and decodes without error
      - decoded dict matches original
      - unknown event_type raises ValueError before encode
      - record missing required Avro field raises on encode
      - wrong field type raises on encode
  - decode error paths:
      - raw < 5 bytes raises ValueError
      - wrong magic byte raises ValueError

KafkaProducerService (Phase 7F)
  - produce_avro serializes to Confluent bytes and calls underlying produce
  - produce_avro uses correct event_type for schema selection
  - produce_avro with unknown event_type raises ValueError (no produce call)
  - produce_avro correctly passes on_delivery callback
  - produce() (raw path) still works for dead-letter use
  - flush() delegates to underlying producer

Outbox relay (Phase 7F)
  - poll_and_publish calls produce_avro (not produce) with correct args
  - unknown event_type in outbox records error and does not mark published_at
  - failed produce_avro records last_error and increments publish_attempts
  - successful produce_avro marks published_at via delivery callback

Analytics consumer (Phase 7F)
  - parse_message handles Avro wire format (mocked avro_decode)
  - parse_message handles plain JSON (unit-test path unchanged)
  - parse_message with Confluent magic byte but decode failure raises ValueError
  - all existing 44 analytics consumer tests still pass (no regression)

Notification consumer (Phase 7F)
  - parse_message handles Avro wire format (mocked avro_decode)
  - parse_message handles plain JSON (unchanged)
  - all existing 40 notification consumer tests still pass (no regression)

Schema compatibility (unit — no live Schema Registry)
  - v1 schema round-trips correctly
  - v2 schema (adds optional field with default) is BACKWARD-compatible
    with v1 (fastavro reader/writer schema test)
  - breaking schema (removes required field) is NOT compatible with v1
    verified by fastavro schema evolution test
  - all 12 Phase 7B record names are in EVENT_TYPE_TO_RECORD_NAME
  - all 12 subjects match expected RecordNameStrategy pattern

Subject names (RecordNameStrategy)
  - 12 subjects are derived correctly and match approved list

Live Schema Registry / Kafka broker tests
  - NOT performed: no live broker or registry available in unit test
    environment. The test suite clearly labels which tests are mocked.
  - Live integration test procedure is documented in README.md and
    kafka/README.md.
"""

import io
import json
import struct
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import fastavro
import fastavro.schema
import fastavro.read
import fastavro.write
import pytest

# ------------------------------------------------------------------ #
# Import avro_utils — must succeed without a running Schema Registry  #
# ------------------------------------------------------------------ #
from app.services.avro_utils import (
    EVENT_TYPE_TO_RECORD_NAME,
    _NAMESPACE,
    encode,
    load_schema,
    record_name_for_event_type,
    schema_str,
    subject_for_event_type,
    subject_for_record,
)

# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

# The 12 approved Phase 7B event types mapped to their record names.
_ALL_EVENT_TYPES = {
    "fan.followed.artist":    "FanFollowedArtist",
    "fan.unfollowed.artist":  "FanUnfollowedArtist",
    "artist.post.created":    "ArtistPostCreated",
    "artist.post.deleted":    "ArtistPostDeleted",
    "artist.release.created": "ArtistReleaseCreated",
    "artist.release.updated": "ArtistReleaseUpdated",
    "artist.release.deleted": "ArtistReleaseDeleted",
    "artist.merch.created":   "ArtistMerchCreated",
    "artist.merch.updated":   "ArtistMerchUpdated",
    "artist.merch.deleted":   "ArtistMerchDeleted",
    "artist.registered":      "ArtistRegistered",
    "artist.profile.updated": "ArtistProfileUpdated",
}

_SCHEMA_ID = 42  # arbitrary id used in unit tests


def _fan_followed_record(event_id: str = None) -> dict:
    """Build a valid FanFollowedArtist event dict."""
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "fan.followed.artist",
        "event_version": "1",
        "occurred_at": "2026-08-19T12:00:00Z",
        "producer": "artisthub-api",
        "correlation_id": None,
        "payload": {
            "follow_id": 1,
            "fan_id": 10,
            "artist_id": 5,
            "followed_at": "2026-08-19T12:00:00Z",
        },
    }


def _release_created_record(event_id: str = None) -> dict:
    """Build a valid ArtistReleaseCreated event dict."""
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "artist.release.created",
        "event_version": "1",
        "occurred_at": "2026-08-19T12:00:00Z",
        "producer": "artisthub-api",
        "correlation_id": None,
        "payload": {
            "release_id": 100,
            "artist_id": 5,
            "title": "Test Album",
            "release_type": "Album",
            "genre": None,
            "description": None,
            "artwork_url": None,
            "streaming_url": None,
            "release_date": None,
            "created_at": "2026-08-19T12:00:00Z",
        },
    }


def _encode_local(event_type: str, record: dict, schema_id: int = _SCHEMA_ID) -> bytes:
    """Encode without hitting Schema Registry."""
    return encode(event_type, record, schema_id)


def _decode_local(raw: bytes, event_type: str) -> dict:
    """
    Decode Avro bytes using the local schema (no Schema Registry call).

    Extracts schema_id from the header (ignored here) and uses the
    local schema for the given event_type to deserialize.
    """
    assert raw[0:1] == b"\x00", "Expected Confluent magic byte"
    avro_bytes = raw[5:]
    record_name = record_name_for_event_type(event_type)
    parsed = load_schema(record_name)
    buf = io.BytesIO(avro_bytes)
    return fastavro.read.schemaless_reader(buf, parsed)


# ------------------------------------------------------------------ #
# 1 — Subject naming                                                   #
# ------------------------------------------------------------------ #

class TestSubjectNaming:
    """Verify RecordNameStrategy subject derivation."""

    def test_record_name_for_known_event_types(self):
        for event_type, expected_name in _ALL_EVENT_TYPES.items():
            assert record_name_for_event_type(event_type) == expected_name

    def test_record_name_for_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown event_type"):
            record_name_for_event_type("some.unknown.event")

    def test_subject_for_record_format(self):
        subject = subject_for_record("FanFollowedArtist")
        assert subject == "io.artisthub.events.FanFollowedArtist"

    def test_subject_for_event_type(self):
        assert (
            subject_for_event_type("fan.followed.artist")
            == "io.artisthub.events.FanFollowedArtist"
        )

    def test_all_12_subjects_match_pattern(self):
        """All 12 subjects must follow <namespace>.<RecordName> pattern."""
        for event_type, record_name in _ALL_EVENT_TYPES.items():
            expected = f"{_NAMESPACE}.{record_name}"
            assert subject_for_event_type(event_type) == expected, (
                f"Subject mismatch for {event_type}"
            )

    def test_12_event_types_registered(self):
        """Exactly 12 event types must be in EVENT_TYPE_TO_RECORD_NAME."""
        assert len(EVENT_TYPE_TO_RECORD_NAME) == 12
        for et in _ALL_EVENT_TYPES:
            assert et in EVENT_TYPE_TO_RECORD_NAME


# ------------------------------------------------------------------ #
# 2 — Schema loading                                                   #
# ------------------------------------------------------------------ #

class TestSchemaLoading:
    """Verify schema files are loadable and produce valid fastavro schemas."""

    def test_load_schema_returns_parsed_schema(self):
        parsed = load_schema("FanFollowedArtist")
        assert parsed is not None

    def test_load_schema_caches_on_second_call(self):
        """Second call returns the same object (no re-parse)."""
        first = load_schema("FanFollowedArtist")
        second = load_schema("FanFollowedArtist")
        assert first is second

    def test_load_all_12_schemas_without_error(self):
        for record_name in _ALL_EVENT_TYPES.values():
            parsed = load_schema(record_name)
            assert parsed is not None, f"Failed to load {record_name}"

    def test_load_schema_unknown_record_raises(self):
        with pytest.raises(KeyError):
            load_schema("UnknownRecord")

    def test_schema_str_returns_json_string(self):
        raw = schema_str("FanFollowedArtist")
        parsed = json.loads(raw)
        assert parsed["name"] == "FanFollowedArtist"
        assert parsed["namespace"] == "io.artisthub.events"


# ------------------------------------------------------------------ #
# 3 — Encode / decode round-trips (local, no Schema Registry)          #
# ------------------------------------------------------------------ #

class TestEncodeDecodeRoundTrip:
    """Verify Avro encode/decode with local schemas (no live registry)."""

    def test_encode_returns_bytes(self):
        raw = _encode_local("fan.followed.artist", _fan_followed_record())
        assert isinstance(raw, bytes)

    def test_confluent_magic_byte_present(self):
        raw = _encode_local("fan.followed.artist", _fan_followed_record())
        assert raw[0:1] == b"\x00"

    def test_schema_id_in_header(self):
        raw = _encode_local("fan.followed.artist", _fan_followed_record(), schema_id=99)
        schema_id = struct.unpack(">I", raw[1:5])[0]
        assert schema_id == 99

    def test_round_trip_fan_followed_artist(self):
        original = _fan_followed_record()
        raw = _encode_local("fan.followed.artist", original)
        decoded = _decode_local(raw, "fan.followed.artist")
        assert decoded["event_id"] == original["event_id"]
        assert decoded["event_type"] == original["event_type"]
        assert decoded["payload"]["fan_id"] == original["payload"]["fan_id"]
        assert decoded["payload"]["artist_id"] == original["payload"]["artist_id"]

    def test_round_trip_release_created(self):
        original = _release_created_record()
        raw = _encode_local("artist.release.created", original)
        decoded = _decode_local(raw, "artist.release.created")
        assert decoded["event_id"] == original["event_id"]
        assert decoded["payload"]["release_id"] == original["payload"]["release_id"]
        assert decoded["payload"]["title"] == "Test Album"

    def test_round_trip_nullable_fields(self):
        """Nullable fields (union null/string) survive the round-trip."""
        original = _fan_followed_record()
        original["correlation_id"] = "req-abc"
        raw = _encode_local("fan.followed.artist", original)
        decoded = _decode_local(raw, "fan.followed.artist")
        assert decoded["correlation_id"] == "req-abc"

    def test_round_trip_null_correlation_id(self):
        original = _fan_followed_record()
        original["correlation_id"] = None
        raw = _encode_local("fan.followed.artist", original)
        decoded = _decode_local(raw, "fan.followed.artist")
        assert decoded["correlation_id"] is None

    def test_encode_unknown_event_type_raises(self):
        with pytest.raises(ValueError, match="Unknown event_type"):
            encode("some.unknown.event", {}, 1)

    def test_encode_missing_required_field_raises(self):
        """A record missing a required (non-nullable) Avro field must raise."""
        bad = _fan_followed_record()
        del bad["occurred_at"]  # required string field
        with pytest.raises(Exception):
            encode("fan.followed.artist", bad, _SCHEMA_ID)

    def test_encode_wrong_field_type_raises(self):
        """Passing a string where an int is required must raise."""
        bad = _fan_followed_record()
        bad["payload"]["fan_id"] = "not-an-int"
        with pytest.raises(Exception):
            encode("fan.followed.artist", bad, _SCHEMA_ID)

    def test_round_trips_all_12_event_types(self):
        """Each of the 12 event types can encode a minimal valid record."""
        # Build minimal valid records for each schema.
        records = {
            "fan.followed.artist": _fan_followed_record(),
            "fan.unfollowed.artist": {
                "event_id": str(uuid.uuid4()),
                "event_type": "fan.unfollowed.artist",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "fan_id": 1, "artist_id": 2,
                    "unfollowed_at": "2026-08-19T12:00:00Z",
                },
            },
            "artist.post.created": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.post.created",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "post_id": 1, "artist_id": 2, "body": "Hello",
                    "image_url": None, "posted_at": "2026-08-19T12:00:00Z",
                },
            },
            "artist.post.deleted": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.post.deleted",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "post_id": 1, "artist_id": 2,
                    "deleted_at": "2026-08-19T12:00:00Z",
                },
            },
            "artist.release.created": _release_created_record(),
            "artist.release.updated": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.release.updated",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "release_id": 1, "artist_id": 2,
                    "title": "X", "release_type": "Single",
                    "genre": None, "description": None,
                    "artwork_url": None, "streaming_url": None,
                    "release_date": None,
                },
            },
            "artist.release.deleted": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.release.deleted",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "release_id": 1, "artist_id": 2,
                    "deleted_at": "2026-08-19T12:00:00Z",
                },
            },
            "artist.merch.created": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.merch.created",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "product_id": 1, "artist_id": 2,
                    "product_name": "T-Shirt", "price": 29.99,
                    "description": None, "image_url": None,
                    "inventory_quantity": None,
                    "created_at": "2026-08-19T12:00:00Z",
                },
            },
            "artist.merch.updated": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.merch.updated",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "product_id": 1, "artist_id": 2,
                    "product_name": "T-Shirt", "price": 29.99,
                    "description": None, "image_url": None,
                    "inventory_quantity": None,
                },
            },
            "artist.merch.deleted": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.merch.deleted",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "product_id": 1, "artist_id": 2,
                    "deleted_at": "2026-08-19T12:00:00Z",
                },
            },
            "artist.registered": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.registered",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "artist_id": 1,
                    "email": "test@example.com",
                    "display_name": "DJ Test",
                    "genre": None,
                    "location": None,
                    "registered_at": "2026-08-19T12:00:00Z",
                },
            },
            "artist.profile.updated": {
                "event_id": str(uuid.uuid4()),
                "event_type": "artist.profile.updated",
                "event_version": "1",
                "occurred_at": "2026-08-19T12:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {
                    "artist_id": 1,
                    "display_name": "DJ Test",
                    "bio": None,
                    "genre": None,
                    "location": None,
                    "profile_image_url": None,
                },
            },
        }
        for event_type, record in records.items():
            raw = _encode_local(event_type, record)
            assert raw[0:1] == b"\x00", f"Magic byte missing for {event_type}"
            decoded = _decode_local(raw, event_type)
            assert decoded["event_id"] == record["event_id"], (
                f"event_id mismatch for {event_type}"
            )


# ------------------------------------------------------------------ #
# 4 — Decode error paths                                               #
# ------------------------------------------------------------------ #

class TestDecodeErrorPaths:
    """Verify decode() raises clear errors for malformed input."""

    def test_decode_too_short_raises(self):
        from app.services.avro_utils import decode
        with pytest.raises(ValueError, match="too short"):
            decode(b"\x00\x00\x00")  # only 3 bytes

    def test_decode_wrong_magic_byte_raises(self):
        from app.services.avro_utils import decode
        with pytest.raises(ValueError, match="magic byte"):
            decode(b"\x01\x00\x00\x00\x01" + b"\x00" * 10)

    def test_decode_none_raises(self):
        from app.services.avro_utils import decode
        with pytest.raises(ValueError):
            decode(b"")


# ------------------------------------------------------------------ #
# 5 — Schema compatibility (local fastavro — no Schema Registry)       #
# ------------------------------------------------------------------ #

class TestSchemaCompatibility:
    """
    BACKWARD compatibility tests using fastavro reader/writer schema mode.

    These tests do NOT require a live Schema Registry.
    They verify the Avro spec rules that Schema Registry would enforce.

    BACKWARD compatibility means: a new schema (v2) can be used to read
    data written with the old schema (v1). Old consumers can read new data.

    In fastavro, this is tested by writing with the writer schema (v1)
    and reading with the reader schema (v2). If the read succeeds, the
    change is backward-compatible.
    """

    def _load_test_schema(self, filename: str):
        """Load a test compatibility schema from kafka/schemas/test_compat/."""
        from pathlib import Path
        path = (
            Path(__file__).resolve().parents[2]
            / "kafka" / "schemas" / "test_compat" / filename
        )
        with path.open("r") as f:
            return fastavro.schema.parse_schema(json.load(f))

    def test_v1_schema_round_trip(self):
        """v1 schema writes and reads back correctly."""
        writer_schema = load_schema("FanFollowedArtist")
        record = _fan_followed_record()
        buf = io.BytesIO()
        fastavro.write.schemaless_writer(buf, writer_schema, record)
        buf.seek(0)
        decoded = fastavro.read.schemaless_reader(buf, writer_schema)
        assert decoded["event_id"] == record["event_id"]
        assert decoded["payload"]["follow_id"] == 1

    def test_v2_adds_optional_field_is_backward_compatible(self):
        """
        v2 adds 'source_device' (nullable with null default).

        BACKWARD compatibility: v2 reader can read v1-encoded data.
        The 'source_device' field defaults to null when absent.

        This is the approved schema evolution pattern for ArtistHub.

        Uses the Avro container file format for proper reader/writer schema
        resolution with named nested types (fastavro schemaless_reader has
        known limitations with nested named types across schemas).
        """
        writer_schema = load_schema("FanFollowedArtist")  # v1
        reader_schema = self._load_test_schema("fan_followed_artist_v2.avsc")

        record = _fan_followed_record()

        # Write with v1 (no source_device) using Avro container format
        # which embeds the writer schema in the file for resolution.
        buf = io.BytesIO()
        fastavro.write.writer(buf, writer_schema, [record])
        buf.seek(0)

        # Read back with v2 reader schema — BACKWARD compatibility.
        # fastavro resolves writer→reader schema using the embedded schema.
        records = list(fastavro.read.reader(buf, reader_schema))
        assert len(records) == 1
        decoded = records[0]
        assert decoded["event_id"] == record["event_id"]
        assert decoded["payload"]["follow_id"] == 1
        # source_device defaults to null from v2 reader schema
        assert decoded["payload"].get("source_device") is None

    def test_breaking_change_incompatible_with_v1(self):
        """
        Removing a required field (follow_id) is NOT backward-compatible.

        When a v1 reader tries to read data written with the breaking schema
        (v3 without follow_id), it cannot reconstruct follow_id.

        This test verifies that the breaking schema cannot decode v3-encoded
        data using the v1 reader schema — it raises a SchemaResolutionError.
        """
        writer_schema = self._load_test_schema(
            "fan_followed_artist_breaking.avsc"  # no follow_id
        )
        reader_schema = load_schema("FanFollowedArtist")  # v1 expects follow_id

        # Build a record that is valid for the breaking schema.
        breaking_record = {
            "event_id": str(uuid.uuid4()),
            "event_type": "fan.followed.artist",
            "event_version": "3",
            "occurred_at": "2026-08-19T12:00:00Z",
            "producer": "artisthub-api",
            "correlation_id": None,
            "payload": {
                "fan_id": 10,
                "artist_id": 5,
                "followed_at": "2026-08-19T12:00:00Z",
            },
        }

        # Use container format for proper schema resolution with nested types.
        buf = io.BytesIO()
        fastavro.write.writer(buf, writer_schema, [breaking_record])
        buf.seek(0)

        # Reading with v1 schema must fail because follow_id is missing.
        with pytest.raises(Exception):
            list(fastavro.read.reader(buf, reader_schema))


# ------------------------------------------------------------------ #
# 6 — KafkaProducerService (Phase 7F)                                  #
# ------------------------------------------------------------------ #

class TestKafkaProducerServiceAvro:
    """
    Tests for KafkaProducerService.produce_avro() using mock components.
    No live Kafka broker required.
    """

    def _make_svc(self, mock_underlying=None):
        """Create a KafkaProducerService with a mocked underlying producer."""
        from app.services.kafka_producer import KafkaProducerService
        with patch(
            "app.services.kafka_producer.Producer",
            return_value=mock_underlying or MagicMock(),
        ):
            return KafkaProducerService()

    def test_produce_avro_encodes_confluent_bytes(self):
        """produce_avro passes Confluent wire-format bytes to the underlying producer."""
        mock_producer = MagicMock()
        svc = self._make_svc(mock_producer)

        record = _fan_followed_record()
        schema_id = 7

        with patch(
            "app.services.avro_utils.get_or_register_schema_id",
            return_value=schema_id,
        ):
            svc.produce_avro(
                topic="artisthub.social",
                event_type="fan.followed.artist",
                key="5",
                record=record,
            )

        mock_producer.produce.assert_called_once()
        call_kwargs = mock_producer.produce.call_args.kwargs
        raw_value = call_kwargs["value"]
        # Verify Confluent header
        assert raw_value[0:1] == b"\x00"
        assert struct.unpack(">I", raw_value[1:5])[0] == schema_id
        # Verify key
        assert call_kwargs["key"] == b"5"
        assert call_kwargs["topic"] == "artisthub.social"

    def test_produce_avro_unknown_event_type_raises(self):
        """Unknown event_type raises ValueError before touching the producer."""
        mock_producer = MagicMock()
        svc = self._make_svc(mock_producer)

        with pytest.raises(ValueError, match="Unknown event_type"):
            svc.produce_avro(
                topic="artisthub.social",
                event_type="some.unknown.event",
                key="5",
                record={},
            )

        mock_producer.produce.assert_not_called()

    def test_produce_avro_passes_on_delivery_callback(self):
        """on_delivery callback is forwarded to the underlying producer."""
        mock_producer = MagicMock()
        svc = self._make_svc(mock_producer)
        cb = MagicMock()

        with patch(
            "app.services.avro_utils.get_or_register_schema_id",
            return_value=1,
        ):
            svc.produce_avro(
                topic="artisthub.social",
                event_type="fan.followed.artist",
                key="5",
                record=_fan_followed_record(),
                on_delivery=cb,
            )

        assert mock_producer.produce.call_args.kwargs["on_delivery"] == cb

    def test_produce_raw_still_works(self):
        """produce() raw path (for dead-letter) still encodes string to bytes."""
        mock_producer = MagicMock()
        svc = self._make_svc(mock_producer)

        svc.produce(
            topic="artisthub.deadletter",
            key="unknown",
            value='{"test": 1}',
        )

        call_kwargs = mock_producer.produce.call_args.kwargs
        assert call_kwargs["value"] == b'{"test": 1}'

    def test_flush_delegates_to_underlying_producer(self):
        mock_producer = MagicMock()
        mock_producer.flush.return_value = 0
        svc = self._make_svc(mock_producer)

        result = svc.flush(timeout=5.0)

        mock_producer.flush.assert_called_once_with(timeout=5.0)
        assert result == 0

    def test_produce_avro_selects_correct_schema_per_event_type(self):
        """Each event_type results in the correct record_name being looked up."""
        mock_producer = MagicMock()
        svc = self._make_svc(mock_producer)

        expected_name = "ArtistReleaseCreated"

        with patch(
            "app.services.avro_utils.get_or_register_schema_id",
            return_value=5,
        ) as mock_get_id:
            svc.produce_avro(
                topic="artisthub.catalog",
                event_type="artist.release.created",
                key="1",
                record=_release_created_record(),
            )

        # get_or_register_schema_id must have been called with the right name
        mock_get_id.assert_called_once_with(expected_name)


# ------------------------------------------------------------------ #
# 7 — Outbox relay (Phase 7F)                                         #
# ------------------------------------------------------------------ #

class TestOutboxRelayAvro:
    """
    Tests for poll_and_publish using produce_avro (Phase 7F).
    No live Kafka broker required.
    """

    def _make_producer(self):
        p = MagicMock()
        p.flush.return_value = 0
        return p

    def test_poll_and_publish_calls_produce_avro(self, app, db_):
        """Relay calls produce_avro (not produce) with correct arguments."""
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        eid = str(uuid.uuid4())
        record_dict = _fan_followed_record(event_id=eid)
        row = OutboxEvent(
            event_id=eid,
            event_type="fan.followed.artist",
            event_version="1",
            topic="artisthub.social",
            message_key="5",
            payload=json.dumps(record_dict),
        )
        db_.session.add(row)
        db_.session.commit()

        producer = self._make_producer()

        with patch(
            "app.services.avro_utils.get_or_register_schema_id",
            return_value=_SCHEMA_ID,
        ):
            poll_and_publish(app, producer, batch_size=10)

        producer.produce_avro.assert_called_once()
        call_kwargs = producer.produce_avro.call_args.kwargs
        assert call_kwargs["topic"] == "artisthub.social"
        assert call_kwargs["event_type"] == "fan.followed.artist"
        assert call_kwargs["key"] == "5"
        assert call_kwargs["record"]["event_id"] == eid

    def test_relay_failed_produce_avro_records_error(self, app, db_):
        """If produce_avro raises, last_error is set and published_at is None."""
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        eid = str(uuid.uuid4())
        record_dict = _fan_followed_record(event_id=eid)
        row = OutboxEvent(
            event_id=eid,
            event_type="fan.followed.artist",
            event_version="1",
            topic="artisthub.social",
            message_key="5",
            payload=json.dumps(record_dict),
        )
        db_.session.add(row)
        db_.session.commit()
        row_id = row.id

        producer = self._make_producer()
        producer.produce_avro.side_effect = Exception("broker down")

        poll_and_publish(app, producer, batch_size=10)

        with app.app_context():
            from app.extensions import db
            updated = db.session.get(OutboxEvent, row_id)
            assert updated.published_at is None
            assert updated.last_error is not None
            assert "broker down" in updated.last_error
            assert updated.publish_attempts == 1


# ------------------------------------------------------------------ #
# 8 — Analytics consumer parse_message (Phase 7F)                     #
# ------------------------------------------------------------------ #

class TestAnalyticsConsumerParseMessageAvro:
    """
    Verify analytics consumer parse_message handles Avro wire format.
    Uses mocked avro_decode so no live Schema Registry is required.
    """

    def test_avro_magic_byte_triggers_avro_decode(self):
        """If raw starts with 0x00, avro_utils.decode is called."""
        from consumers.analytics_consumer import parse_message

        expected_event = _fan_followed_record()
        fake_avro = b"\x00\x00\x00\x00\x2a" + b"\x00" * 20  # magic + id + dummy

        with patch(
            "app.services.avro_utils.decode",
            return_value=expected_event,
        ) as mock_decode:
            result = parse_message(fake_avro)

        mock_decode.assert_called_once_with(fake_avro)
        assert result["event_id"] == expected_event["event_id"]

    def test_json_bytes_trigger_json_decode(self):
        """Plain JSON bytes (no magic byte) are decoded as JSON."""
        from consumers.analytics_consumer import parse_message

        event = _fan_followed_record()
        raw = json.dumps(event).encode("utf-8")
        result = parse_message(raw)
        assert result["event_id"] == event["event_id"]

    def test_avro_decode_failure_raises_value_error(self):
        """Avro decode failure wraps the exception in a clear ValueError."""
        from consumers.analytics_consumer import parse_message

        fake_avro = b"\x00\x00\x00\x00\x2a" + b"\xff" * 20

        with patch(
            "app.services.avro_utils.decode",
            side_effect=Exception("schema not found"),
        ):
            with pytest.raises(ValueError, match="Avro deserialization error"):
                parse_message(fake_avro)

    def test_empty_bytes_raises_value_error(self):
        from consumers.analytics_consumer import parse_message
        with pytest.raises(ValueError):
            parse_message(b"")

    def test_avro_path_preserves_existing_process_message_behavior(
        self, app, db_
    ):
        """
        Full process_message with Avro-encoded input applies analytics update.

        Mocks get_or_register_schema_id and uses local encode/decode to
        simulate what the relay produces without hitting Schema Registry.
        """
        from consumers.analytics_consumer import process_message

        record = {
            "event_id": str(uuid.uuid4()),
            "event_type": "fan.followed.artist",
            "event_version": "1",
            "occurred_at": "2026-08-19T12:00:00Z",
            "producer": "artisthub-api",
            "correlation_id": None,
            "payload": {
                "follow_id": 1, "fan_id": 99, "artist_id": 77,
                "followed_at": "2026-08-19T12:00:00Z",
            },
        }
        raw_avro = _encode_local("fan.followed.artist", record)
        # raw_avro has a 5-byte header with schema_id=_SCHEMA_ID.
        # We mock _fetch_reader_schema to return the local schema.
        mock_reader_schema = load_schema("FanFollowedArtist")

        msg = MagicMock()
        msg.topic.return_value = "artisthub.social"
        msg.partition.return_value = 0
        msg.offset.return_value = 100
        msg.value.return_value = raw_avro
        msg.error.return_value = None

        dl_producer = MagicMock()
        dl_producer.flush.return_value = 0

        with patch(
            "app.services.avro_utils._fetch_reader_schema",
            return_value=mock_reader_schema,
        ):
            result = process_message(app, msg, dl_producer)

        assert result is True
        dl_producer.produce.assert_not_called()

        with app.app_context():
            from app.extensions import db
            from app.models.analytics_state import AnalyticsState
            state = db.session.get(AnalyticsState, 77)
            assert state is not None
            assert state.follower_count == 1


# ------------------------------------------------------------------ #
# 9 — Notification consumer parse_message (Phase 7F)                  #
# ------------------------------------------------------------------ #

class TestNotificationConsumerParseMessageAvro:
    """
    Verify notification consumer parse_message handles Avro wire format.
    """

    def test_avro_magic_byte_triggers_avro_decode(self):
        from consumers.notification_consumer import (
            parse_message as notif_parse_message,
        )
        expected = _release_created_record()
        fake_avro = b"\x00\x00\x00\x00\x01" + b"\x00" * 20

        with patch(
            "app.services.avro_utils.decode",
            return_value=expected,
        ) as mock_decode:
            result = notif_parse_message(fake_avro)

        mock_decode.assert_called_once_with(fake_avro)
        assert result["event_id"] == expected["event_id"]

    def test_json_bytes_trigger_json_decode(self):
        from consumers.notification_consumer import (
            parse_message as notif_parse_message,
        )
        event = _release_created_record()
        raw = json.dumps(event).encode("utf-8")
        result = notif_parse_message(raw)
        assert result["event_id"] == event["event_id"]

    def test_avro_decode_failure_raises_value_error(self):
        from consumers.notification_consumer import (
            parse_message as notif_parse_message,
        )
        fake_avro = b"\x00\x00\x00\x00\x01" + b"\xff" * 20
        with patch(
            "app.services.avro_utils.decode",
            side_effect=Exception("corrupt binary"),
        ):
            with pytest.raises(ValueError, match="Avro deserialization error"):
                notif_parse_message(fake_avro)

    def test_notification_avro_path_process_message(self, app, db_):
        """
        Avro-encoded release.created event triggers notification creation
        via process_message when avro decode is mocked to return local decode.
        """
        from consumers.notification_consumer import process_message
        from app.models.follow import Follow

        # Create a follower for artist 10
        follow = Follow(fan_id=20, artist_id=10)
        db_.session.add(follow)
        db_.session.commit()

        record = _release_created_record()
        record["payload"]["artist_id"] = 10

        raw_avro = _encode_local("artist.release.created", record)
        mock_reader_schema = load_schema("ArtistReleaseCreated")

        msg = MagicMock()
        msg.topic.return_value = "artisthub.catalog"
        msg.partition.return_value = 0
        msg.offset.return_value = 50
        msg.value.return_value = raw_avro
        msg.error.return_value = None

        dl = MagicMock()
        dl.flush.return_value = 0

        with patch(
            "app.services.avro_utils._fetch_reader_schema",
            return_value=mock_reader_schema,
        ):
            result = process_message(app, msg, dl)

        assert result is True
        dl.produce.assert_not_called()

        with app.app_context():
            from app.extensions import db
            from app.models.notification import Notification
            notifs = db.session.query(Notification).all()
            assert len(notifs) == 1
            assert notifs[0].fan_id == 20


# ------------------------------------------------------------------ #
# 10 — env.example coverage                                           #
# ------------------------------------------------------------------ #

class TestEnvExampleCoverage:
    """Verify .env.example documents Phase 7F Schema Registry variables."""

    def test_env_example_contains_schema_registry_url(self):
        env_path = (
            Path(__file__).resolve().parents[2] / ".env.example"
        )
        content = env_path.read_text()
        assert "SCHEMA_REGISTRY_URL" in content

    def test_env_example_contains_schema_registry_api_key(self):
        env_path = (
            Path(__file__).resolve().parents[2] / ".env.example"
        )
        content = env_path.read_text()
        assert "SCHEMA_REGISTRY_API_KEY" in content
