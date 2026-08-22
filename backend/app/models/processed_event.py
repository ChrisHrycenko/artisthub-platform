"""
models/processed_event.py

Consumer-side deduplication store for Kafka consumers.

Every successfully processed event is recorded here so that re-delivered
Kafka messages (e.g. after a consumer crash-and-restart) do not apply the
same side-effects twice.

Deduplication strategy
----------------------
The PRIMARY KEY is (event_id, consumer_group).  This is intentional:

- event_id (UUID v4) uniquely identifies a single *business event*.
- consumer_group identifies which consumer processed the event.

Using a composite PK allows the analytics consumer and the notification
consumer to each record their own dedup marker for the same event without
one consumer's marker blocking the other.

Before applying any side effect, each consumer calls is_duplicate():
  - Row absent  → event is new for this consumer; process it and insert row.
  - Row present → event was already handled; skip and commit Kafka offset.

The processed-event row and the consumer's side-effects are committed in
the same SQLAlchemy transaction so a partial failure cannot leave the DB
in an inconsistent state.

Retention note
--------------
Processed event rows accumulate indefinitely. A periodic cleanup job
(e.g. DELETE WHERE processed_at < now() - 90d) can be added without any
schema change; the dedup guarantee only needs to cover the Kafka topic
retention window (max 90 days for identity events).
"""

from datetime import datetime
from app.extensions import db


class ProcessedEvent(db.Model):
    """
    Deduplication record for a consumer-processed Kafka event.

    The composite primary key (event_id, consumer_group) ensures that
    the analytics consumer and the notification consumer can each record
    their own dedup marker for the same event without interference.
    """

    __tablename__ = "processed_event"

    # ------------------------------------------------------------------ #
    # Composite primary key                                                #
    # (event_id, consumer_group) — one row per consumer per event.        #
    # ------------------------------------------------------------------ #
    event_id = db.Column(
        db.String(36),
        primary_key=True,
        doc="UUID v4 from the Kafka event envelope.",
    )
    consumer_group = db.Column(
        db.String(100),
        primary_key=True,
        nullable=False,
        doc="Kafka consumer-group ID that processed this event. "
            "Part of the composite PK so each consumer tracks dedup "
            "independently (e.g. artisthub.analytics.v1 and "
            "artisthub.notifications.v1 process the same event "
            "without one blocking the other).",
    )

    # ------------------------------------------------------------------ #
    # Metadata for troubleshooting                                         #
    # ------------------------------------------------------------------ #
    event_type = db.Column(
        db.String(100),
        nullable=False,
        doc="Event type string, e.g. 'fan.followed.artist'.",
    )
    topic = db.Column(
        db.String(255),
        nullable=False,
        doc="Kafka topic from which the event was consumed.",
    )
    partition = db.Column(
        db.Integer,
        nullable=False,
        doc="Kafka partition from which the event was consumed.",
    )
    offset = db.Column(
        db.Integer,
        nullable=False,
        doc="Kafka offset of the consumed message.",
    )
    artist_id = db.Column(
        db.Integer,
        nullable=True,
        doc="Artist the event pertains to, if applicable.",
    )
    processed_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        doc="UTC datetime when the consumer successfully "
            "processed this event.",
    )

    def to_dict(self) -> dict:
        """Serialise the record to a plain dict for logging / debugging."""
        return {
            "event_id": self.event_id,
            "consumer_group": self.consumer_group,
            "event_type": self.event_type,
            "topic": self.topic,
            "partition": self.partition,
            "offset": self.offset,
            "artist_id": self.artist_id,
            "processed_at": self.processed_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<ProcessedEvent event_id={self.event_id!r} "
            f"consumer_group={self.consumer_group!r} "
            f"event_type={self.event_type!r}>"
        )
