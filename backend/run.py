"""
run.py

ArtistHub application entry point.

Usage (local development):
    cd backend
    python run.py

    Or with the Flask CLI:
    flask --app run:app run --debug

Usage (production via gunicorn):
    gunicorn -w 2 -b 0.0.0.0:5000 "run:app"

The `app` object is created at module level so both `python run.py` and
gunicorn can import it without executing the if __name__ == '__main__' block.
"""

from app import create_app

# Build the application using the active FLASK_ENV environment variable.
# Defaults to DevelopmentConfig if FLASK_ENV is not set.
app = create_app()

if __name__ == "__main__":
    # Development server only — do not use in production.
    # Use gunicorn (see docker/Dockerfile) for production deployments.
    app.run(host="0.0.0.0", port=5000, debug=True)
