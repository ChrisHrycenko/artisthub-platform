"""
run_relay.py

Entry point for the ArtistHub outbox relay process.

Run from the backend/ directory:
    python run_relay.py

The relay polls the event_outbox table and publishes pending events to Kafka.
It must be run alongside (not inside) the Flask API server.

Environment variables required:
    KAFKA_BOOTSTRAP_SERVERS  — comma-separated Kafka broker addresses
    SECRET_KEY               — required by the Flask app factory
    DATABASE_URL             — optional; defaults to SQLite

See app/services/outbox_relay.py for full documentation.
"""

from app.services.outbox_relay import run

if __name__ == "__main__":
    run()
