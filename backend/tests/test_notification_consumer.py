"""
tests/test_notification_consumer.py

Test suite for Phase 7E — Notification Consumer.

Coverage targets
----------------
Model
  - Notification: creation, to_dict(), UNIQUE(event_id, fan_id) constraint

Helpers
  - parse_message: valid JSON, non-JSON, missing envelope field, payload
    not a dict
  - publish_dead_letter: assembles correct record; flush failure does not
    raise
  - get_follower_ids: returns fan_ids for artist; empty list when none
  - build_notifications: correct count; correct fields per row

process_release_created (unit — no Kafka required)
  - with followers → Notification rows created + ProcessedEvent inserted
  - no followers → no Notification rows, ProcessedEvent still inserted
  - duplicate event_id → returns True, no second write
  - missing artist_id → returns False (caller dead-letters)
  - missing release_id → returns False (caller dead-letters)
  - events for different artists are isolated

process_message (mock Kafka message)
  - malformed JSON → dead-letter, returns True
  - missing envelope field → dead-letter, returns True
  - unsupported event_type → skip, returns True (no dead-letter)
  - artist.release.created with followers → Notification rows, returns True
  - artist.release.created with no followers → no-op, returns True
  - duplicate event_id → dedup, returns True, no double write
  - missing required payload field (artist_id) → dead-letter, returns True
  - DB exception → retry; exhausted retries → dead-letter, returns True
  - DB failure → offset NOT committed (return False not possible here —
    retries exhaust to dead-letter then True; test verifies retry count)
  - successful processing → returns True (caller commits offset)

Consumer restart / offset behaviour
  - ProcessedEvent rows survive re-invocation (restart idempotency)
  - follower query correctness: only fans following the specific artist
    get notifications; fans following other artists are not included
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.models.follow import Follow
from app.models.notification import Notification
from app.models.processed_event import ProcessedEvent
from consumers.notification_consumer import (
    build_notifications,
    get_follower_ids,
    parse_message,
    process_message,
    process_release_created,
    publish_dead_letter,
)


# ====================================================================
# Helpers
# ====================================================================

def _make_release_event(
    artist_id: int = 1,
    release_id: int = 100,
    title: str = "Test Album",
    event_id: str = None,
) -> dict:
    """Build a minimal valid artist.release.created event dict."""
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "artist.release.created",
        "event_version": "1",
        "occurred_at": "2026-08-19T12:00:00Z",
        "producer": "artisthub-api",
        "correlation_id": None,
        "payload": {
            "artist_id": artist_id,
            "release_id": release_id,
            "title": title,
        },
    }


def _make_msg(event: dict, topic: str = "artisthub.catalog",
              partition: int = 0, offset: int = 0):
    """Build a mock Kafka Message from an event dict."""
    msg = MagicMock()
    msg.topic.return_value = topic
    msg.partition.return_value = partition
    msg.offset.return_value = offset
    msg.value.return_value = json.dumps(event).encode("utf-8")
    msg.error.return_value = None
    return msg


def _mock_dl_producer():
    p = MagicMock()
    p.flush.return_value = 0
    return p


def _add_follow(db_, fan_id: int, artist_id: int) -> Follow:
    """Persist a Follow row and return it."""
    f = Follow(fan_id=fan_id, artist_id=artist_id)
    db_.session.add(f)
    db_.session.commit()
    return f


def _count_notifications(db_) -> int:
    return db_.session.query(Notification).count()


def _count_processed_events(db_) -> int:
    return db_.session.query(ProcessedEvent).count()


# ====================================================================
# 1 — Notification model
# ====================================================================

class TestNotificationModel:
    """Unit tests for the Notification SQLAlchemy model."""

    def test_create_and_persist(self, db_):
        notif = Notification(
            event_id=str(uuid.uuid4()),
            fan_id=1,
            artist_id=2,
            release_id=10,
            notification_type="new_release",
            subject="New release!",
            message="Check it out.",
            status="pending",
        )
        db_.session.add(notif)
        db_.session.commit()
        assert db_.session.query(Notification).count() == 1

    def test_to_dict_keys(self, db_):
        notif = Notification(
            event_id=str(uuid.uuid4()),
            fan_id=1,
            artist_id=2,
            release_id=10,
            notification_type="new_release",
            subject="New release!",
            message="Check it out.",
            status="pending",
        )
        db_.session.add(notif)
        db_.session.commit()
        d = notif.to_dict()
        for key in (
            "id", "event_id", "fan_id", "artist_id", "release_id",
            "notification_type", "subject", "message", "status",
            "created_at", "sent_at",
        ):
            assert key in d

    def test_sent_at_is_none_by_default(self, db_):
        notif = Notification(
            event_id=str(uuid.uuid4()),
            fan_id=1,
            artist_id=2,
            release_id=10,
            notification_type="new_release",
            subject="s",
            message="m",
            status="pending",
        )
        db_.session.add(notif)
        db_.session.commit()
        assert notif.sent_at is None
        assert notif.to_dict()["sent_at"] is None

    def test_unique_event_fan_constraint(self, db_):
        """Inserting the same (event_id, fan_id) twice must raise."""
        eid = str(uuid.uuid4())
        for _ in range(2):
            db_.session.add(Notification(
                event_id=eid,
                fan_id=1,
                artist_id=2,
                release_id=10,
                notification_type="new_release",
                subject="s",
                message="m",
                status="pending",
            ))
        with pytest.raises(Exception):
            db_.session.commit()
        db_.session.rollback()

    def test_different_fan_same_event_allowed(self, db_):
        """Same event_id but different fan_ids is allowed."""
        eid = str(uuid.uuid4())
        for fan_id in (1, 2):
            db_.session.add(Notification(
                event_id=eid,
                fan_id=fan_id,
                artist_id=2,
                release_id=10,
                notification_type="new_release",
                subject="s",
                message="m",
                status="pending",
            ))
        db_.session.commit()
        assert db_.session.query(Notification).count() == 2


# ====================================================================
# 2 — parse_message
# ====================================================================

class TestParseMessage:
    """Unit tests for parse_message()."""

    def test_valid_json_returned_as_dict(self):
        event = _make_release_event()
        raw = json.dumps(event).encode()
        result = parse_message(raw)
        assert result["event_type"] == "artist.release.created"

    def test_non_json_raises_value_error(self):
        with pytest.raises(ValueError, match="JSON decode error"):
            parse_message(b"not valid json {")

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
# 3 — publish_dead_letter
# ====================================================================

class TestPublishDeadLetter:
    """Unit tests for the dead-letter publisher."""

    def test_produces_correct_fields(self):
        producer = MagicMock()
        producer.flush.return_value = 0

        publish_dead_letter(
            producer,
            original_topic="artisthub.catalog",
            original_partition=2,
            original_offset=55,
            reason="missing artist_id",
            original_payload='{"raw": "data"}',
            event_id="test-uuid-1",
        )

        producer.produce.assert_called_once()
        call_kwargs = producer.produce.call_args.kwargs
        assert call_kwargs["topic"] == "artisthub.deadletter"
        payload = json.loads(call_kwargs["value"].decode())
        assert payload["original_topic"] == "artisthub.catalog"
        assert payload["original_partition"] == 2
        assert payload["original_offset"] == 55
        assert payload["failure_reason"] == "missing artist_id"
        assert payload["event_id"] == "test-uuid-1"
        assert payload["original_payload"] == '{"raw": "data"}'
        assert "dead_letter_at" in payload

    def test_producer_failure_does_not_raise(self):
        """A flush/produce failure must be swallowed, not re-raised."""
        producer = MagicMock()
        producer.produce.side_effect = Exception("broker unavailable")

        # Must not raise.
        publish_dead_letter(
            producer,
            original_topic="artisthub.catalog",
            original_partition=0,
            original_offset=1,
            reason="test",
            original_payload="{}",
        )

    def test_no_event_id_uses_unknown_key(self):
        producer = MagicMock()
        producer.flush.return_value = 0

        publish_dead_letter(
            producer,
            original_topic="t",
            original_partition=0,
            original_offset=0,
            reason="r",
            original_payload="{}",
            event_id=None,
        )

        key = producer.produce.call_args.kwargs["key"]
        assert key == b"unknown"


# ====================================================================
# 4 — get_follower_ids
# ====================================================================

class TestGetFollowerIds:
    """Unit tests for the follower query helper."""

    def test_returns_fan_ids_for_artist(self, db_):
        _add_follow(db_, fan_id=10, artist_id=1)
        _add_follow(db_, fan_id=11, artist_id=1)

        result = get_follower_ids(db_.session, artist_id=1)
        assert sorted(result) == [10, 11]

    def test_returns_empty_when_no_followers(self, db_):
        result = get_follower_ids(db_.session, artist_id=99)
        assert result == []

    def test_only_returns_fans_for_specified_artist(self, db_):
        """Fans following artist 2 must NOT appear for artist 1."""
        _add_follow(db_, fan_id=20, artist_id=1)
        _add_follow(db_, fan_id=21, artist_id=2)

        result = get_follower_ids(db_.session, artist_id=1)
        assert result == [20]

    def test_fan_following_multiple_artists_counted_separately(self, db_):
        """A fan following both artists appears in each artist's list."""
        _add_follow(db_, fan_id=30, artist_id=1)
        _add_follow(db_, fan_id=30, artist_id=2)
        _add_follow(db_, fan_id=31, artist_id=2)

        assert get_follower_ids(db_.session, artist_id=1) == [30]
        assert sorted(get_follower_ids(db_.session, artist_id=2)) == [30, 31]


# ====================================================================
# 5 — build_notifications
# ====================================================================

class TestBuildNotifications:
    """Unit tests for the notification builder."""

    def test_one_row_per_fan(self):
        eid = str(uuid.uuid4())
        rows = build_notifications(
            event_id=eid,
            artist_id=1,
            release_id=50,
            release_title="Great Album",
            fan_ids=[10, 11, 12],
        )
        assert len(rows) == 3
        fan_ids_in_rows = [r.fan_id for r in rows]
        assert sorted(fan_ids_in_rows) == [10, 11, 12]

    def test_empty_fan_ids_returns_empty_list(self):
        rows = build_notifications(
            event_id=str(uuid.uuid4()),
            artist_id=1,
            release_id=50,
            release_title="X",
            fan_ids=[],
        )
        assert rows == []

    def test_notification_fields_populated(self):
        eid = str(uuid.uuid4())
        rows = build_notifications(
            event_id=eid,
            artist_id=7,
            release_id=42,
            release_title="My Release",
            fan_ids=[99],
        )
        n = rows[0]
        assert n.event_id == eid
        assert n.artist_id == 7
        assert n.release_id == 42
        assert n.notification_type == "new_release"
        assert n.status == "pending"
        assert "My Release" in n.subject
        assert "My Release" in n.message


# ====================================================================
# 6 — process_release_created (unit — no Kafka)
# ====================================================================

class TestProcessReleaseCreated:
    """Tests for process_release_created() using real in-memory DB."""

    def test_creates_notifications_for_followers(self, db_):
        _add_follow(db_, fan_id=1, artist_id=10)
        _add_follow(db_, fan_id=2, artist_id=10)

        event = _make_release_event(artist_id=10, release_id=100)
        ok = process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=0, offset=0,
        )
        db_.session.commit()

        assert ok is True
        assert _count_notifications(db_) == 2
        assert _count_processed_events(db_) == 1

    def test_no_followers_no_notifications_but_processed_event_written(
        self, db_
    ):
        event = _make_release_event(artist_id=99, release_id=200)
        ok = process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=0, offset=1,
        )
        db_.session.commit()

        assert ok is True
        assert _count_notifications(db_) == 0
        assert _count_processed_events(db_) == 1

    def test_duplicate_event_id_returns_true_and_skips(self, db_):
        _add_follow(db_, fan_id=1, artist_id=5)
        eid = str(uuid.uuid4())
        event = _make_release_event(artist_id=5, event_id=eid)

        process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=0, offset=0,
        )
        db_.session.commit()

        # Second call with same event_id.
        ok = process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=0, offset=0,
        )
        db_.session.commit()

        assert ok is True
        assert _count_notifications(db_) == 1  # not 2

    def test_missing_artist_id_returns_false(self, db_):
        event = _make_release_event()
        del event["payload"]["artist_id"]
        ok = process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=0, offset=0,
        )
        assert ok is False
        assert _count_notifications(db_) == 0

    def test_missing_release_id_returns_false(self, db_):
        event = _make_release_event()
        del event["payload"]["release_id"]
        ok = process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=0, offset=0,
        )
        assert ok is False
        assert _count_notifications(db_) == 0

    def test_artist_isolation(self, db_):
        """Events for artist A must not create notifications for artist B fans."""
        _add_follow(db_, fan_id=10, artist_id=1)
        _add_follow(db_, fan_id=11, artist_id=2)

        event_a = _make_release_event(artist_id=1, release_id=1)
        process_release_created(
            db_.session, event_a,
            topic="artisthub.catalog", partition=0, offset=0,
        )
        db_.session.commit()

        # Fan 11 (follows artist 2 only) must have no notification.
        notifs = db_.session.query(Notification).all()
        assert len(notifs) == 1
        assert notifs[0].fan_id == 10

    def test_processed_event_row_fields(self, db_):
        event = _make_release_event(artist_id=3, release_id=7)
        eid = event["event_id"]
        process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=2, offset=99,
        )
        db_.session.commit()

        pe = db_.session.get(ProcessedEvent, eid)
        assert pe is not None
        assert pe.event_type == "artist.release.created"
        assert pe.topic == "artisthub.catalog"
        assert pe.partition == 2
        assert pe.offset == 99


# ====================================================================
# 7 — process_message (mock Kafka message)
# ====================================================================

class TestProcessMessage:
    """
    Tests for process_message() using mock Kafka messages.
    No live broker required.
    """

    def test_malformed_json_dead_lettered_returns_true(self, app, db_):
        msg = MagicMock()
        msg.topic.return_value = "artisthub.catalog"
        msg.partition.return_value = 0
        msg.offset.return_value = 5
        msg.value.return_value = b"not json {"
        msg.error.return_value = None
        dl = _mock_dl_producer()

        result = process_message(app, msg, dl)

        assert result is True
        dl.produce.assert_called_once()
        payload = json.loads(dl.produce.call_args.kwargs["value"].decode())
        assert "JSON" in payload["failure_reason"]

    def test_missing_envelope_field_dead_lettered(self, app, db_):
        raw = json.dumps(
            {"event_type": "artist.release.created", "payload": {}}
            # no event_id
        ).encode()
        msg = MagicMock()
        msg.topic.return_value = "artisthub.catalog"
        msg.partition.return_value = 0
        msg.offset.return_value = 6
        msg.value.return_value = raw
        msg.error.return_value = None
        dl = _mock_dl_producer()

        result = process_message(app, msg, dl)

        assert result is True
        dl.produce.assert_called_once()

    def test_unsupported_event_type_skipped(self, app, db_):
        """Unknown event_type: returns True, no dead-letter, no DB write."""
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "some.other.event",
            "payload": {"artist_id": 1},
        }
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        result = process_message(app, msg, dl)

        assert result is True
        dl.produce.assert_not_called()
        with app.app_context():
            assert _db.session.query(Notification).count() == 0

    def test_release_created_with_followers_creates_notifications(
        self, app, db_
    ):
        _add_follow(db_, fan_id=1, artist_id=10)
        _add_follow(db_, fan_id=2, artist_id=10)
        event = _make_release_event(artist_id=10, release_id=77)
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        result = process_message(app, msg, dl)

        assert result is True
        dl.produce.assert_not_called()
        with app.app_context():
            assert _db.session.query(Notification).count() == 2

    def test_release_created_no_followers_noop(self, app, db_):
        """No followers → no Notification rows, but still returns True."""
        event = _make_release_event(artist_id=999, release_id=88)
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        result = process_message(app, msg, dl)

        assert result is True
        dl.produce.assert_not_called()
        with app.app_context():
            assert _db.session.query(Notification).count() == 0
            # ProcessedEvent row still written.
            assert _db.session.query(ProcessedEvent).count() == 1

    def test_duplicate_event_id_no_double_write(self, app, db_):
        """Re-delivering the same event_id does not create duplicate rows."""
        _add_follow(db_, fan_id=5, artist_id=20)
        eid = str(uuid.uuid4())
        event = _make_release_event(artist_id=20, event_id=eid)
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        process_message(app, msg, dl)   # first delivery
        process_message(app, msg, dl)   # re-delivery

        with app.app_context():
            assert _db.session.query(Notification).count() == 1

    def test_missing_artist_id_in_payload_dead_lettered(self, app, db_):
        event = _make_release_event(artist_id=1)
        del event["payload"]["artist_id"]
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        result = process_message(app, msg, dl)

        assert result is True
        dl.produce.assert_called_once()
        payload = json.loads(dl.produce.call_args.kwargs["value"].decode())
        assert "artist_id" in payload["failure_reason"] or \
               "payload" in payload["failure_reason"]

    def test_db_exception_exhausts_retries_and_dead_letters(
        self, app, db_
    ):
        """DB error on every attempt → retries exhausted → dead-letter."""
        event = _make_release_event(artist_id=30)
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        with patch(
            "consumers.notification_consumer.process_release_created",
            side_effect=Exception("simulated DB failure"),
        ):
            with patch(
                "consumers.notification_consumer.MAX_RETRIES", 1
            ):
                with patch(
                    "consumers.notification_consumer.RETRY_BACKOFF", 0.0
                ):
                    result = process_message(app, msg, dl)

        assert result is True  # dead-lettered; offset committed
        dl.produce.assert_called_once()
        payload = json.loads(dl.produce.call_args.kwargs["value"].decode())
        assert "DB retries exhausted" in payload["failure_reason"]

    def test_successful_processing_returns_true_for_offset_commit(
        self, app, db_
    ):
        """True return signals the caller to commit the Kafka offset."""
        event = _make_release_event(artist_id=40)
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        result = process_message(app, msg, dl)

        assert result is True


# ====================================================================
# 8 — Consumer restart / idempotency
# ====================================================================

class TestConsumerRestartIdempotency:
    """
    Verify that ProcessedEvent rows prevent re-processing on restart
    and that follower queries correctly scope to the specified artist.
    """

    def test_processed_event_survives_restart(self, app, db_):
        """
        After processing once, a second call with the same event_id
        is treated as a duplicate and no new rows are written.
        """
        _add_follow(db_, fan_id=7, artist_id=3)
        eid = str(uuid.uuid4())
        event = _make_release_event(artist_id=3, event_id=eid)

        # First consumer invocation.
        with app.app_context():
            process_release_created(
                _db.session, event,
                topic="artisthub.catalog", partition=0, offset=20,
            )
            _db.session.commit()

        # Second consumer invocation (simulates restart + re-delivery).
        with app.app_context():
            ok = process_release_created(
                _db.session, event,
                topic="artisthub.catalog", partition=0, offset=20,
            )
            _db.session.commit()

        assert ok is True  # duplicate: True means commit offset
        with app.app_context():
            assert _db.session.query(Notification).count() == 1  # not 2

    def test_follower_query_correctness(self, db_):
        """
        Only fans following the triggering artist receive notifications.
        Fans of other artists must not appear.
        """
        # Fan 50 follows artist 1; fan 51 follows artist 2 only.
        _add_follow(db_, fan_id=50, artist_id=1)
        _add_follow(db_, fan_id=51, artist_id=2)

        event = _make_release_event(artist_id=1, release_id=300)
        process_release_created(
            db_.session, event,
            topic="artisthub.catalog", partition=0, offset=0,
        )
        db_.session.commit()

        notifs = db_.session.query(Notification).all()
        assert len(notifs) == 1
        assert notifs[0].fan_id == 50

    def test_multiple_restarts_exactly_one_processed_event(
        self, app, db_
    ):
        """
        Three deliveries of the same event must result in exactly one
        ProcessedEvent row (PK uniqueness enforces this at the DB level).
        """
        _add_follow(db_, fan_id=60, artist_id=4)
        eid = str(uuid.uuid4())
        event = _make_release_event(artist_id=4, event_id=eid)
        msg = _make_msg(event)
        dl = _mock_dl_producer()

        for _ in range(3):
            process_message(app, msg, dl)

        with app.app_context():
            assert _db.session.query(ProcessedEvent).count() == 1
            assert _db.session.query(Notification).count() == 1
