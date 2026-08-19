"""
models/processed_event.py

Consumer-side deduplication store for the Phase 7D analytics consumer.

Every successfully processed event is recorded here so that re-delivered
Kafka messages (e.g. after a consumer crash-and-restart) do not alter
counters twice.

Deduplication strategy
----------------------
event_id (UUID v4 from the event envelope) is unique per business event.
Before applying any side effect the consumer queries this table:
  - Row absent  → event is new; process it, then insert the row.
  - Row present → event is a duplicate; skip, commit the Kafka offset.

The processed-event row and the analytics state update are committed in
the same SQLAlchemy transaction so a partial failure cannot leave the DB
in an inconsistent state (counter updated but dedup marker absent, or
vice versa).

Retention note
--------------
Processed event rows accumulate indefinitely in Phase 7D. A periodic
cleanup job (e.g. DELETE WHERE processed_at < now() - 90d) can be added
without any schema change; the event_id uniqueness guarantee only needs
to cover the Kafka topic retention window (max 90 days for identity events).
"""

from datetime import datetime
from app.extensions import db


class ProcessedEvent(db.Model):
    """
    Deduplication record for a consumer-processed Kafka event.

    Created atomically with the analytics state update inside the same
    database transaction.
    """

    __tablename__ = "processed_event"

    # ------------------------------------------------------------------ #
    # Primary key — event_id is globally unique across all event types.   #
    # ------------------------------------------------------------------ #
    event_id = db.Column(
        db.String(36),
        primary_key=True,
        doc="UUID v4 from the Kafka event envelope. Primary key ensures "
            "uniqueness at the DB level.",
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
            f"event_type={self.event_type!r}>"
        )
