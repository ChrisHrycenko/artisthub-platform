"""
models/outbox.py

Transactional Outbox model for ArtistHub.

Every business event is written to this table in the SAME database
transaction as the business object mutation (create/update/delete).
A separate relay process (app/services/outbox_relay.py) polls this table
and publishes pending rows to Kafka, then marks them as published.

This decouples API availability from Kafka availability:
  - If Kafka is down, the API continues working and events accumulate here.
  - No events are silently lost.
  - If the relay crashes after publishing but before marking published,
    re-delivery is safe because the Kafka producer uses enable.idempotence=True
    and consumers use event_id for deduplication.

Serialisation note (Phase 7C):
  The payload column stores a JSON-serialised dict matching the Avro schema
  envelope defined in Phase 7B. Live Avro serialisation via Schema Registry
  will be activated in Phase 7F. This interim format is wire-compatible with
  the Avro schema field layout.
"""

import json
from datetime import datetime
from app.extensions import db


class OutboxEvent(db.Model):
    """
    A pending or published domain event in the transactional outbox.

    Rows are written atomically with the business mutation.
    The relay process reads published_at IS NULL, publishes to Kafka,
    then sets published_at = now(). Failed rows have published_at = NULL
    and last_error != NULL — they are retried on the next relay cycle.
    """

    __tablename__ = "event_outbox"

    # ------------------------------------------------------------------ #
    # Primary key                                                          #
    # ------------------------------------------------------------------ #
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # ------------------------------------------------------------------ #
    # Event identity — deduplication key for consumers                    #
    # ------------------------------------------------------------------ #
    event_id = db.Column(
        db.String(36),
        nullable=False,
        unique=True,
        index=True,
        doc="UUID v4 uniquely identifying this event. Used by consumers "
            "for idempotent processing.",
    )

    # ------------------------------------------------------------------ #
    # Event metadata (mirrors the Avro envelope fields from Phase 7B)     #
    # ------------------------------------------------------------------ #
    event_type = db.Column(
        db.String(100),
        nullable=False,
        doc="Dot-separated event type, e.g. 'fan.followed.artist'.",
    )
    event_version = db.Column(
        db.String(10),
        nullable=False,
        default="1",
        doc="Schema version string, e.g. '1'.",
    )

    # ------------------------------------------------------------------ #
    # Routing                                                              #
    # ------------------------------------------------------------------ #
    topic = db.Column(
        db.String(255),
        nullable=False,
        doc="Kafka topic this event should be published to, "
            "e.g. 'artisthub.social'.",
    )
    message_key = db.Column(
        db.String(255),
        nullable=False,
        doc="Kafka message key (string representation of artist_id). "
            "Ensures per-artist ordering within a partition.",
    )

    # ------------------------------------------------------------------ #
    # Payload — full JSON-serialised event including envelope + payload   #
    # ------------------------------------------------------------------ #
    payload = db.Column(
        db.Text,
        nullable=False,
        doc="JSON string of the complete event (envelope + domain payload). "
            "Matches the field layout of the Phase 7B Avro schemas.",
    )

    # ------------------------------------------------------------------ #
    # Correlation                                                          #
    # ------------------------------------------------------------------ #
    correlation_id = db.Column(
        db.String(255),
        nullable=True,
        doc="Optional Flask request trace ID for cross-service correlation.",
    )

    # ------------------------------------------------------------------ #
    # Lifecycle timestamps                                                  #
    # ------------------------------------------------------------------ #
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        doc="When the outbox row was inserted (within the business tx).",
    )
    published_at = db.Column(
        db.DateTime,
        nullable=True,
        default=None,
        index=True,
        doc="Set by the relay after Kafka acknowledges delivery. "
            "NULL = pending; non-NULL = delivered.",
    )

    # ------------------------------------------------------------------ #
    # Retry tracking                                                       #
    # ------------------------------------------------------------------ #
    publish_attempts = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        doc="Number of times the relay has attempted to publish this event.",
    )
    last_error = db.Column(
        db.Text,
        nullable=True,
        default=None,
        doc="Last error message from the relay. NULL when published_at "
            "is set.",
    )

    def payload_dict(self) -> dict:
        """Deserialise and return the payload JSON as a Python dict."""
        return json.loads(self.payload)

    def to_dict(self) -> dict:
        """Serialise the outbox row to a plain dict (for logging/debugging)."""
        return {
            "id": self.id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "topic": self.topic,
            "message_key": self.message_key,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(),
            "published_at": (
                self.published_at.isoformat() if self.published_at else None
            ),
            "publish_attempts": self.publish_attempts,
            "last_error": self.last_error,
        }

    def __repr__(self) -> str:
        return (
            f"<OutboxEvent id={self.id} event_type={self.event_type!r} "
            f"published={'yes' if self.published_at else 'no'}>"
        )
