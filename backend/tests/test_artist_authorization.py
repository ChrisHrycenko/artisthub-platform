"""Regression coverage for typed identities and artist mutation ownership."""

from contextlib import ExitStack
from importlib import import_module
from unittest.mock import patch

import pytest

from app.models.artist import Artist
from app.models.fan import Fan
from app.models.merchandise import MerchProduct
from app.models.outbox import OutboxEvent
from app.models.post import SocialPost
from app.models.release import MusicRelease


# method, URL template, valid payload, target model, changed field, event count
MUTATIONS = [
    ("POST", "/api/artists", {"display_name": "Changed"}, Artist, "display_name", 0),
    ("PUT", "/api/artists/{artist}", {"display_name": "Changed"}, Artist, "display_name", 1),
    ("POST", "/api/releases", {"title": "Changed"}, MusicRelease, "title", 1),
    ("PUT", "/api/releases/{release}", {"title": "Changed"}, MusicRelease, "title", 1),
    ("DELETE", "/api/releases/{release}", None, MusicRelease, None, 1),
    ("POST", "/api/posts", {"body": "Changed"}, SocialPost, "body", 1),
    ("DELETE", "/api/posts/{post}", None, SocialPost, None, 1),
    ("POST", "/api/merch", {"product_name": "Changed", "price": 10}, MerchProduct, "product_name", 1),
    ("PUT", "/api/merch/{merch}", {"product_name": "Changed"}, MerchProduct, "product_name", 1),
    ("DELETE", "/api/merch/{merch}", None, MerchProduct, None, 1),
]


@pytest.fixture
def targets(artist_record, release_record, post_record, merch_record):
    """Use shared fixtures to identify resources owned by one artist."""
    return {
        "artist": artist_record.id,
        "release": release_record.id,
        "post": post_record.id,
        "merch": merch_record.id,
    }


def snapshot(db_) -> dict:
    """Read persisted columns, including outbox payloads, after expiration."""
    db_.session.expire_all()
    return {
        model.__name__: sorted(
            [tuple(getattr(row, column.key) for column in model.__table__.columns)
             for row in db_.session.query(model).all()],
            key=repr,
        )
        for model in (Artist, Fan, MusicRelease, SocialPost, MerchProduct, OutboxEvent)
    }


@pytest.mark.parametrize("principal,mutation", [
    (principal, mutation)
    for principal in ("anonymous", "fan", "other_artist")
    for mutation in MUTATIONS
    if principal != "other_artist" or "{" in mutation[1]
])
def test_rejected_mutation_preserves_state_and_events(
    mutation, principal, client, db_, targets, artist_record, fan_record,
):
    """Deny wrong identities/owners before committing or emitting events."""
    method, template, payload, _, _, _ = mutation
    if principal == "fan":
        assert fan_record.id == artist_record.id  # Deliberate cross-table collision.
        response = client.post("/api/auth/fan/login", json={
            "email": fan_record.email, "password": "fanpass123",
        })
        assert response.status_code == 200
    elif principal == "other_artist":
        other = Artist(
            email="other-authorization@example.com",
            password_hash=artist_record.password_hash,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.commit()
        response = client.post("/api/auth/artist/login", json={
            "email": other.email, "password": "password123",
        })
        assert response.status_code == 200

    before = snapshot(db_)
    # A rejected request must not commit, even if its writes were later undone.
    route_module = import_module(f"app.routes.{template.split('/')[2]}")
    with ExitStack() as stack:
        builders = [
            stack.enter_context(patch.object(route_module, name, wraps=getattr(route_module, name)))
            for name in dir(route_module) if name.startswith("build_")
        ]
        commit = stack.enter_context(patch.object(db_.session, "commit", wraps=db_.session.commit))
        response = client.open(template.format(**targets), method=method, json=payload)
        commit.assert_not_called()
        for builder in builders:
            builder.assert_not_called()
    assert response.status_code == (401 if principal == "anonymous" else 403)
    if principal != "anonymous":
        assert response.get_json()["status"] == "error"
    assert snapshot(db_) == before


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_owner_can_mutate(mutation, artist_client, db_, targets):
    """Owners retain successful mutations and their existing event contracts."""
    method, template, payload, model, field, event_count = mutation
    before_events = OutboxEvent.query.count()
    response = artist_client.open(template.format(**targets), method=method, json=payload)
    assert response.status_code == (201 if method == "POST" else 200)
    assert response.get_json()["status"] == "success"
    db_.session.expire_all()
    if method == "DELETE":
        target_key = {MusicRelease: "release", SocialPost: "post", MerchProduct: "merch"}[model]
        assert db_.session.get(model, targets[target_key]) is None
    else:
        row = db_.session.query(model).filter(getattr(model, field) == "Changed").one()
        assert (row.id if model is Artist else row.artist_id) == targets["artist"]
    assert OutboxEvent.query.count() == before_events + event_count


@pytest.mark.parametrize("mutation", [item for item in MUTATIONS if item[0] == "POST"])
def test_client_artist_id_cannot_select_another_owner(
    mutation, artist_client, db_, targets,
):
    """Existing schemas reject client-supplied ownership instead of trusting it."""
    other = Artist(
        email="spoof-target@example.com", password_hash="unused", display_name="Other",
    )
    db_.session.add(other)
    db_.session.commit()
    method, template, payload, _, _, _ = mutation
    before = snapshot(db_)
    response = artist_client.open(
        template.format(**targets), method=method, json={**payload, "artist_id": other.id},
    )
    assert response.status_code == 400
    assert snapshot(db_) == before
