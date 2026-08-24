"""
schemas/ai.py

Marshmallow validation schemas for the ArtistHub AI endpoints.

Phase 9A: validates POST /api/ai/ask request bodies.
"""

from marshmallow import Schema, fields, validate


class AiAskSchema(Schema):
    """
    Validates the request body for POST /api/ai/ask.

    Fields:
        question  (required) — the creator's natural-language question.
                               Minimum 3 characters, maximum 1000 characters.
    """

    question = fields.String(
        required=True,
        validate=validate.Length(min=3, max=1000),
        metadata={
            "description": (
                "The creator's music-business question. "
                "3–1000 characters."
            )
        },
    )
