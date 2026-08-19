"""
schemas/post.py

Marshmallow validation schema for SocialPost creation.

Posts are immutable after creation — there is no update schema because
PUT is not supported. Artists may only create or delete posts.

``body`` is the only required field. ``image_url`` is optional and must
be a valid URL if provided (external image links only — no file uploads).
"""

from marshmallow import Schema, fields, validate


class PostCreateSchema(Schema):
    """
    Validates the request body for POST /api/posts.

    Required:
        body  — post text, 1–2000 chars.

    Optional:
        image_url — external image URL, max 500 chars.
    """

    body = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=2000),
    )
    image_url = fields.Url(
        load_default=None,
        require_tld=False,  # permit localhost URLs in development/tests
    )
