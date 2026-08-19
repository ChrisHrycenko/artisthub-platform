"""
schemas/auth.py

Marshmallow schemas for Auth endpoints.

ArtistLoginSchema  Validates POST /api/auth/artist/login body.
FanLoginSchema     Validates POST /api/auth/fan/login body.

Note: Registration schemas live in their respective model schema files
(ArtistCreateSchema, FanRegisterSchema).
"""

from marshmallow import Schema, fields, validate


class LoginSchema(Schema):
    """Shared login schema — email + password."""

    email = fields.Email(required=True)
    password = fields.Str(
        required=True,
        load_only=True,
        validate=validate.Length(min=1),
    )


#: Re-export as explicit aliases so import sites are self-documenting.
ArtistLoginSchema = LoginSchema
FanLoginSchema = LoginSchema
