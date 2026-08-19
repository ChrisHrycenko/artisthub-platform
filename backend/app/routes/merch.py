"""
routes/merch.py

Merchandise Blueprint for ArtistHub.

Endpoints
---------
GET    /api/merch             Browse all products, paginated. Public.
GET    /api/merch/<id>        Retrieve a single product. Public.
POST   /api/merch             Create a product listing. Protected (Artist).
PUT    /api/merch/<id>        Update own product. Protected (owner only).
DELETE /api/merch/<id>        Delete own product. Protected (owner only).

The nested artist-scoped endpoint lives in routes/artists.py:
    GET /api/artists/<id>/merch

Auth / ownership
----------------
- POST:   authenticated artist; artist_id set from current_user.id.
- PUT / DELETE: caller must own the product; returns 403 otherwise.
- GET:    fully public — no payment, no auth required to browse.

No purchases are processed here. The future Order endpoint (Phase 3)
will POST to /api/orders with item_type="merch" and item_id=<product_id>.
See README.md — Payment Integration Architecture for the full plan.

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
from app.models.merchandise import MerchProduct
from app.schemas.merch import MerchCreateSchema, MerchUpdateSchema
from app.services.event_factory import (
    build_artist_merch_created,
    build_artist_merch_updated,
    build_artist_merch_deleted,
)
from app.utils.responses import success, error

merch_bp = Blueprint("merch", __name__)

_create_schema = MerchCreateSchema()
_update_schema = MerchUpdateSchema()

MAX_PER_PAGE = 50


@merch_bp.get("/merch")
def list_merch():
    """
    Browse all merchandise products across all artists, newest first.

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
    """
    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", 20, type=int),
        MAX_PER_PAGE,
    )

    pagination = (
        MerchProduct.query
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


@merch_bp.get("/merch/<int:product_id>")
def get_merch(product_id: int):
    """
    Retrieve a single merchandise product by its primary key.

    Path parameter:
        product_id (int) — primary key of the product.

    Response 200:
        { "status": "success", "data": { "product": { ...fields... } } }

    Response 404:
        { "status": "error", "error": "Product not found." }
    """
    product = db.session.get(MerchProduct, product_id)
    if product is None:
        return error("Product not found.", 404)

    return success({"product": product.to_dict()})


@merch_bp.post("/merch")
@login_required
def create_merch():
    """
    Create a new merchandise product listing for the authenticated artist.

    ``artist_id`` is set from ``current_user.id`` — artists cannot list
    products under another artist's name.

    Request body (JSON):
        product_name        string, required, 1–200 chars
        price               float, required, >= 0
        description         string, optional, max 5000 chars
        image_url           URL string, optional
        inventory_quantity  int or null, optional (null = unlimited)

    Response 201:
        { "status": "success", "data": { "product": { ...fields... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }
    """
    try:
        data = _create_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    product = MerchProduct(
        artist_id=current_user.id,
        product_name=data["product_name"],
        description=data.get("description"),
        price=data["price"],
        image_url=data.get("image_url"),
        inventory_quantity=data.get("inventory_quantity"),
    )
    db.session.add(product)
    db.session.add(build_artist_merch_created(product))
    db.session.commit()

    return success({"product": product.to_dict()}, 201)


@merch_bp.put("/merch/<int:product_id>")
@login_required
def update_merch(product_id: int):
    """
    Update a merchandise product owned by the authenticated artist.

    Path parameter:
        product_id (int) — primary key of the product.

    Request body (JSON) — all fields optional:
        product_name, description, price, image_url, inventory_quantity

    Response 200:
        { "status": "success", "data": { "product": { ...updated... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }

    Response 403:
        { "status": "error",
          "error": "You may only edit your own products." }

    Response 404:
        { "status": "error", "error": "Product not found." }
    """
    product = db.session.get(MerchProduct, product_id)
    if product is None:
        return error("Product not found.", 404)

    if product.artist_id != current_user.id:
        return error("You may only edit your own products.", 403)

    try:
        data = _update_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    # Apply only explicitly provided (non-None) fields.
    if data.get("product_name") is not None:
        product.product_name = data["product_name"]
    if data.get("description") is not None:
        product.description = data["description"]
    if data.get("price") is not None:
        product.price = data["price"]
    if data.get("image_url") is not None:
        product.image_url = data["image_url"]
    # inventory_quantity can be explicitly set to 0 or None, so check
    # the key presence rather than truthiness.
    if "inventory_quantity" in data and data["inventory_quantity"] != -1:
        product.inventory_quantity = data["inventory_quantity"]

    db.session.add(build_artist_merch_updated(product))
    db.session.commit()
    return success({"product": product.to_dict()})


@merch_bp.delete("/merch/<int:product_id>")
@login_required
def delete_merch(product_id: int):
    """
    Delete a merchandise product owned by the authenticated artist.

    Path parameter:
        product_id (int) — primary key of the product.

    Response 200:
        { "status": "success", "data": { "message": "Product deleted." } }

    Response 403:
        { "status": "error",
          "error": "You may only delete your own products." }

    Response 404:
        { "status": "error", "error": "Product not found." }
    """
    product = db.session.get(MerchProduct, product_id)
    if product is None:
        return error("Product not found.", 404)

    if product.artist_id != current_user.id:
        return error("You may only delete your own products.", 403)

    # Capture IDs before deletion.
    prod_id = product.id
    prod_artist_id = product.artist_id
    db.session.delete(product)
    db.session.add(build_artist_merch_deleted(prod_id, prod_artist_id))
    db.session.commit()
    return success({"message": "Product deleted."})
