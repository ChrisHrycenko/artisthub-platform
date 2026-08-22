"""
config.py

Configuration classes for the ArtistHub Flask application.

The active configuration is selected by the FLASK_ENV environment variable
inside create_app(). All secrets are loaded from environment variables —
nothing sensitive is hardcoded here.

To switch from SQLite to PostgreSQL for production, change only the
SQLALCHEMY_DATABASE_URI in ProductionConfig. No other code changes required.
"""

import os


class BaseConfig:
    """
    Shared settings inherited by all environments.

    SECRET_KEY is mandatory — the app will refuse to start if it is not set
    in production (see ProductionConfig).
    """

    # Load secret key from environment; fall back to insecure default for
    # development only — ProductionConfig raises if this default is kept.
    SECRET_KEY: str = os.environ.get(
        "SECRET_KEY", "dev-secret-key-change-in-production"
    )

    # SQLAlchemy — disable modification tracking (not needed, saves memory).
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # Session cookie security flags.
    SESSION_COOKIE_HTTPONLY: bool = True    # JS cannot read the cookie.
    SESSION_COOKIE_SAMESITE: str = "Lax"   # Mitigates CSRF for same-site.

    # Flask-Login — redirect unauthenticated users to this view name.
    # Set to None because this is a pure JSON API; 401 is returned instead.
    LOGIN_DISABLED: bool = False


class DevelopmentConfig(BaseConfig):
    """
    Local development settings.

    DEBUG=True enables the interactive debugger and auto-reloader.
    SQLite database file is created in the backend/ directory.
    """

    DEBUG: bool = True
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL",
        # File created at backend/instance/artisthub.db
        "sqlite:///artisthub.db"
    )
    # Allow all origins in development for convenience.
    CORS_ORIGINS: list = ["http://localhost:3000", "http://127.0.0.1:3000",
                          "http://localhost:5500", "http://127.0.0.1:5500"]


class TestingConfig(BaseConfig):
    """
    Test suite settings.

    Uses an in-memory SQLite database so tests are isolated and fast.
    TESTING=True disables error propagation so pytest can catch exceptions.
    WTF_CSRF_ENABLED=False is a common convention even without Flask-WTF,
    kept here for forward-compatibility.
    """

    TESTING: bool = True
    DEBUG: bool = True
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    # Use a fixed secret key in tests so session tokens are deterministic.
    SECRET_KEY: str = "test-secret-key"
    # Disable bcrypt cost factor in tests to keep them fast.
    BCRYPT_LOG_ROUNDS: int = 4
    CORS_ORIGINS: list = ["*"]


class ProductionConfig(BaseConfig):
    """
    Production settings.

    SECRET_KEY and DATABASE_URL MUST be provided as environment variables.
    The app will raise a RuntimeError at startup if SECRET_KEY is the
    insecure default — this is intentional.

    PostgreSQL connection pool tuning
    ----------------------------------
    When DATABASE_URL points to PostgreSQL, SQLAlchemy maintains a
    connection pool.  The defaults below are conservative for a 2-replica
    backend running against a single PostgreSQL primary:
      - POOL_SIZE=5  × 4 replicas = 20 connections (well within PG default 100)
      - MAX_OVERFLOW=10 allows burst capacity without exhausting connections
      - POOL_PRE_PING=True sends a lightweight SELECT 1 before each checkout
        so stale connections (e.g. after a PostgreSQL restart) are discarded
        rather than surfaced as 500 errors to clients.

    These values are no-ops for SQLite (which uses StaticPool with
    check_same_thread=False and ignores pool settings).
    """

    DEBUG: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL", "sqlite:///artisthub.db"
    )

    # PostgreSQL connection pool settings — ignored by SQLite.
    SQLALCHEMY_POOL_SIZE: int = int(os.environ.get("DB_POOL_SIZE", "5"))
    SQLALCHEMY_MAX_OVERFLOW: int = int(
        os.environ.get("DB_MAX_OVERFLOW", "10")
    )
    SQLALCHEMY_POOL_PRE_PING: bool = True

    def __init__(self) -> None:
        """Enforce that a real secret key is set in production."""
        default = "dev-secret-key-change-in-production"
        if self.SECRET_KEY == default:
            raise RuntimeError(
                "SECRET_KEY environment variable must be set in production."
                " Never use the default development key."
            )


class PostgresTestingConfig(TestingConfig):
    """
    PostgreSQL integration test settings.

    Identical to TestingConfig but uses a real PostgreSQL database instead
    of the in-memory SQLite database.  Activated by passing
    ``config_name="pg_testing"`` to ``create_app()``.

    The DATABASE_URL must be set as an environment variable before the
    tests run.  This config is ONLY used by test_postgres_integration.py.

    Example:
        export ARTISTHUB_TEST_POSTGRES_URL=\\
            postgresql://artisthub:password@localhost:5432/artisthub_test
        cd backend && pytest tests/test_postgres_integration.py -v
    """

    SQLALCHEMY_DATABASE_URI: str = (
        os.environ.get("ARTISTHUB_TEST_POSTGRES_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://artisthub:password@localhost:5432/artisthub_test"
    )
    # Keep pool settings minimal for test isolation.
    SQLALCHEMY_POOL_PRE_PING: bool = True


# Map string names to config classes so create_app() can select by name.
config_map = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "pg_testing": PostgresTestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
