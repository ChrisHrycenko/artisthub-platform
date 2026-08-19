"""
routes/auth.py

Auth Blueprint for ArtistHub.

Endpoints
---------
POST /api/auth/artist/register  Register a new artist account.
POST /api/auth/artist/login     Artist login — sets session cookie.
POST /api/auth/fan/login        Fan login — sets session cookie.
POST /api/auth/logout           Clear the current session.
GET  /api/auth/me               Return current user id + role.

Notes
-----
- Fan *registration* lives at POST /api/fans/register (fans_bp).
  Artist *registration* lives here alongside artist login for symmetry.
- Passwords are checked with bcrypt.check_password_hash.
- login_user() from Flask-Login writes the session cookie.
- logout_user() from Flask-Login clears it.
- GET /api/auth/me is the canonical "am I logged in?" probe used by the
  frontend and by artist-profile.js to decide which button to show.
"""

from flask import Blueprint, request
from flask_login import current_user, login_user, logout_user, login_required
from marshmallow import Schema, fields, validate, ValidationError

from app.extensions import db, bcrypt
from app.models.artist import Artist
from app.models.fan import Fan
from app.utils.responses import success, error

auth_bp = Blueprint("auth", __name__)


# ------------------------------------------------------------------ #
# Registration schemas — local to this module                         #
# ------------------------------------------------------------------ #

class _ArtistRegisterSchema(Schema):
    """Validates POST /api/auth/artist/register."""

    email = fields.Email(required=True)
    password = fields.Str(
        required=True,
        load_only=True,
        validate=validate.Length(min=8),
    )
    display_name = fields.Str(
        load_default=None,
        validate=validate.Length(min=1, max=100),
    )
    bio = fields.Str(
        load_default=None,
        validate=validate.Length(max=2000),
    )
    genre = fields.Str(
        load_default=None,
        validate=validate.Length(max=100),
    )
    location = fields.Str(
        load_default=None,
        validate=validate.Length(max=100),
    )
    profile_image_url = fields.Url(
        load_default=None,
        require_tld=False,
    )


_artist_register_schema = _ArtistRegisterSchema()


# ------------------------------------------------------------------ #
# Artist registration                                                  #
# ------------------------------------------------------------------ #

@auth_bp.post("/auth/artist/register")
def artist_register():
    """
    Register a new artist account.

    On success returns the new artist's public profile and sets a session.

    Responses
    ---------
    201  Artist created and logged in.
    400  Validation error or duplicate email.
    """
    raw = request.get_json(silent=True) or {}
    try:
        data = _artist_register_schema.load(raw)
    except ValidationError as exc:
        return error(exc.messages, 400)

    # Uniqueness check.
    if Artist.query.filter_by(email=data["email"]).first():
        return error("Email already registered.", 400)

    pw_hash = bcrypt.generate_password_hash(
        data["password"]
    ).decode("utf-8")

    artist = Artist(
        email=data["email"],
        password_hash=pw_hash,
        display_name=data.get("display_name") or data["email"],
        genre=data.get("genre"),
        location=data.get("location"),
        bio=data.get("bio"),
        profile_image_url=data.get("profile_image_url"),
    )
    db.session.add(artist)
    db.session.commit()

    login_user(artist)
    return success({"artist": artist.to_dict()}, 201)


# ------------------------------------------------------------------ #
# Artist login                                                         #
# ------------------------------------------------------------------ #

@auth_bp.post("/auth/artist/login")
def artist_login():
    """
    Authenticate an artist by email + password.

    Request body
    ------------
    { "email": "...", "password": "..." }

    Responses
    ---------
    200  Logged in — session cookie set.
    400  Missing email or password.
    401  Invalid credentials.
    """
    raw = request.get_json(silent=True) or {}
    email = raw.get("email", "").strip().lower()
    password = raw.get("password", "")

    if not email or not password:
        return error("email and password are required.", 400)

    artist = Artist.query.filter_by(email=email).first()
    if not artist or not bcrypt.check_password_hash(
        artist.password_hash, password
    ):
        return error("Invalid email or password.", 401)

    login_user(artist)
    return success({"artist": artist.to_dict()}, 200)


# ------------------------------------------------------------------ #
# Fan login                                                            #
# ------------------------------------------------------------------ #

@auth_bp.post("/auth/fan/login")
def fan_login():
    """
    Authenticate a fan by email + password.

    Request body
    ------------
    { "email": "...", "password": "..." }

    Responses
    ---------
    200  Logged in — session cookie set.
    400  Missing email or password.
    401  Invalid credentials.
    """
    raw = request.get_json(silent=True) or {}
    email = raw.get("email", "").strip().lower()
    password = raw.get("password", "")

    if not email or not password:
        return error("email and password are required.", 400)

    fan = Fan.query.filter_by(email=email).first()
    if not fan or not bcrypt.check_password_hash(
        fan.password_hash, password
    ):
        return error("Invalid email or password.", 401)

    login_user(fan)
    return success({"fan": fan.to_dict()}, 200)


# ------------------------------------------------------------------ #
# Logout                                                               #
# ------------------------------------------------------------------ #

@auth_bp.post("/auth/logout")
@login_required
def logout():
    """
    Clear the current user's session cookie.

    Responses
    ---------
    200  Logged out.
    401  Not authenticated.
    """
    logout_user()
    return success({"message": "Logged out successfully."}, 200)


# ------------------------------------------------------------------ #
# Current user probe                                                   #
# ------------------------------------------------------------------ #

@auth_bp.get("/auth/me")
@login_required
def me():
    """
    Return the currently authenticated user's id and role.

    Used by the frontend to:
      - Determine which nav links to show (auth.js).
      - Decide whether to show the Edit or Follow button (artist-profile.js).

    Responses
    ---------
    200  { id, role, username, display_name } — role is 'artist' or 'fan'.
    401  Not authenticated.
    """
    # current_user is an Artist or Fan instance, depending on who logged in.
    # We return a minimal payload — just enough for the frontend to branch.
    payload = current_user.to_dict()
    payload["role"] = (
        "artist" if isinstance(current_user, Artist) else "fan"
    )
    return success(payload, 200)
