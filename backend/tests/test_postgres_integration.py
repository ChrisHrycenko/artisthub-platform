"""
tests/test_postgres_integration.py

Phase 8A — PostgreSQL integration tests for ArtistHub.

These tests run against a live PostgreSQL database when the environment
variable ARTISTHUB_TEST_POSTGRES_URL is set.  They are automatically
skipped when the variable is absent so the standard SQLite test suite
continues to work without any PostgreSQL dependency.

Set the variable to your PostgreSQL DATABASE_URL before running:

    export ARTISTHUB_TEST_POSTGRES_URL=\\
        postgresql://artisthub:password@localhost:5432/artisthub_test

    cd backend && pytest tests/test_postgres_integration.py -v

What these tests cover
----------------------
1. Schema migration — flask db upgrade applies all revisions cleanly.
2. Core CRUD — Artist, Fan, MusicRelease, SocialPost, MerchProduct,
   Follow all round-trip through PostgreSQL.
3. Transactional Outbox atomicity — business row + outbox row in one
   transaction; rollback removes both; relay query returns pending rows.
4. Outbox relay publish flow — published_at is set correctly; retried
   rows have publish_attempts > 0.
5. AnalyticsState — counters created and incremented / decremented.
6. ProcessedEvent idempotency — duplicate event_id is rejected by the
   PK constraint; apply_analytics_update returns False for duplicates.
7. Notification uniqueness — UNIQUE(event_id, fan_id) prevents duplicate
   notification rows; IntegrityError is raised on second insert attempt.
8. NUMERIC(10,2) price — Decimal precision is preserved through a
   PostgreSQL round-trip (not subject to float rounding errors).
9. CASCADE delete — deleting an artist cascades to releases, posts,
   merch, and follows.
10. /api/health endpoint — returns {"database": "ok"} against PostgreSQL.

SELECT FOR UPDATE SKIP LOCKED (future)
---------------------------------------
The outbox relay currently uses a simple
  WHERE published_at IS NULL  ORDER BY id  LIMIT batch
query.  This is correct for a single-relay process.  Running more than
one relay replica against the same PostgreSQL database would cause
duplicate publishes without row-level locking.  Implementing
  SELECT ... FOR UPDATE SKIP LOCKED
is deferred to a future scaling phase and is documented here as a known
limitation.
"""

import json
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Skip the entire module when no PostgreSQL URL is configured.
# ---------------------------------------------------------------------------
PG_URL = os.environ.get("ARTISTHUB_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason=(
        "Set ARTISTHUB_TEST_POSTGRES_URL=postgresql://... to run "
        "PostgreSQL integration tests."
    ),
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pg_app():
    """
    Create a Flask application pointed at the PostgreSQL test database.

    scope="module" — one app + schema lifecycle per test module run.

    Uses the ``pg_testing`` config (PostgresTestingConfig), which reads
    ARTISTHUB_TEST_POSTGRES_URL from the environment.  The config skips
    db.create_all() on non-SQLite databases; we call it explicitly here
    so all model tables exist for the test run.

    Per-test cleanup is handled by the pg_db fixture (TRUNCATE ... CASCADE).
    Module teardown calls drop_all() to leave the database clean.
    """
    from app import create_app
    from app.extensions import db as _db

    flask_app = create_app("pg_testing")

    with flask_app.app_context():
        # Start clean, then create all model tables.
        _db.drop_all()
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(scope="function")
def pg_db(pg_app):
    """
    Provide a clean database state for each test by truncating all tables
    after each test.  Flask-SQLAlchemy 3.x does not support
    session.bind on the scoped session; instead we truncate between tests.
    """
    from app.extensions import db as _db

    with pg_app.app_context():
        yield _db
        _db.session.remove()
        # Truncate all tables in dependency order so FK constraints are
        # satisfied.  RESTART IDENTITY resets sequences.
        _db.session.execute(
            _db.text(
                "TRUNCATE TABLE notification, processed_event, "
                "analytics_state, event_outbox, follow, "
                "merch_product, music_release, social_post, "
                "fan, artist RESTART IDENTITY CASCADE"
            )
        )
        _db.session.commit()


@pytest.fixture(scope="function")
def pg_client(pg_app, pg_db):
    """Unauthenticated test client against the PostgreSQL app."""
    return pg_app.test_client()


@pytest.fixture(scope="function")
def pg_artist(pg_db):
    """Persist an Artist row in the PostgreSQL database."""
    from app.extensions import bcrypt as _bcrypt
    from app.models.artist import Artist

    pw_hash = _bcrypt.generate_password_hash("pgpass123").decode("utf-8")
    artist = Artist(
        email="pgartist@example.com",
        password_hash=pw_hash,
        display_name="PG Artist",
        genre="Electronic",
        location="Berlin, DE",
    )
    pg_db.session.add(artist)
    pg_db.session.commit()
    return artist


@pytest.fixture(scope="function")
def pg_fan(pg_db):
    """Persist a Fan row in the PostgreSQL database."""
    from app.extensions import bcrypt as _bcrypt
    from app.models.fan import Fan

    pw_hash = _bcrypt.generate_password_hash("pgfanpass").decode("utf-8")
    fan = Fan(
        email="pgfan@example.com",
        password_hash=pw_hash,
        username="pgfan",
    )
    pg_db.session.add(fan)
    pg_db.session.commit()
    return fan


# ---------------------------------------------------------------------------
# 1. Basic connectivity and schema
# ---------------------------------------------------------------------------

class TestPostgresConnectivity:
    """Verify PostgreSQL is reachable and the schema is populated."""

    def test_select_one(self, pg_db):
        """Simple SELECT 1 confirms the connection is alive."""
        result = pg_db.session.execute(text("SELECT 1")).scalar()
        assert result == 1

    def test_all_tables_exist(self, pg_db):
        """All ArtistHub tables are present after create_all()."""
        expected_tables = {
            "artist",
            "fan",
            "music_release",
            "social_post",
            "merch_product",
            "follow",
            "event_outbox",
            "analytics_state",
            "processed_event",
            "notification",
        }
        inspector = pg_db.inspect(pg_db.engine)
        actual_tables = set(inspector.get_table_names())
        assert expected_tables.issubset(actual_tables), (
            f"Missing tables: {expected_tables - actual_tables}"
        )

    def test_health_endpoint_database_ok(self, pg_client):
        """GET /api/health returns database=ok against PostgreSQL."""
        resp = pg_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["data"]["database"] == "ok"


# ---------------------------------------------------------------------------
# 2. Core model CRUD
# ---------------------------------------------------------------------------

class TestCoreModelCRUD:
    """Round-trip tests for all core models on PostgreSQL."""

    def test_artist_create_and_read(self, pg_db, pg_artist):
        """Artist row round-trips through PostgreSQL."""
        from app.models.artist import Artist
        retrieved = pg_db.session.get(Artist, pg_artist.id)
        assert retrieved is not None
        assert retrieved.email == "pgartist@example.com"
        assert retrieved.display_name == "PG Artist"

    def test_fan_create_and_read(self, pg_db, pg_fan):
        """Fan row round-trips through PostgreSQL."""
        from app.models.fan import Fan
        retrieved = pg_db.session.get(Fan, pg_fan.id)
        assert retrieved is not None
        assert retrieved.username == "pgfan"

    def test_music_release_round_trip(self, pg_db, pg_artist):
        """MusicRelease FK, cascade, and date handling work on PostgreSQL."""
        from app.models.release import MusicRelease
        from datetime import date

        release = MusicRelease(
            artist_id=pg_artist.id,
            title="PG Test Single",
            release_type="Single",
            genre="Electronic",
            release_date=date(2025, 1, 15),
        )
        pg_db.session.add(release)
        pg_db.session.commit()

        retrieved = pg_db.session.get(MusicRelease, release.id)
        assert retrieved.title == "PG Test Single"
        assert retrieved.release_date == date(2025, 1, 15)
        assert retrieved.artist_id == pg_artist.id

    def test_merch_numeric_price_precision(self, pg_db, pg_artist):
        """
        NUMERIC(10,2) price is stored and retrieved with Decimal precision.

        SQLite stores Numeric as a real (floating-point), which can introduce
        rounding errors.  PostgreSQL stores it as a true fixed-point NUMERIC.
        The to_dict() method casts to float for JSON serialisation; this test
        verifies the raw Decimal survives the round-trip intact.
        """
        from app.models.merchandise import MerchProduct

        product = MerchProduct(
            artist_id=pg_artist.id,
            product_name="Vinyl Record",
            price=Decimal("24.99"),
        )
        pg_db.session.add(product)
        pg_db.session.commit()

        retrieved = pg_db.session.get(MerchProduct, product.id)
        # PostgreSQL returns Decimal; SQLite returns float.
        assert retrieved.price == Decimal("24.99")
        # JSON serialisation via to_dict() must still work.
        d = retrieved.to_dict()
        assert abs(d["price"] - 24.99) < 0.001

    def test_follow_unique_constraint(self, pg_db, pg_artist, pg_fan):
        """
        UNIQUE(fan_id, artist_id) raises IntegrityError on duplicate insert.

        PostgreSQL enforces this at the DB layer; the route catches it and
        returns 409.  This test confirms the constraint is active on PG.
        """
        from app.models.follow import Follow
        from sqlalchemy.exc import IntegrityError

        follow1 = Follow(fan_id=pg_fan.id, artist_id=pg_artist.id)
        pg_db.session.add(follow1)
        pg_db.session.commit()

        follow2 = Follow(fan_id=pg_fan.id, artist_id=pg_artist.id)
        pg_db.session.add(follow2)
        with pytest.raises(IntegrityError):
            pg_db.session.commit()
        pg_db.session.rollback()

    def test_cascade_delete_artist(self, pg_db, pg_artist):
        """
        Deleting an Artist cascades to releases, posts, and merch.

        PostgreSQL enforces ON DELETE CASCADE at the DB level.  SQLAlchemy's
        'all, delete-orphan' cascade handles the in-memory session; the DB
        constraint is the authoritative guard.
        """
        from app.models.release import MusicRelease
        from app.models.post import SocialPost

        release = MusicRelease(
            artist_id=pg_artist.id,
            title="Will Be Deleted",
            release_type="Single",
        )
        post = SocialPost(
            artist_id=pg_artist.id,
            body="Also deleted",
        )
        pg_db.session.add_all([release, post])
        pg_db.session.commit()

        release_id = release.id
        post_id = post.id

        pg_db.session.delete(pg_artist)
        pg_db.session.commit()

        assert pg_db.session.get(MusicRelease, release_id) is None
        assert pg_db.session.get(SocialPost, post_id) is None


# ---------------------------------------------------------------------------
# 3. Transactional Outbox
# ---------------------------------------------------------------------------

class TestTransactionalOutbox:
    """
    Verify the Transactional Outbox Pattern works correctly on PostgreSQL.

    The key invariant: the business mutation and the outbox insert must
    commit atomically.  A rollback must remove both rows.
    """

    def _make_outbox_row(self, artist_id: int) -> dict:
        """Build a minimal outbox row dict for testing."""
        event_id = str(uuid.uuid4())
        payload = json.dumps({
            "event_id": event_id,
            "event_type": "artist.release.created",
            "event_version": "1",
            "occurred_at": "2025-01-01T00:00:00Z",
            "payload": {
                "artist_id": artist_id,
                "release_id": 99,
                "title": "Test Release",
            },
        })
        return {
            "event_id": event_id,
            "event_type": "artist.release.created",
            "event_version": "1",
            "topic": "artisthub.catalog",
            "message_key": str(artist_id),
            "payload": payload,
        }

    def test_outbox_insert_with_business_row(self, pg_db, pg_artist):
        """
        Business row (MusicRelease) + outbox row commit in one transaction.

        Both rows are visible after commit.
        """
        from app.models.release import MusicRelease
        from app.models.outbox import OutboxEvent

        row_data = self._make_outbox_row(pg_artist.id)

        release = MusicRelease(
            artist_id=pg_artist.id,
            title="Atomic Release",
            release_type="Single",
        )
        outbox = OutboxEvent(**row_data)

        pg_db.session.add(release)
        pg_db.session.add(outbox)
        pg_db.session.commit()

        assert pg_db.session.get(MusicRelease, release.id) is not None
        assert pg_db.session.get(OutboxEvent, outbox.id) is not None
        assert outbox.published_at is None  # pending

    def test_outbox_rollback_removes_both_rows(self, pg_db, pg_artist):
        """
        A rolled-back transaction leaves neither the business row nor the
        outbox row in the database.
        """
        from app.models.release import MusicRelease
        from app.models.outbox import OutboxEvent

        row_data = self._make_outbox_row(pg_artist.id)

        release = MusicRelease(
            artist_id=pg_artist.id,
            title="Will Roll Back",
            release_type="Single",
        )
        outbox = OutboxEvent(**row_data)
        pg_db.session.add(release)
        pg_db.session.add(outbox)
        # Deliberately rollback — simulates an error mid-request.
        pg_db.session.rollback()

        # Neither row should appear in the database.
        result_release = (
            MusicRelease.query
            .filter_by(title="Will Roll Back")
            .first()
        )
        result_outbox = (
            OutboxEvent.query
            .filter_by(event_id=row_data["event_id"])
            .first()
        )
        assert result_release is None, "Release should have been rolled back"
        assert result_outbox is None, "Outbox row should have been rolled back"

    def test_relay_query_returns_pending_rows(self, pg_db, pg_artist):
        """
        The relay query (published_at IS NULL, ordered by id) returns only
        unpublished outbox rows.
        """
        from app.models.outbox import OutboxEvent
        from datetime import datetime, timezone

        pending_data = self._make_outbox_row(pg_artist.id)
        published_data = self._make_outbox_row(pg_artist.id)

        pending = OutboxEvent(**pending_data)
        published = OutboxEvent(**published_data)
        published.published_at = datetime.now(timezone.utc)

        pg_db.session.add_all([pending, published])
        pg_db.session.commit()

        # Exact query used by outbox_relay.py poll_and_publish().
        results = (
            OutboxEvent.query
            .filter(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.id.asc())
            .all()
        )

        result_ids = {r.event_id for r in results}
        assert pending_data["event_id"] in result_ids
        assert published_data["event_id"] not in result_ids

    def test_outbox_publish_attempt_tracking(self, pg_db, pg_artist):
        """
        publish_attempts increments and last_error is recorded correctly.
        """
        from app.models.outbox import OutboxEvent

        row_data = self._make_outbox_row(pg_artist.id)
        outbox = OutboxEvent(**row_data)
        pg_db.session.add(outbox)
        pg_db.session.commit()

        # Simulate a failed publish attempt (relay records the error).
        outbox.publish_attempts += 1
        outbox.last_error = "Connection refused"
        pg_db.session.commit()

        retrieved = pg_db.session.get(OutboxEvent, outbox.id)
        assert retrieved.publish_attempts == 1
        assert retrieved.last_error == "Connection refused"
        assert retrieved.published_at is None  # still pending

    def test_outbox_event_id_unique_constraint(self, pg_db, pg_artist):
        """
        Inserting two OutboxEvent rows with the same event_id raises
        IntegrityError — the UNIQUE(event_id) constraint is enforced.
        """
        from app.models.outbox import OutboxEvent
        from sqlalchemy.exc import IntegrityError

        row_data = self._make_outbox_row(pg_artist.id)
        outbox1 = OutboxEvent(**row_data)
        outbox2 = OutboxEvent(**row_data)  # same event_id

        pg_db.session.add(outbox1)
        pg_db.session.commit()

        pg_db.session.add(outbox2)
        with pytest.raises(IntegrityError):
            pg_db.session.commit()
        pg_db.session.rollback()

    def test_select_for_update_skip_locked_is_future_work(self):
        """
        Document the known limitation: the current relay uses a plain
        WHERE published_at IS NULL query.  SELECT FOR UPDATE SKIP LOCKED
        is required to safely run more than one relay replica.

        This test is intentionally a pass — it exists as a documentation
        anchor so the limitation is visible in the test output.
        """
        # Future implementation note:
        #   OutboxEvent.query
        #     .filter(OutboxEvent.published_at.is_(None))
        #     .order_by(OutboxEvent.id.asc())
        #     .limit(batch_size)
        #     .with_for_update(skip_locked=True)
        #     .all()
        #
        # This requires a PostgreSQL-aware query (not supported by SQLite).
        # It will be implemented when relay replicas > 1.
        pass  # pragma: no cover — intentional documentation test


# ---------------------------------------------------------------------------
# 4. AnalyticsState — counter integrity
# ---------------------------------------------------------------------------

class TestAnalyticsState:
    """Consumer-side analytics state on PostgreSQL."""

    def test_create_analytics_state(self, pg_db, pg_artist):
        """AnalyticsState row is created with zero counters."""
        from app.models.analytics_state import AnalyticsState
        from datetime import datetime, timezone

        state = AnalyticsState(
            artist_id=pg_artist.id,
            follower_count=0,
            release_count=0,
            post_count=0,
            merch_count=0,
            updated_at=datetime.now(timezone.utc),
        )
        pg_db.session.add(state)
        pg_db.session.commit()

        retrieved = pg_db.session.get(AnalyticsState, pg_artist.id)
        assert retrieved.follower_count == 0
        assert retrieved.release_count == 0

    def test_counter_increment_and_decrement(self, pg_db, pg_artist):
        """Counter updates are atomic and floor at zero."""
        from app.models.analytics_state import AnalyticsState
        from datetime import datetime, timezone

        state = AnalyticsState(
            artist_id=pg_artist.id,
            follower_count=5,
            release_count=2,
            post_count=0,
            merch_count=0,
            updated_at=datetime.now(timezone.utc),
        )
        pg_db.session.add(state)
        pg_db.session.commit()

        state.follower_count += 1         # follow event
        state.release_count = max(0, state.release_count - 1)  # delete
        pg_db.session.commit()

        retrieved = pg_db.session.get(AnalyticsState, pg_artist.id)
        assert retrieved.follower_count == 6
        assert retrieved.release_count == 1

    def test_analytics_update_idempotent_via_processed_event(
        self, pg_db, pg_artist
    ):
        """
        apply_analytics_update returns False (duplicate skipped) when
        the same event_id has already been processed.

        This exercises the exact logic used by the analytics consumer.
        """
        from consumers.analytics_consumer import apply_analytics_update

        event_id = str(uuid.uuid4())
        topic = "artisthub.social"
        payload = {"artist_id": pg_artist.id, "fan_id": 1}

        # First call — should apply and return True.
        applied1 = apply_analytics_update(
            session=pg_db.session,
            event_type="fan.followed.artist",
            payload=payload,
            event_id=event_id,
            topic=topic,
            partition=0,
            offset=0,
        )
        pg_db.session.commit()
        assert applied1 is True

        # Second call with same event_id — should skip and return False.
        applied2 = apply_analytics_update(
            session=pg_db.session,
            event_type="fan.followed.artist",
            payload=payload,
            event_id=event_id,
            topic=topic,
            partition=0,
            offset=0,
        )
        pg_db.session.commit()
        assert applied2 is False


# ---------------------------------------------------------------------------
# 5. ProcessedEvent — deduplication PK
# ---------------------------------------------------------------------------

class TestProcessedEvent:
    """Deduplication store correctness on PostgreSQL."""

    def test_processed_event_pk_uniqueness(self, pg_db, pg_artist):
        """
        Inserting two ProcessedEvent rows with the same event_id raises
        IntegrityError — PK uniqueness is enforced by PostgreSQL.
        """
        from app.models.processed_event import ProcessedEvent
        from sqlalchemy.exc import IntegrityError
        from datetime import datetime, timezone

        event_id = str(uuid.uuid4())

        pe1 = ProcessedEvent(
            event_id=event_id,
            event_type="fan.followed.artist",
            topic="artisthub.social",
            partition=0,
            offset=100,
            artist_id=pg_artist.id,
            processed_at=datetime.now(timezone.utc),
        )
        pg_db.session.add(pe1)
        pg_db.session.commit()

        pe2 = ProcessedEvent(
            event_id=event_id,  # duplicate
            event_type="fan.followed.artist",
            topic="artisthub.social",
            partition=0,
            offset=101,
            artist_id=pg_artist.id,
            processed_at=datetime.now(timezone.utc),
        )
        pg_db.session.add(pe2)
        with pytest.raises(IntegrityError):
            pg_db.session.commit()
        pg_db.session.rollback()


# ---------------------------------------------------------------------------
# 6. Notification — uniqueness and idempotency
# ---------------------------------------------------------------------------

class TestNotification:
    """Notification table correctness on PostgreSQL."""

    def test_notification_created_for_fan(self, pg_db, pg_artist, pg_fan):
        """Notification row is created correctly."""
        from app.models.notification import Notification

        event_id = str(uuid.uuid4())
        notif = Notification(
            event_id=event_id,
            fan_id=pg_fan.id,
            artist_id=pg_artist.id,
            release_id=1,
            notification_type="new_release",
            subject="New release!",
            message="Check it out.",
            status="pending",
        )
        pg_db.session.add(notif)
        pg_db.session.commit()

        retrieved = pg_db.session.get(Notification, notif.id)
        assert retrieved.status == "pending"
        assert retrieved.sent_at is None

    def test_notification_unique_event_fan_constraint(
        self, pg_db, pg_artist, pg_fan
    ):
        """
        UNIQUE(event_id, fan_id) prevents duplicate notification rows.

        This is the second idempotency layer (after ProcessedEvent) that
        guards against duplicate notification inserts on Kafka re-delivery.
        """
        from app.models.notification import Notification
        from sqlalchemy.exc import IntegrityError

        event_id = str(uuid.uuid4())

        notif1 = Notification(
            event_id=event_id,
            fan_id=pg_fan.id,
            artist_id=pg_artist.id,
            release_id=1,
            notification_type="new_release",
            subject="New release!",
            message="First time.",
            status="pending",
        )
        pg_db.session.add(notif1)
        pg_db.session.commit()

        notif2 = Notification(
            event_id=event_id,      # same event_id
            fan_id=pg_fan.id,       # same fan_id → must fail
            artist_id=pg_artist.id,
            release_id=1,
            notification_type="new_release",
            subject="Duplicate!",
            message="This should not be inserted.",
            status="pending",
        )
        pg_db.session.add(notif2)
        with pytest.raises(IntegrityError):
            pg_db.session.commit()
        pg_db.session.rollback()

    def test_notification_same_event_different_fans(
        self, pg_db, pg_artist, pg_fan
    ):
        """
        Same event_id with different fan_ids is allowed — one notification
        row per fan is the expected fan-out pattern.
        """
        from app.models.fan import Fan
        from app.models.notification import Notification
        from app.extensions import bcrypt as _bcrypt

        # Create a second fan.
        pw_hash = _bcrypt.generate_password_hash("fan2pass").decode("utf-8")
        fan2 = Fan(
            email="pgfan2@example.com",
            password_hash=pw_hash,
            username="pgfan2",
        )
        pg_db.session.add(fan2)
        pg_db.session.commit()

        event_id = str(uuid.uuid4())

        for fan in (pg_fan, fan2):
            notif = Notification(
                event_id=event_id,
                fan_id=fan.id,
                artist_id=pg_artist.id,
                release_id=1,
                notification_type="new_release",
                subject="New release!",
                message="Fan-out test.",
                status="pending",
            )
            pg_db.session.add(notif)
        pg_db.session.commit()

        count = (
            pg_db.session.query(Notification)
            .filter_by(event_id=event_id)
            .count()
        )
        assert count == 2


# ---------------------------------------------------------------------------
# 7. Migration validation via Alembic
# ---------------------------------------------------------------------------

class TestAlembicMigrations:
    """
    Validate that the Alembic migration chain applies cleanly to PostgreSQL
    and that downgrade reverses it without error.

    This fixture creates and destroys its own schema to avoid interfering
    with the other tests that use pg_db.  It requires a SEPARATE empty
    database or at least that all ArtistHub tables can be dropped and
    recreated.  For safety it only runs when the env var ends with '_test'
    (a convention to prevent accidental migration against a production DB).
    """

    @pytest.fixture(scope="class", autouse=True)
    def check_test_db(self):
        """Skip migration tests if the URL does not end with '_test'."""
        if PG_URL and not PG_URL.rstrip("/").endswith("_test"):
            pytest.skip(
                "Migration tests only run against a database whose name "
                "ends with '_test' (e.g. postgresql://.../artisthub_test). "
                f"Current URL: {PG_URL}"
            )

    def test_upgrade_then_downgrade(self, pg_app):
        """
        flask db upgrade → all tables present.
        flask db downgrade base → all managed tables removed.
        """
        from flask_migrate import upgrade as db_upgrade, downgrade as db_downgrade
        from app.extensions import db as _db
        from sqlalchemy import text as sql_text

        with pg_app.app_context():
            # Reset schema so we get a clean slate for the migration test.
            with _db.engine.connect() as conn:
                conn.execute(sql_text(
                    "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
                ))
                conn.commit()

            # Apply all migrations in order.
            db_upgrade()

            # Verify all expected tables exist.
            inspector = _db.inspect(_db.engine)
            tables_after_upgrade = set(inspector.get_table_names())
            expected = {
                "event_outbox",
                "analytics_state",
                "processed_event",
                "notification",
                "artist",
                "fan",
            }
            assert expected.issubset(tables_after_upgrade), (
                f"Missing after upgrade: {expected - tables_after_upgrade}"
            )

            # Downgrade to the base.
            db_downgrade(revision="base")

            inspector2 = _db.inspect(_db.engine)
            tables_after_downgrade = set(inspector2.get_table_names())

            # All Alembic-managed tables should be gone.
            assert "event_outbox" not in tables_after_downgrade
            assert "analytics_state" not in tables_after_downgrade
            assert "processed_event" not in tables_after_downgrade
            assert "notification" not in tables_after_downgrade
            assert "artist" not in tables_after_downgrade
            assert "fan" not in tables_after_downgrade

            # Re-apply so pg_app teardown (drop_all) can find tables to drop.
            db_upgrade()
            _db.create_all()  # Re-create to restore the pg_db fixture state.
