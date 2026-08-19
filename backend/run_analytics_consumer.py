"""
run_analytics_consumer.py

Entry point for the ArtistHub real-time analytics consumer (Phase 7D).

Run from the backend/ directory:
    python run_analytics_consumer.py

The consumer subscribes to artisthub.social, artisthub.catalog, and
artisthub.identity, processes domain events, and updates per-artist
engagement counters in the analytics_state table.

Environment variables required:
    KAFKA_BOOTSTRAP_SERVERS  — comma-separated Kafka broker addresses
    SECRET_KEY               — required by the Flask app factory
    DATABASE_URL             — optional; defaults to SQLite

See consumers/analytics_consumer.py for full documentation.
"""

from consumers.analytics_consumer import run

if __name__ == "__main__":
    run()
