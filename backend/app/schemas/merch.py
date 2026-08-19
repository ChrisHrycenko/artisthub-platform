"""
schemas/merch.py

Marshmallow validation schemas for MerchProduct create and update.

Two schemas follow the same pattern as release.py and post.py:
  - MerchCreateSchema  — POST /api/merch
                         product_name and price are REQUIRED.
  - MerchUpdateSchema  — PUT  /api/merch/<id>
                         every field is optional (partial update semantics).

``price`` is validated as a float >= 0.  The model stores it as
NUMERIC(10, 2) so two-decimal precision is preserved in the DB.

``inventory_quantity`` accepts None (unlimited), 0 (out of stock),
or any non-negative integer.
"""

from marshmallow import Schema, fields, validate, validates, ValidationError


class MerchCreateSchema(Schema):
    """
    Validates the request body for POST /api/merch.

    Required:
        product_name  — display name shown in listings, 1–200 chars.
        price         — non-negative decimal price (e.g. 24.99).

    Optional:
        description, image_url, inventory_quantity.
    """

    product_name = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=200),
    )
    description = fields.Str(
        load_default=None,
        validate=validate.Length(max=5000),
    )
    price = fields.Float(
        required=True,
    )
    image_url = fields.Url(
        load_default=None,
        require_tld=False,
    )
    # None = unlimited; 0 = out of stock; positive int = stock count.
    inventory_quantity = fields.Integer(
        load_default=None,
        allow_none=True,
    )

    @validates("price")
    def validate_price(self, value: float) -> float:
        """Price must be zero or positive — no negative prices."""
        if value < 0:
            raise ValidationError("price must be 0 or greater.")
        return value

    @validates("inventory_quantity")
    def validate_inventory(self, value) -> int:
        """Inventory must be None (unlimited) or a non-negative integer."""
        if value is not None and value < 0:
            raise ValidationError(
                "inventory_quantity must be 0 or greater."
            )
        return value


class MerchUpdateSchema(Schema):
    """
    Validates the request body for PUT /api/merch/<id>.

    Every field is optional — callers send only the fields they wish to
    change. An empty body is a valid no-op.
    """

    product_name = fields.Str(
        load_default=None,
        validate=validate.Length(min=1, max=200),
    )
    description = fields.Str(
        load_default=None,
        validate=validate.Length(max=5000),
    )
    price = fields.Float(
        load_default=None,
        allow_none=True,
    )
    image_url = fields.Url(
        load_default=None,
        require_tld=False,
    )
    inventory_quantity = fields.Integer(
        load_default=None,
        allow_none=True,
    )

    @validates("price")
    def validate_price(self, value) -> float:
        """Price must be zero or positive when provided."""
        if value is not None and value < 0:
            raise ValidationError("price must be 0 or greater.")
        return value

    @validates("inventory_quantity")
    def validate_inventory(self, value) -> int:
        """Inventory must be None or non-negative when provided."""
        if value is not None and value < 0:
            raise ValidationError(
                "inventory_quantity must be 0 or greater."
            )
        return value
