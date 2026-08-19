"""
app/__init__.py

ArtistHub application factory.

create_app() is the single entry point for constructing the Flask
application. It:
  1. Loads configuration from the config_map using FLASK_ENV.
  2. Initialises all Flask extensions
     (db, login_manager, bcrypt, migrate, CORS).
  3. Imports all models so SQLAlchemy is aware of every table.
  4. Registers all Blueprints under their /api/* prefixes.
  5. Configures Flask-Login's user_loader for Artist and Fan sessions.

Nothing is instantiated at module level — extensions live in extensions.py.
This pattern makes it trivial to spin up a test instance with a different
config (e.g. TestingConfig with an in-memory SQLite DB) without touching
any other file.
"""

import os
from flask import Flask
from flask_cors import CORS

from app.config import config_map
from app.extensions import db, login_manager, bcrypt, migrate


def create_app(config_name: str | None = None) -> Flask:
    """
    Construct and return a configured Flask application instance.

    Args:
        config_name: Key into config_map (e.g. 'development', 'testing',
                     'production'). If None, falls back to the FLASK_ENV
                     environment variable, then to 'default'.

    Returns:
        A fully initialised Flask application ready to serve requests.
    """
    # Use flask_app (not `app`) to avoid shadowing the `app` package name
    # when we later do `import app.models` inside the app context.

    # Resolve the frontend static folder relative to this file's location so
    # the path works both locally (backend/app/ → ../../frontend) and inside
    # the Docker container (where only the backend is present and the path
    # would not exist).  Passing static_folder=None when the directory is
    # absent tells Flask to skip static-file serving entirely — nginx handles
    # that in production, so this is the correct behaviour.
    _here = os.path.dirname(os.path.abspath(__file__))
    _frontend = os.path.join(_here, "..", "..", "frontend")
    _static_folder: str | None = (
        os.path.abspath(_frontend) if os.path.isdir(_frontend) else None
    )

    flask_app = Flask(
        __name__,
        static_folder=_static_folder,
        static_url_path="" if _static_folder else None,
    )

    # ------------------------------------------------------------------ #
    # 1. Load configuration
    # ------------------------------------------------------------------ #
    env = config_name or os.environ.get("FLASK_ENV", "default")
    cfg_class = config_map.get(env, config_map["default"])
    # Instantiate the config class so that __init__ runs.
    # ProductionConfig.__init__ raises RuntimeError when SECRET_KEY is the
    # insecure development default — passing the class (not an instance) to
    # from_object() would silently skip that guard.
    flask_app.config.from_object(cfg_class())

    # ------------------------------------------------------------------ #
    # 2. Initialise extensions
    # ------------------------------------------------------------------ #

    # SQLAlchemy ORM — all DB access goes through `db` from extensions.py.
    db.init_app(flask_app)

    # Alembic migrations — use `flask db migrate` / `flask db upgrade`.
    migrate.init_app(flask_app, db)

    # Bcrypt password hashing.
    bcrypt.init_app(flask_app)

    # Flask-Login session management.
    login_manager.init_app(flask_app)

    # Return JSON 401 for unauthenticated requests rather than redirecting
    # to a login page — this is a pure JSON API, no HTML login view.
    login_manager.login_view = None  # type: ignore[assignment]

    # CORS — restrict to configured origins so the browser allows
    # cross-origin requests from the static frontend in development.
    CORS(
        flask_app,
        origins=flask_app.config.get("CORS_ORIGINS", []),
        supports_credentials=True,
    )

    # ------------------------------------------------------------------ #
    # 3. Import models so SQLAlchemy registers all table metadata.
    #    This must happen after db.init_app() and before any db.create_all().
    # ------------------------------------------------------------------ #
    with flask_app.app_context():
        import app.models  # noqa: F401  — registers Artist and Fan tables

        # Create all tables that do not yet exist.
        # In production this is replaced by `flask db upgrade`.
        db.create_all()

    # ------------------------------------------------------------------ #
    # 4. Register Blueprints
    # ------------------------------------------------------------------ #
    from app.routes.health import health_bp
    from app.routes.artists import artists_bp
    from app.routes.releases import releases_bp
    from app.routes.posts import posts_bp
    from app.routes.merch import merch_bp
    from app.routes.fans import fans_bp
    from app.routes.follows import follows_bp
    from app.routes.auth import auth_bp

    flask_app.register_blueprint(health_bp, url_prefix="/api")
    flask_app.register_blueprint(artists_bp, url_prefix="/api")
    flask_app.register_blueprint(releases_bp, url_prefix="/api")
    flask_app.register_blueprint(posts_bp, url_prefix="/api")
    flask_app.register_blueprint(merch_bp, url_prefix="/api")
    flask_app.register_blueprint(fans_bp, url_prefix="/api")
    flask_app.register_blueprint(follows_bp, url_prefix="/api")
    flask_app.register_blueprint(auth_bp, url_prefix="/api")

    # Phase 5+ will add: orders_bp

    # ------------------------------------------------------------------ #
    # 5. Configure Flask-Login user_loader
    #
    # Because Artist and Fan are separate models, get_id() returns a
    # prefixed string ("artist-<id>" or "fan-<id>"). The loader splits
    # on '-' to determine which table to query.
    # ------------------------------------------------------------------ #
    @login_manager.user_loader
    def load_user(user_id: str):
        """
        Reload a user from the session cookie's stored user_id.

        Flask-Login calls this on every request where a session cookie
        exists. Returns None if the user cannot be found (session will
        be cleared automatically).
        """
        from app.models.artist import Artist
        from app.models.fan import Fan

        if user_id.startswith("artist-"):
            return db.session.get(Artist, int(user_id.split("-", 1)[1]))
        if user_id.startswith("fan-"):
            return db.session.get(Fan, int(user_id.split("-", 1)[1]))
        return None

    return flask_app
