#!/usr/bin/env python3
"""
kafka/confluent-smoke-test.py

Phase 8D — Kafka producer and consumer smoke test against Confluent Cloud.

Validates:
  1. TLS connection to Confluent Cloud bootstrap
  2. SASL_SSL authentication (PLAIN mechanism)
  3. Topic existence and partition counts (social:6, catalog:6, identity:3, deadletter:3)
  4. Producer: Avro serialization + Schema Registry lookup + broker ack
  5. Consumer analytics group: subscribe, receive, deserialize, offset commit
  6. Consumer notification group: subscribe, receive, deserialize, offset commit
  7. Dead-letter topic: writeable by producer

Usage:
  # All credentials from environment — never passed as arguments.
  export KAFKA_BOOTSTRAP_SERVERS=pkc-XXXXX.us-east-1.aws.confluent.cloud:9092
  export KAFKA_SECURITY_PROTOCOL=SASL_SSL
  export KAFKA_SASL_MECHANISMS=PLAIN
  export CONFLUENT_API_KEY=<key>
  export CONFLUENT_API_SECRET=<secret>
  export SCHEMA_REGISTRY_URL=https://psrc-XXXXX.us-east-1.aws.confluent.cloud
  export SCHEMA_REGISTRY_API_KEY=<sr-key>
  export SCHEMA_REGISTRY_API_SECRET=<sr-secret>
  python3 kafka/confluent-smoke-test.py

Exit codes:
  0  all checks passed
  1  one or more checks failed
  2  credentials missing

SECURITY: Credential values are never logged or printed by this script.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

# ─── Dependency check ─────────────────────────────────────────────────────────
try:
    from confluent_kafka import Producer, Consumer, KafkaException, KafkaError
    from confluent_kafka.admin import AdminClient, ConfigResource
except ImportError:
    print("ERROR: confluent-kafka not installed. Run: pip install confluent-kafka")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# ─── Colours ──────────────────────────────────────────────────────────────────
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASSED = 0
FAILED = 0


def ok(msg):
    global PASSED
    PASSED += 1
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def fail(msg):
    global FAILED
    FAILED += 1
    print(f"  {RED}[FAIL]{RESET} {msg}")


def skip(msg):
    print(f"  {YELLOW}[SKIP]{RESET} {msg}")


def info(msg):
    print(f"  {BLUE}[INFO]{RESET} {msg}")


def section(title):
    print(f"\n{BOLD}{CYAN}═══ {title} ═══{RESET}\n")


REPO_ROOT = Path(__file__).resolve().parent.parent

# ─── Credential check ─────────────────────────────────────────────────────────

def _check_creds():
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")

    if protocol != "SASL_SSL" or "confluent.cloud" not in bootstrap:
        print(
            f"\n{RED}BLOCKED{RESET}: Confluent Cloud credentials not configured.\n"
            "Set in .env:\n"
            "  KAFKA_BOOTSTRAP_SERVERS=pkc-XXXXX...confluent.cloud:9092\n"
            "  KAFKA_SECURITY_PROTOCOL=SASL_SSL\n"
            "  KAFKA_SASL_MECHANISMS=PLAIN\n"
            "  CONFLUENT_API_KEY=<key>\n"
            "  CONFLUENT_API_SECRET=<secret>\n"
            "See scripts/confluent-provision.md for provisioning steps."
        )
        sys.exit(2)

    if not os.environ.get("CONFLUENT_API_KEY") or \
       not os.environ.get("CONFLUENT_API_SECRET"):
        print(
            f"\n{RED}BLOCKED{RESET}: CONFLUENT_API_KEY / CONFLUENT_API_SECRET not set."
        )
        sys.exit(2)

    return bootstrap, protocol


def _kafka_config():
    """Build confluent-kafka config from env. No secrets in return value logs."""
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    cfg = {
        "bootstrap.servers": bootstrap,
        "security.protocol": protocol,
    }
    if protocol == "SASL_SSL":
        cfg["sasl.mechanisms"] = os.environ.get("KAFKA_SASL_MECHANISMS", "PLAIN")
        cfg["sasl.username"] = os.environ.get("CONFLUENT_API_KEY", "")
        cfg["sasl.password"] = os.environ.get("CONFLUENT_API_SECRET", "")
    return cfg


# ─── Section A: Bootstrap connectivity + topic verification ───────────────────

EXPECTED_TOPICS = {
    "artisthub.social":      6,
    "artisthub.catalog":     6,
    "artisthub.identity":    3,
    "artisthub.deadletter":  3,
}


def test_connectivity_and_topics():
    section("A. Kafka Connectivity and Topic Verification")
    admin_cfg = _kafka_config()
    admin_cfg["socket.timeout.ms"] = 15000
    admin_cfg["request.timeout.ms"] = 15000

    try:
        admin = AdminClient(admin_cfg)
    except Exception as exc:
        fail(f"AdminClient creation failed: {exc}")
        return False

    try:
        metadata = admin.list_topics(timeout=20)
        ok("Bootstrap connection established (AdminClient metadata fetch)")
    except KafkaException as exc:
        fail(f"Cannot connect to Confluent Cloud: {exc}")
        return False

    found_topics = metadata.topics

    for topic, expected_parts in EXPECTED_TOPICS.items():
        if topic in found_topics:
            actual_parts = len(found_topics[topic].partitions)
            if actual_parts == expected_parts:
                ok(f"Topic {topic}: {actual_parts} partitions ✓")
            else:
                fail(
                    f"Topic {topic}: expected {expected_parts} partitions, "
                    f"got {actual_parts}"
                )
        else:
            fail(
                f"Topic {topic} NOT FOUND. "
                "Create it: confluent kafka topic create " + topic +
                f" --partitions {expected_parts}"
            )

    return True


# ─── Section B: Producer smoke test ───────────────────────────────────────────

def test_producer():
    section("B. Producer Smoke Test (Avro + Schema Registry)")

    # Add SR-specific paths to Python path so avro_utils imports work.
    sys.path.insert(0, str(REPO_ROOT / "backend"))

    try:
        from app.services.avro_utils import (
            get_or_register_schema_id, encode, record_name_for_event_type
        )
    except Exception as exc:
        fail(f"Cannot import avro_utils: {exc}")
        return

    # Build a minimal FanFollowedArtist test event.
    smoke_event_id = f"smoke-8d-{uuid.uuid4()}"
    smoke_record = {
        "event_id": smoke_event_id,
        "event_type": "fan.followed.artist",
        "occurred_at": "2024-01-01T00:00:00Z",
        "schema_version": "1.0",
        "payload": {
            "follow_id": 99999,
            "fan_id": 99999,
            "artist_id": 99999,
        },
    }

    # Step 1: Schema Registry — register/fetch schema_id
    try:
        schema_id = get_or_register_schema_id("FanFollowedArtist")
        ok(f"Schema Registry: FanFollowedArtist registered, schema_id={schema_id}")
    except Exception as exc:
        fail(f"Schema Registry registration failed: {exc}")
        return

    # Step 2: Avro serialization
    try:
        avro_bytes = encode("fan.followed.artist", smoke_record, schema_id)
        ok(f"Avro serialization: {len(avro_bytes)} bytes encoded")
    except Exception as exc:
        fail(f"Avro encoding failed: {exc}")
        return

    # Step 3: Produce with delivery callback
    delivery_results = []

    def on_delivery(err, msg):
        if err:
            delivery_results.append(("error", str(err)))
        else:
            delivery_results.append((
                "ok",
                f"topic={msg.topic()} partition={msg.partition()} offset={msg.offset()}"
            ))

    prod_cfg = _kafka_config()
    prod_cfg.update({
        "acks": "all",
        "enable.idempotence": True,
        "retries": 3,
    })

    try:
        prod = Producer(prod_cfg)
        prod.produce(
            topic="artisthub.social",
            key=smoke_event_id.encode("utf-8"),
            value=avro_bytes,
            on_delivery=on_delivery,
        )
        remaining = prod.flush(timeout=30.0)
    except Exception as exc:
        fail(f"Producer.produce / flush error: {exc}")
        return

    if remaining > 0:
        fail(f"Producer flush timed out — {remaining} message(s) not delivered")
        return

    if delivery_results and delivery_results[0][0] == "ok":
        ok(f"Broker acknowledgement received: {delivery_results[0][1]}")
    elif delivery_results and delivery_results[0][0] == "error":
        fail(f"Delivery error from broker: {delivery_results[0][1]}")
    else:
        fail("No delivery callback received")

    # Return event_id so consumer test can look for it.
    return smoke_event_id


# ─── Section C: Consumer smoke test ───────────────────────────────────────────

def test_consumer(group_id: str, topic: str, event_id_to_find: str):
    section(f"C. Consumer Smoke Test — {group_id}")
    info(f"Subscribing to {topic}, looking for event_id={event_id_to_find[:12]}…")

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    try:
        from app.services.avro_utils import decode as avro_decode
    except Exception as exc:
        fail(f"Cannot import avro_utils: {exc}")
        return

    con_cfg = _kafka_config()
    con_cfg.update({
        "group.id": group_id,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
        "session.timeout.ms": 30000,
    })

    found = False
    try:
        consumer = Consumer(con_cfg)
        consumer.subscribe([topic])

        deadline = time.time() + 45  # 45s to find the smoke test message
        while time.time() < deadline:
            msg = consumer.poll(timeout=2.0)
            if msg is None:
                continue
            if msg.error():
                if (KafkaError is not None and
                        msg.error().code() == KafkaError._PARTITION_EOF):
                    continue
                fail(f"Consumer error: {msg.error()}")
                break

            raw = msg.value()
            try:
                if raw and raw[0:1] == b"\x00":
                    event = avro_decode(raw)
                else:
                    event = json.loads(raw.decode("utf-8"))

                received_id = event.get("event_id", "")
                if received_id == event_id_to_find:
                    ok(f"Smoke test event received and deserialized: "
                       f"event_id={received_id[:12]}… "
                       f"topic={msg.topic()} partition={msg.partition()} "
                       f"offset={msg.offset()}")
                    consumer.commit(message=msg)
                    ok(f"Manual offset commit completed for {group_id}")
                    found = True
                    break
                else:
                    # Commit other messages to advance past them.
                    consumer.commit(message=msg)
            except Exception as exc:
                info(f"Skipping non-smoke message: {exc}")
                consumer.commit(message=msg)

    except Exception as exc:
        fail(f"Consumer error: {exc}")
        return
    finally:
        try:
            consumer.close()
        except Exception:
            pass

    if not found:
        fail(
            f"Smoke test event NOT received by {group_id} within 45 s. "
            "Check topic ACLs and consumer group permissions."
        )


# ─── Section D: Dead-letter writability ───────────────────────────────────────

def test_dead_letter():
    section("D. Dead-Letter Topic Write Test")
    dl_record = json.dumps({
        "dead_letter_at": "2024-01-01T00:00:00Z",
        "original_topic": "artisthub.social",
        "original_partition": 0,
        "original_offset": 0,
        "event_id": f"smoke-dl-{uuid.uuid4()}",
        "failure_reason": "Phase 8D dead-letter smoke test",
        "original_payload": "{}",
    })

    prod_cfg = _kafka_config()
    prod_cfg["acks"] = "1"  # single ack for DLQ (best-effort)
    delivery_results = []

    def on_delivery(err, msg):
        delivery_results.append(("error" if err else "ok", str(err or msg.offset())))

    try:
        prod = Producer(prod_cfg)
        prod.produce(
            topic="artisthub.deadletter",
            key=b"smoke-dl-test",
            value=dl_record.encode("utf-8"),
            on_delivery=on_delivery,
        )
        prod.flush(timeout=15.0)
    except Exception as exc:
        fail(f"Dead-letter produce error: {exc}")
        return

    if delivery_results and delivery_results[0][0] == "ok":
        ok(f"Dead-letter message delivered (offset={delivery_results[0][1]})")
    else:
        fail(
            f"Dead-letter delivery failed: "
            f"{delivery_results[0][1] if delivery_results else 'no callback'}"
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}ArtistHub Phase 8D — Kafka Smoke Test{RESET}")
    print("=" * 60)

    bootstrap, protocol = _check_creds()
    # Log endpoint without credentials
    info(f"Bootstrap: {bootstrap}")
    info(f"Security protocol: {protocol}")

    connected = test_connectivity_and_topics()
    if not connected:
        print(f"\n{RED}Cannot proceed — broker unreachable.{RESET}")
        sys.exit(1)

    smoke_event_id = test_producer()

    if smoke_event_id:
        # Give the message a moment to propagate before consuming.
        time.sleep(3)
        test_consumer(
            "artisthub.analytics.v1",
            "artisthub.social",
            smoke_event_id,
        )
        # Notification consumer subscribes to catalog, not social —
        # test its connectivity with a catalog smoke message instead.
        smoke_catalog_id = f"smoke-8d-catalog-{uuid.uuid4()}"
        info(f"Producing a catalog smoke message for notification consumer test …")
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        try:
            from app.services.avro_utils import get_or_register_schema_id, encode
            schema_id = get_or_register_schema_id("ArtistReleaseCreated")
            cat_record = {
                "event_id": smoke_catalog_id,
                "event_type": "artist.release.created",
                "occurred_at": "2024-01-01T00:00:00Z",
                "schema_version": "1.0",
                "payload": {
                    "release_id": 99999,
                    "artist_id": 99999,
                    "title": "Smoke Test Release 8D",
                    "release_type": "Single",
                    "genre": "Test",
                },
            }
            avro_bytes = encode("artist.release.created", cat_record, schema_id)
            prod_cfg = _kafka_config()
            prod_cfg.update({"acks": "all", "enable.idempotence": True})
            prod = Producer(prod_cfg)
            prod.produce(
                topic="artisthub.catalog",
                key=smoke_catalog_id.encode("utf-8"),
                value=avro_bytes,
            )
            prod.flush(timeout=15.0)
            time.sleep(3)
            test_consumer(
                "artisthub.notifications.v1",
                "artisthub.catalog",
                smoke_catalog_id,
            )
        except Exception as exc:
            fail(f"Catalog smoke message failed: {exc}")
    else:
        skip("Skipping consumer tests — producer smoke test did not succeed")

    test_dead_letter()

    # Summary
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"  {GREEN}PASSED: {PASSED}{RESET}")
    if FAILED:
        print(f"  {RED}FAILED: {FAILED}{RESET}")
        sys.exit(1)
    else:
        print(f"  {GREEN}FAILED: 0{RESET}")
        print(f"\n  {GREEN}{BOLD}✓ All Kafka smoke tests passed.{RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
