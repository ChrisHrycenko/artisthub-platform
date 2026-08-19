"""
run_notification_consumer.py

Entry point for the ArtistHub notification consumer (Phase 7E).

Run from the backend/ directory:
    python run_notification_consumer.py

The consumer subscribes to artisthub.catalog, processes
artist.release.created events, and writes one Notification row per
follower to the notification table.

Environment variables required:
    KAFKA_BOOTSTRAP_SERVERS  — comma-separated Kafka broker addresses
    SECRET_KEY               — required by the Flask app factory
    DATABASE_URL             — optional; defaults to SQLite

See consumers/notification_consumer.py for full documentation.
"""

from consumers.notification_consumer import run

if __name__ == "__main__":
    run()
