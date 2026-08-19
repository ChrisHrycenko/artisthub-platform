"""
models/__init__.py

Model package for ArtistHub.

Importing all models here ensures SQLAlchemy is aware of every table
when db.create_all() or Flask-Migrate runs. If a model is not imported
before the DB is initialised, its table will not be created.
"""

from app.models.artist import Artist                # noqa: F401
from app.models.fan import Fan                      # noqa: F401
from app.models.release import MusicRelease         # noqa: F401
from app.models.post import SocialPost              # noqa: F401
from app.models.merchandise import MerchProduct     # noqa: F401
from app.models.follow import Follow                # noqa: F401

# Phase 3 remaining model (Order) will be added here.
