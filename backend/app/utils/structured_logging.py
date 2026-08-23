"""
app/utils/structured_logging.py

Structured JSON logging for ArtistHub.

Phase 8H — Observability
========================
All ArtistHub processes (Flask backend, outbox relay, analytics consumer,
notification consumer) configure Python's ``logging`` module to emit
structured JSON lines.  Each log record contains:

  timestamp    ISO-8601 UTC timestamp
  level        Python log level name (INFO, WARNING, ERROR, DEBUG)
  logger       Logger name (e.g. "app.services.outbox_relay")
  message      The human-readable log message
  correlation_id  Request correlation ID (backend only; empty for workers)
  worker       Worker name (relay / analytics / notification; backend empty)

This format is compatible with:
  - OpenShift cluster logging (Elasticsearch / Loki)
  - Splunk / Datadog JSON ingestion
  - jq for local debugging

Usage
-----
Call ``configure_structured_logging()`` at the start of each process entry
point (run.py, run_relay.py, run_analytics_consumer.py, etc.) BEFORE any
logging calls.  In the Flask app, call it from create_app().

For Flask request logging, the correlation ID is read from flask.g if
available.  Workers set a static ``worker`` field.

Example output
--------------
{
  "timestamp": "2025-01-15T10:23:45.123456Z",
  "level": "INFO",
  "logger": "app.services.outbox_relay",
  "message": "Relay: published 3 event(s)",
  "worker": "relay",
  "correlation_id": ""
}
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class _JSONFormatter(logging.Formatter):
    """
    Python logging Formatter that emits one JSON object per log record.

    Thread-safe: each call to format() is stateless.
    """

    def __init__(self, worker: str = "") -> None:
        """
        Initialise the JSON formatter.

        Args:
            worker: Static worker identifier string (e.g. "relay").
                    Empty for the Flask backend process.
        """
        super().__init__()
        self._worker = worker

    def format(  # type: ignore[override]
        self,
        record: logging.LogRecord,
    ) -> str:
        """
        Render a LogRecord as a JSON string.

        Reads ``correlation_id`` from the LogRecord if it was injected via
        a LoggerAdapter or ``extra=``.  Falls back to empty string.
        """
        timestamp = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        # Allow callers to inject a correlation_id via extra=.
        correlation_id: str = getattr(record, "correlation_id", "")

        # formatMessage() requires record.message to be pre-populated.
        # Call record.getMessage() which expands % args safely.
        record.message = record.getMessage()

        record_dict = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            "worker": self._worker,
            "correlation_id": correlation_id,
        }

        # Attach exception info if present.
        if record.exc_info:
            record_dict["exception"] = self.formatException(record.exc_info)

        return json.dumps(record_dict)


def configure_structured_logging(
    level: str = "INFO",
    worker: str = "",
    stream=None,
) -> None:
    """
    Configure the root Python logger to emit structured JSON lines.

    Must be called once per process at startup, BEFORE any logging calls.
    Subsequent calls are idempotent (handlers are replaced, not doubled).

    Args:
        level:  Logging level name (e.g. "INFO", "DEBUG").
                Defaults to LOG_LEVEL env var, then "INFO".
        worker: Static worker name embedded in every log record.
                Use "" for the Flask backend process.
        stream: Output stream (default: sys.stdout).
                Override in tests to capture output.
    """
    effective_level = os.environ.get("LOG_LEVEL", level).upper()
    target_stream = stream or sys.stdout

    formatter = _JSONFormatter(worker=worker)
    handler = logging.StreamHandler(target_stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace existing handlers to avoid duplicated output when called
    # multiple times (e.g. dev server reloads).
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(effective_level)

    # Suppress noisy third-party loggers at WARNING+ unless DEBUG requested.
    if effective_level != "DEBUG":
        for noisy in ("werkzeug", "gunicorn.access", "urllib3", "botocore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
