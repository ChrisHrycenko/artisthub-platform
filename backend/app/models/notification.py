"""
models/notification.py

Outbound notification work table for ArtistHub — Phase 7E.

The notification consumer (consumers/notification_consumer.py) writes
one row per fan per triggering event. Each row is a notification work
item ready to be dispatched by a future delivery channel (email, push,
SMS) without any schema change.

Phase 7E does NOT send real notifications. The table is the durable
record that a fan should be notified; delivery is Phase 7F+ scope.

Design decisions
----------------
- (event_id, fan_id) is UNIQUE — the primary idempotency guard.
  Re-delivering the same Kafka event never produces duplicate
  notification rows for the same fan, even if the consumer restarts
  between the DB commit and the Kafka offset commit.
- notification_type is a short string such as 'new_release'; new types
  can be added without a schema change.
- status starts as 'pending'. A future delivery worker sets it to
  'sent', 'failed', or 'skipped'.
- sent_at is nullable — set by the delivery worker, not the consumer.
- artist_id and release_id are denormalised for efficient querying by
  the delivery worker (no joins required to fetch the relevant IDs).
"""

from datetime import datetime
from app.extensions import db


class Notification(db.Model):
    """
    A single fan notification work item generated from a Kafka event.

    Status lifecycle (Phase 7E):
        pending  — created by the notification consumer; not yet sent.
        sent     — delivery worker confirmed dispatch (Phase 7F+).
        failed   — delivery worker encountered a permanent error.
        skipped  — fan opted out or fan no longer follows the artist.
    """

    __tablename__ = "notification"

    # ------------------------------------------------------------------ #
    # Primary key                                                          #
    # ------------------------------------------------------------------ #
    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )

    # ------------------------------------------------------------------ #
    # Event linkage                                                        #
    # ------------------------------------------------------------------ #
    event_id = db.Column(
        db.String(36),
        nullable=False,
        index=True,
        doc="UUID v4 of the Kafka event that triggered this notification. "
            "Used with fan_id to enforce uniqueness.",
    )

    # ------------------------------------------------------------------ #
    # Recipients and subjects                                              #
    # ------------------------------------------------------------------ #
    fan_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
        doc="Fan who should receive this notification.",
    )
    artist_id = db.Column(
        db.Integer,
        nullable=False,
        index=True,
        doc="Artist who triggered the event (denormalised).",
    )
    release_id = db.Column(
        db.Integer,
        nullable=True,
        doc="Release associated with the event, if applicable.",
    )

    # ------------------------------------------------------------------ #
    # Notification content                                                 #
    # ------------------------------------------------------------------ #
    notification_type = db.Column(
        db.String(50),
        nullable=False,
        doc="Short type tag, e.g. 'new_release'.",
    )
    subject = db.Column(
        db.String(255),
        nullable=False,
        doc="Short subject line, e.g. 'DJ Artsy dropped a new release!'",
    )
    message = db.Column(
        db.Text,
        nullable=False,
        doc="Full notification body text.",
    )

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #
    status = db.Column(
        db.String(20),
        nullable=False,
        default="pending",
        index=True,
        doc="'pending' | 'sent' | 'failed' | 'skipped'",
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        doc="UTC datetime when the consumer created this row.",
    )
    sent_at = db.Column(
        db.DateTime,
        nullable=True,
        default=None,
        doc="UTC datetime when the delivery worker confirmed dispatch. "
            "NULL = not yet sent.",
    )

    # ------------------------------------------------------------------ #
    # Uniqueness constraint: one notification per (event_id, fan_id).     #
    # Prevents duplicates on Kafka re-delivery.                           #
    # ------------------------------------------------------------------ #
    __table_args__ = (
        db.UniqueConstraint(
            "event_id", "fan_id",
            name="uq_notification_event_fan",
        ),
    )

    def to_dict(self) -> dict:
        """Serialise the notification to a plain dict."""
        return {
            "id": self.id,
            "event_id": self.event_id,
            "fan_id": self.fan_id,
            "artist_id": self.artist_id,
            "release_id": self.release_id,
            "notification_type": self.notification_type,
            "subject": self.subject,
            "message": self.message,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "sent_at": (
                self.sent_at.isoformat() if self.sent_at else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} fan_id={self.fan_id} "
            f"event_id={self.event_id!r} status={self.status!r}>"
        )
