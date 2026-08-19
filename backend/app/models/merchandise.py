"""
models/merchandise.py

SQLAlchemy model for a Merchandise Product on ArtistHub.

Design decisions:
  - ``product_name`` (not ``name``) avoids shadowing Python's built-in
    ``name`` attribute and is more descriptive in queries and logs.
  - ``price`` is NUMERIC(10, 2) — two decimal places, appropriate for
    currency. Never use Float for money (floating-point rounding errors).
  - ``inventory_quantity`` is nullable: NULL means "unlimited / not
    tracked"; 0 means "out of stock"; any positive integer is the
    available count. This avoids a boolean + integer pair.
  - ``image_url`` is an external link only. ArtistHub does not host
    image files in the MVP.
  - No payment processing in the MVP. The ``Order`` model (Phase 3)
    will reference MerchProduct via the polymorphic item_type / item_id
    pattern defined in artisthub-plan.md Section 3.
"""

from datetime import datetime
from app.extensions import db


class MerchProduct(db.Model):
    """
    Represents a merchandise item listed by an Artist on ArtistHub.

    Fans can browse all merch on GET /api/merch or view an artist's
    products via the nested endpoint GET /api/artists/<id>/merch.
    No purchases are processed in the MVP — that is Phase 3.
    """

    __tablename__ = "merch_product"

    # Primary key.
    id = db.Column(db.Integer, primary_key=True)

    # Foreign key — which artist owns this product listing.
    # ON DELETE CASCADE removes products when the artist is deleted.
    artist_id = db.Column(
        db.Integer,
        db.ForeignKey("artist.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Required fields.
    product_name = db.Column(db.String(200), nullable=False)

    # Optional extended description.
    description = db.Column(db.Text, nullable=True)

    # Price in the artist's chosen currency (USD by default).
    # NUMERIC(10, 2) avoids floating-point rounding errors.
    price = db.Column(db.Numeric(10, 2), nullable=False)

    # External product image. Not hosted by ArtistHub.
    image_url = db.Column(db.String(500), nullable=True)

    # NULL = unlimited / not tracked.
    # 0   = out of stock.
    # N>0 = N units available.
    inventory_quantity = db.Column(db.Integer, nullable=True)

    # Audit timestamp.
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )

    # ORM relationship — ``product.artist`` resolves to the owning Artist.
    artist = db.relationship(
        "Artist",
        backref=db.backref(
            "merch",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    def to_dict(self) -> dict:
        """
        Serialise the product to a plain dict safe for JSON responses.

        ``price`` is cast to float for JSON serialisation — the Numeric
        type returns a Python Decimal which is not JSON-serialisable by
        default. Precision is preserved to 2 decimal places.
        """
        return {
            "id": self.id,
            "artist_id": self.artist_id,
            "product_name": self.product_name,
            "description": self.description,
            "price": float(self.price),
            "image_url": self.image_url,
            "inventory_quantity": self.inventory_quantity,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<MerchProduct id={self.id} "
            f"product_name={self.product_name!r} "
            f"artist_id={self.artist_id}>"
        )
