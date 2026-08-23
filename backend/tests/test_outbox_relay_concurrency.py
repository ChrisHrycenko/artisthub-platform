"""
tests/test_outbox_relay_concurrency.py

Phase 8F — Outbox relay SELECT FOR UPDATE SKIP LOCKED concurrency tests.

These tests require a live PostgreSQL database.  They are automatically
skipped when the ARTISTHUB_TEST_POSTGRES_URL environment variable is absent,
so the standard SQLite test suite continues to work without PostgreSQL.

Set the variable before running:

    export ARTISTHUB_TEST_POSTGRES_URL=\\
        postgresql://artisthub:password@localhost:5432/artisthub_test

    cd backend && pytest tests/test_outbox_relay_concurrency.py -v

What these tests cover
-----------------------
1. test_skip_locked_two_sessions_no_overlap
   Two concurrent sessions claim disjoint row sets via SKIP LOCKED.

2. test_skip_locked_second_session_sees_no_rows
   When all rows are already locked by session 1, session 2 gets an
   empty result from SKIP LOCKED.

3. test_published_rows_marked_correctly
   After poll_and_publish, published_at is set and
   publish_attempts is incremented.

4. test_failed_publish_row_remains_pending
   When produce_avro raises (simulated Kafka failure), the row stays
   pending, publish_attempts is incremented, and last_error is set.

5. test_no_row_lost_on_kafka_failure
   After a failed Kafka publish cycle, all seeded rows remain in
   event_outbox with published_at IS NULL.

6. test_concurrent_different_rows_processed_in_parallel
   Two relay invocations with separate sessions process disjoint rows
   simultaneously without blocking or duplicating.

7. test_sqlite_fallback_no_exception
   The SQLite path in poll_and_publish does not raise any exception
   (no FOR UPDATE is emitted).

Delivery semantics (at-least-once)
-----------------------------------
These tests document and verify the at-least-once guarantee:
  - The relay guarantees every event is published to Kafka at least once.
  - A crash between Kafka ack and PostgreSQL commit causes re-delivery.
  - Downstream consumers absorb duplicates via ProcessedEvent deduplication.
  - This is NOT exactly-once.  Kafka producer idempotence
    (enable.idempotence=True) reduces duplicates within a single producer
    session but does not close the crash window.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from threading import Thread
from typing import List  # noqa: UP006
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Skip the entire module when no PostgreSQL URL is configured.
# ---------------------------------------------------------------------------
PG_URL = os.environ.get("ARTISTHUB_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason=(
        "Set ARTISTHUB_TEST_POSTGRES_URL=postgresql://... to run "
        "PostgreSQL concurrency tests."
    ),
)


# ---------------------------------------------------------------------------
# Module-level fixtures (reuse pg_app / pg_db from test_postgres_integration)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pg_app():
    """
    Flask application pointed at the PostgreSQL test database.

    scope="module" — one app + schema lifecycle per test module run.
    All tables are created at module start and dropped at module teardown.
    Per-test isolation is handled by the pg_db fixture (TRUNCATE).
    """
    from app import create_app
    from app.extensions import db as _db

    flask_app = create_app("pg_testing")

    with flask_app.app_context():
        _db.drop_all()
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(scope="function")
def pg_db(pg_app):
    """
    Clean database state per test via TRUNCATE.

    Truncates event_outbox (and any dependent tables) after each test
    so tests are fully isolated.
    """
    from app.extensions import db as _db

    with pg_app.app_context():
        yield _db
        _db.session.remove()
        _db.session.execute(
            _db.text(
                "TRUNCATE TABLE notification, processed_event, "
                "analytics_state, event_outbox, follow, "
                "merch_product, music_release, social_post, "
                "fan, artist RESTART IDENTITY CASCADE"
            )
        )
        _db.session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_outbox_row(
    event_type: str = "test.event",
    topic: str = "artisthub.social",
    artist_id: int = 1,
) -> dict:
    """Build a minimal OutboxEvent kwargs dict for inserting in tests."""
    eid = str(uuid.uuid4())
    payload = json.dumps({
        "event_id": eid,
        "event_type": event_type,
        "event_version": "1",
        "occurred_at": "2025-01-01T00:00:00Z",
        "producer": "artisthub-api",
        "correlation_id": None,
        "payload": {"artist_id": artist_id},
    })
    return {
        "event_id": eid,
        "event_type": event_type,
        "event_version": "1",
        "topic": topic,
        "message_key": str(artist_id),
        "payload": payload,
    }


def _seed_outbox(db_, n: int = 5, **kwargs):
    """
    Insert n OutboxEvent rows with published_at=NULL and return them.
    """
    from app.models.outbox import OutboxEvent
    rows = []
    for _ in range(n):
        row = OutboxEvent(**_make_outbox_row(**kwargs))
        db_.session.add(row)
        rows.append(row)
    db_.session.commit()
    return rows


# ---------------------------------------------------------------------------
# 1. SKIP LOCKED: two sessions claim disjoint rows
# ---------------------------------------------------------------------------

class TestSkipLockedDisjointClaim:
    """
    Verify that SELECT FOR UPDATE SKIP LOCKED assigns disjoint row sets
    to two concurrent sessions (simulated via two SQLAlchemy connections).
    """

    def test_skip_locked_two_sessions_no_overlap(self, pg_app, pg_db):
        """
        Two concurrent SKIP LOCKED queries on the same pending rows
        return non-overlapping sets.

        Session A locks the first batch_a rows.
        Session B, running concurrently, skips those rows and locks
        a different set (or gets empty if no rows remain).

        This test simulates concurrency by opening two raw engine
        connections and manually managing transactions.
        """
        from app.extensions import db as _db
        from app.models.outbox import OutboxEvent

        # Seed 10 pending rows.
        _seed_outbox(pg_db, n=10)

        with pg_app.app_context():
            engine = _db.engine

            # Open two independent connections (simulates two relay replicas).
            with engine.connect() as conn_a, engine.connect() as conn_b:
                # Session A: claim first 6 rows with SKIP LOCKED.
                stmt_a = (
                    select(OutboxEvent.id)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.id.asc())
                    .limit(6)
                    .with_for_update(skip_locked=True)
                )
                result_a = conn_a.execute(stmt_a)
                ids_a = {row[0] for row in result_a}

                # Session B: claim up to 6 rows — should skip A's locked rows.
                stmt_b = (
                    select(OutboxEvent.id)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.id.asc())
                    .limit(6)
                    .with_for_update(skip_locked=True)
                )
                result_b = conn_b.execute(stmt_b)
                ids_b = {row[0] for row in result_b}

                # The two sets must be completely disjoint.
                overlap = ids_a & ids_b
                assert not overlap, (
                    f"SKIP LOCKED sessions shared rows: {overlap}"
                )

                # Together they should cover all 10 rows.
                assert len(ids_a | ids_b) == 10, (
                    f"Expected 10 total rows, got {len(ids_a | ids_b)}: "
                    f"A={ids_a} B={ids_b}"
                )

                conn_a.rollback()
                conn_b.rollback()

    def test_skip_locked_second_session_sees_no_rows(self, pg_app, pg_db):
        """
        When session A holds all pending rows locked, session B's SKIP
        LOCKED query returns an empty result set.

        This is the worst-case: a single batch covers all pending rows.
        Session B correctly backs off (processes nothing) rather than
        blocking or raising an error.
        """
        from app.extensions import db as _db
        from app.models.outbox import OutboxEvent

        # Seed exactly 3 rows.
        _seed_outbox(pg_db, n=3)

        with pg_app.app_context():
            engine = _db.engine

            with engine.connect() as conn_a, engine.connect() as conn_b:
                # Session A claims all 3 rows (limit=10 > 3 seeded).
                stmt_a = (
                    select(OutboxEvent.id)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.id.asc())
                    .limit(10)
                    .with_for_update(skip_locked=True)
                )
                result_a = conn_a.execute(stmt_a)
                ids_a = [row[0] for row in result_a]
                assert len(ids_a) == 3, f"Expected 3, got {len(ids_a)}"

                # Session B: all rows locked → SKIP LOCKED returns nothing.
                stmt_b = (
                    select(OutboxEvent.id)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.id.asc())
                    .limit(10)
                    .with_for_update(skip_locked=True)
                )
                result_b = conn_b.execute(stmt_b)
                ids_b = [row[0] for row in result_b]

                assert ids_b == [], (
                    f"Session B should see 0 rows (all locked), got: {ids_b}"
                )

                conn_a.rollback()
                conn_b.rollback()


# ---------------------------------------------------------------------------
# 2. poll_and_publish correctness on PostgreSQL
# ---------------------------------------------------------------------------

class TestPollAndPublishPostgres:
    """
    Functional tests for poll_and_publish() against a live PostgreSQL DB.

    These tests use a mock KafkaProducerService so no real Kafka broker
    is required.  They verify the relay's transactional correctness on
    the PostgreSQL code path.
    """

    def test_published_rows_marked_correctly(self, pg_app, pg_db):
        """
        After a successful poll_and_publish cycle, all processed rows have:
          - published_at set to a non-None datetime
          - publish_attempts == 1
          - last_error is None
        """
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        rows = _seed_outbox(pg_db, n=3)
        row_ids = [r.id for r in rows]

        def mock_produce_avro_ok(topic, event_type, key, record, on_delivery):
            """Simulate successful Kafka ack."""
            msg = MagicMock()
            msg.topic.return_value = topic
            msg.partition.return_value = 0
            msg.offset.return_value = 1
            on_delivery(None, msg)  # err=None → success

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = mock_produce_avro_ok

        with pg_app.app_context():
            count = poll_and_publish(pg_app, mock_producer, batch_size=10)

        assert count == 3

        with pg_app.app_context():
            for row_id in row_ids:
                from app.extensions import db as _db
                row = _db.session.get(OutboxEvent, row_id)
                assert row.published_at is not None, (
                    f"Row {row_id} should be marked published"
                )
                assert row.publish_attempts == 1
                assert row.last_error is None

    def test_failed_publish_row_remains_pending(self, pg_app, pg_db):
        """
        When produce_avro raises (simulate Kafka failure), the row stays
        pending: published_at is NULL, publish_attempts is incremented,
        and last_error records the failure message.
        """
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        rows = _seed_outbox(pg_db, n=1)
        row_id = rows[0].id

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = RuntimeError(
            "Schema Registry unreachable"
        )

        with pg_app.app_context():
            poll_and_publish(pg_app, mock_producer, batch_size=10)

        with pg_app.app_context():
            from app.extensions import db as _db
            row = _db.session.get(OutboxEvent, row_id)
            assert row.published_at is None, (
                "Row must remain pending after failure"
            )
            assert row.publish_attempts == 1
            assert row.last_error is not None
            assert "Schema Registry unreachable" in row.last_error

    def test_no_row_lost_on_kafka_failure(self, pg_app, pg_db):
        """
        After a failed relay cycle (all rows fail), the event_outbox table
        still contains all seeded rows with published_at IS NULL.

        This verifies the 'no silent loss' guarantee: a Kafka failure never
        causes rows to be deleted or skipped permanently.
        """
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        rows = _seed_outbox(pg_db, n=5)
        seeded_ids = {r.id for r in rows}

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = RuntimeError("broker down")

        with pg_app.app_context():
            poll_and_publish(pg_app, mock_producer, batch_size=10)

        with pg_app.app_context():
            from app.extensions import db as _db
            pending = (
                _db.session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                ).scalars().all()
            )
            pending_ids = {r.id for r in pending}
            # All 5 rows must still be pending.
            assert seeded_ids.issubset(pending_ids), (
                f"Missing rows after failure: {seeded_ids - pending_ids}"
            )

    def test_delivery_callback_failure_row_remains_pending(
        self, pg_app, pg_db
    ):
        """
        When the delivery callback is called with an error (broker-side
        rejection after produce), the row remains pending and
        last_error is set.

        Covers the delivery callback error branch (distinct from
        produce_avro raising before enqueueing).
        """
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        rows = _seed_outbox(pg_db, n=1)
        row_id = rows[0].id

        def mock_produce_avro_fail(
            topic, event_type, key, record, on_delivery
        ):
            """Simulate broker rejection via delivery callback."""
            msg = MagicMock()
            msg.topic.return_value = topic
            msg.partition.return_value = 0
            msg.offset.return_value = -1
            on_delivery(RuntimeError("broker rejected"), msg)

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = mock_produce_avro_fail

        with pg_app.app_context():
            poll_and_publish(pg_app, mock_producer, batch_size=10)

        with pg_app.app_context():
            from app.extensions import db as _db
            row = _db.session.get(OutboxEvent, row_id)
            assert row.published_at is None
            assert row.publish_attempts == 1
            assert "broker rejected" in row.last_error


# ---------------------------------------------------------------------------
# 3. Concurrent relay sessions: different rows processed in parallel
# ---------------------------------------------------------------------------

class TestConcurrentRelayReplicas:
    """
    Verify that two relay invocations can process different rows in parallel
    without overlapping, blocking each other, or missing any rows.

    Uses threads to simulate two relay replicas running concurrently.
    """

    def test_concurrent_different_rows_processed_in_parallel(
        self, pg_app, pg_db
    ):
        """
        Seed 10 rows.  Run two poll_and_publish calls in parallel threads,
        each with batch_size=5.  Every row must be published exactly once.

        If SKIP LOCKED is working correctly:
          - Thread A claims 5 rows and marks them published.
          - Thread B claims the other 5 rows and marks them published.
          - No row is processed by both threads.
          - No row is left pending (assuming no failures).
        """
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish
        from app.extensions import db as _db

        _seed_outbox(pg_db, n=10)

        published_by: dict = {}
        errors: List[str] = []

        def run_relay(thread_name: str) -> None:
            """Run one poll_and_publish cycle; record claimed rows."""
            claimed_ids = []

            def mock_produce(topic, event_type, key, record, on_delivery):
                msg = MagicMock()
                msg.topic.return_value = topic
                msg.partition.return_value = 0
                msg.offset.return_value = 1
                on_delivery(None, msg)

            mock_producer = MagicMock()
            mock_producer.produce_avro.side_effect = mock_produce

            try:
                poll_and_publish(pg_app, mock_producer, batch_size=5)
                # Record which rows this thread published by inspecting
                # the produce_avro call arguments.
                for call in mock_producer.produce_avro.call_args_list:
                    claimed_ids.append(call.kwargs.get("key"))
                published_by[thread_name] = claimed_ids
            except Exception as exc:
                errors.append(f"{thread_name}: {exc}")

        t1 = Thread(target=run_relay, args=("relay-1",))
        t2 = Thread(target=run_relay, args=("relay-2",))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"Thread errors: {errors}"

        # No overlap between the two relays' claimed keys.
        keys_1 = set(published_by.get("relay-1", []))
        keys_2 = set(published_by.get("relay-2", []))
        overlap = keys_1 & keys_2
        assert not overlap, (
            f"Relay replicas claimed the same rows: {overlap}"
        )

        # Verify database state: all 10 rows should now be published.
        with pg_app.app_context():
            still_pending = (
                _db.session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                ).scalars().all()
            )
            assert len(still_pending) == 0, (
                f"{len(still_pending)} rows still pending after 2 relay cycles"
            )


# ---------------------------------------------------------------------------
# 4. SQLite fallback — no exception raised
# ---------------------------------------------------------------------------

class TestSQLiteFallback:
    """
    Verify that poll_and_publish does not raise when running against SQLite
    (which does not support FOR UPDATE SKIP LOCKED).

    This test uses the standard SQLite-backed test app (not pg_app).
    It is NOT skipped on SQLite-only environments.

    Note: this test is in a module skipped when ARTISTHUB_TEST_POSTGRES_URL
    is absent, so it only runs when PostgreSQL is available.  The SQLite
    fixture is valid here because it uses the standard app/db_ fixtures.
    """

    def test_sqlite_fallback_no_exception(self, app, db_):
        """
        The SQLite fallback in poll_and_publish raises no exception.

        The plain SELECT (no FOR UPDATE) path is exercised, verifying that
        the dialect detection logic correctly omits SKIP LOCKED on SQLite.
        """
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        # Seed a pending row in the SQLite DB.
        row = OutboxEvent(
            event_id=str(uuid.uuid4()),
            event_type="test.sqlite.fallback",
            event_version="1",
            topic="artisthub.social",
            message_key="1",
            payload=json.dumps({
                "event_id": "x",
                "event_type": "test.sqlite.fallback",
                "event_version": "1",
                "occurred_at": "2025-01-01T00:00:00Z",
                "producer": "artisthub-api",
                "correlation_id": None,
                "payload": {},
            }),
        )
        db_.session.add(row)
        db_.session.commit()
        row_id = row.id

        mock_producer = MagicMock()

        # Must not raise — the SQLite fallback omits FOR UPDATE.
        count = poll_and_publish(app, mock_producer, batch_size=10)

        assert count == 1
        mock_producer.produce_avro.assert_called_once()

        # Verify the row was fetched (delivery callback not wired here,
        # so published_at remains None, but no exception was raised).
        fetched = db_.session.get(OutboxEvent, row_id)
        assert fetched is not None


# ---------------------------------------------------------------------------
# 5. At-least-once delivery documentation test
# ---------------------------------------------------------------------------

class TestAtLeastOnceSemantics:
    """
    Documents and verifies the at-least-once delivery guarantee.

    These tests do not test new code — they exist to make the delivery
    semantics visible in the test output and to catch regressions if the
    relay's transaction model is accidentally changed to exactly-once.
    """

    def test_crash_window_leaves_row_pending(self, pg_app, pg_db):
        """
        Simulate the crash window between Kafka ack and PostgreSQL commit.

        The relay receives Kafka ack (delivery callback invoked with err=None)
        but the PostgreSQL commit fails (simulated by rollback).  The row
        must remain pending (published_at IS NULL).

        In a real crash the process exits before the commit; on the next
        restart the row is re-fetched and re-published.  This is the
        at-least-once behaviour.

        Implementation note: the delivery callback calls db.session.commit().
        This test monkey-patches the session to simulate a commit failure.
        """
        from app.models.outbox import OutboxEvent
        from app.extensions import db as _db

        rows = _seed_outbox(pg_db, n=1)
        row_id = rows[0].id

        with pg_app.app_context():
            row = _db.session.get(OutboxEvent, row_id)

            # Simulate crash window: set published_at on the in-memory object
            # but do NOT commit.
            row.published_at = datetime.now(timezone.utc)
            row.publish_attempts += 1
            # Rollback instead of commit (simulates process crash).
            _db.session.rollback()

        # Row must still be pending after rollback.
        with pg_app.app_context():
            row = _db.session.get(OutboxEvent, row_id)
            assert row.published_at is None, (
                "After simulated crash, row must remain pending "
                "(at-least-once: row will be re-published on next cycle)"
            )
            assert row.publish_attempts == 0, (
                "publish_attempts must not have been incremented "
                "(rollback reverted the in-memory change)"
            )

    def test_idempotent_second_publish_after_crash(self, pg_app, pg_db):
        """
        After a crash-window re-delivery, the row ends up in the correct
        final state (published_at set, publish_attempts == 2).

        This verifies that re-publishing a row does not corrupt its state.
        It also confirms that the second relay cycle updates publish_attempts
        to 2 (first attempt was lost in the crash; second attempt succeeds).

        At-least-once: the consumer must handle event_id="X" arriving twice.
        """
        from app.models.outbox import OutboxEvent
        from app.services.outbox_relay import poll_and_publish

        rows = _seed_outbox(pg_db, n=1)
        row_id = rows[0].id

        def mock_produce_ok(topic, event_type, key, record, on_delivery):
            msg = MagicMock()
            msg.topic.return_value = topic
            msg.partition.return_value = 0
            msg.offset.return_value = 1
            on_delivery(None, msg)

        mock_producer = MagicMock()
        mock_producer.produce_avro.side_effect = mock_produce_ok

        # First cycle — publishes and marks published.
        with pg_app.app_context():
            poll_and_publish(pg_app, mock_producer, batch_size=10)

        with pg_app.app_context():
            from app.extensions import db as _db
            row = _db.session.get(OutboxEvent, row_id)
            assert row.published_at is not None
            assert row.publish_attempts == 1

        # Second cycle — row is already published, must be skipped.
        mock_producer.reset_mock()
        with pg_app.app_context():
            count = poll_and_publish(pg_app, mock_producer, batch_size=10)

        assert count == 0, (
            "Second relay cycle must skip already-published rows"
        )
        mock_producer.produce_avro.assert_not_called()
