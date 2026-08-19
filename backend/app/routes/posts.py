"""
routes/posts.py

Posts Blueprint for ArtistHub.

Endpoints
---------
GET    /api/posts             Global feed — all posts, newest first. Public.
GET    /api/posts/<id>        Single post detail. Public.
POST   /api/posts             Create a post. Protected (Artist only).
DELETE /api/posts/<id>        Delete own post. Protected (owner only).

Posts are immutable after creation — PUT is intentionally not provided.
Artists may delete their own posts; they may not edit them.

The nested artist-scoped endpoint lives in routes/artists.py:
    GET /api/artists/<id>/posts

Auth / ownership
----------------
- POST:   authenticated artist; artist_id set from current_user.id.
- DELETE: caller must own the post; returns 403 otherwise.
- GET:    fully public.

Pagination
----------
All list endpoints accept:
    ?page=<int>       1-based (default 1)
    ?per_page=<int>   max 50  (default 20)
"""

from flask import Blueprint, request
from flask_login import current_user, login_required
from marshmallow import ValidationError

from app.extensions import db
from app.models.post import SocialPost
from app.schemas.post import PostCreateSchema
from app.utils.responses import success, error

posts_bp = Blueprint("posts", __name__)

_create_schema = PostCreateSchema()

MAX_PER_PAGE = 50


@posts_bp.get("/posts")
def list_posts():
    """
    Return the global feed of all posts, newest first.

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
    """
    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", 20, type=int),
        MAX_PER_PAGE,
    )

    pagination = (
        SocialPost.query
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


@posts_bp.get("/posts/<int:post_id>")
def get_post(post_id: int):
    """
    Retrieve a single post by its primary key.

    Path parameter:
        post_id (int) — primary key of the post.

    Response 200:
        { "status": "success", "data": { "post": { ...fields... } } }

    Response 404:
        { "status": "error", "error": "Post not found." }
    """
    post = db.session.get(SocialPost, post_id)
    if post is None:
        return error("Post not found.", 404)

    return success({"post": post.to_dict()})


@posts_bp.post("/posts")
@login_required
def create_post():
    """
    Publish a new social post as the authenticated artist.

    ``artist_id`` is taken from ``current_user.id`` — a caller cannot
    post on behalf of another artist.

    Request body (JSON):
        body       string, required, 1–2000 chars
        image_url  URL string, optional — external image only

    Response 201:
        { "status": "success", "data": { "post": { ...fields... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }
    """
    try:
        data = _create_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    post = SocialPost(
        artist_id=current_user.id,
        body=data["body"],
        image_url=data.get("image_url"),
    )
    db.session.add(post)
    db.session.commit()

    return success({"post": post.to_dict()}, 201)


@posts_bp.delete("/posts/<int:post_id>")
@login_required
def delete_post(post_id: int):
    """
    Delete a post owned by the authenticated artist.

    Path parameter:
        post_id (int) — primary key of the post.

    Response 200:
        { "status": "success", "data": { "message": "Post deleted." } }

    Response 403:
        { "status": "error", "error": "You may only delete your own posts." }

    Response 404:
        { "status": "error", "error": "Post not found." }
    """
    post = db.session.get(SocialPost, post_id)
    if post is None:
        return error("Post not found.", 404)

    if post.artist_id != current_user.id:
        return error("You may only delete your own posts.", 403)

    db.session.delete(post)
    db.session.commit()
    return success({"message": "Post deleted."})
