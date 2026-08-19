"""
routes/follows.py

Follows Blueprint for ArtistHub.

Endpoints
---------
POST   /api/follows              Fan follows an artist. Protected (Fan only).
DELETE /api/follows/<artist_id>  Fan unfollows an artist. Protected (Fan only).
GET    /api/follows              List artists the fan follows. Protected.

Auth / ownership
----------------
All three endpoints require an authenticated Fan session. An Artist
session is rejected with 403 ("Only fans can follow artists.") to
prevent confusion from the dual-model auth system.

Duplicate follow
----------------
Attempting to follow an already-followed artist returns 409 (Conflict).
The DB-level UNIQUE(fan_id, artist_id) constraint is the authoritative
guard; IntegrityError is caught and surfaced as 409.

Unfollow non-existent
---------------------
Attempting to unfollow an artist the fan does not follow returns 404.
"""

from flask import Blueprint, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.artist import Artist
from app.models.fan import Fan
from app.models.follow import Follow
from app.services.event_factory import (
    build_fan_followed_artist,
    build_fan_unfollowed_artist,
)
from app.utils.responses import success, error

follows_bp = Blueprint("follows", __name__)

MAX_PER_PAGE = 50


def _require_fan():
    """
    Return an error response if ``current_user`` is not a Fan.

    Used by all follows endpoints to prevent artists from accidentally
    (or maliciously) following other artists via the same session system.

    Returns None if the check passes, or a JSON error response if not.
    """
    if not isinstance(current_user, Fan):
        return error("Only fans can follow artists.", 403)
    return None


@follows_bp.post("/follows")
@login_required
def follow_artist():
    """
    Follow an artist.

    Request body (JSON):
        artist_id  int, required — primary key of the artist to follow.

    Response 201:
        { "status": "success", "data": { "follow": { ...fields... } } }

    Response 400:
        { "status": "error", "error": "artist_id is required." }

    Response 403:
        { "status": "error", "error": "Only fans can follow artists." }

    Response 404:
        { "status": "error", "error": "Artist not found." }

    Response 409:
        { "status": "error", "error": "Already following this artist." }
    """
    guard = _require_fan()
    if guard:
        return guard

    body = request.get_json(silent=True) or {}
    artist_id = body.get("artist_id")
    if artist_id is None:
        return error("artist_id is required.", 400)

    # Validate the target artist exists.
    artist = db.session.get(Artist, artist_id)
    if artist is None:
        return error("Artist not found.", 404)

    follow = Follow(fan_id=current_user.id, artist_id=artist_id)
    db.session.add(follow)

    try:
        db.session.add(build_fan_followed_artist(follow))
        db.session.commit()
    except IntegrityError:
        # UNIQUE(fan_id, artist_id) violation — already following.
        db.session.rollback()
        return error("Already following this artist.", 409)

    return success({"follow": follow.to_dict()}, 201)


@follows_bp.delete("/follows/<int:artist_id>")
@login_required
def unfollow_artist(artist_id: int):
    """
    Unfollow an artist.

    Path parameter:
        artist_id (int) — primary key of the artist to unfollow.

    Response 200:
        { "status": "success", "data": { "message": "Unfollowed." } }

    Response 403:
        { "status": "error", "error": "Only fans can follow artists." }

    Response 404:
        { "status": "error", "error": "Not following this artist." }
    """
    guard = _require_fan()
    if guard:
        return guard

    follow = Follow.query.filter_by(
        fan_id=current_user.id,
        artist_id=artist_id,
    ).first()

    if follow is None:
        return error("Not following this artist.", 404)

    # Capture IDs before deletion — the Follow row will be removed in this tx.
    fan_id = follow.fan_id
    evt_artist_id = follow.artist_id
    db.session.delete(follow)
    db.session.add(build_fan_unfollowed_artist(fan_id, evt_artist_id))
    db.session.commit()
    return success({"message": "Unfollowed."})


@follows_bp.get("/follows")
@login_required
def list_following():
    """
    List all artists the authenticated fan is currently following.

    Query parameters:
        page     (int, default 1)   — 1-based page number.
        per_page (int, default 20)  — items per page, capped at 50.

    Response 200:
        {
            "status": "success",
            "data": {
                "following": [ { ...artist fields... }, ... ],
                "total":    <int>,
                "page":     <int>,
                "per_page": <int>,
                "pages":    <int>
            }
        }

    Response 403:
        { "status": "error", "error": "Only fans can follow artists." }
    """
    guard = _require_fan()
    if guard:
        return guard

    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", 20, type=int),
        MAX_PER_PAGE,
    )

    # current_user.following is the dynamic backref from Follow → Fan.
    # Join to Artist so we can call artist.to_dict() on results.
    pagination = (
        current_user.following
        .order_by(Follow.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    # Each item in pagination.items is a Follow row; resolve to Artist.
    artists = [
        db.session.get(Artist, f.artist_id)
        for f in pagination.items
    ]

    return success({
        "following": [a.to_dict() for a in artists if a],
        "total":     pagination.total,
        "page":      pagination.page,
        "per_page":  pagination.per_page,
        "pages":     pagination.pages,
    })
