"""
services/kafka_producer.py

Centralised Kafka producer service for ArtistHub.

Phase 7F: Messages are now serialized using Confluent Avro wire format
governed by Schema Registry. The 5-byte Confluent header (magic byte +
schema_id) is prepended by avro_utils.encode() before produce() is called.

This module is used exclusively by the outbox relay process
(app/services/outbox_relay.py). Routes NEVER call this directly —
they write to the outbox table and let the relay handle publishing.

Configuration (all from environment variables — no secrets hardcoded):
    KAFKA_BOOTSTRAP_SERVERS    Comma-separated broker list.
                               Default: localhost:9092
    KAFKA_SECURITY_PROTOCOL    PLAINTEXT (default) or SASL_SSL
    KAFKA_SASL_MECHANISM       PLAIN (only used when protocol=SASL_SSL)
    CONFLUENT_API_KEY          SASL username (Confluent Cloud only)
    CONFLUENT_API_SECRET       SASL password (Confluent Cloud only)
    SCHEMA_REGISTRY_URL        Schema Registry base URL.
                               Default: http://localhost:8081
    SCHEMA_REGISTRY_API_KEY    Schema Registry basic-auth username
                               (Confluent Cloud; leave blank locally)
    SCHEMA_REGISTRY_API_SECRET Schema Registry basic-auth password

Serialisation (Phase 7F):
    The relay calls produce_avro() which:
      1. Looks up / registers the Avro schema for the event_type in
         Schema Registry (RecordNameStrategy).
      2. Serializes the full event dict to Confluent wire format using
         avro_utils.encode().
      3. Passes the resulting bytes to the underlying confluent_kafka.Producer.

    The JSON outbox payload is never written directly to Kafka in Phase 7F.

Subject naming:
    RecordNameStrategy — subject = "io.artisthub.events.<RecordName>"
    See avro_utils.py for the full event_type → subject mapping.

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
    Avro-aware Kafka producer service for ArtistHub.

    The relay process creates one instance and reuses it for the lifetime
    of the process. The producer is not thread-safe; the relay runs in a
    single thread.

    Phase 7F changes:
      - ``produce_avro()`` is the primary method for Avro-serialized events.
      - ``produce()`` is retained for backward-compatibility in tests and
        dead-letter publishing; it writes raw bytes/string values directly.

    Usage:
        svc = KafkaProducerService()
        svc.produce_avro(
            topic="artisthub.social",
            event_type="fan.followed.artist",
            key="42",
            record={...},          # full event dict
            on_delivery=callback,
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
            "KafkaProducerService initialised | bootstrap=%s sr_url=%s",
            os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081"),
        )

    def produce_avro(
        self,
        topic: str,
        event_type: str,
        key: str,
        record: dict,
        on_delivery: Optional[Callable] = None,
    ) -> None:
        """
        Serialize record to Confluent Avro wire format and enqueue for
        delivery.

        Steps:
          1. Determine the Avro record name from event_type.
          2. Register / retrieve the schema_id from Schema Registry
             (cached per process after first call per event type).
          3. Serialize record with avro_utils.encode() → Confluent bytes.
          4. Enqueue via the underlying confluent_kafka.Producer.

        Args:
            topic:       Kafka topic name.
            event_type:  ArtistHub event type (selects the Avro schema).
            key:         Message key (string; encoded to UTF-8 bytes).
            record:      Full event dict (envelope + payload).
            on_delivery: Optional callback invoked on ack or error.

        Raises:
            ValueError:       if event_type is not one of the 12 known types.
            KafkaException:   on producer transport errors.
            requests.HTTPError: on Schema Registry communication failure.
            fastavro.write.ValidationError: if record violates the schema.
        """
        from app.services.avro_utils import (
            get_or_register_schema_id,
            encode,
        )

        record_name = None
        try:
            from app.services.avro_utils import record_name_for_event_type
            record_name = record_name_for_event_type(event_type)
            schema_id = get_or_register_schema_id(record_name)
            avro_bytes = encode(event_type, record, schema_id)
        except ValueError:
            raise
        except Exception as exc:
            logger.error(
                "Avro serialization error | event_type=%s record=%s err=%s",
                event_type, record_name, exc,
            )
            raise

        try:
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=avro_bytes,
                on_delivery=on_delivery,
            )
            # Poll to serve delivery callbacks for already-sent messages.
            self._producer.poll(0)
        except KafkaException as exc:
            logger.error(
                "KafkaProducerService.produce_avro error | "
                "topic=%s key=%s event_type=%s err=%s",
                topic, key, event_type, exc,
            )
            raise

    def produce(
        self,
        topic: str,
        key: str,
        value: str,
        on_delivery: Optional[Callable] = None,
    ) -> None:
        """
        Enqueue a raw string/bytes message for delivery.

        Retained for dead-letter publishing (which uses plain JSON, not Avro)
        and for test injection. New relay code should use produce_avro().

        Args:
            topic:       Kafka topic name.
            key:         Message key (string; encoded to UTF-8 bytes).
            value:       Message value (string or bytes).
            on_delivery: Optional delivery callback.
        """
        raw = value.encode("utf-8") if isinstance(value, str) else value
        try:
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8") if isinstance(key, str) else key,
                value=raw,
                on_delivery=on_delivery,
            )
            self._producer.poll(0)
        except KafkaException as exc:
            logger.error(
                "KafkaProducerService.produce error | "
                "topic=%s key=%s err=%s",
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
