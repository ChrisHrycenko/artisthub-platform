"""
schemas/artist.py

Marshmallow validation schemas for Artist create and update operations.

Two schemas are intentionally separate:
  - ArtistCreateSchema  — used for POST /api/artists
                          display_name is REQUIRED; all others optional.
  - ArtistUpdateSchema  — used for PUT /api/artists/:id
                          every field is optional (partial update semantics).

Usage in a route:
    from app.schemas.artist import ArtistCreateSchema, ArtistUpdateSchema
    from marshmallow import ValidationError

    schema = ArtistCreateSchema()
    try:
        data = schema.load(request.get_json())
    except ValidationError as err:
        return error(str(err.messages), 400)
"""

from marshmallow import Schema, fields, validate


class ArtistCreateSchema(Schema):
    """
    Validates the request body for POST /api/artists.

    Required fields:
        display_name  — public artist name shown on profile and listings.

    Optional fields:
        bio, genre, location, profile_image_url
    """

    display_name = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Public artist name shown on listings."},
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
        # require_tld=False lets localhost URLs validate in development
        # without special-casing the environment.
        require_tld=False,
    )


class ArtistUpdateSchema(Schema):
    """
    Validates the request body for PUT /api/artists/:id.

    Every field is optional — callers send only the fields they want to change.
    An empty body is accepted (no-op update).
    """

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
