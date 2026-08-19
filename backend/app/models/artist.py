"""
models/artist.py

SQLAlchemy model for the Artist user type.

Artists create and manage music releases, social posts, and merchandise.
They are a completely separate model from Fan — there is no shared user
table. Artist authentication uses its own /api/auth/artist/* endpoints.
"""

from datetime import datetime
from flask_login import UserMixin
from app.extensions import db


class Artist(UserMixin, db.Model):
    """
    Represents a musician with a public profile on ArtistHub.

    Inherits UserMixin so Flask-Login can manage artist sessions
    via is_authenticated, is_active, get_id(), etc.
    """

    __tablename__ = "artist"

    # Primary key — auto-incremented integer.
    id = db.Column(db.Integer, primary_key=True)

    # Login credential — must be unique across all artists.
    email = db.Column(db.String(255), unique=True, nullable=False)

    # bcrypt hash — never store or log the plain-text password.
    password_hash = db.Column(db.String(255), nullable=False)

    # Publicly displayed name on the artist's profile page.
    display_name = db.Column(db.String(100), nullable=False)

    # Optional profile fields — all nullable for MVP.
    bio = db.Column(db.Text, nullable=True)
    profile_image_url = db.Column(db.String(500), nullable=True)
    genre = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(100), nullable=True)

    # Audit timestamp — set once at creation, never updated.
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )

    def get_id(self) -> str:
        """
        Flask-Login requires get_id() to return a unique string identifier.

        We prefix with 'artist-' so the login_manager user_loader can
        distinguish between Artist and Fan sessions.
        """
        return f"artist-{self.id}"

    def to_dict(self) -> dict:
        """
        Serialise the artist to a plain dict safe for JSON responses.

        password_hash is intentionally excluded — it must never leave
        the server in any API response.

        ``follower_count`` uses the dynamic ``followers`` backref defined
        on the Follow model. Calling ``.count()`` issues a single
        ``SELECT COUNT(*)`` — it does not load all Follow rows.
        """
        # followers backref is only present after Follow model is imported.
        # Guard with hasattr so to_dict() is safe to call before Follow is
        # registered (e.g. in early-boot or migration contexts).
        follower_count = (
            self.followers.count()
            if hasattr(self, "followers")
            else 0
        )
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "bio": self.bio,
            "profile_image_url": self.profile_image_url,
            "genre": self.genre,
            "location": self.location,
            "created_at": self.created_at.isoformat(),
            "role": "artist",
            "follower_count": follower_count,
        }

    def __repr__(self) -> str:
        return f"<Artist id={self.id} email={self.email!r}>"
