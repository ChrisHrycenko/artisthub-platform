"""
app/utils/worker_metrics.py

Prometheus metrics helpers for ArtistHub worker processes.

Phase 8H — Observability
========================
Flask workers (outbox relay, analytics consumer, notification consumer) are
not HTTP servers, so ``prometheus_flask_exporter`` is not appropriate for
them.  Instead, each worker starts a lightweight HTTP server via
``prometheus_client.start_http_server()`` on a dedicated port (default 9090).

OpenShift User Workload Monitoring scrapes this endpoint via a ``PodMonitor``
(not a ``ServiceMonitor``, because workers have no Kubernetes Service).  The
existing ``allow-ocp-monitoring`` NetworkPolicy is updated to permit ingress
on port 9090 from the ``openshift-monitoring`` namespace.

Metrics conventions
-------------------
- Label cardinality is strictly bounded.  Allowed label values: event_type
  (enumerated domain events), consumer_group (static string), topic
  (enumerated topic names).
- Never use user IDs, artist IDs, fan IDs, event IDs, or any free-form
  string as a label value.
- All counter names follow the Prometheus naming convention:
  <namespace>_<subsystem>_<name>_total  (for counters)
  <namespace>_<subsystem>_<name>        (for gauges/histograms)

Registry isolation
------------------
Each worker creates its own CollectorRegistry to avoid conflicts with the
Flask app's default registry (which prometheus_flask_exporter uses).  The
default global registry is intentionally NOT used for worker metrics.

Usage
-----
In a worker's ``run()`` function, before the main loop:

    from app.utils.worker_metrics import start_metrics_server

    registry, metrics = start_metrics_server("relay", port=9090)

The returned ``metrics`` dict contains named Counter/Gauge objects the
worker increments during processing.

Heartbeat probes are NOT replaced by Prometheus metrics.  Workers continue
to write /tmp/<name>_alive on every loop iteration for OpenShift exec
liveness probes.  The Prometheus server is an ADDITIONAL observability
channel.
"""

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Default port for worker Prometheus metrics endpoints.
# Overridable via WORKER_METRICS_PORT environment variable.
DEFAULT_METRICS_PORT: int = int(
    os.environ.get("WORKER_METRICS_PORT", "9090")
)


def _make_relay_metrics(registry) -> Dict[str, Any]:
    """
    Create and return Prometheus metric objects for the outbox relay.

    Returns:
        dict with keys:
            poll_cycles_total     - Counter: relay poll loop iterations
            publish_total         - Counter: published (label: topic)
            publish_failures_total - Counter: failed (label: topic)
            outbox_backlog         - Gauge: pending outbox row count
            outbox_oldest_age_seconds - Gauge: oldest pending row age (s)
    """
    try:
        from prometheus_client import Counter, Gauge
    except ImportError:  # pragma: no cover
        return {}

    return {
        "poll_cycles_total": Counter(
            "artisthub_relay_poll_cycles_total",
            "Total outbox relay poll loop iterations",
            registry=registry,
        ),
        "publish_total": Counter(
            "artisthub_relay_publish_total",
            "Outbox events successfully published to Kafka",
            ["topic"],
            registry=registry,
        ),
        "publish_failures_total": Counter(
            "artisthub_relay_publish_failures_total",
            "Outbox events that failed Kafka publish",
            ["topic"],
            registry=registry,
        ),
        "outbox_backlog": Gauge(
            "artisthub_relay_outbox_backlog",
            "Pending outbox rows (published_at IS NULL)",
            registry=registry,
        ),
        "outbox_oldest_age_seconds": Gauge(
            "artisthub_relay_outbox_oldest_age_seconds",
            "Age in seconds of the oldest pending outbox row",
            registry=registry,
        ),
    }


def _make_analytics_metrics(registry) -> Dict[str, Any]:
    """
    Create and return Prometheus metric objects for the analytics consumer.

    Returns:
        dict with keys:
            messages_processed_total    - Counter (label: event_type)
            messages_skipped_total      - Counter
            messages_deadlettered_total - Counter (label: reason)
            db_retries_total            - Counter
    """
    try:
        from prometheus_client import Counter
    except ImportError:  # pragma: no cover
        return {}

    return {
        "messages_processed_total": Counter(
            "artisthub_analytics_messages_processed_total",
            "Analytics events successfully processed",
            ["event_type"],
            registry=registry,
        ),
        "messages_skipped_total": Counter(
            "artisthub_analytics_messages_skipped_total",
            "Analytics messages skipped (unknown type or duplicate)",
            registry=registry,
        ),
        "messages_deadlettered_total": Counter(
            "artisthub_analytics_messages_deadlettered_total",
            "Analytics messages sent to dead-letter topic",
            ["reason"],
            registry=registry,
        ),
        "db_retries_total": Counter(
            "artisthub_analytics_db_retries_total",
            "DB retry attempts in the analytics consumer",
            registry=registry,
        ),
    }


def _make_notification_metrics(registry) -> Dict[str, Any]:
    """
    Create and return Prometheus metric objects for the notification consumer.

    Returns:
        dict with keys:
            messages_processed_total    - Counter
            messages_skipped_total      - Counter
            messages_deadlettered_total - Counter (label: reason)
            notifications_created_total - Counter
            db_retries_total            - Counter
    """
    try:
        from prometheus_client import Counter
    except ImportError:  # pragma: no cover
        return {}

    return {
        "messages_processed_total": Counter(
            "artisthub_notification_messages_processed_total",
            "Notification events successfully processed",
            registry=registry,
        ),
        "messages_skipped_total": Counter(
            "artisthub_notification_messages_skipped_total",
            "Notification messages skipped (unknown type or duplicate)",
            registry=registry,
        ),
        "messages_deadlettered_total": Counter(
            "artisthub_notification_messages_deadlettered_total",
            "Notification messages sent to dead-letter topic",
            ["reason"],
            registry=registry,
        ),
        "notifications_created_total": Counter(
            "artisthub_notification_notifications_created_total",
            "Notification rows inserted (fan notifications queued)",
            registry=registry,
        ),
        "db_retries_total": Counter(
            "artisthub_notification_db_retries_total",
            "DB retry attempts in the notification consumer",
            registry=registry,
        ),
    }


# Map of worker name → metrics factory function.
_METRICS_FACTORIES = {
    "relay": _make_relay_metrics,
    "analytics": _make_analytics_metrics,
    "notification": _make_notification_metrics,
}


def start_metrics_server(
    worker_name: str,
    port: int = DEFAULT_METRICS_PORT,
) -> Tuple[Any, Dict[str, Any]]:
    """
    Start a Prometheus HTTP metrics server for a worker process.

    Creates an isolated CollectorRegistry (not the global default), registers
    worker-specific metrics on it, starts an HTTP server on ``port``, and
    returns the registry and the metrics dict.

    Args:
        worker_name: One of "relay", "analytics", "notification".
        port:        TCP port for the /metrics HTTP server (default: 9090).

    Returns:
        Tuple of (registry, metrics_dict).  ``metrics_dict`` is empty if
        prometheus_client is not installed (graceful degradation).

    Raises:
        Nothing — errors are logged but never propagate so the worker loop
        is not disrupted by a metrics server startup failure.
    """
    try:
        from prometheus_client import CollectorRegistry, start_http_server
    except ImportError:  # pragma: no cover
        logger.warning(
            "prometheus_client not installed; worker metrics disabled "
            "for %s", worker_name,
        )
        return None, {}

    registry = CollectorRegistry()

    factory = _METRICS_FACTORIES.get(worker_name)
    if factory is None:
        logger.warning(
            "Unknown worker_name=%r for metrics; no metrics registered",
            worker_name,
        )
        metrics: Dict[str, Any] = {}
    else:
        try:
            metrics = factory(registry)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to create metrics for worker=%s err=%s",
                worker_name, exc,
            )
            metrics = {}

    try:
        start_http_server(port, registry=registry)
        logger.info(
            "Worker metrics server started | worker=%s port=%d",
            worker_name, port,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to start metrics server | worker=%s port=%d err=%s",
            worker_name, port, exc,
        )

    return registry, metrics


def safe_inc(
    counter,
    labels: Optional[dict] = None,
) -> None:
    """
    Increment a Prometheus Counter safely (ignores missing/None counter).

    Args:
        counter: A prometheus_client.Counter instance, or None.
        labels:  Optional label dict for labeled counters.
    """
    if counter is None:
        return
    try:
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:  # noqa: BLE001
        pass  # Never crash the worker loop on a metrics error.


def safe_set(gauge, value: float) -> None:
    """
    Set a Prometheus Gauge value safely (ignores missing/None gauge).

    Args:
        gauge: A prometheus_client.Gauge instance, or None.
        value: Numeric value to set.
    """
    if gauge is None:
        return
    try:
        gauge.set(value)
    except Exception:  # noqa: BLE001
        pass  # Never crash the worker loop on a metrics error.
