"""
models/post.py

SQLAlchemy model for an Artist Social Post on ArtistHub.

Design decisions:
  - Posts are intentionally simple: body text + optional image URL.
    No likes, comments, or reactions in the MVP — those are Phase 4+.
  - ``image_url`` stores an external link only. ArtistHub does NOT host
    image files; artists link to images already hosted elsewhere (e.g.
    Imgur, their own CDN). This is enforced in the schema layer.
  - Posts are immutable after creation in the MVP. Artists can delete
    but not edit a post, which keeps the data model simple and avoids
    edit-history complexity.
  - ``body`` max length is 2000 chars — enough for a substantial update
    without enabling essay-length content that belongs in a blog.
"""

from datetime import datetime
from app.extensions import db


class SocialPost(db.Model):
    """
    Represents a short-form social post published by an Artist.

    Fans can read posts on the global feed (GET /api/posts) or on an
    artist's profile page via the nested endpoint
    (GET /api/artists/<id>/posts).
    """

    __tablename__ = "social_post"

    # Primary key.
    id = db.Column(db.Integer, primary_key=True)

    # Foreign key — which artist published this post.
    # ON DELETE CASCADE: deleting an artist removes all their posts.
    artist_id = db.Column(
        db.Integer,
        db.ForeignKey("artist.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Required post body — plain text, max 2000 chars.
    body = db.Column(db.String(2000), nullable=False)

    # Optional external image URL. Not hosted by ArtistHub.
    image_url = db.Column(db.String(500), nullable=True)

    # Audit timestamp — immutable after insert.
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )

    # ORM relationship — ``post.artist`` resolves to the owning Artist.
    # dynamic backref lets ``artist.posts`` be filtered/paginated.
    artist = db.relationship(
        "Artist",
        backref=db.backref(
            "posts",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    def to_dict(self) -> dict:
        """
        Serialise the post to a plain dict safe for JSON responses.

        ``artist_id`` is included so the frontend can link back to the
        artist profile without an extra request.
        """
        return {
            "id": self.id,
            "artist_id": self.artist_id,
            "body": self.body,
            "image_url": self.image_url,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<SocialPost id={self.id} artist_id={self.artist_id} "
            f"body={self.body[:30]!r}>"
        )
