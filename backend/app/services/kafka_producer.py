"""
services/kafka_producer.py

Centralised Kafka producer service for ArtistHub.

This module is used exclusively by the outbox relay process
(app/services/outbox_relay.py). Routes NEVER call this directly —
they write to the outbox table and let the relay handle publishing.

Configuration (all from environment variables — no secrets hardcoded):
    KAFKA_BOOTSTRAP_SERVERS   Comma-separated broker list.
                              Default: localhost:9092
    KAFKA_SECURITY_PROTOCOL   PLAINTEXT (default) or SASL_SSL
    KAFKA_SASL_MECHANISM      PLAIN (only used when protocol=SASL_SSL)
    CONFLUENT_API_KEY         SASL username (Confluent Cloud only)
    CONFLUENT_API_SECRET      SASL password (Confluent Cloud only)

Serialisation (Phase 7C):
    Messages are published as UTF-8-encoded JSON strings.
    The message value is the full event JSON stored in OutboxEvent.payload.
    Live Avro serialisation via Confluent Schema Registry will be
    activated in Phase 7F. The JSON wire format is structurally compatible
    with the Phase 7B Avro schema field layout so that no data migration
    is required when Phase 7F is activated.

Producer settings:
    enable.idempotence=true  — exactly-once delivery within a session
    acks=all                 — wait for all ISR replicas
    retries=5                — retry transient broker failures
    linger.ms=5              — batch for up to 5 ms before sending
    compression.type=snappy  — reduce network I/O
"""

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# confluent-kafka is a required runtime dependency (added in Phase 7C).
# Import it at module level so import errors surface early when the relay
# process starts, not on the first produce() call.
try:
    from confluent_kafka import Producer, KafkaException  # type: ignore
    _CONFLUENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    Producer = None  # type: ignore
    KafkaException = Exception  # type: ignore
    _CONFLUENT_AVAILABLE = False


def _build_config() -> dict:
    """
    Build the confluent-kafka Producer config dict from environment variables.

    No secrets are hardcoded here. All sensitive values are loaded from the
    process environment at runtime.
    """
    bootstrap = os.environ.get(
        "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
    )
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")

    config: dict = {
        "bootstrap.servers": bootstrap,
        # Idempotent producer — exactly-once within a session.
        # confluent-kafka requires retries > 0 and acks=all with idempotence.
        "enable.idempotence": True,
        "acks": "all",
        "retries": 5,
        "retry.backoff.ms": 200,
        # Safe with idempotence enabled (Kafka 3.x default).
        "max.in.flight.requests.per.connection": 5,
        # Throughput / latency.
        "linger.ms": 5,
        "compression.type": "snappy",
        "security.protocol": protocol,
    }

    if protocol == "SASL_SSL":
        config["sasl.mechanisms"] = os.environ.get(
            "KAFKA_SASL_MECHANISM", "PLAIN"
        )
        config["sasl.username"] = os.environ.get("CONFLUENT_API_KEY", "")
        config["sasl.password"] = os.environ.get("CONFLUENT_API_SECRET", "")

    return config


class KafkaProducerService:
    """
    Thin wrapper around the confluent-kafka Producer.

    The relay process creates one instance and reuses it for the lifetime
    of the process. The producer is not thread-safe; the relay runs in a
    single thread.

    Usage:
        svc = KafkaProducerService()
        svc.produce(
            topic="artisthub.social",
            key="42",
            value='{"event_type": "fan.followed.artist", ...}',
            on_delivery=my_callback,
        )
        svc.flush()
    """

    def __init__(self) -> None:
        """Initialise the confluent-kafka Producer."""
        if not _CONFLUENT_AVAILABLE:
            raise RuntimeError(
                "confluent-kafka is not installed. "
                "Run: pip install confluent-kafka"
            )
        self._producer: Producer = Producer(_build_config())
        logger.info(
            "KafkaProducerService initialised — bootstrap=%s",
            os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        )

    def produce(
        self,
        topic: str,
        key: str,
        value: str,
        on_delivery: Optional[Callable] = None,
    ) -> None:
        """
        Enqueue a message for delivery to Kafka.

        The message is not guaranteed delivered until flush() is called or
        the internal buffer is full. The on_delivery callback receives
        (err, msg) after acknowledgement or failure.

        Args:
            topic:       Kafka topic name.
            key:         Message key (string; encoded to UTF-8 bytes).
            value:       Message value (JSON string; encoded to UTF-8 bytes).
            on_delivery: Optional callback invoked on ack or error.
        """
        try:
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=value.encode("utf-8"),
                on_delivery=on_delivery,
            )
            # Poll to serve delivery callbacks for already-sent messages.
            self._producer.poll(0)
        except KafkaException as exc:
            logger.error(
                "KafkaProducerService.produce error | topic=%s key=%s err=%s",
                topic, key, exc,
            )
            raise

    def flush(self, timeout: float = 30.0) -> int:
        """
        Block until all enqueued messages are delivered or timeout expires.

        Returns the number of messages still in the queue (0 = all acked).
        """
        remaining = self._producer.flush(timeout=timeout)
        if remaining:
            logger.warning(
                "KafkaProducerService.flush: %d message(s) not delivered "
                "within %.1fs timeout",
                remaining,
                timeout,
            )
        return remaining

    def close(self) -> None:
        """Flush pending messages and release producer resources."""
        self.flush()
        logger.info("KafkaProducerService closed.")
