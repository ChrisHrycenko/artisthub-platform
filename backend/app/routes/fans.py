"""
routes/fans.py

Fans Blueprint for ArtistHub.

Endpoints
---------
POST /api/fans/register    Create a new fan account. Public.
GET  /api/fans/<id>        Retrieve a fan's public profile. Public.

Auth note: Fan login/logout will be added in Phase 2 (auth Blueprint).
For now, fans can register and their record can be queried, but there
is no login endpoint yet. Tests authenticate fans directly via
Flask-Login's session_transaction helper (same pattern as artist_client
in conftest.py).
"""

from flask import Blueprint, request
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from app.extensions import db, bcrypt
from app.models.fan import Fan
from app.schemas.fan import FanRegisterSchema
from app.utils.responses import success, error

fans_bp = Blueprint("fans", __name__)

_register_schema = FanRegisterSchema()


@fans_bp.post("/fans/register")
def register_fan():
    """
    Create a new fan account.

    Validates the request body, hashes the password with bcrypt, and
    inserts a new Fan row. Returns 409 if the email or username is
    already taken.

    Request body (JSON):
        username  string, required, 1–100 chars, unique
        email     string, required, valid email format, unique
        password  string, required, min 8 chars (never stored plain)

    Response 201:
        { "status": "success", "data": { "fan": { ...fields... } } }

    Response 400:
        { "status": "error", "error": { ...validation errors... } }

    Response 409:
        { "status": "error", "error": "Email or username already registered." }
    """
    try:
        data = _register_schema.load(request.get_json(silent=True) or {})
    except ValidationError as err:
        return error(err.messages, 400)

    # Hash the password before any DB access.
    pw_hash = bcrypt.generate_password_hash(data["password"]).decode("utf-8")

    fan = Fan(
        username=data["username"],
        email=data["email"],
        password_hash=pw_hash,
    )
    db.session.add(fan)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("Email or username already registered.", 409)

    return success({"fan": fan.to_dict()}, 201)


@fans_bp.get("/fans/<int:fan_id>")
def get_fan(fan_id: int):
    """
    Retrieve a fan's public profile by ID.

    Path parameter:
        fan_id (int) — primary key of the fan.

    Response 200:
        { "status": "success", "data": { "fan": { ...fields... } } }

    Response 404:
        { "status": "error", "error": "Fan not found." }
    """
    fan = db.session.get(Fan, fan_id)
    if fan is None:
        return error("Fan not found.", 404)

    return success({"fan": fan.to_dict()})
