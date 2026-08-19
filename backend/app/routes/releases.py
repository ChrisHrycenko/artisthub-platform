"""
routes/releases.py

Releases Blueprint for ArtistHub.

Endpoints
---------
GET    /api/releases             Browse all releases, paginated. Public.
GET    /api/releases/<id>        Retrieve a single release. Public.
POST   /api/releases             Create a release. Protected (Artist only).
PUT    /api/releases/<id>        Update own release. Protected (owner only).
DELETE /api/releases/<id>        Delete own release. Protected (owner only).

The nested listing endpoint for a specific artist's releases lives in
routes/artists.py:
    GET /api/artists/<id>/releases

Auth / ownership
----------------
- POST:   authenticated artist only; ``artist_id`` is taken from
          ``current_user.id`` — callers cannot create releases for others.
- PUT / DELETE: caller must own the release (``release.artist_id ==
          current_user.id``), otherwise 403.
- GET endpoints are fully public.

Pagination
----------
All list endpoints accept:
    ?page=<int>       1-based (default 1)
    ?per_page=<int>   max 50  (default 20)
    ?genre=<str>      optional genre filter (case-insensitive contains)
"""

from flask import Blueprint, request
from flask_login import current_user, login_required
from marshmallow import ValidationError

from app.extensions import db
from app.models.release import MusicRelease
from app.schemas.release import ReleaseCreateSchema, ReleaseUpdateSchema
from app.utils.responses import success, error

releases_bp = Blueprint("releases", __name__)

_create_schema = ReleaseCreateSchema()
_update_schema = ReleaseUpdateSchema()

MAX_PER_PAGE = 50


@releases_bp.get("/releases")
def list_releases():
    """
    Browse all releases across all artists, newest first.

    Query parameters:
        page     (int, default 1)    — 1-based page number.
        per_page (int, default 20)   — items per page, capped at 50.
        genre    (str, optional)     — case-insensitive substring filter.

    Response 200:
        {
            "status": "success",
            "data": {
                "releases": [ { ...release fields... }, ... ],
                "total":    <int>,
                "page":     <int>,
                "per_page": <int>,
                "pages":    <int>
            }
        }
    """
    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", 20, type=int),
        MAX_PER_PAGE,
    )
    genre_filter = request.args.get("genre", None)

    query = MusicRelease.query.order_by(MusicRelease.created_at.desc())

    # Optional genre filter — ilike is case-insensitive on SQLite and Postgres.
    if genre_filter:
        query = query.filter(
            MusicRelease.genre.ilike(f"%{genre_filter}%")
        )

    pagination = query.paginate(
        page=page, per_page=per_page, error_out=False
    )

    return success({
        "releases": [r.to_dict() for r in pagination.items],
        "total":    pagination.total,
        "page":     pagination.page,
        "per_page": pagination.per_page,
        "pages":    pagination.pages,
    })


@releases_bp.get("/releases/<int:release_id>")
def get_release(release_id: int):
    """
    Retrieve a single release by its primary key.

    Path parameter:
        release_id (int) — primary key of the release.

    Response 200:
        { "status": "success", "data": { "release": { ...fields... } } }

    Response 404:
        { "status": "error", "error": "Release not found." }
    """
    release = db.session.get(MusicRelease, release_id)
    if release is None:
        return error("Release not found.", 404)

    return success({"release": release.to_dict()})


@releases_bp.post("/releases")
@login_required
def create_release():
    """
    Create a new music release for the authenticated artist.

    ``artist_id`` is taken directly from ``current_user.id`` — callers
    cannot specify a different artist. This is intentional: an artist
    can only publish releases under their own name.

    Request body (JSON):
        title         string, required, 1–200 chars
        release_type  string, optional — one of Single/EP/Album/Mixtape/
                      Compilation/Live (default "Single")
        genre         string, optional, max 100 chars
        description   string, optional, max 5000 chars
        artwork_url   URL string, optional
        streaming_url URL string, optional
        release_date  ISO date string "YYYY-MM-DD", optional

    Response 201:
        { "status": "success", "data": { "release": { ...fields... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }
    """
    try:
        data = _create_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    release = MusicRelease(
        artist_id=current_user.id,
        title=data["title"],
        release_type=data["release_type"],
        genre=data.get("genre"),
        description=data.get("description"),
        artwork_url=data.get("artwork_url"),
        streaming_url=data.get("streaming_url"),
        release_date=data.get("release_date"),
    )
    db.session.add(release)
    db.session.commit()

    return success({"release": release.to_dict()}, 201)


@releases_bp.put("/releases/<int:release_id>")
@login_required
def update_release(release_id: int):
    """
    Update a release owned by the authenticated artist.

    Path parameter:
        release_id (int) — primary key of the release.

    Request body (JSON) — all fields optional:
        title, release_type, genre, description,
        artwork_url, streaming_url, release_date

    Response 200:
        { "status": "success", "data": { "release": { ...updated... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }

    Response 403:
        { "status": "error", "error": "You may only edit your own releases." }

    Response 404:
        { "status": "error", "error": "Release not found." }
    """
    release = db.session.get(MusicRelease, release_id)
    if release is None:
        return error("Release not found.", 404)

    # Ownership check — before parsing the body to avoid wasted work.
    if release.artist_id != current_user.id:
        return error("You may only edit your own releases.", 403)

    try:
        data = _update_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    # Apply only explicitly provided (non-None) fields.
    if data.get("title") is not None:
        release.title = data["title"]
    if data.get("release_type") is not None:
        release.release_type = data["release_type"]
    if data.get("genre") is not None:
        release.genre = data["genre"]
    if data.get("description") is not None:
        release.description = data["description"]
    if data.get("artwork_url") is not None:
        release.artwork_url = data["artwork_url"]
    if data.get("streaming_url") is not None:
        release.streaming_url = data["streaming_url"]
    if data.get("release_date") is not None:
        release.release_date = data["release_date"]

    db.session.commit()
    return success({"release": release.to_dict()})


@releases_bp.delete("/releases/<int:release_id>")
@login_required
def delete_release(release_id: int):
    """
    Delete a release owned by the authenticated artist.

    Path parameter:
        release_id (int) — primary key of the release.

    Response 200:
        { "status": "success", "data": { "message": "Release deleted." } }

    Response 403:
        { "status": "error",
          "error": "You may only delete your own releases." }

    Response 404:
        { "status": "error", "error": "Release not found." }
    """
    release = db.session.get(MusicRelease, release_id)
    if release is None:
        return error("Release not found.", 404)

    if release.artist_id != current_user.id:
        return error("You may only delete your own releases.", 403)

    db.session.delete(release)
    db.session.commit()
    return success({"message": "Release deleted."})
