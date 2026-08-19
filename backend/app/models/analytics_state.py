"""
models/analytics_state.py

Per-artist analytics counters maintained by the Phase 7D consumer.

The analytics consumer (consumers/analytics_consumer.py) updates these
counters in response to domain events consumed from Kafka. Each row holds
the live engagement snapshot for one artist.

Design decisions
----------------
- One row per artist_id — UPSERT on first event for that artist.
- Counters are never recalculated from scratch; they are incremented /
  decremented by the consumer. Starting from 0 is intentional: the consumer
  only reflects events it has actually seen (i.e. since the topic's
  retention window). A catch-up backfill from the DB can seed initial
  counts when needed (out of Phase 7D scope).
- follower_count has a floor of 0 — an unfollow on an artist with
  count=0 is a no-op (guards against duplicate or out-of-order events).
- updated_at is set by the consumer on every write.

The GET /api/artists/<id>/analytics endpoint continues to serve its own
COUNT(*) queries in Phase 7D. The AnalyticsState table exists as the
consumer's write target and can be wired to the analytics endpoint in a
future phase without any model changes.
"""

from datetime import datetime
from app.extensions import db


class AnalyticsState(db.Model):
    """
    Live per-artist engagement counters updated by the analytics consumer.

    Row is created with all counters at 0 on the first event for an artist
    and updated in-place thereafter.
    """

    __tablename__ = "analytics_state"

    # ------------------------------------------------------------------ #
    # Primary key — artist_id IS the primary key; one row per artist.     #
    # ------------------------------------------------------------------ #
    artist_id = db.Column(
        db.Integer,
        primary_key=True,
        doc="ArtistHub artist primary key. One row per artist.",
    )

    # ------------------------------------------------------------------ #
    # Counters                                                             #
    # ------------------------------------------------------------------ #
    follower_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        doc="Number of fans currently following this artist. "
            "Never goes below 0.",
    )
    release_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        doc="Number of active music releases.",
    )
    post_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        doc="Number of active social posts.",
    )
    merch_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        doc="Number of active merchandise products.",
    )

    # ------------------------------------------------------------------ #
    # Audit                                                                #
    # ------------------------------------------------------------------ #
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        doc="UTC datetime of the last counter update.",
    )

    def to_dict(self) -> dict:
        """Serialise the analytics state to a plain dict."""
        return {
            "artist_id": self.artist_id,
            "follower_count": self.follower_count,
            "release_count": self.release_count,
            "post_count": self.post_count,
            "merch_count": self.merch_count,
            "updated_at": self.updated_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<AnalyticsState artist_id={self.artist_id} "
            f"followers={self.follower_count} releases={self.release_count} "
            f"posts={self.post_count} merch={self.merch_count}>"
        )
