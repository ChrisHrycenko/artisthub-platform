"""
models/follow.py

SQLAlchemy model for the Follow relationship on ArtistHub.

A Follow represents a Fan subscribing to an Artist's content. It is a
pure join table with an audit timestamp — no payload beyond the two
foreign keys and the time the follow was created.

Design decisions:
  - UNIQUE(fan_id, artist_id) is enforced both in SQLAlchemy (via
    UniqueConstraint) and in the route (IntegrityError → 409). The
    double-check is intentional: the DB constraint is the authoritative
    guard; the route check surfaces a clean JSON error to the caller.
  - ON DELETE CASCADE on both FKs ensures that deleting an Artist or
    Fan automatically removes all associated follow rows — no orphan
    rows, no application-level cleanup needed.
  - The relationship is exposed as:
      fan.following    → list of Artist objects the fan follows
      artist.followers → list of Fan objects following the artist
    Both use ``lazy="dynamic"`` so they return queries, not loaded
    lists, and can be paginated or counted without a full fetch.
"""

from datetime import datetime
from app.extensions import db


class Follow(db.Model):
    """
    Represents a Fan following an Artist on ArtistHub.

    Fans use POST /api/follows to follow an artist and
    DELETE /api/follows/<artist_id> to unfollow.
    """

    __tablename__ = "follow"

    id = db.Column(db.Integer, primary_key=True)

    # The fan who is following.
    fan_id = db.Column(
        db.Integer,
        db.ForeignKey("fan.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The artist being followed.
    artist_id = db.Column(
        db.Integer,
        db.ForeignKey("artist.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # When the follow was created.
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )

    # DB-level uniqueness: a fan may follow an artist at most once.
    __table_args__ = (
        db.UniqueConstraint("fan_id", "artist_id", name="uq_follow"),
    )

    # ORM relationships — accessed as fan.following and artist.followers.
    fan = db.relationship(
        "Fan",
        backref=db.backref("following", lazy="dynamic"),
    )
    artist = db.relationship(
        "Artist",
        backref=db.backref("followers", lazy="dynamic"),
    )

    def to_dict(self) -> dict:
        """Serialise the follow relationship to a plain dict."""
        return {
            "id": self.id,
            "fan_id": self.fan_id,
            "artist_id": self.artist_id,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<Follow fan_id={self.fan_id} artist_id={self.artist_id}>"
        )
