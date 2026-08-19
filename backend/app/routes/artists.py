"""
routes/artists.py

Artists Blueprint for ArtistHub.

Endpoints
---------
GET  /api/artists                   List all artists, paginated. Public.
GET  /api/artists/<id>              Retrieve single artist profile. Public.
POST /api/artists                   Create artist profile. Auth required.
PUT  /api/artists/<id>              Update own profile. Owner only.
GET  /api/artists/<id>/analytics    Artist analytics snapshot. Public.
GET  /api/artists/<id>/releases     Artist's releases. Public.
GET  /api/artists/<id>/posts        Artist's social posts. Public.
GET  /api/artists/<id>/merch        Artist's merch catalog. Public.

Auth notes
----------
- POST requires an authenticated Artist session (login via auth Blueprint).
- PUT enforces ownership: the authenticated artist may only update their
  own profile. Attempting to update another artist's profile returns 403.
- GET endpoints are fully public — no session required.

Pagination
----------
All list endpoints accept:
    ?page=<int>      Page number, 1-based (default: 1)
    ?per_page=<int>  Items per page, max 50 (default: 20)
"""

from flask import Blueprint, request
from flask_login import current_user, login_required
from marshmallow import ValidationError

from app.extensions import db
from app.models.artist import Artist
from app.models.merchandise import MerchProduct
from app.models.post import SocialPost
from app.models.release import MusicRelease
from app.schemas.artist import ArtistCreateSchema, ArtistUpdateSchema
from app.utils.responses import success, error

artists_bp = Blueprint("artists", __name__)

# Marshmallow schema instances — reused across requests (stateless).
_create_schema = ArtistCreateSchema()
_update_schema = ArtistUpdateSchema()

# Maximum items per page — prevents excessively large responses.
MAX_PER_PAGE = 50


@artists_bp.get("/artists")
def list_artists():
    """
    List all artist profiles, newest first.

    Query parameters:
        page     (int, default 1)   — 1-based page number.
        per_page (int, default 20)  — items per page, capped at 50.

    Response 200:
        {
            "status": "success",
            "data": {
                "artists": [ { ...artist fields... }, ... ],
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

    # SQLAlchemy paginate() returns a Pagination object — no raw SQL.
    pagination = (
        Artist.query
        .order_by(Artist.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return success({
        "artists":  [a.to_dict() for a in pagination.items],
        "total":    pagination.total,
        "page":     pagination.page,
        "per_page": pagination.per_page,
        "pages":    pagination.pages,
    })


@artists_bp.get("/artists/<int:artist_id>")
def get_artist(artist_id: int):
    """
    Retrieve a single artist's public profile by ID.

    Path parameter:
        artist_id (int) — primary key of the artist.

    Response 200:
        { "status": "success", "data": { "artist": { ...fields... } } }

    Response 404:
        { "status": "error", "error": "Artist not found." }
    """
    artist = db.session.get(Artist, artist_id)
    if artist is None:
        return error("Artist not found.", 404)

    return success({"artist": artist.to_dict()})


@artists_bp.post("/artists")
@login_required
def create_artist():
    """
    Create a new artist profile for the currently authenticated artist.

    This endpoint is intentionally available in Phase 1 for testing but
    will be superseded by the registration flow in Phase 2. In the full
    auth flow, profile creation is folded into POST /api/auth/artist/register.

    Request body (JSON):
        display_name     string, required, 1–100 chars
        bio              string, optional, max 2000 chars
        genre            string, optional, max 100 chars
        location         string, optional, max 100 chars
        profile_image_url  URL string, optional

    Response 201:
        { "status": "success", "data": { "artist": { ...fields... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }

    Response 403:
        Returned by Flask-Login if the request has no valid session.
    """
    # Validate request body before touching the database.
    try:
        data = _create_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    # Ownership: update the currently-authenticated artist's profile fields.
    # In Phase 2, this will be driven by register — for now current_user IS
    # the artist record being populated.
    artist = current_user

    # Only write fields that were actually provided.
    artist.display_name = data["display_name"]
    if data.get("bio") is not None:
        artist.bio = data["bio"]
    if data.get("genre") is not None:
        artist.genre = data["genre"]
    if data.get("location") is not None:
        artist.location = data["location"]
    if data.get("profile_image_url") is not None:
        artist.profile_image_url = data["profile_image_url"]

    db.session.commit()
    return success({"artist": artist.to_dict()}, 201)


@artists_bp.put("/artists/<int:artist_id>")
@login_required
def update_artist(artist_id: int):
    """
    Update an existing artist profile.

    Only the artist who owns the profile may update it. Any attempt to
    update another artist's profile returns 403.

    Path parameter:
        artist_id (int) — primary key of the artist to update.

    Request body (JSON) — all fields optional:
        display_name, bio, genre, location, profile_image_url

    Response 200:
        { "status": "success", "data": { "artist": { ...updated fields... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }

    Response 403:
        { "status": "error", "error": "You may only update your own profile." }

    Response 404:
        { "status": "error", "error": "Artist not found." }
    """
    artist = db.session.get(Artist, artist_id)
    if artist is None:
        return error("Artist not found.", 404)

    # Ownership check — must come before any data processing.
    # current_user is the logged-in Artist loaded by Flask-Login.
    if artist.id != current_user.id:
        return error("You may only update your own profile.", 403)

    # Validate the update body.
    try:
        data = _update_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    # Apply only the fields that were explicitly provided (non-None).
    if data.get("display_name") is not None:
        artist.display_name = data["display_name"]
    if data.get("bio") is not None:
        artist.bio = data["bio"]
    if data.get("genre") is not None:
        artist.genre = data["genre"]
    if data.get("location") is not None:
        artist.location = data["location"]
    if data.get("profile_image_url") is not None:
        artist.profile_image_url = data["profile_image_url"]

    db.session.commit()
    return success({"artist": artist.to_dict()})


@artists_bp.get("/artists/<int:artist_id>/releases")
def list_artist_releases(artist_id: int):
    """
    List all releases belonging to a specific artist, newest first.

    This nested endpoint is the canonical way for the artist profile page
    to load releases. It is equivalent to GET /api/releases?artist_id=x
    but more RESTfully expressive and easier to cache by artist.

    Path parameter:
        artist_id (int) — primary key of the artist.

    Query parameters:
        page     (int, default 1)   — 1-based page number.
        per_page (int, default 20)  — items per page, capped at 50.

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

    Response 404:
        { "status": "error", "error": "Artist not found." }
    """
    artist = db.session.get(Artist, artist_id)
    if artist is None:
        return error("Artist not found.", 404)

    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", 20, type=int),
        MAX_PER_PAGE,
    )

    # ``artist.releases`` is a dynamic relationship — filter + paginate
    # without loading the entire collection into memory.
    pagination = (
        artist.releases
        .order_by(MusicRelease.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return success({
        "releases": [r.to_dict() for r in pagination.items],
        "total":    pagination.total,
        "page":     pagination.page,
        "per_page": pagination.per_page,
        "pages":    pagination.pages,
    })


@artists_bp.get("/artists/<int:artist_id>/posts")
def list_artist_posts(artist_id: int):
    """
    List all social posts published by a specific artist, newest first.

    Mirrors the pattern of GET /api/artists/<id>/releases. The artist
    profile page calls this endpoint to populate the posts section.

    Path parameter:
        artist_id (int) — primary key of the artist.

    Query parameters:
        page     (int, default 1)   — 1-based page number.
        per_page (int, default 20)  — items per page, capped at 50.

    Response 200:
        {
            "status": "success",
            "data": {
                "posts":    [ { ...post fields... }, ... ],
                "total":    <int>,
                "page":     <int>,
                "per_page": <int>,
                "pages":    <int>
            }
        }

    Response 404:
        { "status": "error", "error": "Artist not found." }
    """
    artist = db.session.get(Artist, artist_id)
    if artist is None:
        return error("Artist not found.", 404)

    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", 20, type=int),
        MAX_PER_PAGE,
    )

    pagination = (
        artist.posts
        .order_by(SocialPost.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return success({
        "posts":    [p.to_dict() for p in pagination.items],
        "total":    pagination.total,
        "page":     pagination.page,
        "per_page": pagination.per_page,
        "pages":    pagination.pages,
    })


@artists_bp.get("/artists/<int:artist_id>/merch")
def list_artist_merch(artist_id: int):
    """
    List all merchandise products belonging to a specific artist.

    Mirrors the pattern of GET /api/artists/<id>/releases and
    GET /api/artists/<id>/posts. The artist profile page calls this
    endpoint to populate the merchandise section.

    Path parameter:
        artist_id (int) — primary key of the artist.

    Query parameters:
        page     (int, default 1)   — 1-based page number.
        per_page (int, default 20)  — items per page, capped at 50.

    Response 200:
        {
            "status": "success",
            "data": {
                "products": [ { ...product fields... }, ... ],
                "total":    <int>,
                "page":     <int>,
                "per_page": <int>,
                "pages":    <int>
            }
        }

    Response 404:
        { "status": "error", "error": "Artist not found." }
    """
    artist = db.session.get(Artist, artist_id)
    if artist is None:
        return error("Artist not found.", 404)

    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", 20, type=int),
        MAX_PER_PAGE,
    )

    pagination = (
        artist.merch
        .order_by(MerchProduct.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return success({
        "products": [p.to_dict() for p in pagination.items],
        "total":    pagination.total,
        "page":     pagination.page,
        "per_page": pagination.per_page,
        "pages":    pagination.pages,
    })


@artists_bp.get("/artists/<int:artist_id>/analytics")
def get_artist_analytics(artist_id: int):
    """
    Return an analytics snapshot for a single artist.

    This endpoint aggregates four core content metrics and exposes them
    in a single JSON response so the analytics dashboard can be populated
    with one HTTP request instead of four.

    All counts are computed with ``SELECT COUNT(*)`` queries against the
    relevant tables — no full table scans, no Python-level iteration.

    Path parameter
    --------------
    artist_id (int) — primary key of the artist.

    Response 200
    ------------
    {
        "status": "success",
        "data": {
            "analytics": {
                "artist_id":       <int>,
                "display_name":    <str>,
                "follower_count":  <int>,
                "release_count":   <int>,
                "post_count":      <int>,
                "merch_count":     <int>,
                "generated_at":    <ISO-8601 datetime string>
            }
        }
    }

    Response 404
    ------------
    { "status": "error", "error": "Artist not found." }

    --- Future expansion ---

    The analytics payload is intentionally minimal for the MVP.  Each
    field below maps to a concrete data source that can be added later
    without breaking existing consumers (new keys are additive).

    Streaming / Play analytics
    --------------------------
    Add a ``stream_event`` table:
        id, artist_id, release_id, fan_id (nullable), streamed_at, source
    Then surface:
        "total_streams":          COUNT(*) on stream_event
        "streams_last_30_days":   COUNT(*) WHERE streamed_at > now - 30d
        "top_release":            release with highest stream count
        "streams_by_release":     [ { release_id, title, count }, ... ]

    With IBM Confluent (Apache Kafka), every play button click on the
    frontend publishes a ``release.streamed`` event to a Kafka topic.
    A consumer microservice writes these events to the stream_event table
    (or a time-series store like InfluxDB), making real-time play counts
    available without polling the Flask API.

    Sales analytics
    ---------------
    Add an ``order`` table (already in the plan):
        id, fan_id, item_type, item_id, quantity, unit_price, created_at
    Then surface:
        "total_orders":           COUNT(*) on order WHERE artist owns item
        "total_revenue":          SUM(quantity * unit_price)
        "revenue_last_30_days":   SUM(...) WHERE created_at > now - 30d
        "top_product":            merch item with highest order count
        "orders_by_product":      [ { product_id, name, orders, revenue } ]

    Audience analytics
    ------------------
    The ``follow`` table already exists.  Extend it with:
        - ``follow.source`` (e.g. "browse", "recommendation", "share")
        - ``follow.unfollowed_at`` (nullable DATETIME — soft-delete follows)
    Then surface:
        "new_followers_last_30_days":  COUNT(*) on follow.created_at range
        "churn_rate":                  unfollowed / total follows over period
        "follower_locations":          GROUP BY fan.location (once fan has
                                       location field)
        "fan_engagement_score":        composite of follows + streams + orders

    IBM watsonx AI integration
    --------------------------
    Pass the analytics snapshot to ``watsonx.ai`` to generate natural-language
    summaries:
        "Your streams are up 42% this week. Your top release is 'Summer Fade'.
         Consider posting more content — artists with 3+ posts/week see 18%
         higher follower growth."
    Integration path: ``services/watsonx.py`` wraps the REST API.
    The route calls the service after computing raw metrics and attaches
    an ``"ai_summary"`` key to the analytics payload.
    """
    from datetime import datetime as _dt, timezone as _tz

    artist = db.session.get(Artist, artist_id)
    if artist is None:
        return error("Artist not found.", 404)

    # All four counts use ``SELECT COUNT(*)`` — no rows are loaded into
    # Python memory.  The dynamic backrefs return a BaseQuery object that
    # supports .count() directly.
    follower_count = (
        artist.followers.count() if hasattr(artist, "followers") else 0
    )
    release_count = artist.releases.count()
    post_count = artist.posts.count()
    merch_count = artist.merch.count()

    return success({
        "analytics": {
            "artist_id":      artist.id,
            "display_name":   artist.display_name,
            "follower_count": follower_count,
            "release_count":  release_count,
            "post_count":     post_count,
            "merch_count":    merch_count,
            # ISO-8601 UTC timestamp — JS Date() parses the trailing Z.
            "generated_at":   (
                _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
            ),
        }
    })
