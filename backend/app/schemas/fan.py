"""
schemas/fan.py

Marshmallow validation schema for Fan registration.

Fan registration requires a username, email, and password. Auth
(login/logout) endpoints will be added in Phase 2. For now this schema
validates the fields needed to create a Fan row.
"""

from marshmallow import Schema, fields, validate


class FanRegisterSchema(Schema):
    """
    Validates the request body for POST /api/fans/register.

    Required:
        username  — public display name, 1–100 chars, unique in DB.
        email     — login credential, valid email format.
        password  — plain-text password, min 8 chars (hashed before storage).
    """

    username = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=100),
    )
    email = fields.Email(
        required=True,
    )
    password = fields.Str(
        required=True,
        validate=validate.Length(min=8),
        # load_only prevents the password ever appearing in dumps.
        load_only=True,
    )
