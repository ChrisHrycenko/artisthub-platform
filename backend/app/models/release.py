"""
models/release.py

SQLAlchemy model for a Music Release on ArtistHub.

A release belongs to exactly one Artist (many-to-one). The back-reference
``artist.releases`` lets the Artist model access its releases as a list
without a separate query.

Design decisions:
  - ``streaming_url`` stores an external link (Spotify, SoundCloud, Bandcamp,
    YouTube, etc.). ArtistHub does NOT host audio files — no upload endpoint
    will ever be built for MVP. This is enforced at the schema layer by
    accepting only a URL string, never a file upload.
  - ``release_type`` is a free-text field (max 50 chars) so artists can use
    "Single", "EP", "Album", "Mixtape", etc. without being constrained to an
    enum that would require a migration to extend.
  - ``release_date`` is a DATE (not DATETIME) — precision to the day is
    sufficient and avoids timezone complexity.
"""

from datetime import datetime
from app.extensions import db


# Valid release_type values — validated in the schema, documented here.
RELEASE_TYPES = ("Single", "EP", "Album", "Mixtape", "Compilation", "Live")


class MusicRelease(db.Model):
    """
    Represents a music release (single, EP, album, etc.) on ArtistHub.

    Each release is owned by one Artist. Fans can browse all releases or
    filter by artist via the nested ``GET /api/artists/<id>/releases``
    endpoint.
    """

    __tablename__ = "music_release"

    # Primary key.
    id = db.Column(db.Integer, primary_key=True)

    # Foreign key — which artist owns this release.
    # ON DELETE CASCADE means deleting an artist removes all their releases.
    artist_id = db.Column(
        db.Integer,
        db.ForeignKey("artist.id", ondelete="CASCADE"),
        nullable=False,
        index=True,          # Indexed for fast artist-scoped queries.
    )

    # Required fields.
    title = db.Column(db.String(200), nullable=False)

    # "Single", "EP", "Album", etc. — free text, validated in schema.
    release_type = db.Column(db.String(50), nullable=False, default="Single")

    # Optional metadata.
    genre = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)

    # External artwork image URL (e.g. a CDN link).
    # ArtistHub does not host image files in the MVP.
    artwork_url = db.Column(db.String(500), nullable=True)

    # External streaming link — Spotify, SoundCloud, Bandcamp, YouTube, etc.
    # ArtistHub does NOT host or proxy audio. This is intentional.
    streaming_url = db.Column(db.String(500), nullable=True)

    # Calendar date of release — DATE precision is sufficient.
    release_date = db.Column(db.Date, nullable=True)

    # Audit timestamp.
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )

    # ORM relationship — lets us write ``release.artist`` to get the owner.
    # ``lazy="select"`` (default) loads the artist only when accessed.
    artist = db.relationship(
        "Artist",
        backref=db.backref(
            "releases",
            lazy="dynamic",    # backref returns a query, not a list,
            cascade="all, delete-orphan",  # so we can filter/paginate.
        ),
    )

    def to_dict(self) -> dict:
        """
        Serialise the release to a plain dict safe for JSON responses.

        ``release_date`` is ISO-formatted (YYYY-MM-DD) when set.
        ``artist_id`` is included so the frontend can link back to the
        artist profile without a second request.
        """
        return {
            "id": self.id,
            "artist_id": self.artist_id,
            "title": self.title,
            "release_type": self.release_type,
            "genre": self.genre,
            "description": self.description,
            "artwork_url": self.artwork_url,
            "streaming_url": self.streaming_url,
            "release_date": (
                self.release_date.isoformat() if self.release_date else None
            ),
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<MusicRelease id={self.id} "
            f"title={self.title!r} artist_id={self.artist_id}>"
        )
