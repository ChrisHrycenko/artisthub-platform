"""
tests/test_outbox.py

Test suite for Phase 7C — Transactional Outbox Pattern.

Coverage targets
----------------
- OutboxEvent model: creation, to_dict(), payload_dict()
- event_factory: all 12 build_* functions (type, version, topic, key, payload)
- Atomicity: business mutation + outbox row committed in the same transaction
- Rollback: both business object and outbox row are removed on rollback
- Route integration: each of the 12 instrumented routes creates exactly one
  outbox row with the correct event_type
- event_id uniqueness across builds
- Relay: poll_and_publish with a mock producer
- Relay: successful publish marks published_at and increments attempts
- Relay: failed publish records last_error and leaves published_at NULL
- Relay: restart does not lose pending events (pending rows remain)
- Relay: duplicate invocation does not corrupt already-published rows

Serialisation note
------------------
Phase 7C uses JSON. Tests assert on JSON-decoded payload content, not
binary Avro.  Live Schema Registry validation is Phase 7F.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from app.extensions import db as _db
from app.models.outbox import OutboxEvent
from app.services import event_factory as ef
from app.services.outbox_relay import poll_and_publish


# ====================================================================
# Helpers
# ====================================================================

def _count_outbox(session) -> int:
    """Return the current count of outbox rows."""
    return session.query(OutboxEvent).count()


def _pending_outbox(session):
    """Return all un-published outbox rows."""
    return (
        session.query(OutboxEvent)
        .filter(OutboxEvent.published_at.is_(None))
        .all()
    )


def _published_outbox(session):
    """Return all published outbox rows."""
    return (
        session.query(OutboxEvent)
        .filter(OutboxEvent.published_at.isnot(None))
        .all()
    )


# ====================================================================
# 1 — OutboxEvent model unit tests
# ====================================================================

class TestOutboxEventModel:
    """Unit tests for the OutboxEvent SQLAlchemy model."""

    def test_create_and_persist(self, db_):
        """An OutboxEvent can be persisted and re-queried."""
        row = OutboxEvent(
            event_id=str(uuid.uuid4()),
            event_type="fan.followed.artist",
            event_version="1",
            topic="artisthub.social",
            message_key="42",
            payload='{"event_id": "x"}',
        )
        db_.session.add(row)
        db_.session.commit()

        fetched = db_.session.get(OutboxEvent, row.id)
        assert fetched is not None
        assert fetched.event_type == "fan.followed.artist"
        assert fetched.published_at is None
        assert fetched.publish_attempts == 0
        assert fetched.last_error is None

    def test_to_dict_keys(self, db_):
        """to_dict() returns all required keys."""
        row = OutboxEvent(
            event_id=str(uuid.uuid4()),
            event_type="artist.registered",
            event_version="1",
            topic="artisthub.identity",
            message_key="1",
            payload='{}',
        )
        db_.session.add(row)
        db_.session.commit()

        d = row.to_dict()
        for key in (
            "id", "event_id", "event_type", "event_version",
            "topic", "message_key", "correlation_id",
            "created_at", "published_at", "publish_attempts", "last_error",
        ):
            assert key in d, f"Missing key: {key}"

        assert d["published_at"] is None
        assert d["publish_attempts"] == 0

    def test_payload_dict(self, db_):
        """payload_dict() deserialises the JSON payload column."""
        data = {"event_id": "abc", "payload": {"fan_id": 1}}
        row = OutboxEvent(
            event_id=str(uuid.uuid4()),
            event_type="fan.followed.artist",
            event_version="1",
            topic="artisthub.social",
            message_key="2",
            payload=json.dumps(data),
        )
        db_.session.add(row)
        db_.session.commit()

        assert row.payload_dict() == data

    def test_event_id_unique_constraint(self, db_):
        """Inserting two rows with the same event_id raises an error."""
        eid = str(uuid.uuid4())
        for _ in range(2):
            db_.session.add(OutboxEvent(
                event_id=eid,
                event_type="test",
                event_version="1",
                topic="artisthub.social",
                message_key="1",
                payload='{}',
            ))
        with pytest.raises(Exception):
            db_.session.commit()
        db_.session.rollback()


# ====================================================================
# 2 — event_factory unit tests
# ====================================================================

class TestEventFactory:
    """Unit tests for the centralised event factory module."""

    def _make_follow(self):
        m = MagicMock()
        m.id = 10
        m.fan_id = 5
        m.artist_id = 3
        m.created_at = datetime(2026, 1, 15, 12, 0, 0)
        return m

    def _make_post(self):
        m = MagicMock()
        m.id = 20
        m.artist_id = 3
        m.body = "Hello fans!"
        m.image_url = None
        m.created_at = datetime(2026, 1, 15, 12, 0, 0)
        return m

    def _make_release(self):
        m = MagicMock()
        m.id = 30
        m.artist_id = 3
        m.title = "Test EP"
        m.release_type = "EP"
        m.genre = "Electronic"
        m.description = None
        m.artwork_url = None
        m.streaming_url = None
        m.release_date = None
        m.created_at = datetime(2026, 1, 15, 12, 0, 0)
        return m

    def _make_product(self):
        m = MagicMock()
        m.id = 40
        m.artist_id = 3
        m.product_name = "T-Shirt"
        m.price = 29.99
        m.description = None
        m.image_url = None
        m.inventory_quantity = 50
        m.created_at = datetime(2026, 1, 15, 12, 0, 0)
        return m

    def _make_artist(self):
        m = MagicMock()
        m.id = 3
        m.email = "a@b.com"
        m.display_name = "DJ Test"
        m.bio = None
        m.genre = "Electronic"
        m.location = "Toronto"
        m.profile_image_url = None
        m.created_at = datetime(2026, 1, 15, 12, 0, 0)
        return m

    # ---- fan.followed.artist ----------------------------------------

    def test_fan_followed_artist_type_topic_key(self):
        row = ef.build_fan_followed_artist(self._make_follow())
        assert row.event_type == "fan.followed.artist"
        assert row.event_version == "1"
        assert row.topic == ef.TOPIC_SOCIAL
        assert row.message_key == "3"

    def test_fan_followed_artist_payload(self):
        row = ef.build_fan_followed_artist(self._make_follow())
        p = row.payload_dict()
        assert p["payload"]["follow_id"] == 10
        assert p["payload"]["fan_id"] == 5
        assert p["payload"]["artist_id"] == 3

    def test_fan_followed_artist_event_id_unique(self):
        r1 = ef.build_fan_followed_artist(self._make_follow())
        r2 = ef.build_fan_followed_artist(self._make_follow())
        assert r1.event_id != r2.event_id

    # ---- fan.unfollowed.artist --------------------------------------

    def test_fan_unfollowed_artist_type_topic_key(self):
        row = ef.build_fan_unfollowed_artist(5, 3)
        assert row.event_type == "fan.unfollowed.artist"
        assert row.topic == ef.TOPIC_SOCIAL
        assert row.message_key == "3"

    def test_fan_unfollowed_artist_payload(self):
        row = ef.build_fan_unfollowed_artist(5, 3)
        p = row.payload_dict()
        assert p["payload"]["fan_id"] == 5
        assert p["payload"]["artist_id"] == 3
        assert "unfollowed_at" in p["payload"]

    # ---- artist.post.created ----------------------------------------

    def test_artist_post_created_type_topic_key(self):
        row = ef.build_artist_post_created(self._make_post())
        assert row.event_type == "artist.post.created"
        assert row.topic == ef.TOPIC_SOCIAL
        assert row.message_key == "3"

    def test_artist_post_created_payload_uses_posted_at(self):
        """Avro schema uses posted_at, not created_at."""
        row = ef.build_artist_post_created(self._make_post())
        p = row.payload_dict()
        assert "posted_at" in p["payload"]
        assert "created_at" not in p["payload"]
        assert p["payload"]["body"] == "Hello fans!"

    # ---- artist.post.deleted ----------------------------------------

    def test_artist_post_deleted_type_topic_key(self):
        row = ef.build_artist_post_deleted(20, 3)
        assert row.event_type == "artist.post.deleted"
        assert row.topic == ef.TOPIC_SOCIAL
        assert row.message_key == "3"

    def test_artist_post_deleted_payload(self):
        row = ef.build_artist_post_deleted(20, 3)
        p = row.payload_dict()
        assert p["payload"]["post_id"] == 20
        assert p["payload"]["artist_id"] == 3
        assert "deleted_at" in p["payload"]

    # ---- artist.release.created -------------------------------------

    def test_artist_release_created_type_topic_key(self):
        row = ef.build_artist_release_created(self._make_release())
        assert row.event_type == "artist.release.created"
        assert row.topic == ef.TOPIC_CATALOG
        assert row.message_key == "3"

    def test_artist_release_created_payload(self):
        row = ef.build_artist_release_created(self._make_release())
        p = row.payload_dict()
        assert p["payload"]["release_id"] == 30
        assert p["payload"]["title"] == "Test EP"
        assert p["payload"]["release_type"] == "EP"

    # ---- artist.release.updated -------------------------------------

    def test_artist_release_updated_type_topic_key(self):
        row = ef.build_artist_release_updated(self._make_release())
        assert row.event_type == "artist.release.updated"
        assert row.topic == ef.TOPIC_CATALOG
        assert row.message_key == "3"

    # ---- artist.release.deleted -------------------------------------

    def test_artist_release_deleted_type_topic_key(self):
        row = ef.build_artist_release_deleted(30, 3)
        assert row.event_type == "artist.release.deleted"
        assert row.topic == ef.TOPIC_CATALOG
        assert row.message_key == "3"

    def test_artist_release_deleted_payload(self):
        row = ef.build_artist_release_deleted(30, 3)
        p = row.payload_dict()
        assert p["payload"]["release_id"] == 30
        assert "deleted_at" in p["payload"]

    # ---- artist.merch.created ---------------------------------------

    def test_artist_merch_created_type_topic_key(self):
        row = ef.build_artist_merch_created(self._make_product())
        assert row.event_type == "artist.merch.created"
        assert row.topic == ef.TOPIC_CATALOG
        assert row.message_key == "3"

    def test_artist_merch_created_price_is_float(self):
        row = ef.build_artist_merch_created(self._make_product())
        p = row.payload_dict()
        assert isinstance(p["payload"]["price"], float)
        assert p["payload"]["price"] == 29.99

    # ---- artist.merch.updated ---------------------------------------

    def test_artist_merch_updated_type_topic_key(self):
        row = ef.build_artist_merch_updated(self._make_product())
        assert row.event_type == "artist.merch.updated"
        assert row.topic == ef.TOPIC_CATALOG

    # ---- artist.merch.deleted ---------------------------------------

    def test_artist_merch_deleted_type_topic_key(self):
        row = ef.build_artist_merch_deleted(40, 3)
        assert row.event_type == "artist.merch.deleted"
        assert row.topic == ef.TOPIC_CATALOG

    # ---- artist.registered ------------------------------------------

    def test_artist_registered_type_topic_key(self):
        row = ef.build_artist_registered(self._make_artist())
        assert row.event_type == "artist.registered"
        assert row.topic == ef.TOPIC_IDENTITY
        assert row.message_key == "3"

    def test_artist_registered_payload_contains_email(self):
        """Email is included in identity events only."""
        row = ef.build_artist_registered(self._make_artist())
        p = row.payload_dict()
        assert p["payload"]["email"] == "a@b.com"

    # ---- artist.profile.updated -------------------------------------

    def test_artist_profile_updated_type_topic_key(self):
        row = ef.build_artist_profile_updated(self._make_artist())
        assert row.event_type == "artist.profile.updated"
        assert row.topic == ef.TOPIC_IDENTITY
        assert row.message_key == "3"

    def test_artist_profile_updated_payload_no_email(self):
        """Email must NOT appear in profile.updated payload (PII boundary)."""
        row = ef.build_artist_profile_updated(self._make_artist())
        p = row.payload_dict()
        assert "email" not in p["payload"]

    # ---- Envelope fields present in all events ----------------------

    def test_envelope_fields_present(self):
        """Every built event contains all 6 standard envelope fields."""
        builders = [
            ef.build_fan_followed_artist(self._make_follow()),
            ef.build_fan_unfollowed_artist(5, 3),
            ef.build_artist_post_created(self._make_post()),
            ef.build_artist_post_deleted(20, 3),
            ef.build_artist_release_created(self._make_release()),
            ef.build_artist_release_updated(self._make_release()),
            ef.build_artist_release_deleted(30, 3),
            ef.build_artist_merch_created(self._make_product()),
            ef.build_artist_merch_updated(self._make_product()),
            ef.build_artist_merch_deleted(40, 3),
            ef.build_artist_registered(self._make_artist()),
            ef.build_artist_profile_updated(self._make_artist()),
        ]
        for row in builders:
            p = row.payload_dict()
            for field in (
                "event_id", "event_type", "event_version",
                "occurred_at", "producer", "correlation_id",
            ):
                assert field in p, (
                    f"{row.event_type}: missing envelope field '{field}'"
                )
            assert p["producer"] == "artisthub-api"
            assert p["event_version"] == "1"


# ====================================================================
# 3 — Atomicity: route creates outbox row in the same transaction
# ====================================================================

class TestAtomicOutbox:
    """
    Verify that each instrumented route creates exactly one outbox row
    in the same transaction as the business mutation.
    """

    def test_follow_creates_outbox_row(self, fan_client, artist_record, db_):
        before = _count_outbox(db_.session)
        resp = fan_client.post(
            "/api/follows",
            json={"artist_id": artist_record.id},
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "fan.followed.artist"
        assert row.published_at is None

    def test_unfollow_creates_outbox_row(
        self, fan_client, artist_record, db_
    ):
        # First follow
        fan_client.post(
            "/api/follows",
            json={"artist_id": artist_record.id},
            content_type="application/json",
        )
        before = _count_outbox(db_.session)
        resp = fan_client.delete(f"/api/follows/{artist_record.id}")
        assert resp.status_code == 200
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "fan.unfollowed.artist"

    def test_create_post_creates_outbox_row(
        self, artist_client, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.post(
            "/api/posts",
            json={"body": "Test post for outbox"},
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.post.created"
        assert row.topic == "artisthub.social"
        p = row.payload_dict()
        assert p["payload"]["body"] == "Test post for outbox"

    def test_delete_post_creates_outbox_row(
        self, artist_client, post_record, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.delete(f"/api/posts/{post_record.id}")
        assert resp.status_code == 200
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.post.deleted"

    def test_create_release_creates_outbox_row(
        self, artist_client, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.post(
            "/api/releases",
            json={"title": "New Release", "release_type": "Single"},
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.release.created"
        assert row.topic == "artisthub.catalog"

    def test_update_release_creates_outbox_row(
        self, artist_client, release_record, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.put(
            f"/api/releases/{release_record.id}",
            json={"title": "Updated Title"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.release.updated"

    def test_delete_release_creates_outbox_row(
        self, artist_client, release_record, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.delete(f"/api/releases/{release_record.id}")
        assert resp.status_code == 200
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.release.deleted"

    def test_create_merch_creates_outbox_row(
        self, artist_client, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.post(
            "/api/merch",
            json={"product_name": "Hoodie", "price": 49.99},
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.merch.created"
        assert row.topic == "artisthub.catalog"

    def test_update_merch_creates_outbox_row(
        self, artist_client, merch_record, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.put(
            f"/api/merch/{merch_record.id}",
            json={"product_name": "Updated Hoodie"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.merch.updated"

    def test_delete_merch_creates_outbox_row(
        self, artist_client, merch_record, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.delete(f"/api/merch/{merch_record.id}")
        assert resp.status_code == 200
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.merch.deleted"

    def test_artist_register_creates_outbox_row(self, client, db_):
        before = _count_outbox(db_.session)
        resp = client.post(
            "/api/auth/artist/register",
            json={
                "email": "newartist@outbox.test",
                "password": "securepass1",
                "display_name": "Outbox Artist",
            },
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.registered"
        assert row.topic == "artisthub.identity"
        p = row.payload_dict()
        assert p["payload"]["email"] == "newartist@outbox.test"

    def test_update_artist_profile_creates_outbox_row(
        self, artist_client, artist_record, db_
    ):
        before = _count_outbox(db_.session)
        resp = artist_client.put(
            f"/api/artists/{artist_record.id}",
            json={"display_name": "Updated Name"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert _count_outbox(db_.session) == before + 1
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.event_type == "artist.profile.updated"
        assert row.topic == "artisthub.identity"
        p = row.payload_dict()
        assert "email" not in p["payload"]  # PII boundary

    def test_message_key_is_artist_id_string(
        self, artist_client, artist_record, db_
    ):
        """Kafka message key must be artist_id as a string."""
        artist_client.post(
            "/api/releases",
            json={"title": "Key Test Release", "release_type": "Single"},
            content_type="application/json",
        )
        row = db_.session.query(OutboxEvent).order_by(
            OutboxEvent.id.desc()
        ).first()
        assert row.message_key == str(artist_record.id)


# ====================================================================
# 4 — Atomicity: rollback removes both business object and outbox row
# ====================================================================

class TestAtomicRollback:
    """Verify that a transaction rollback removes both rows atomically."""

    def test_rollback_removes_outbox_row(self, db_):
        """If the transaction is rolled back, the outbox row is gone too."""
        import uuid as _uuid
        from app.models.release import MusicRelease
        from app.models.artist import Artist
        from app.extensions import bcrypt as _bcrypt

        pw = _bcrypt.generate_password_hash("pw").decode()
        artist = Artist(
            email="rollback@test.com",
            password_hash=pw,
            display_name="Rollback",
        )
        db_.session.add(artist)
        db_.session.commit()

        # Begin a transaction that we will roll back.
        release = MusicRelease(
            artist_id=artist.id,
            title="Will be rolled back",
            release_type="Single",
        )
        db_.session.add(release)
        outbox_row = OutboxEvent(
            event_id=str(_uuid.uuid4()),
            event_type="artist.release.created",
            event_version="1",
            topic="artisthub.catalog",
            message_key=str(artist.id),
            payload='{}',
        )
        db_.session.add(outbox_row)

        # Roll back before commit.
        db_.session.rollback()

        # Neither row should exist.
        assert db_.session.query(MusicRelease).filter_by(
            title="Will be rolled back"
        ).first() is None
        assert db_.session.query(OutboxEvent).filter_by(
            event_type="artist.release.created"
        ).first() is None


# ====================================================================
# 5 — Relay: poll_and_publish with mock producer
# ====================================================================

class TestOutboxRelay:
    """
    Tests for the outbox relay logic using a mock KafkaProducerService.

    Tests inject a mock producer so they do not require a live Kafka broker.
    """

    def _seed_pending(self, db_, n: int = 1):
        """Insert n pending outbox rows and return them."""
        rows = []
        for i in range(n):
            row = OutboxEvent(
                event_id=str(uuid.uuid4()),
                event_type=f"test.event.{i}",
                event_version="1",
                topic="artisthub.social",
                message_key=str(i + 1),
                payload=json.dumps({"event_id": str(uuid.uuid4())}),
            )
            db_.session.add(row)
            rows.append(row)
        db_.session.commit()
        return rows

    def test_no_pending_returns_zero(self, app, db_):
        """poll_and_publish returns 0 when there are no pending rows."""
        mock_producer = MagicMock()
        count = poll_and_publish(app, mock_producer)
        assert count == 0
        mock_producer.produce_avro.assert_not_called()

    def test_pending_row_is_published(self, app, db_):
        """
        Phase 7F: poll_and_publish calls producer.produce_avro() for each
        pending row (not produce()).
        """
        rows = self._seed_pending(db_, 2)
        mock_producer = MagicMock()

        count = poll_and_publish(app, mock_producer, batch_size=10)
        assert count == 2
        assert mock_producer.produce_avro.call_count == 2
        mock_producer.flush.assert_called_once()

        # Verify produce_avro was called with correct topic and key.
        calls = mock_producer.produce_avro.call_args_list
        topics = {c.kwargs["topic"] for c in calls}
        assert topics == {"artisthub.social"}

    def test_successful_delivery_marks_published_at(self, app, db_):
        """
        When the delivery callback reports success, published_at is set
        and publish_attempts is incremented.
        Phase 7F: relay calls produce_avro(); on_delivery still works.
        """
        rows = self._seed_pending(db_, 1)
        row_id = rows[0].id

        def mock_produce_avro(
            topic, event_type, key, record, on_delivery
        ):
            # Simulate successful broker acknowledgement.
            msg = MagicMock()
            msg.topic.return_value = topic
            msg.partition.return_value = 0
            msg.offset.return_value = 1
            on_delivery(None, msg)  # err=None → success

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = mock_produce_avro

        poll_and_publish(app, mock_producer, batch_size=10)

        with app.app_context():
            row = db_.session.get(OutboxEvent, row_id)
            assert row.published_at is not None
            assert row.publish_attempts == 1
            assert row.last_error is None

    def test_failed_delivery_records_error_and_leaves_pending(
        self, app, db_
    ):
        """
        When the delivery callback reports an error, last_error is set,
        published_at remains NULL, and publish_attempts is incremented.
        The row is NOT deleted — it will be retried.
        Phase 7F: relay calls produce_avro().
        """
        rows = self._seed_pending(db_, 1)
        row_id = rows[0].id

        def mock_produce_avro_fail(
            topic, event_type, key, record, on_delivery
        ):
            msg = MagicMock()
            msg.topic.return_value = topic
            msg.partition.return_value = 0
            msg.offset.return_value = -1
            on_delivery(Exception("broker timeout"), msg)

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = mock_produce_avro_fail

        poll_and_publish(app, mock_producer, batch_size=10)

        with app.app_context():
            row = db_.session.get(OutboxEvent, row_id)
            assert row.published_at is None       # still pending
            assert row.publish_attempts == 1      # attempt recorded
            assert row.last_error is not None     # error recorded
            assert "broker timeout" in row.last_error

    def test_already_published_rows_are_skipped(self, app, db_):
        """
        Rows with published_at set are not picked up by the relay.
        """
        # Seed one published and one pending row.
        published = OutboxEvent(
            event_id=str(uuid.uuid4()),
            event_type="already.published",
            event_version="1",
            topic="artisthub.social",
            message_key="1",
            payload='{}',
            published_at=datetime.now(timezone.utc),
        )
        pending = OutboxEvent(
            event_id=str(uuid.uuid4()),
            event_type="still.pending",
            event_version="1",
            topic="artisthub.social",
            message_key="2",
            payload='{}',
        )
        db_.session.add_all([published, pending])
        db_.session.commit()

        mock_producer = MagicMock()
        count = poll_and_publish(app, mock_producer, batch_size=10)

        # Only the pending row should be processed.
        assert count == 1
        assert mock_producer.produce_avro.call_count == 1
        produce_call = mock_producer.produce_avro.call_args
        assert produce_call.kwargs["key"] == "2"

    def test_relay_restart_preserves_pending_events(self, app, db_):
        """
        Pending rows survive between relay cycles. A restart does not
        lose unprocessed events.
        Phase 7F: produce_avro raises to simulate crash.
        """
        rows = self._seed_pending(db_, 3)

        # First cycle: relay crashes before flush completes.
        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = Exception("crash")

        poll_and_publish(app, mock_producer, batch_size=10)

        # All 3 rows still have published_at IS NULL — none were lost.
        with app.app_context():
            still_pending = _pending_outbox(db_.session)
            seeded_ids = {r.id for r in rows}
            still_seeded = [r for r in still_pending if r.id in seeded_ids]
            assert len(still_seeded) == 3

    def test_duplicate_relay_call_does_not_corrupt_published_rows(
        self, app, db_
    ):
        """
        Running the relay twice does not re-publish already-published rows
        or corrupt their published_at timestamps.
        Phase 7F: uses produce_avro.
        """
        rows = self._seed_pending(db_, 1)
        row_id = rows[0].id
        first_published_at = None

        def mock_produce_avro_ok(
            topic, event_type, key, record, on_delivery
        ):
            msg = MagicMock()
            msg.topic.return_value = topic
            msg.partition.return_value = 0
            msg.offset.return_value = 1
            on_delivery(None, msg)

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = mock_produce_avro_ok

        # First cycle — publishes the row.
        poll_and_publish(app, mock_producer, batch_size=10)

        with app.app_context():
            row = db_.session.get(OutboxEvent, row_id)
            first_published_at = row.published_at
            assert first_published_at is not None

        # Second cycle — row is already published, must be skipped.
        mock_producer.reset_mock()
        count = poll_and_publish(app, mock_producer, batch_size=10)
        assert count == 0
        mock_producer.produce_avro.assert_not_called()

        with app.app_context():
            row = db_.session.get(OutboxEvent, row_id)
            # published_at must not have changed.
            assert row.published_at == first_published_at

    def test_batch_size_limits_rows_per_cycle(self, app, db_):
        """The relay processes at most batch_size rows per cycle."""
        self._seed_pending(db_, 5)
        mock_producer = MagicMock()

        count = poll_and_publish(app, mock_producer, batch_size=2)
        assert count == 2
        assert mock_producer.produce_avro.call_count == 2

    def test_correct_payload_is_sent_to_kafka(self, app, db_):
        """
        Phase 7F: produce_avro is called with the decoded record dict
        (not the raw JSON string).
        """
        eid = str(uuid.uuid4())
        record_dict = {
            "event_id": eid,
            "event_type": "fan.followed.artist",
            "event_version": "1",
            "occurred_at": "2026-08-19T12:00:00Z",
            "producer": "artisthub-api",
            "correlation_id": None,
            "payload": {
                "follow_id": 1, "fan_id": 2, "artist_id": 3,
                "followed_at": "2026-08-19T12:00:00Z",
            },
        }
        payload_str = json.dumps(record_dict)
        row = OutboxEvent(
            event_id=eid,
            event_type="fan.followed.artist",
            event_version="1",
            topic="artisthub.social",
            message_key="3",
            payload=payload_str,
        )
        db_.session.add(row)
        db_.session.commit()

        mock_producer = MagicMock()
        poll_and_publish(app, mock_producer, batch_size=10)

        produce_call = mock_producer.produce_avro.call_args
        assert produce_call.kwargs["event_type"] == "fan.followed.artist"
        assert produce_call.kwargs["topic"] == "artisthub.social"
        assert produce_call.kwargs["key"] == "3"
        assert produce_call.kwargs["record"]["event_id"] == eid
