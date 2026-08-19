"""
models/fan.py

SQLAlchemy model for the Fan user type.

Fans browse content, follow artists, and simulate purchases.
They are a completely separate model from Artist — there is no shared
user table. Fan authentication uses its own /api/auth/fan/* endpoints.
"""

from datetime import datetime
from flask_login import UserMixin
from app.extensions import db


class Fan(UserMixin, db.Model):
    """
    Represents a music fan on ArtistHub.

    Inherits UserMixin so Flask-Login can manage fan sessions.
    """

    __tablename__ = "fan"

    # Primary key — auto-incremented integer.
    id = db.Column(db.Integer, primary_key=True)

    # Login credential — must be unique across all fans.
    email = db.Column(db.String(255), unique=True, nullable=False)

    # bcrypt hash — never store or log the plain-text password.
    password_hash = db.Column(db.String(255), nullable=False)

    # Public-facing username displayed on the fan's dashboard.
    username = db.Column(db.String(100), unique=True, nullable=False)

    # Audit timestamp.
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )

    def get_id(self) -> str:
        """
        Flask-Login requires get_id() to return a unique string identifier.

        We prefix with 'fan-' so the login_manager user_loader can
        distinguish between Artist and Fan sessions.
        """
        return f"fan-{self.id}"

    def to_dict(self) -> dict:
        """
        Serialise the fan to a plain dict safe for JSON responses.

        password_hash is intentionally excluded.
        """
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "created_at": self.created_at.isoformat(),
            "role": "fan",
        }

    def __repr__(self) -> str:
        return f"<Fan id={self.id} username={self.username!r}>"
