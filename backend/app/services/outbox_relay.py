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
2. Publishes each pending row to Kafka using KafkaProducerService.
3. Waits for the broker acknowledgement via the delivery callback.
4. On success: sets published_at = now().
5. On failure: increments publish_attempts, records last_error,
   leaves published_at = NULL so the row is retried.
6. Loops indefinitely with a configurable poll interval.

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

Configuration (environment variables)
--------------------------------------
KAFKA_BOOTSTRAP_SERVERS  Broker list (default: localhost:9092)
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


# ------------------------------------------------------------------ #
# Core relay logic                                                      #
# ------------------------------------------------------------------ #

def _make_delivery_callback(
    outbox_id: int,
    flask_app,
):
    """
    Return a delivery callback closure bound to the outbox row id.

    The callback is invoked by the Kafka producer after the broker
    acknowledges (or rejects) the message. It runs on the same thread
    as flush() — no concurrent DB access.
    """
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
                logger.error(
                    "Kafka delivery failed | outbox_id=%d event_type=%s "
                    "topic=%s err=%s",
                    outbox_id, row.event_type, row.topic, err,
                )
            else:
                row.published_at = datetime.now(timezone.utc)
                row.last_error = None
                logger.debug(
                    "Kafka delivery confirmed | outbox_id=%d event_type=%s "
                    "topic=%s partition=%d offset=%d",
                    outbox_id, row.event_type, msg.topic(),
                    msg.partition(), msg.offset(),
                )
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
    """
    with flask_app.app_context():
        # Fetch up to batch_size rows that have not been published yet.
        # Order by id (insertion order) to preserve per-artist event ordering
        # within a relay cycle — the Kafka message key handles ordering within
        # the topic partition.
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
            callback = _make_delivery_callback(row.id, flask_app)
            try:
                producer.produce(
                    topic=row.topic,
                    key=row.message_key,
                    value=row.payload,
                    on_delivery=callback,
                )
            except Exception as exc:  # noqa: BLE001
                # produce() raised before enqueueing — update row directly.
                row.publish_attempts += 1
                row.last_error = str(exc)
                db.session.commit()
                logger.error(
                    "Relay produce error | outbox_id=%d event_type=%s err=%s",
                    row.id, row.event_type, exc,
                )

        # Block until all enqueued messages are acknowledged or 30 s elapses.
        producer.flush(timeout=30.0)
        return len(pending)


def run(
    poll_interval: float = POLL_INTERVAL,
    batch_size: int = BATCH_SIZE,
    _producer: Optional[KafkaProducerService] = None,
) -> None:
    """
    Main relay loop — polls the outbox and publishes indefinitely.

    Runs until interrupted (KeyboardInterrupt / SIGTERM).

    Args:
        poll_interval: Seconds to sleep between poll cycles.
        batch_size:    Max outbox rows to process per cycle.
        _producer:     Inject a producer instance (for testing).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    flask_app = create_app()
    producer = _producer or KafkaProducerService()

    logger.info(
        "Outbox relay starting | poll_interval=%.1fs batch_size=%d",
        poll_interval, batch_size,
    )

    try:
        while True:
            count = poll_and_publish(flask_app, producer, batch_size)
            if count:
                logger.info("Relay: published %d event(s)", count)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Outbox relay stopping (KeyboardInterrupt).")
    finally:
        producer.close()
        logger.info("Outbox relay stopped.")


if __name__ == "__main__":
    run()
