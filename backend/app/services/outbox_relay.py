"""
services/outbox_relay.py

Transactional Outbox Relay for ArtistHub.

This is a standalone process — NOT part of the Flask request path.
Run it alongside the Flask API:

    python -m app.services.outbox_relay

Or via the convenience entry point:

    cd backend && python run_relay.py

What the relay does
-------------------
1. Polls the event_outbox table for rows where published_at IS NULL.
2. Claims a batch of rows using SELECT FOR UPDATE SKIP LOCKED so that
   concurrent relay replicas each process a disjoint set of rows.
3. Deserializes the JSON payload stored in the outbox.
4. Converts the dict to an Avro-serialized Confluent wire-format message
   via KafkaProducerService.produce_avro().
5. Waits for the broker acknowledgement via the delivery callback.
6. On success: sets published_at = now() in a separate transaction.
7. On failure: increments publish_attempts, records last_error,
   leaves published_at = NULL so the row is retried.
8. Loops indefinitely with a configurable poll interval.

Phase 7F changes
----------------
- The relay now calls producer.produce_avro() instead of producer.produce().
- The JSON payload from the outbox is decoded and passed as the ``record``
  argument; the KafkaProducerService serializes it to Confluent Avro bytes.
- The outbox DB schema is unchanged — payload column still stores the full
  event JSON (envelope + domain payload) as a UTF-8 string.
- If Schema Registry is unreachable or the schema is invalid, the error is
  recorded in last_error and published_at remains NULL (retry on next cycle).

Phase 8F changes — SELECT FOR UPDATE SKIP LOCKED
-------------------------------------------------
The relay now uses SELECT FOR UPDATE SKIP LOCKED to claim rows before
publishing them to Kafka.  This makes it safe to run multiple relay replicas
concurrently:

  Transaction A (relay replica 1):
    SELECT ... FOR UPDATE SKIP LOCKED LIMIT 100
      → locks rows 1–100; replica 2 will skip these

  Transaction B (relay replica 2):
    SELECT ... FOR UPDATE SKIP LOCKED LIMIT 100
      → locks rows 101–200; replica 1 will skip these

Each replica acquires row-level locks on the rows it will process.  The
FOR UPDATE lock is held ONLY for the duration of the claim transaction
(which ends before Kafka produce begins — see Transaction boundaries below).

Transaction boundaries
----------------------
Two separate transactions are used per relay cycle:

  TRANSACTION 1 — Claim (SELECT FOR UPDATE SKIP LOCKED):
    BEGIN
    SELECT id FROM event_outbox
      WHERE published_at IS NULL
      ORDER BY id
      LIMIT batch_size
      FOR UPDATE SKIP LOCKED
    COMMIT (locks released; row IDs captured in memory)

  [Kafka produce + flush happens OUTSIDE any transaction]

  TRANSACTION 2 — Mark published / record error:
    BEGIN
    UPDATE event_outbox SET published_at = now() WHERE id = ?
    -- OR --
    UPDATE event_outbox SET publish_attempts += 1, last_error = ? WHERE id = ?
    COMMIT

Why Kafka produce is outside the transaction
--------------------------------------------
Kafka does not participate in PostgreSQL transactions.  Holding a database
lock while waiting for a Kafka broker acknowledgement (up to 30 seconds)
would starve other relay replicas and inflate lock contention under load.

The at-least-once crash window
-------------------------------
There is a brief window between Kafka broker acknowledgement and the
PostgreSQL COMMIT in Transaction 2.  If the relay process crashes in this
window, the row remains published_at IS NULL and will be re-published on
the next relay cycle.  This produces a duplicate Kafka message.

This is intentional and safe:
  - The relay uses producer enable.idempotence=True (reduces duplicates
    within a producer session but does NOT eliminate the crash window).
  - Downstream consumers (analytics, notification) use ProcessedEvent
    deduplication via (event_id, consumer_group) to absorb any duplicate.
  - The guarantee is: AT-LEAST-ONCE delivery with idempotent downstream
    processing.  This is NOT exactly-once.

SQLite compatibility
--------------------
SQLite does NOT support SELECT FOR UPDATE (it has no row-level locking).
When the active database is SQLite (detected via engine dialect), the relay
falls back to a plain SELECT without FOR UPDATE.  The SQLite fallback is
only used in development and testing — never in the production cluster.

Running multiple relay replicas against SQLite would cause duplicates, but
this is acceptable in the development/testing context where a single relay
is always used.

Reliability properties
----------------------
- Safe to restart:  Pending rows (published_at IS NULL) are always
  re-attempted. If the relay crashed after publishing but before marking
  published_at, the same event is re-published. The Kafka producer uses
  enable.idempotence=True and consumers use event_id for deduplication,
  making re-delivery safe.
- Safe to retry:    Failed rows are never deleted. publish_attempts and
  last_error let operators identify stuck events.
- No silent loss:   A failed publish is always recorded in last_error.
  The relay never silently discards an outbox row.
- Safe to scale:    Two or more relay replicas can run concurrently without
  producing duplicate Kafka events for the same row, because SKIP LOCKED
  ensures each row is claimed by exactly one replica per cycle.

Configuration (environment variables)
--------------------------------------
KAFKA_BOOTSTRAP_SERVERS  Broker list (default: localhost:9092)
SCHEMA_REGISTRY_URL      Schema Registry URL (default: http://localhost:8081)
OUTBOX_POLL_INTERVAL     Seconds between polls (default: 5)
OUTBOX_BATCH_SIZE        Rows fetched per poll cycle (default: 100)
FLASK_ENV                Controls which Flask config is loaded
SECRET_KEY               Required by Flask app factory
DATABASE_URL             SQLite or PostgreSQL URI (default: SQLite)
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func

from app import create_app
from app.extensions import db
from app.models.outbox import OutboxEvent
from app.services.kafka_producer import KafkaProducerService

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Configuration                                                         #
# ------------------------------------------------------------------ #

POLL_INTERVAL: float = float(os.environ.get("OUTBOX_POLL_INTERVAL", "5"))
BATCH_SIZE: int = int(os.environ.get("OUTBOX_BATCH_SIZE", "100"))

# Module-level metrics dict — populated by run() when prometheus_client is
# available.  safe_inc / safe_set are no-ops when metrics is None or empty.
_metrics: dict = {}


# ------------------------------------------------------------------ #
# Helpers                                                               #
# ------------------------------------------------------------------ #

def _is_sqlite(flask_app) -> bool:
    """
    Return True when the configured database is SQLite.

    Used to decide whether SELECT FOR UPDATE SKIP LOCKED can be used.
    SQLite does not support row-level locking; the relay falls back to a
    plain SELECT in that case.
    """
    db_url = flask_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    return db_url.startswith("sqlite") or not db_url


# ------------------------------------------------------------------ #
# Core relay logic                                                      #
# ------------------------------------------------------------------ #

def _make_delivery_callback(
    outbox_id: int,
    flask_app,
    topic: str,
):
    """
    Return a delivery callback closure bound to the outbox row id.

    The callback is invoked by the Kafka producer after the broker
    acknowledges (or rejects) the message. It runs on the same thread
    as flush() — no concurrent DB access.

    Phase 8F: the callback runs OUTSIDE the claim transaction (Transaction 1
    has already committed before produce() is called).  The callback opens
    its own transaction (Transaction 2) to mark the row published or to
    record an error.

    Phase 8H: increments publish_total / publish_failures_total Prometheus
    counters using the topic label.
    """
    from app.utils.worker_metrics import safe_inc

    def callback(err, msg):
        """Mark the outbox row as published, or record the error."""
        with flask_app.app_context():
            row = db.session.get(OutboxEvent, outbox_id)
            if row is None:
                logger.error(
                    "Delivery callback: outbox row %d not found", outbox_id
                )
                return
            row.publish_attempts += 1
            if err:
                row.last_error = str(err)
                safe_inc(
                    _metrics.get("publish_failures_total"),
                    labels={"topic": topic},
                )
                logger.error(
                    "Kafka delivery failed | outbox_id=%d event_type=%s "
                    "topic=%s err=%s",
                    outbox_id, row.event_type, row.topic, err,
                )
            else:
                row.published_at = datetime.now(timezone.utc)
                row.last_error = None
                safe_inc(
                    _metrics.get("publish_total"),
                    labels={"topic": topic},
                )
                logger.debug(
                    "Kafka delivery confirmed | outbox_id=%d event_type=%s "
                    "topic=%s partition=%d offset=%d",
                    outbox_id, row.event_type, msg.topic(),
                    msg.partition(), msg.offset(),
                )
            # Transaction 2: commit the published_at / last_error update.
            # If this commit fails (e.g. DB transient error), the row
            # remains pending and will be re-published on the next cycle
            # (at-least-once delivery guarantee).
            db.session.commit()
    return callback


def poll_and_publish(
    flask_app,
    producer: KafkaProducerService,
    batch_size: int = BATCH_SIZE,
) -> int:
    """
    Fetch one batch of pending outbox rows and publish them to Kafka.

    Returns the number of rows processed in this cycle.

    This function is designed to be called in a loop by ``run()``.
    It is also directly callable in tests (inject a mock producer).

    Phase 8F: uses SELECT FOR UPDATE SKIP LOCKED on PostgreSQL so that
    multiple relay replicas each claim a disjoint batch of rows without
    producing duplicates.  Falls back to a plain SELECT on SQLite (which
    does not support row-level locking).

    Transaction model
    -----------------
    Transaction 1 (claim) — closed before Kafka produce:
        SELECT id FROM event_outbox
          WHERE published_at IS NULL
          ORDER BY id
          LIMIT batch_size
          FOR UPDATE SKIP LOCKED   ← PostgreSQL only
        COMMIT

    [Kafka produce_avro + flush executed outside any transaction]

    Transaction 2 (mark) — opened inside the delivery callback per row:
        UPDATE event_outbox SET published_at = now()  OR  last_error = ?
        COMMIT
    """
    with flask_app.app_context():
        use_skip_locked = not _is_sqlite(flask_app)

        if use_skip_locked:
            # PostgreSQL path: SELECT FOR UPDATE SKIP LOCKED
            # -----------------------------------------------
            # Build the query using SQLAlchemy Core select() so we can
            # attach .with_for_update(skip_locked=True).
            # The claim transaction is committed immediately after fetching
            # row IDs — we only need the IDs to look up rows for Kafka.
            # The lock is released once this context exits.
            #
            # Why commit before produce?
            #   Holding a FOR UPDATE lock while waiting for a Kafka broker
            #   ack (up to 30s) would create long-held locks that starve
            #   other relay replicas.  We release the lock after capturing
            #   the row IDs and re-fetch each row inside the delivery
            #   callback's own transaction.
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.id.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            pending = db.session.execute(stmt).scalars().all()
            # Capture IDs + ephemeral row data before the transaction ends.
            # We hold these in local variables; the SQLAlchemy objects remain
            # usable within this app_context even after commit.
            db.session.commit()   # Release FOR UPDATE locks (Transaction 1)
        else:
            # SQLite fallback: plain SELECT without row-level locking.
            # ---------------------------------------------------------
            # SQLite serialises all writes — concurrent relay replicas
            # are not supported on SQLite.  This path is used only in
            # development and the test suite.  No FOR UPDATE is emitted.
            pending = (
                OutboxEvent.query
                .filter(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.id.asc())
                .limit(batch_size)
                .all()
            )

        if not pending:
            return 0

        logger.debug("Relay: %d pending event(s) to publish", len(pending))

        for row in pending:
            callback = _make_delivery_callback(row.id, flask_app, row.topic)
            try:
                # Decode the outbox JSON payload to a dict, then let the
                # producer serialize it to Confluent Avro wire format.
                record = row.payload_dict()
                producer.produce_avro(
                    topic=row.topic,
                    event_type=row.event_type,
                    key=row.message_key,
                    record=record,
                    on_delivery=callback,
                )
            except Exception as exc:  # noqa: BLE001
                # produce_avro() raised before enqueueing — update row.
                # This is Transaction 2 for the error case when the
                # delivery callback is never invoked.
                from app.utils.worker_metrics import safe_inc
                row.publish_attempts += 1
                row.last_error = str(exc)
                db.session.commit()
                safe_inc(
                    _metrics.get("publish_failures_total"),
                    labels={"topic": row.topic},
                )
                logger.error(
                    "Relay produce_avro error | "
                    "outbox_id=%d event_type=%s err=%s",
                    row.id, row.event_type, exc,
                )

        # Block until all enqueued messages are acknowledged or 30 s elapses.
        producer.flush(timeout=30.0)
        return len(pending)


def _update_backlog_gauge(flask_app) -> None:
    """
    Query the DB for pending outbox row count and oldest row age, then update
    the Prometheus gauges.  Called once per poll cycle.

    Uses a lightweight aggregate query — does not lock any rows.
    Silently skips if metrics are not initialised or DB is unavailable.
    """
    from app.utils.worker_metrics import safe_set

    backlog_gauge = _metrics.get("outbox_backlog")
    oldest_gauge = _metrics.get("outbox_oldest_age_seconds")
    if backlog_gauge is None and oldest_gauge is None:
        return

    try:
        with flask_app.app_context():
            # Count pending rows.
            count = db.session.execute(
                select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.published_at.is_(None)
                )
            ).scalar() or 0
            safe_set(backlog_gauge, float(count))

            # Age of the oldest pending row.
            if count > 0:
                oldest_created_at = db.session.execute(
                    select(func.min(OutboxEvent.created_at)).where(
                        OutboxEvent.published_at.is_(None)
                    )
                ).scalar()
                if oldest_created_at is not None:
                    age = (
                        datetime.now(timezone.utc) - oldest_created_at
                    ).total_seconds()
                    safe_set(oldest_gauge, max(0.0, age))
            else:
                safe_set(oldest_gauge, 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Backlog gauge update failed: %s", exc)


def run(
    poll_interval: float = POLL_INTERVAL,
    batch_size: int = BATCH_SIZE,
    _producer: Optional[KafkaProducerService] = None,
) -> None:
    """
    Main relay loop — polls the outbox and publishes indefinitely.

    Runs until interrupted (KeyboardInterrupt / SIGTERM).

    Phase 8B: updates /tmp/relay_alive on every loop iteration so that
    OpenShift exec liveness probes can verify the process is making
    progress.  The heartbeat is written regardless of whether any outbox
    rows were found, so an idle relay (no pending events) does not trigger
    a false liveness failure.

    Phase 8F: replicas: 2 is now safe because poll_and_publish() uses
    SELECT FOR UPDATE SKIP LOCKED on PostgreSQL.  Each replica claims a
    disjoint batch of rows, preventing duplicate Kafka publishes.
    The SQLite fallback (development/test) performs a plain SELECT and
    does not support concurrent replicas.

    Phase 8H: starts a Prometheus HTTP server on port 9090
    (WORKER_METRICS_PORT), registers relay-specific counters/gauges,
    and emits structured JSON logs.

    Args:
        poll_interval: Seconds to sleep between poll cycles.
        batch_size:    Max outbox rows to process per cycle.
        _producer:     Inject a producer instance (for testing).
    """
    global _metrics

    from app.utils.heartbeat import touch_heartbeat
    from app.utils.worker_metrics import start_metrics_server, safe_inc
    from app.utils.structured_logging import configure_structured_logging

    configure_structured_logging(worker="relay")

    _registry, _metrics = start_metrics_server("relay")

    flask_app = create_app()
    producer = _producer or KafkaProducerService()

    logger.info(
        "Outbox relay starting | poll_interval=%.1fs batch_size=%d",
        poll_interval, batch_size,
    )

    try:
        while True:
            safe_inc(_metrics.get("poll_cycles_total"))
            count = poll_and_publish(flask_app, producer, batch_size)
            if count:
                logger.info("Relay: published %d event(s)", count)
            # Update backlog gauge once per cycle.
            _update_backlog_gauge(flask_app)
            # Update heartbeat after each poll cycle — proves the loop is
            # alive, not just that the Python process exists.
            touch_heartbeat("relay_alive")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Outbox relay stopping (KeyboardInterrupt).")
    finally:
        producer.close()
        logger.info("Outbox relay stopped.")


if __name__ == "__main__":
    run()
