"""
services/__init__.py

Services package for ArtistHub.

Phase 7C services:
  event_factory   — builds OutboxEvent rows from model instances
  kafka_producer  — wraps confluent-kafka Producer
  outbox_relay    — polls outbox and publishes to Kafka
"""
