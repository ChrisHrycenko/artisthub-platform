"""
schemas/release.py

Marshmallow validation schemas for MusicRelease create and update operations.

Two schemas follow the same pattern used by artist.py:
  - ReleaseCreateSchema  — POST /api/releases; ``title`` is required.
  - ReleaseUpdateSchema  — PUT  /api/releases/<id>; all fields optional.

``release_type`` is validated against the RELEASE_TYPES constant defined
in the model so the two sources of truth stay in sync.

``streaming_url`` and ``artwork_url`` accept full HTTP/HTTPS URLs only.
require_tld=False permits localhost URLs during development/testing.

``release_date`` is accepted as an ISO 8601 date string (YYYY-MM-DD) and
deserialised to a Python ``datetime.date`` object by marshmallow.
"""

from marshmallow import Schema, fields, validate, validates, ValidationError
from app.models.release import RELEASE_TYPES


class ReleaseCreateSchema(Schema):
    """
    Validates the request body for POST /api/releases.

    Required:
        title        — release title, 1–200 chars.

    Optional:
        release_type — one of the RELEASE_TYPES values (default "Single").
        genre, description, artwork_url, streaming_url, release_date.
    """

    title = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=200),
    )
    release_type = fields.Str(
        load_default="Single",
        validate=validate.Length(max=50),
    )
    genre = fields.Str(
        load_default=None,
        validate=validate.Length(max=100),
    )
    description = fields.Str(
        load_default=None,
        validate=validate.Length(max=5000),
    )
    artwork_url = fields.Url(
        load_default=None,
        require_tld=False,
    )
    streaming_url = fields.Url(
        load_default=None,
        require_tld=False,
    )
    # Deserialises "YYYY-MM-DD" → datetime.date automatically.
    release_date = fields.Date(
        load_default=None,
    )

    @validates("release_type")
    def validate_release_type(self, value: str) -> str:
        """
        Ensure release_type is one of the accepted values.

        This validator runs after the Length check, so by the time it
        executes we know ``value`` is a non-empty string.
        """
        if value not in RELEASE_TYPES:
            raise ValidationError(
                f"release_type must be one of: "
                f"{', '.join(RELEASE_TYPES)}."
            )
        return value


class ReleaseUpdateSchema(Schema):
    """
    Validates the request body for PUT /api/releases/<id>.

    Every field is optional — callers send only the fields they wish to
    change. An empty body is a valid no-op.
    """

    title = fields.Str(
        load_default=None,
        validate=validate.Length(min=1, max=200),
    )
    release_type = fields.Str(
        load_default=None,
        validate=validate.Length(max=50),
    )
    genre = fields.Str(
        load_default=None,
        validate=validate.Length(max=100),
    )
    description = fields.Str(
        load_default=None,
        validate=validate.Length(max=5000),
    )
    artwork_url = fields.Url(
        load_default=None,
        require_tld=False,
    )
    streaming_url = fields.Url(
        load_default=None,
        require_tld=False,
    )
    release_date = fields.Date(
        load_default=None,
    )

    @validates("release_type")
    def validate_release_type(self, value: str) -> str:
        """Reject release_type values not in the accepted list."""
        if value is not None and value not in RELEASE_TYPES:
            raise ValidationError(
                f"release_type must be one of: "
                f"{', '.join(RELEASE_TYPES)}."
            )
        return value
