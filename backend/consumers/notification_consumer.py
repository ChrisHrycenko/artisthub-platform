"""
consumers/notification_consumer.py

Notification Consumer for ArtistHub — Phase 7E.

Consumer group:    artisthub.notifications.v1
Subscribed topics: artisthub.catalog

What this consumer does
-----------------------
It reads domain events from the catalog topic and creates per-fan
notification work items in the ``notification`` table.

Currently handled:
  artist.release.created → query artist's followers, create one
                           Notification row per follower with
                           status='pending'

All other event types are logged and safely skipped.

Phase 7E does NOT dispatch real notifications. The notification rows
are the durable work queue. A future delivery worker (Phase 7F+) will
read pending rows and send email/push/SMS.

Idempotency
-----------
Two layers prevent duplicate notifications on Kafka re-delivery:

  1. ProcessedEvent table (shared with the analytics consumer):
     - event_id PK — if the row already exists, the event was already
       handled; skip immediately.

  2. Notification table UNIQUE(event_id, fan_id) constraint:
     - Prevents duplicate notification rows at the DB level even if
       the ProcessedEvent check somehow races (defence in depth).

Both the ProcessedEvent insert and all Notification inserts for a
given event are committed in the same db.session.commit(), so a partial
failure leaves no orphan rows.

Offset management
-----------------
- enable.auto.commit = False
- Sequence per message:
    1. Consume message
    2. Deserialise JSON
    3. Validate required envelope fields
    4. Check ProcessedEvent deduplication
    5. Query followers for artist_id
    6. Build Notification rows (one per follower)
    7. Insert ProcessedEvent marker
    8. db.session.commit()   ← DB transaction commits here
    9. consumer.commit()     ← Kafka offset commits here
  If step 8 fails, step 9 is never reached.

Error handling
--------------
- Malformed JSON / missing envelope fields → dead-letter
- Missing required payload field (artist_id, release_id, title) → dead-letter
- DB exception → retry up to MAX_RETRIES with exponential backoff;
  on exhaustion → dead-letter
- Unknown event_type → log INFO, skip (not dead-letter)
- No followers → successful no-op (zero notification rows, ProcessedEvent
  row still written to prevent repeated follower queries)

Dead-letter topic: artisthub.deadletter
  Payload includes: original_topic, original_partition, original_offset,
  event_id (if available), failure_reason, original_payload.

Serialisation
-------------
Phase 7E processes Phase 7C JSON payloads.
Avro/Schema Registry enforcement is Phase 7F.

Configuration (environment variables)
--------------------------------------
KAFKA_BOOTSTRAP_SERVERS   Broker list (default: localhost:9092)
KAFKA_SECURITY_PROTOCOL   PLAINTEXT (default) or SASL_SSL
KAFKA_SASL_MECHANISM      PLAIN
CONFLUENT_API_KEY         SASL username (Confluent Cloud)
CONFLUENT_API_SECRET      SASL password (Confluent Cloud)
NOTIF_CONSUMER_GROUP      Consumer group (default: artisthub.notifications.v1)
NOTIF_MAX_RETRIES         Max DB retries before dead-letter (default: 3)
NOTIF_RETRY_BACKOFF       Base retry sleep in seconds (default: 1.0)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Constants                                                             #
# ------------------------------------------------------------------ #

CONSUMER_GROUP: str = os.environ.get(
    "NOTIF_CONSUMER_GROUP", "artisthub.notifications.v1"
)
SUBSCRIBED_TOPICS: list = ["artisthub.catalog"]
DEAD_LETTER_TOPIC: str = "artisthub.deadletter"

MAX_RETRIES: int = int(os.environ.get("NOTIF_MAX_RETRIES", "3"))
RETRY_BACKOFF: float = float(os.environ.get("NOTIF_RETRY_BACKOFF", "1.0"))

# Event types this consumer handles.
_HANDLED_EVENT_TYPES: frozenset = frozenset({"artist.release.created"})

# ------------------------------------------------------------------ #
# Kafka import guard                                                    #
# ------------------------------------------------------------------ #

try:
    from confluent_kafka import (  # type: ignore
        Consumer,
        Producer,
        KafkaError,
    )
    _CONFLUENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    Consumer = None  # type: ignore
    Producer = None  # type: ignore
    KafkaError = None  # type: ignore
    _CONFLUENT_AVAILABLE = False


# ------------------------------------------------------------------ #
# Configuration builders                                               #
# ------------------------------------------------------------------ #

def _consumer_config() -> dict:
    """Build Consumer config from environment variables."""
    bootstrap = os.environ.get(
        "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
    )
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    config: dict = {
        "bootstrap.servers": bootstrap,
        "group.id": CONSUMER_GROUP,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
        "security.protocol": protocol,
    }
    if protocol == "SASL_SSL":
        config["sasl.mechanisms"] = os.environ.get(
            "KAFKA_SASL_MECHANISM", "PLAIN"
        )
        config["sasl.username"] = os.environ.get("CONFLUENT_API_KEY", "")
        config["sasl.password"] = os.environ.get(
            "CONFLUENT_API_SECRET", ""
        )
    return config


def _producer_config() -> dict:
    """Build dead-letter Producer config from environment variables."""
    bootstrap = os.environ.get(
        "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
    )
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    config: dict = {
        "bootstrap.servers": bootstrap,
        "acks": "1",
        "security.protocol": protocol,
    }
    if protocol == "SASL_SSL":
        config["sasl.mechanisms"] = os.environ.get(
            "KAFKA_SASL_MECHANISM", "PLAIN"
        )
        config["sasl.username"] = os.environ.get("CONFLUENT_API_KEY", "")
        config["sasl.password"] = os.environ.get(
            "CONFLUENT_API_SECRET", ""
        )
    return config


# ------------------------------------------------------------------ #
# Message parsing                                                       #
# ------------------------------------------------------------------ #

def parse_message(raw_value: bytes) -> dict:
    """
    Deserialise a raw Kafka message value to a Python dict.

    Raises ValueError on non-JSON or missing required envelope fields.
    This function is identical in contract to the analytics consumer's
    parse_message; it is re-implemented here to keep consumers fully
    independent (no shared module coupling).
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

    Never raises — a dead-letter publish failure is logged but does not
    abort the consumer loop or the message offset commit decision.
    """
    dl_record = json.dumps({
        "dead_letter_at": (
            datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ) + "Z"
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
            "Dead-letter published | topic=%s partition=%d "
            "offset=%d reason=%s",
            original_topic, original_partition, original_offset, reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to publish dead-letter | topic=%s "
            "partition=%d offset=%d err=%s",
            original_topic, original_partition, original_offset, exc,
        )


# ------------------------------------------------------------------ #
# Follower query                                                        #
# ------------------------------------------------------------------ #

def get_follower_ids(session, artist_id: int) -> List[int]:
    """
    Return the list of fan_id values currently following artist_id.

    Uses the Follow model rather than a raw query to stay consistent
    with the ORM layer.
    """
    from app.models.follow import Follow
    rows = (
        session.query(Follow.fan_id)
        .filter(Follow.artist_id == artist_id)
        .all()
    )
    return [r.fan_id for r in rows]


# ------------------------------------------------------------------ #
# Notification builder                                                  #
# ------------------------------------------------------------------ #

def build_notifications(
    event_id: str,
    artist_id: int,
    release_id: int,
    release_title: str,
    fan_ids: List[int],
) -> List:
    """
    Build a list of Notification model instances for a release event.

    One row is created per fan in fan_ids. The subject and message are
    generated from the event payload; real formatting templates are
    Phase 7F+ scope.
    """
    from app.models.notification import Notification
    notifications = []
    subject = f"New release from artist #{artist_id}: {release_title}"
    message = (
        f"Artist #{artist_id} just released '{release_title}' "
        f"(release_id={release_id}). Check it out!"
    )
    for fan_id in fan_ids:
        notifications.append(Notification(
            event_id=event_id,
            fan_id=fan_id,
            artist_id=artist_id,
            release_id=release_id,
            notification_type="new_release",
            subject=subject,
            message=message,
            status="pending",
        ))
    return notifications


# ------------------------------------------------------------------ #
# Core processing                                                       #
# ------------------------------------------------------------------ #

def process_release_created(
    session,
    event: dict,
    topic: str,
    partition: int,
    offset: int,
) -> bool:
    """
    Handle an ``artist.release.created`` event.

    Returns True if the event was processed (new) or was a duplicate.
    Returns False if a required payload field is missing (caller will
    dead-letter the message).

    Steps:
      1. Deduplication check via ProcessedEvent.
      2. Extract required payload fields.
      3. Query followers.
      4. Build Notification rows.
      5. Insert ProcessedEvent marker.
      (Caller commits the transaction.)
    """
    from app.models.processed_event import ProcessedEvent

    event_id = event["event_id"]
    payload = event["payload"]

    # Step 1: deduplication.
    if session.get(ProcessedEvent, event_id) is not None:
        logger.info(
            "Duplicate event skipped | event_id=%s event_type=%s",
            event_id, event["event_type"],
        )
        return True  # already handled

    # Step 2: extract payload fields.
    artist_id = payload.get("artist_id")
    release_id = payload.get("release_id")
    release_title = payload.get("title", "")

    if artist_id is None or release_id is None:
        return False  # caller will dead-letter

    # Step 3: query followers.
    fan_ids = get_follower_ids(session, artist_id)
    logger.info(
        "Processing release.created | event_id=%s artist_id=%d "
        "release_id=%d followers=%d",
        event_id, artist_id, release_id, len(fan_ids),
    )

    # Step 4: create notification rows.
    if fan_ids:
        notifications = build_notifications(
            event_id=event_id,
            artist_id=artist_id,
            release_id=release_id,
            release_title=release_title,
            fan_ids=fan_ids,
        )
        for notif in notifications:
            session.add(notif)

    # Step 5: record deduplication marker.
    session.add(ProcessedEvent(
        event_id=event_id,
        event_type="artist.release.created",
        topic=topic,
        partition=partition,
        offset=offset,
        artist_id=artist_id,
    ))

    return True


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

    Returns True  → caller should commit the Kafka offset.
    Returns False → DB failure not dead-lettered; do NOT commit offset.
    """
    from app.extensions import db

    topic = msg.topic()
    partition = msg.partition()
    offset = msg.offset()
    raw = msg.value()
    raw_str = raw.decode("utf-8", errors="replace") if raw else ""

    # ---- Deserialise and validate -----------------------------------
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
        return True  # dead-lettered; commit offset

    event_id = event["event_id"]
    event_type = event["event_type"]

    # ---- Skip unsupported event types --------------------------------
    if event_type not in _HANDLED_EVENT_TYPES:
        logger.info(
            "Unsupported event_type skipped | event_type=%s "
            "topic=%s partition=%d offset=%d",
            event_type, topic, partition, offset,
        )
        return True  # commit offset; not an error

    # ---- Process with retry on DB failure ---------------------------
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with flask_app.app_context():
                ok = process_release_created(
                    db.session,
                    event=event,
                    topic=topic,
                    partition=partition,
                    offset=offset,
                )
                if not ok:
                    # Missing required payload fields — dead-letter.
                    db.session.rollback()
                    publish_dead_letter(
                        dl_producer, topic, partition, offset,
                        reason=(
                            "Missing required payload fields "
                            "(artist_id or release_id)"
                        ),
                        original_payload=raw_str,
                        event_id=event_id,
                    )
                    return True

                db.session.commit()  # Step 8: DB transaction

            logger.info(
                "Notification event processed | event_id=%s "
                "event_type=%s topic=%s partition=%d offset=%d",
                event_id, event_type, topic, partition, offset,
            )
            return True  # Step 9: caller commits Kafka offset

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            try:
                with flask_app.app_context():
                    db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "DB error processing notification (attempt %d/%d) | "
                "event_id=%s err=%s",
                attempt, MAX_RETRIES, event_id, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))

    # Retries exhausted.
    logger.error(
        "Retries exhausted for notification event | "
        "event_id=%s err=%s",
        event_id, last_exc,
    )
    publish_dead_letter(
        dl_producer, topic, partition, offset,
        reason=f"DB retries exhausted: {last_exc}",
        original_payload=raw_str,
        event_id=event_id,
    )
    return True  # dead-lettered; commit offset


# ------------------------------------------------------------------ #
# Main consumer loop                                                   #
# ------------------------------------------------------------------ #

def run(
    poll_timeout: float = 1.0,
    _consumer=None,
    _dl_producer=None,
) -> None:
    """
    Main notification consumer loop.

    Runs until interrupted (KeyboardInterrupt / SIGTERM).

    Args:
        poll_timeout:  Seconds to wait per poll call.
        _consumer:     Inject a consumer instance (for testing).
        _dl_producer:  Inject a dead-letter producer (for testing).
    """
    if not _CONFLUENT_AVAILABLE and _consumer is None:
        raise RuntimeError(
            "confluent-kafka is not installed."
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
        "Notification consumer started | group=%s topics=%s",
        CONSUMER_GROUP, SUBSCRIBED_TOPICS,
    )

    try:
        while True:
            msg = consumer.poll(timeout=poll_timeout)
            if msg is None:
                continue
            if msg.error():
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

            should_commit = process_message(flask_app, msg, dl_producer)
            if should_commit:
                consumer.commit(message=msg)

    except KeyboardInterrupt:
        logger.info(
            "Notification consumer stopping (KeyboardInterrupt)."
        )
    finally:
        consumer.close()
        dl_producer.flush(timeout=5.0)
        logger.info("Notification consumer stopped.")
