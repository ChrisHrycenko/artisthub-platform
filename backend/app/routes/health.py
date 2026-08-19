"""
routes/health.py

Health-check Blueprint for ArtistHub.

GET /api/health
    Returns a JSON object confirming the application is running, the
    current environment, and the database connectivity status. Used by
    Docker health checks, load balancers, and CI smoke tests.

This endpoint requires no authentication and has no side effects.
"""

import os

from flask import Blueprint, current_app
from app.utils.responses import success, error
from app.extensions import db
from sqlalchemy import text

# Blueprint prefix is registered as /api/health in create_app().
health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health_check():
    """
    Return application health status.

    Checks:
      - Application is running (always true if this endpoint responds)
      - Database is reachable (executes a trivial SELECT 1)

    Response body (200 — healthy):
        {
            "status": "success",
            "data": {
                "app": "ArtistHub",
                "status": "ok",
                "environment": "development",
                "database": "ok"
            }
        }

    Response body (500 — database unreachable):
        {
            "status": "error",
            "error": "Database unavailable: <reason>"
        }
    """
    # Probe the database with the lightest possible query.
    try:
        db.session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # pragma: no cover — hard to trigger in tests
        current_app.logger.error("Health check DB probe failed: %s", exc)
        return error(f"Database unavailable: {exc}", 500)

    return success({
        "app": "ArtistHub",
        "status": "ok",
        # Flask 3.x removed the ENV config key. Read FLASK_ENV from the
        # environment directly; fall back to "development" when unset.
        "environment": os.environ.get("FLASK_ENV", "development"),
        "database": db_status,
    })
