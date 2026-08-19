"""
tests/test_analytics_consumer.py

Test suite for Phase 7D — Real-Time Analytics Consumer.

Coverage targets
----------------
Model
  - AnalyticsState: creation, to_dict(), floor-at-zero behaviour
  - ProcessedEvent: creation, to_dict(), primary-key uniqueness

apply_analytics_update (unit — no Kafka required)
  - fan.followed.artist increments follower_count
  - fan.unfollowed.artist decrements follower_count
  - follower_count never goes below zero
  - artist.release.created/deleted changes release_count
  - artist.post.created/deleted changes post_count
  - artist.merch.created/deleted changes merch_count
  - duplicate event_id returns False and does not alter counters
  - row created on first event for a new artist
  - events for different artists remain isolated

parse_message
  - valid JSON parsed correctly
  - non-JSON raises ValueError
  - missing required envelope field raises ValueError
  - payload not a dict raises ValueError

process_message (mock Kafka message)
  - unsupported event_type is skipped, returns True (commit offset)
  - malformed JSON is dead-lettered, returns True
  - missing envelope field is dead-lettered, returns True
  - valid event applies update and returns True
  - duplicate event is skipped (dedup), returns True, no counter change
  - DB exception triggers retry; exhausted retries send to dead-letter

publish_dead_letter
  - assembles correct dead-letter record
  - producer flush failure is logged but does not raise

Consumer restart / offset behaviour
  - ProcessedEvent rows survive across sessions (restart dedup)
  - New artist_id creates new AnalyticsState row

Preserve all 293 existing tests.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.extensions import db as _db
from app.models.analytics_state import AnalyticsState
from app.models.processed_event import ProcessedEvent
from consumers.analytics_consumer import (
    apply_analytics_update,
    parse_message,
    process_message,
    publish_dead_letter,
)


# ====================================================================
# Helpers
# ====================================================================

def _make_event(
    event_type: str,
    artist_id: int = 1,
    event_id: str = None,
) -> dict:
    """Build a minimal valid event dict."""
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "event_version": "1",
        "occurred_at": "2026-08-19T12:00:00Z",
        "producer": "artisthub-api",
        "correlation_id": None,
        "payload": {"artist_id": artist_id},
    }


def _make_msg(event: dict, topic: str = "artisthub.social",
              partition: int = 0, offset: int = 0):
    """Build a mock Kafka Message object from an event dict."""
    msg = MagicMock()
    msg.topic.return_value = topic
    msg.partition.return_value = partition
    msg.offset.return_value = offset
    msg.value.return_value = json.dumps(event).encode("utf-8")
    msg.error.return_value = None
    return msg


def _get_state(db_, artist_id: int):
    """Fetch AnalyticsState row or None."""
    return db_.session.get(AnalyticsState, artist_id)


# ====================================================================
# 1 — AnalyticsState model
# ====================================================================

class TestAnalyticsStateModel:
    """Unit tests for AnalyticsState."""

    def test_create_and_persist(self, db_):
        row = AnalyticsState(
            artist_id=1,
            follower_count=0,
            release_count=0,
            post_count=0,
            merch_count=0,
        )
        db_.session.add(row)
        db_.session.commit()
        fetched = db_.session.get(AnalyticsState, 1)
        assert fetched is not None
        assert fetched.follower_count == 0

    def test_to_dict_keys(self, db_):
        row = AnalyticsState(
            artist_id=2,
            follower_count=3,
            release_count=1,
            post_count=5,
            merch_count=2,
        )
        db_.session.add(row)
        db_.session.commit()
        d = row.to_dict()
        for k in (
            "artist_id", "follower_count", "release_count",
            "post_count", "merch_count", "updated_at",
        ):
            assert k in d


# ====================================================================
# 2 — ProcessedEvent model
# ====================================================================

class TestProcessedEventModel:
    """Unit tests for ProcessedEvent."""

    def test_create_and_persist(self, db_):
        row = ProcessedEvent(
            event_id=str(uuid.uuid4()),
            event_type="fan.followed.artist",
            topic="artisthub.social",
            partition=0,
            offset=1,
            artist_id=1,
        )
        db_.session.add(row)
        db_.session.commit()
        assert db_.session.query(ProcessedEvent).count() == 1

    def test_to_dict_keys(self, db_):
        eid = str(uuid.uuid4())
        row = ProcessedEvent(
            event_id=eid,
            event_type="fan.followed.artist",
            topic="artisthub.social",
            partition=0,
            offset=0,
            artist_id=1,
        )
        db_.session.add(row)
        db_.session.commit()
        d = row.to_dict()
        for k in (
            "event_id", "event_type", "topic",
            "partition", "offset", "artist_id", "processed_at",
        ):
            assert k in d

    def test_primary_key_uniqueness(self, db_):
        """Inserting duplicate event_id raises an exception."""
        eid = str(uuid.uuid4())
        for _ in range(2):
            db_.session.add(ProcessedEvent(
                event_id=eid,
                event_type="fan.followed.artist",
                topic="artisthub.social",
                partition=0,
                offset=0,
            ))
        with pytest.raises(Exception):
            db_.session.commit()
        db_.session.rollback()


# ====================================================================
# 3 — parse_message
# ====================================================================

class TestParseMessage:
    """Unit tests for parse_message()."""

    def test_valid_json_returned_as_dict(self):
        event = _make_event("fan.followed.artist")
        raw = json.dumps(event).encode()
        result = parse_message(raw)
        assert result["event_type"] == "fan.followed.artist"

    def test_non_json_raises_value_error(self):
        with pytest.raises(ValueError, match="JSON decode error"):
            parse_message(b"not json {")

    def test_missing_event_id_raises(self):
        raw = json.dumps(
            {"event_type": "x", "payload": {}}
        ).encode()
        with pytest.raises(ValueError, match="event_id"):
            parse_message(raw)

    def test_missing_event_type_raises(self):
        raw = json.dumps(
            {"event_id": "x", "payload": {}}
        ).encode()
        with pytest.raises(ValueError, match="event_type"):
            parse_message(raw)

    def test_missing_payload_raises(self):
        raw = json.dumps(
            {"event_id": "x", "event_type": "x"}
        ).encode()
        with pytest.raises(ValueError, match="payload"):
            parse_message(raw)

    def test_payload_not_dict_raises(self):
        raw = json.dumps(
            {"event_id": "x", "event_type": "x", "payload": "string"}
        ).encode()
        with pytest.raises(ValueError, match="JSON object"):
            parse_message(raw)


# ====================================================================
# 4 — apply_analytics_update (core business logic)
# ====================================================================

class TestApplyAnalyticsUpdate:
    """Unit tests for the analytics update function."""

    def _apply(self, db_, event_type, artist_id=1, event_id=None,
               topic="artisthub.social", partition=0, offset=0):
        eid = event_id or str(uuid.uuid4())
        result = apply_analytics_update(
            db_.session,
            event_type=event_type,
            payload={"artist_id": artist_id},
            event_id=eid,
            topic=topic,
            partition=partition,
            offset=offset,
        )
        db_.session.commit()
        return result, eid

    # ---- follower_count ----------------------------------------

    def test_follow_increments_follower_count(self, db_):
        applied, _ = self._apply(db_, "fan.followed.artist", artist_id=1)
        assert applied is True
        assert _get_state(db_, 1).follower_count == 1

    def test_follow_twice_increments_twice(self, db_):
        self._apply(db_, "fan.followed.artist", artist_id=1)
        self._apply(db_, "fan.followed.artist", artist_id=1)
        assert _get_state(db_, 1).follower_count == 2

    def test_unfollow_decrements_follower_count(self, db_):
        self._apply(db_, "fan.followed.artist", artist_id=1)
        self._apply(db_, "fan.followed.artist", artist_id=1)
        self._apply(db_, "fan.unfollowed.artist", artist_id=1)
        assert _get_state(db_, 1).follower_count == 1

    def test_unfollow_floor_at_zero(self, db_):
        """Unfollow on an artist with 0 followers must not go negative."""
        self._apply(db_, "fan.unfollowed.artist", artist_id=1)
        assert _get_state(db_, 1).follower_count == 0

    def test_unfollow_from_zero_floor_stays_zero_multiple_times(self, db_):
        self._apply(db_, "fan.unfollowed.artist", artist_id=1)
        self._apply(db_, "fan.unfollowed.artist", artist_id=1)
        self._apply(db_, "fan.unfollowed.artist", artist_id=1)
        assert _get_state(db_, 1).follower_count == 0

    # ---- release_count -----------------------------------------

    def test_release_created_increments_release_count(self, db_):
        self._apply(
            db_, "artist.release.created", artist_id=1,
            topic="artisthub.catalog"
        )
        assert _get_state(db_, 1).release_count == 1

    def test_release_deleted_decrements_release_count(self, db_):
        self._apply(
            db_, "artist.release.created", artist_id=1,
            topic="artisthub.catalog"
        )
        self._apply(
            db_, "artist.release.deleted", artist_id=1,
            topic="artisthub.catalog"
        )
        assert _get_state(db_, 1).release_count == 0

    def test_release_deleted_floor_at_zero(self, db_):
        self._apply(
            db_, "artist.release.deleted", artist_id=1,
            topic="artisthub.catalog"
        )
        assert _get_state(db_, 1).release_count == 0

    # ---- post_count --------------------------------------------

    def test_post_created_increments_post_count(self, db_):
        self._apply(db_, "artist.post.created", artist_id=1)
        assert _get_state(db_, 1).post_count == 1

    def test_post_deleted_decrements_post_count(self, db_):
        self._apply(db_, "artist.post.created", artist_id=1)
        self._apply(db_, "artist.post.deleted", artist_id=1)
        assert _get_state(db_, 1).post_count == 0

    def test_post_deleted_floor_at_zero(self, db_):
        self._apply(db_, "artist.post.deleted", artist_id=1)
        assert _get_state(db_, 1).post_count == 0

    # ---- merch_count -------------------------------------------

    def test_merch_created_increments_merch_count(self, db_):
        self._apply(
            db_, "artist.merch.created", artist_id=1,
            topic="artisthub.catalog"
        )
        assert _get_state(db_, 1).merch_count == 1

    def test_merch_deleted_decrements_merch_count(self, db_):
        self._apply(
            db_, "artist.merch.created", artist_id=1,
            topic="artisthub.catalog"
        )
        self._apply(
            db_, "artist.merch.deleted", artist_id=1,
            topic="artisthub.catalog"
        )
        assert _get_state(db_, 1).merch_count == 0

    def test_merch_deleted_floor_at_zero(self, db_):
        self._apply(
            db_, "artist.merch.deleted", artist_id=1,
            topic="artisthub.catalog"
        )
        assert _get_state(db_, 1).merch_count == 0

    # ---- deduplication -----------------------------------------

    def test_duplicate_event_id_returns_false(self, db_):
        eid = str(uuid.uuid4())
        result1, _ = self._apply(
            db_, "fan.followed.artist", artist_id=1, event_id=eid
        )
        result2, _ = self._apply(
            db_, "fan.followed.artist", artist_id=1, event_id=eid
        )
        assert result1 is True
        assert result2 is False  # duplicate — skipped

    def test_duplicate_does_not_double_count(self, db_):
        eid = str(uuid.uuid4())
        self._apply(db_, "fan.followed.artist", artist_id=1, event_id=eid)
        self._apply(db_, "fan.followed.artist", artist_id=1, event_id=eid)
        # Should be 1, not 2.
        assert _get_state(db_, 1).follower_count == 1

    def test_different_event_ids_both_applied(self, db_):
        self._apply(db_, "fan.followed.artist", artist_id=1)
        self._apply(db_, "fan.followed.artist", artist_id=1)
        assert _get_state(db_, 1).follower_count == 2

    # ---- isolation between artists -----------------------------

    def test_events_for_different_artists_are_isolated(self, db_):
        self._apply(db_, "fan.followed.artist", artist_id=1)
        self._apply(db_, "fan.followed.artist", artist_id=1)
        self._apply(db_, "fan.followed.artist", artist_id=2)

        assert _get_state(db_, 1).follower_count == 2
        assert _get_state(db_, 2).follower_count == 1

    def test_release_events_isolated_between_artists(self, db_):
        self._apply(
            db_, "artist.release.created", artist_id=1,
            topic="artisthub.catalog"
        )
        self._apply(
            db_, "artist.release.created", artist_id=2,
            topic="artisthub.catalog"
        )
        self._apply(
            db_, "artist.release.deleted", artist_id=1,
            topic="artisthub.catalog"
        )
        assert _get_state(db_, 1).release_count == 0
        assert _get_state(db_, 2).release_count == 1

    # ---- row creation -----------------------------------------

    def test_first_event_creates_analytics_row(self, db_):
        assert _get_state(db_, 99) is None
        self._apply(db_, "fan.followed.artist", artist_id=99)
        assert _get_state(db_, 99) is not None

    def test_processed_event_row_is_created(self, db_):
        eid = str(uuid.uuid4())
        self._apply(db_, "fan.followed.artist", artist_id=1, event_id=eid)
        row = db_.session.get(ProcessedEvent, eid)
        assert row is not None
        assert row.event_type == "fan.followed.artist"


# ====================================================================
# 5 — process_message (mock Kafka message)
# ====================================================================

class TestProcessMessage:
    """
    Tests for process_message() using mock Kafka messages.
    No live broker required.
    """

    def _mock_dl_producer(self):
        p = MagicMock()
        p.flush.return_value = 0
        return p

    def test_unsupported_event_type_is_skipped(self, app, db_):
        """Unknown event_type: returns True (commit offset), no DB change."""
        event = _make_event("some.unknown.type", artist_id=1)
        msg = _make_msg(event)
        dl_producer = self._mock_dl_producer()

        result = process_message(app, msg, dl_producer)

        assert result is True
        dl_producer.produce.assert_not_called()
        assert db_.session.query(AnalyticsState).count() == 0

    def test_malformed_json_is_dead_lettered(self, app, db_):
        """Non-JSON message → dead-letter, returns True."""
        msg = MagicMock()
        msg.topic.return_value = "artisthub.social"
        msg.partition.return_value = 0
        msg.offset.return_value = 5
        msg.value.return_value = b"not valid json {"
        msg.error.return_value = None
        dl_producer = self._mock_dl_producer()

        result = process_message(app, msg, dl_producer)

        assert result is True
        dl_producer.produce.assert_called_once()
        # Dead-letter payload should contain the failure reason.
        dl_payload = json.loads(
            dl_producer.produce.call_args.kwargs["value"].decode()
        )
        assert "failure_reason" in dl_payload
        assert "JSON" in dl_payload["failure_reason"]

    def test_missing_envelope_field_is_dead_lettered(self, app, db_):
        """Event missing required envelope field → dead-letter."""
        raw = json.dumps(
            {"event_type": "fan.followed.artist", "payload": {}}
            # no event_id
        ).encode()
        msg = MagicMock()
        msg.topic.return_value = "artisthub.social"
        msg.partition.return_value = 0
        msg.offset.return_value = 6
        msg.value.return_value = raw
        msg.error.return_value = None
        dl_producer = self._mock_dl_producer()

        result = process_message(app, msg, dl_producer)

        assert result is True
        dl_producer.produce.assert_called_once()

    def test_valid_follow_event_applies_update(self, app, db_):
        """Valid fan.followed.artist increments follower_count."""
        event = _make_event("fan.followed.artist", artist_id=10)
        msg = _make_msg(event)
        dl_producer = self._mock_dl_producer()

        result = process_message(app, msg, dl_producer)

        assert result is True
        dl_producer.produce.assert_not_called()
        with app.app_context():
            state = _db.session.get(AnalyticsState, 10)
            assert state is not None
            assert state.follower_count == 1

    def test_duplicate_event_is_skipped(self, app, db_):
        """Re-delivering the same event_id does not double-count."""
        eid = str(uuid.uuid4())
        event = _make_event("fan.followed.artist", artist_id=20, event_id=eid)
        msg = _make_msg(event)
        dl_producer = self._mock_dl_producer()

        # First delivery
        process_message(app, msg, dl_producer)
        # Second delivery (same message)
        process_message(app, msg, dl_producer)

        with app.app_context():
            state = _db.session.get(AnalyticsState, 20)
            assert state.follower_count == 1  # not 2

    def test_db_exception_exhausts_retries_and_dead_letters(
        self, app, db_
    ):
        """
        When the DB raises on every attempt, retries are exhausted and
        the event is dead-lettered. process_message returns True so the
        Kafka offset is committed and the message is not infinitely
        retried.
        """
        event = _make_event("fan.followed.artist", artist_id=30)
        msg = _make_msg(event)
        dl_producer = self._mock_dl_producer()

        with patch(
            "consumers.analytics_consumer.apply_analytics_update",
            side_effect=Exception("simulated DB failure"),
        ):
            with patch(
                "consumers.analytics_consumer.MAX_RETRIES", 1
            ):
                with patch(
                    "consumers.analytics_consumer.RETRY_BACKOFF", 0.0
                ):
                    result = process_message(app, msg, dl_producer)

        assert result is True  # offset committed
        dl_producer.produce.assert_called_once()
        dl_payload = json.loads(
            dl_producer.produce.call_args.kwargs["value"].decode()
        )
        assert "DB retries exhausted" in dl_payload["failure_reason"]

    def test_successful_processing_returns_true_for_offset_commit(
        self, app, db_
    ):
        """
        True return value signals the caller to commit the Kafka offset
        after successful processing.
        """
        event = _make_event("artist.post.created", artist_id=40)
        msg = _make_msg(event, topic="artisthub.social")
        dl_producer = self._mock_dl_producer()

        result = process_message(app, msg, dl_producer)

        assert result is True

    def test_release_created_via_process_message(self, app, db_):
        """End-to-end via process_message for catalog event."""
        event = _make_event(
            "artist.release.created", artist_id=50
        )
        msg = _make_msg(event, topic="artisthub.catalog")
        dl_producer = self._mock_dl_producer()

        process_message(app, msg, dl_producer)

        with app.app_context():
            state = _db.session.get(AnalyticsState, 50)
            assert state.release_count == 1

    def test_merch_created_via_process_message(self, app, db_):
        event = _make_event("artist.merch.created", artist_id=60)
        msg = _make_msg(event, topic="artisthub.catalog")
        dl_producer = self._mock_dl_producer()
        process_message(app, msg, dl_producer)
        with app.app_context():
            state = _db.session.get(AnalyticsState, 60)
            assert state.merch_count == 1


# ====================================================================
# 6 — publish_dead_letter
# ====================================================================

class TestPublishDeadLetter:
    """Unit tests for dead-letter publisher."""

    def test_produces_correct_fields(self):
        producer = MagicMock()
        producer.flush.return_value = 0

        publish_dead_letter(
            producer,
            original_topic="artisthub.social",
            original_partition=0,
            original_offset=99,
            reason="test error",
            original_payload='{"raw": "data"}',
            event_id="test-uuid",
        )

        producer.produce.assert_called_once()
        call_kwargs = producer.produce.call_args.kwargs
        assert call_kwargs["topic"] == "artisthub.deadletter"
        payload = json.loads(call_kwargs["value"].decode())
        assert payload["original_topic"] == "artisthub.social"
        assert payload["original_partition"] == 0
        assert payload["original_offset"] == 99
        assert payload["failure_reason"] == "test error"
        assert payload["event_id"] == "test-uuid"
        assert payload["original_payload"] == '{"raw": "data"}'

    def test_producer_failure_does_not_raise(self):
        """A dead-letter produce failure is logged, not re-raised."""
        producer = MagicMock()
        producer.produce.side_effect = Exception("broker down")

        # Must not raise.
        publish_dead_letter(
            producer,
            original_topic="artisthub.social",
            original_partition=0,
            original_offset=1,
            reason="original error",
            original_payload="{}",
        )


# ====================================================================
# 7 — Consumer restart preserves deduplication state
# ====================================================================

class TestConsumerRestart:
    """
    Verify that ProcessedEvent rows survive between function calls,
    simulating a consumer restart.
    """

    def test_processed_event_survives_restart(self, app, db_):
        """
        After processing event once, a second call with same event_id
        is treated as duplicate even in a new apply_analytics_update call.
        """
        eid = str(uuid.uuid4())
        # First invocation — simulates first consumer run.
        with app.app_context():
            apply_analytics_update(
                _db.session,
                event_type="fan.followed.artist",
                payload={"artist_id": 70},
                event_id=eid,
                topic="artisthub.social",
                partition=0,
                offset=10,
            )
            _db.session.commit()

        # Second invocation — simulates consumer restart and re-delivery.
        with app.app_context():
            result = apply_analytics_update(
                _db.session,
                event_type="fan.followed.artist",
                payload={"artist_id": 70},
                event_id=eid,
                topic="artisthub.social",
                partition=0,
                offset=10,
            )
            _db.session.commit()

        assert result is False  # duplicate recognised

        with app.app_context():
            state = _db.session.get(AnalyticsState, 70)
            assert state.follower_count == 1  # not 2
