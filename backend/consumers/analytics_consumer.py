"""
consumers/analytics_consumer.py

Real-Time Analytics Consumer for ArtistHub — Phase 7D.

Consumer group:   artisthub.analytics.v1
Subscribed topics: artisthub.social, artisthub.catalog, artisthub.identity

What this consumer does
-----------------------
It reads domain events published by the Flask outbox relay and maintains
per-artist engagement counters in the ``analytics_state`` table:

  fan.followed.artist    → follower_count += 1
  fan.unfollowed.artist  → follower_count = max(0, follower_count - 1)
  artist.release.created → release_count += 1
  artist.release.deleted → release_count = max(0, release_count - 1)
  artist.post.created    → post_count += 1
  artist.post.deleted    → post_count = max(0, post_count - 1)
  artist.merch.created   → merch_count += 1
  artist.merch.deleted   → merch_count = max(0, merch_count - 1)

All other event types are logged and skipped safely.

Idempotency
-----------
Every processed event's event_id is stored in the ``processed_event``
table. Before applying any side effect the consumer checks this table.
If the event_id is already present the message is skipped and the Kafka
offset is committed without re-applying the update. The ProcessedEvent
row and the AnalyticsState update are committed in the same transaction.

Offset management
-----------------
- enable.auto.commit = False
- Sequence per message:
    1. Consume message
    2. Deserialise JSON
    3. Validate required envelope fields (event_id, event_type, payload)
    4. Check ProcessedEvent deduplication table
    5. Apply analytics side effect (update AnalyticsState)
    6. Insert ProcessedEvent marker
    7. db.session.commit()  ← database transaction commits here
    8. consumer.commit(message)  ← Kafka offset commits here
  If step 7 fails, step 8 is never reached and the offset is not
  committed. On restart the message is re-delivered.

Error handling
--------------
- JSONDecodeError / missing envelope fields → dead-letter
- Unknown event_type → log INFO, skip (not dead-letter — expected)
- DB exception on analytics update → retry up to MAX_RETRIES with
  exponential backoff; if retries exhausted → dead-letter
- Dead-letter publishes to artisthub.deadletter (JSON, no Avro)
- Dead-letter messages are never re-consumed by this consumer (different
  topic, different consumer group)

Serialisation
-------------
Phase 7D processes Phase 7C JSON payloads. Avro/Schema Registry
enforcement is Phase 7F and does not affect this consumer's correctness.

Configuration (environment variables)
--------------------------------------
KAFKA_BOOTSTRAP_SERVERS  Comma-separated broker list (localhost:9092)
KAFKA_SECURITY_PROTOCOL  PLAINTEXT (default) or SASL_SSL
KAFKA_SASL_MECHANISM     PLAIN
CONFLUENT_API_KEY        SASL username (Confluent Cloud)
CONFLUENT_API_SECRET     SASL password (Confluent Cloud)
KAFKA_CONSUMER_GROUP     Consumer group (default: artisthub.analytics.v1)
ANALYTICS_MAX_RETRIES    Max DB retries before dead-letter (default: 3)
ANALYTICS_RETRY_BACKOFF  Base retry sleep in seconds (default: 1.0)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Constants                                                             #
# ------------------------------------------------------------------ #

CONSUMER_GROUP: str = os.environ.get(
    "KAFKA_CONSUMER_GROUP", "artisthub.analytics.v1"
)
SUBSCRIBED_TOPICS: list = [
    "artisthub.social",
    "artisthub.catalog",
    "artisthub.identity",
]
DEAD_LETTER_TOPIC: str = "artisthub.deadletter"

MAX_RETRIES: int = int(os.environ.get("ANALYTICS_MAX_RETRIES", "3"))
RETRY_BACKOFF: float = float(
    os.environ.get("ANALYTICS_RETRY_BACKOFF", "1.0")
)

# Event types this consumer handles. All others are ignored.
_HANDLED_EVENT_TYPES: frozenset = frozenset({
    "fan.followed.artist",
    "fan.unfollowed.artist",
    "artist.release.created",
    "artist.release.deleted",
    "artist.post.created",
    "artist.post.deleted",
    "artist.merch.created",
    "artist.merch.deleted",
})

# ------------------------------------------------------------------ #
# Kafka import guard                                                    #
# ------------------------------------------------------------------ #

try:
    from confluent_kafka import (  # type: ignore
        Consumer,
        Producer,
        KafkaException,
        KafkaError,
        TopicPartition,
    )
    _CONFLUENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    Consumer = None  # type: ignore
    Producer = None  # type: ignore
    KafkaException = Exception  # type: ignore
    KafkaError = None  # type: ignore
    TopicPartition = None  # type: ignore
    _CONFLUENT_AVAILABLE = False


# ------------------------------------------------------------------ #
# Configuration builders                                               #
# ------------------------------------------------------------------ #

def _consumer_config() -> dict:
    """
    Build the confluent-kafka Consumer config from environment variables.
    No credentials are hardcoded.
    """
    bootstrap = os.environ.get(
        "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
    )
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")

    config: dict = {
        "bootstrap.servers": bootstrap,
        "group.id": CONSUMER_GROUP,
        # Manual offset commit — we commit only after successful DB write.
        "enable.auto.commit": False,
        # Read from the beginning if no committed offset exists for this
        # group. This ensures we process all events after a fresh deploy.
        "auto.offset.reset": "earliest",
        "security.protocol": protocol,
    }

    if protocol == "SASL_SSL":
        config["sasl.mechanisms"] = os.environ.get(
            "KAFKA_SASL_MECHANISM", "PLAIN"
        )
        config["sasl.username"] = os.environ.get("CONFLUENT_API_KEY", "")
        config["sasl.password"] = os.environ.get("CONFLUENT_API_SECRET", "")

    return config


def _producer_config() -> dict:
    """
    Build the dead-letter Producer config from environment variables.
    """
    bootstrap = os.environ.get(
        "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
    )
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")

    config: dict = {
        "bootstrap.servers": bootstrap,
        "acks": "1",          # single ack for dead-letter (best effort)
        "security.protocol": protocol,
    }

    if protocol == "SASL_SSL":
        config["sasl.mechanisms"] = os.environ.get(
            "KAFKA_SASL_MECHANISM", "PLAIN"
        )
        config["sasl.username"] = os.environ.get("CONFLUENT_API_KEY", "")
        config["sasl.password"] = os.environ.get("CONFLUENT_API_SECRET", "")

    return config


# ------------------------------------------------------------------ #
# Analytics state helpers                                              #
# ------------------------------------------------------------------ #

def _get_or_create_state(session, artist_id: int):
    """
    Return the AnalyticsState row for artist_id, creating it if absent.

    Imported locally to keep the module testable without a running Flask
    app at import time.
    """
    from app.models.analytics_state import AnalyticsState
    row = session.get(AnalyticsState, artist_id)
    if row is None:
        row = AnalyticsState(
            artist_id=artist_id,
            follower_count=0,
            release_count=0,
            post_count=0,
            merch_count=0,
        )
        session.add(row)
    return row


def apply_analytics_update(
    session,
    event_type: str,
    payload: dict,
    event_id: str,
    topic: str,
    partition: int,
    offset: int,
) -> bool:
    """
    Apply the analytics side effect for a single event and record
    the ProcessedEvent deduplication marker.

    Returns True if the event was applied, False if it was a duplicate.

    Steps:
      1. Check deduplication — return False immediately if already seen.
      2. Extract artist_id from payload.
      3. Get or create AnalyticsState row.
      4. Apply the counter delta.
      5. Insert ProcessedEvent row.
      (Caller commits the transaction.)
    """
    from app.models.processed_event import ProcessedEvent

    # Step 1: deduplication check.
    if session.get(ProcessedEvent, event_id) is not None:
        logger.info(
            "Duplicate event skipped | event_id=%s event_type=%s",
            event_id, event_type,
        )
        return False

    # Step 2: extract artist_id.
    artist_id: Optional[int] = payload.get("artist_id")
    if artist_id is None:
        raise ValueError(
            f"Missing artist_id in payload for event_type={event_type}"
        )

    # Step 3: get or create state row.
    state = _get_or_create_state(session, artist_id)

    # Step 4: apply counter delta.
    if event_type == "fan.followed.artist":
        state.follower_count += 1
    elif event_type == "fan.unfollowed.artist":
        state.follower_count = max(0, state.follower_count - 1)
    elif event_type == "artist.release.created":
        state.release_count += 1
    elif event_type == "artist.release.deleted":
        state.release_count = max(0, state.release_count - 1)
    elif event_type == "artist.post.created":
        state.post_count += 1
    elif event_type == "artist.post.deleted":
        state.post_count = max(0, state.post_count - 1)
    elif event_type == "artist.merch.created":
        state.merch_count += 1
    elif event_type == "artist.merch.deleted":
        state.merch_count = max(0, state.merch_count - 1)
    else:
        # Should not reach here — callers filter on _HANDLED_EVENT_TYPES.
        # Guard defensively.
        raise ValueError(f"Unhandled event_type in apply: {event_type}")

    state.updated_at = datetime.now(timezone.utc)

    # Step 5: record processed event for deduplication.
    session.add(ProcessedEvent(
        event_id=event_id,
        event_type=event_type,
        topic=topic,
        partition=partition,
        offset=offset,
        artist_id=artist_id,
    ))

    logger.debug(
        "Analytics updated | artist_id=%d event_type=%s "
        "followers=%d releases=%d posts=%d merch=%d",
        artist_id, event_type,
        state.follower_count, state.release_count,
        state.post_count, state.merch_count,
    )
    return True


# ------------------------------------------------------------------ #
# Message parsing                                                       #
# ------------------------------------------------------------------ #

def parse_message(raw_value: bytes) -> dict:
    """
    Deserialise a raw Kafka message value to a Python dict.

    Raises ValueError on non-JSON or missing required envelope fields.
    """
    try:
        event = json.loads(raw_value.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"JSON decode error: {exc}") from exc

    for field in ("event_id", "event_type", "payload"):
        if field not in event:
            raise ValueError(
                f"Missing required envelope field: '{field}'"
            )
    if not isinstance(event["payload"], dict):
        raise ValueError("'payload' must be a JSON object")

    return event


# ------------------------------------------------------------------ #
# Dead-letter publisher                                                 #
# ------------------------------------------------------------------ #

def publish_dead_letter(
    producer,
    original_topic: str,
    original_partition: int,
    original_offset: int,
    reason: str,
    original_payload: str,
    event_id: Optional[str] = None,
) -> None:
    """
    Publish a dead-letter record to artisthub.deadletter.

    Never raises — a dead-letter failure is logged but does not abort
    the consumer loop. The original message will be retried on restart
    (since the Kafka offset was not committed) unless the dead-letter
    publish itself signals the message should be skipped.
    """
    dl_record = json.dumps({
        "dead_letter_at": (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        ),
        "original_topic": original_topic,
        "original_partition": original_partition,
        "original_offset": original_offset,
        "event_id": event_id,
        "failure_reason": reason,
        "original_payload": original_payload,
    })
    try:
        producer.produce(
            topic=DEAD_LETTER_TOPIC,
            value=dl_record.encode("utf-8"),
            key=(event_id or "unknown").encode("utf-8"),
        )
        producer.flush(timeout=10.0)
        logger.warning(
            "Dead-letter published | topic=%s partition=%d offset=%d "
            "reason=%s",
            original_topic, original_partition, original_offset, reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to publish dead-letter | topic=%s partition=%d "
            "offset=%d err=%s",
            original_topic, original_partition, original_offset, exc,
        )


# ------------------------------------------------------------------ #
# Per-message processing                                               #
# ------------------------------------------------------------------ #

def process_message(
    flask_app,
    msg,
    dl_producer,
) -> bool:
    """
    Process a single Kafka message.

    Returns True if the Kafka offset should be committed (message was
    handled — either processed, deduplicated, or dead-lettered).
    Returns False if processing failed transiently and the offset must
    NOT be committed so the message is retried.

    The caller is responsible for committing the Kafka offset when this
    returns True.
    """
    from app.extensions import db

    topic = msg.topic()
    partition = msg.partition()
    offset = msg.offset()
    raw = msg.value()
    raw_str = raw.decode("utf-8", errors="replace") if raw else ""

    # ---- Step 1–3: Deserialise and validate --------------------------
    try:
        event = parse_message(raw)
    except ValueError as exc:
        logger.error(
            "Malformed message | topic=%s partition=%d offset=%d err=%s",
            topic, partition, offset, exc,
        )
        publish_dead_letter(
            dl_producer, topic, partition, offset,
            reason=str(exc),
            original_payload=raw_str,
        )
        return True  # dead-lettered; commit offset to move past it

    event_id = event["event_id"]
    event_type = event["event_type"]
    payload = event["payload"]

    # ---- Step 4 (pre-check): skip unsupported event types -----------
    if event_type not in _HANDLED_EVENT_TYPES:
        logger.info(
            "Unsupported event_type skipped | event_type=%s "
            "topic=%s partition=%d offset=%d",
            event_type, topic, partition, offset,
        )
        return True  # commit offset; not an error

    # ---- Steps 4–7: apply with retry on DB failure ------------------
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with flask_app.app_context():
                applied = apply_analytics_update(
                    db.session,
                    event_type=event_type,
                    payload=payload,
                    event_id=event_id,
                    topic=topic,
                    partition=partition,
                    offset=offset,
                )
                db.session.commit()   # Step 7: DB transaction commits here

            if applied:
                logger.info(
                    "Event processed | event_id=%s event_type=%s "
                    "topic=%s partition=%d offset=%d",
                    event_id, event_type, topic, partition, offset,
                )
            return True  # Step 8: caller will commit Kafka offset

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            try:
                with flask_app.app_context():
                    db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "DB error processing event (attempt %d/%d) | "
                "event_id=%s event_type=%s err=%s",
                attempt, MAX_RETRIES, event_id, event_type, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))

    # All retries exhausted — dead-letter the message.
    logger.error(
        "Retries exhausted for event | event_id=%s event_type=%s err=%s",
        event_id, event_type, last_exc,
    )
    publish_dead_letter(
        dl_producer, topic, partition, offset,
        reason=f"DB retries exhausted: {last_exc}",
        original_payload=raw_str,
        event_id=event_id,
    )
    return True  # commit offset; message is in dead-letter


# ------------------------------------------------------------------ #
# Main consumer loop                                                   #
# ------------------------------------------------------------------ #

def run(
    poll_timeout: float = 1.0,
    _consumer=None,
    _dl_producer=None,
) -> None:
    """
    Main analytics consumer loop.

    Runs until interrupted (KeyboardInterrupt / SIGTERM).

    Args:
        poll_timeout:  Seconds to wait for a new message per poll call.
        _consumer:     Inject a consumer instance (for testing).
        _dl_producer:  Inject a dead-letter producer (for testing).
    """
    if not _CONFLUENT_AVAILABLE and _consumer is None:
        raise RuntimeError(
            "confluent-kafka is not installed. "
            "Run: pip install confluent-kafka"
        )  # pragma: no cover

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from app import create_app
    flask_app = create_app()

    consumer = _consumer or Consumer(_consumer_config())
    dl_producer = _dl_producer or Producer(_producer_config())

    consumer.subscribe(SUBSCRIBED_TOPICS)
    logger.info(
        "Analytics consumer started | group=%s topics=%s",
        CONSUMER_GROUP, SUBSCRIBED_TOPICS,
    )

    try:
        while True:
            msg = consumer.poll(timeout=poll_timeout)
            if msg is None:
                continue

            if msg.error():
                # Partition EOF is informational — not an error.
                if (
                    KafkaError is not None
                    and msg.error().code() == KafkaError._PARTITION_EOF
                ):
                    logger.debug(
                        "Partition EOF | topic=%s partition=%d",
                        msg.topic(), msg.partition(),
                    )
                    continue
                logger.error(
                    "Kafka consumer error | err=%s", msg.error()
                )
                continue

            # Process the message; commit offset only on success.
            should_commit = process_message(flask_app, msg, dl_producer)
            if should_commit:
                # Step 8: manual offset commit after successful DB write.
                consumer.commit(message=msg)

    except KeyboardInterrupt:
        logger.info("Analytics consumer stopping (KeyboardInterrupt).")
    finally:
        consumer.close()
        dl_producer.flush(timeout=5.0)
        logger.info("Analytics consumer stopped.")
