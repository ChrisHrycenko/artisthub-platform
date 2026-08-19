"""
services/event_factory.py

Centralised factory for building ArtistHub domain events.

Every outbox row is created through one of the ``build_*`` functions here.
This ensures:
  - event_id is always a fresh UUID v4
  - occurred_at is always the current UTC time in ISO 8601 format
  - event_version and producer are set consistently
  - payload field names exactly match the Phase 7B Avro schema definitions
  - correlation_id is threaded through where Flask's request context is
    available

Serialisation note (Phase 7C):
  Payloads are serialised as plain JSON. Live Avro serialisation via
  Confluent Schema Registry will be activated in Phase 7F.

Usage:
    from app.services.event_factory import build_fan_followed_artist
    from app.models.outbox import OutboxEvent

    outbox_row = build_fan_followed_artist(follow, correlation_id=req_id)
    db.session.add(outbox_row)
    # outbox_row is committed in the same transaction as the Follow row.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.outbox import OutboxEvent

# ------------------------------------------------------------------ #
# Topic constants                                                       #
# ------------------------------------------------------------------ #

TOPIC_SOCIAL = "artisthub.social"
TOPIC_CATALOG = "artisthub.catalog"
TOPIC_IDENTITY = "artisthub.identity"

# ------------------------------------------------------------------ #
# Internal helpers                                                      #
# ------------------------------------------------------------------ #

_PRODUCER = "artisthub-api"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string ending in 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _uuid() -> str:
    """Return a new UUID v4 as a hyphenated string."""
    return str(uuid.uuid4())


def _outbox(
    event_type: str,
    event_version: str,
    topic: str,
    message_key: str,
    payload_dict: dict,
    correlation_id: Optional[str],
) -> OutboxEvent:
    """
    Assemble an OutboxEvent from the common envelope + caller-supplied payload.

    The full event (envelope + payload) is JSON-serialised into the
    ``payload`` column so the relay can publish it verbatim.
    """
    event_id = _uuid()
    occurred_at = _now_iso()

    full_event = {
        "event_id": event_id,
        "event_type": event_type,
        "event_version": event_version,
        "occurred_at": occurred_at,
        "producer": _PRODUCER,
        "correlation_id": correlation_id,
        "payload": payload_dict,
    }

    return OutboxEvent(
        event_id=event_id,
        event_type=event_type,
        event_version=event_version,
        topic=topic,
        message_key=message_key,
        payload=json.dumps(full_event),
        correlation_id=correlation_id,
    )


# ------------------------------------------------------------------ #
# Social — follows                                                      #
# ------------------------------------------------------------------ #

def build_fan_followed_artist(
    follow,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``fan.followed.artist`` event.

    Source: Follow model after POST /api/follows commit.
    Topic:  artisthub.social
    Key:    artist_id (preserves per-artist ordering)

    Payload fields match FanFollowedArtistPayload in
    kafka/schemas/social/fan_followed_artist.avsc.
    """
    return _outbox(
        event_type="fan.followed.artist",
        event_version="1",
        topic=TOPIC_SOCIAL,
        message_key=str(follow.artist_id),
        payload_dict={
            "follow_id": follow.id,
            "fan_id": follow.fan_id,
            "artist_id": follow.artist_id,
            # created_at is set by SQLAlchemy at flush; use now() as fallback
            # when the factory is called before flush (pre-commit path).
            "followed_at": (
                follow.created_at.isoformat()
                if follow.created_at else _now_iso()
            ),
        },
        correlation_id=correlation_id,
    )


def build_fan_unfollowed_artist(
    fan_id: int,
    artist_id: int,
    occurred_at: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``fan.unfollowed.artist`` event.

    Source: follows.py DELETE route, before the Follow row is deleted.
    Topic:  artisthub.social
    Key:    artist_id

    Note: The Follow row is deleted in the same transaction, so we capture
    the IDs before the deletion and pass them as arguments.

    Payload fields match FanUnfollowedArtistPayload in
    kafka/schemas/social/fan_unfollowed_artist.avsc.
    """
    return _outbox(
        event_type="fan.unfollowed.artist",
        event_version="1",
        topic=TOPIC_SOCIAL,
        message_key=str(artist_id),
        payload_dict={
            "fan_id": fan_id,
            "artist_id": artist_id,
            "unfollowed_at": occurred_at or _now_iso(),
        },
        correlation_id=correlation_id,
    )


# ------------------------------------------------------------------ #
# Social — posts                                                        #
# ------------------------------------------------------------------ #

def build_artist_post_created(
    post,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.post.created`` event.

    Source: SocialPost model after POST /api/posts commit.
    Topic:  artisthub.social
    Key:    artist_id

    Payload fields match ArtistPostCreatedPayload in
    kafka/schemas/social/artist_post_created.avsc.
    Note: the Avro schema uses ``posted_at`` (not ``created_at``) for
    the timestamp field name.
    """
    return _outbox(
        event_type="artist.post.created",
        event_version="1",
        topic=TOPIC_SOCIAL,
        message_key=str(post.artist_id),
        payload_dict={
            "post_id": post.id,
            "artist_id": post.artist_id,
            "body": post.body,
            "image_url": post.image_url,
            "posted_at": (
                post.created_at.isoformat()
                if post.created_at else _now_iso()
            ),
        },
        correlation_id=correlation_id,
    )


def build_artist_post_deleted(
    post_id: int,
    artist_id: int,
    deleted_at: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.post.deleted`` event.

    Source: posts.py DELETE route, before the SocialPost row is deleted.
    Topic:  artisthub.social
    Key:    artist_id

    Payload fields match ArtistPostDeletedPayload in
    kafka/schemas/social/artist_post_deleted.avsc.
    """
    return _outbox(
        event_type="artist.post.deleted",
        event_version="1",
        topic=TOPIC_SOCIAL,
        message_key=str(artist_id),
        payload_dict={
            "post_id": post_id,
            "artist_id": artist_id,
            "deleted_at": deleted_at or _now_iso(),
        },
        correlation_id=correlation_id,
    )


# ------------------------------------------------------------------ #
# Catalog — releases                                                    #
# ------------------------------------------------------------------ #

def build_artist_release_created(
    release,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.release.created`` event.

    Source: MusicRelease model after POST /api/releases commit.
    Topic:  artisthub.catalog
    Key:    artist_id

    Payload fields match ArtistReleaseCreatedPayload in
    kafka/schemas/catalog/artist_release_created.avsc.
    """
    return _outbox(
        event_type="artist.release.created",
        event_version="1",
        topic=TOPIC_CATALOG,
        message_key=str(release.artist_id),
        payload_dict={
            "release_id": release.id,
            "artist_id": release.artist_id,
            "title": release.title,
            "release_type": release.release_type,
            "genre": release.genre,
            "description": release.description,
            "artwork_url": release.artwork_url,
            "streaming_url": release.streaming_url,
            "release_date": (
                release.release_date.isoformat()
                if release.release_date else None
            ),
            "created_at": (
                release.created_at.isoformat()
                if release.created_at else _now_iso()
            ),
        },
        correlation_id=correlation_id,
    )


def build_artist_release_updated(
    release,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.release.updated`` event.

    Source: MusicRelease model after PUT /api/releases/<id> commit.
    Topic:  artisthub.catalog
    Key:    artist_id

    Carries the full post-update state (not a delta).
    Payload fields match ArtistReleaseUpdatedPayload in
    kafka/schemas/catalog/artist_release_updated.avsc.
    """
    return _outbox(
        event_type="artist.release.updated",
        event_version="1",
        topic=TOPIC_CATALOG,
        message_key=str(release.artist_id),
        payload_dict={
            "release_id": release.id,
            "artist_id": release.artist_id,
            "title": release.title,
            "release_type": release.release_type,
            "genre": release.genre,
            "description": release.description,
            "artwork_url": release.artwork_url,
            "streaming_url": release.streaming_url,
            "release_date": (
                release.release_date.isoformat()
                if release.release_date else None
            ),
        },
        correlation_id=correlation_id,
    )


def build_artist_release_deleted(
    release_id: int,
    artist_id: int,
    deleted_at: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.release.deleted`` event.

    Source: releases.py DELETE route, before the MusicRelease row is deleted.
    Topic:  artisthub.catalog
    Key:    artist_id

    Payload fields match ArtistReleaseDeletedPayload in
    kafka/schemas/catalog/artist_release_deleted.avsc.
    """
    return _outbox(
        event_type="artist.release.deleted",
        event_version="1",
        topic=TOPIC_CATALOG,
        message_key=str(artist_id),
        payload_dict={
            "release_id": release_id,
            "artist_id": artist_id,
            "deleted_at": deleted_at or _now_iso(),
        },
        correlation_id=correlation_id,
    )


# ------------------------------------------------------------------ #
# Catalog — merchandise                                                 #
# ------------------------------------------------------------------ #

def build_artist_merch_created(
    product,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.merch.created`` event.

    Source: MerchProduct model after POST /api/merch commit.
    Topic:  artisthub.catalog
    Key:    artist_id

    Payload fields match ArtistMerchCreatedPayload in
    kafka/schemas/catalog/artist_merch_created.avsc.
    """
    return _outbox(
        event_type="artist.merch.created",
        event_version="1",
        topic=TOPIC_CATALOG,
        message_key=str(product.artist_id),
        payload_dict={
            "product_id": product.id,
            "artist_id": product.artist_id,
            "product_name": product.product_name,
            "price": float(product.price),
            "description": product.description,
            "image_url": product.image_url,
            "inventory_quantity": product.inventory_quantity,
            "created_at": (
                product.created_at.isoformat()
                if product.created_at else _now_iso()
            ),
        },
        correlation_id=correlation_id,
    )


def build_artist_merch_updated(
    product,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.merch.updated`` event.

    Source: MerchProduct model after PUT /api/merch/<id> commit.
    Topic:  artisthub.catalog
    Key:    artist_id

    Carries the full post-update state (not a delta).
    Payload fields match ArtistMerchUpdatedPayload in
    kafka/schemas/catalog/artist_merch_updated.avsc.
    """
    return _outbox(
        event_type="artist.merch.updated",
        event_version="1",
        topic=TOPIC_CATALOG,
        message_key=str(product.artist_id),
        payload_dict={
            "product_id": product.id,
            "artist_id": product.artist_id,
            "product_name": product.product_name,
            "price": float(product.price),
            "description": product.description,
            "image_url": product.image_url,
            "inventory_quantity": product.inventory_quantity,
        },
        correlation_id=correlation_id,
    )


def build_artist_merch_deleted(
    product_id: int,
    artist_id: int,
    deleted_at: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.merch.deleted`` event.

    Source: merch.py DELETE route, before the MerchProduct row is deleted.
    Topic:  artisthub.catalog
    Key:    artist_id

    Payload fields match ArtistMerchDeletedPayload in
    kafka/schemas/catalog/artist_merch_deleted.avsc.
    """
    return _outbox(
        event_type="artist.merch.deleted",
        event_version="1",
        topic=TOPIC_CATALOG,
        message_key=str(artist_id),
        payload_dict={
            "product_id": product_id,
            "artist_id": artist_id,
            "deleted_at": deleted_at or _now_iso(),
        },
        correlation_id=correlation_id,
    )


# ------------------------------------------------------------------ #
# Identity                                                              #
# ------------------------------------------------------------------ #

def build_artist_registered(
    artist,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.registered`` event.

    Source: Artist model after POST /api/auth/artist/register commit.
    Topic:  artisthub.identity  (90-day retention; PII boundary)
    Key:    artist_id

    PII note: ``email`` is included here because the identity topic drives
    onboarding workflows. It must NEVER appear in social or catalog events.

    Payload fields match ArtistRegisteredPayload in
    kafka/schemas/identity/artist_registered.avsc.
    """
    return _outbox(
        event_type="artist.registered",
        event_version="1",
        topic=TOPIC_IDENTITY,
        message_key=str(artist.id),
        payload_dict={
            "artist_id": artist.id,
            "email": artist.email,
            "display_name": artist.display_name,
            "genre": artist.genre,
            "location": artist.location,
            "registered_at": (
                artist.created_at.isoformat()
                if artist.created_at else _now_iso()
            ),
        },
        correlation_id=correlation_id,
    )


def build_artist_profile_updated(
    artist,
    correlation_id: Optional[str] = None,
) -> OutboxEvent:
    """
    Build an outbox row for the ``artist.profile.updated`` event.

    Source: Artist model after PUT /api/artists/<id> commit.
    Topic:  artisthub.identity
    Key:    artist_id

    Note: email is intentionally excluded — this event carries public
    profile fields only; email updates are not currently supported.

    Payload fields match ArtistProfileUpdatedPayload in
    kafka/schemas/identity/artist_profile_updated.avsc.
    """
    return _outbox(
        event_type="artist.profile.updated",
        event_version="1",
        topic=TOPIC_IDENTITY,
        message_key=str(artist.id),
        payload_dict={
            "artist_id": artist.id,
            "display_name": artist.display_name,
            "bio": artist.bio,
            "genre": artist.genre,
            "location": artist.location,
            "profile_image_url": artist.profile_image_url,
        },
        correlation_id=correlation_id,
    )
