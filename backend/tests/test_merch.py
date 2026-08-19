"""
tests/test_merch.py

Unit tests for the Merchandise Blueprint.

Endpoints under test:
    GET    /api/merch                  — list_merch
    GET    /api/merch/<id>             — get_merch
    POST   /api/merch                  — create_merch
    PUT    /api/merch/<id>             — update_merch
    DELETE /api/merch/<id>             — delete_merch
    GET    /api/artists/<id>/merch     — list_artist_merch (nested)

Test categories per endpoint:
    - Public access (unauthenticated GET)
    - Validation (missing/invalid fields, negative price/inventory)
    - Ownership enforcement (403 for wrong artist)
    - Happy-path CRUD
    - Inventory edge cases (null = unlimited, 0 = out of stock)
    - price serialised as float
"""

from app.models.artist import Artist
from app.models.merchandise import MerchProduct
from app.extensions import db as _db, bcrypt as _bcrypt


# ------------------------------------------------------------------ #
# GET /api/merch                                                       #
# ------------------------------------------------------------------ #

class TestListMerch:
    """Tests for the public merchandise browse endpoint."""

    def test_returns_200(self, client):
        assert client.get("/api/merch").status_code == 200

    def test_envelope_shape(self, client):
        body = client.get("/api/merch").get_json()
        assert body["status"] == "success"
        assert "data" in body

    def test_pagination_keys_present(self, client):
        data = client.get("/api/merch").get_json()["data"]
        for key in ("products", "total", "page", "per_page", "pages"):
            assert key in data

    def test_empty_catalog(self, client):
        data = client.get("/api/merch").get_json()["data"]
        assert data["products"] == []
        assert data["total"] == 0

    def test_lists_created_product(self, client, merch_record):
        data = client.get("/api/merch").get_json()["data"]
        assert data["total"] == 1
        assert data["products"][0]["id"] == merch_record.id

    def test_per_page_capped_at_50(self, client):
        data = client.get("/api/merch?per_page=999").get_json()["data"]
        assert data["per_page"] <= 50

    def test_price_is_float(self, client, merch_record):
        """price must be serialised as float, never Decimal/string."""
        data = client.get("/api/merch").get_json()["data"]
        price = data["products"][0]["price"]
        assert isinstance(price, float)
        assert price == 29.99

    def test_artist_id_present(self, client, merch_record):
        data = client.get("/api/merch").get_json()["data"]
        assert data["products"][0]["artist_id"] == merch_record.artist_id


# ------------------------------------------------------------------ #
# GET /api/merch/<id>                                                  #
# ------------------------------------------------------------------ #

class TestGetMerch:
    """Tests for the public single-product detail endpoint."""

    def test_returns_200_for_existing(self, client, merch_record):
        assert client.get(f"/api/merch/{merch_record.id}").status_code == 200

    def test_returns_product_data(self, client, merch_record):
        body = client.get(f"/api/merch/{merch_record.id}").get_json()
        product = body["data"]["product"]
        assert product["id"] == merch_record.id
        assert product["product_name"] == "Fixture T-Shirt"
        assert product["price"] == 29.99
        assert product["inventory_quantity"] == 100

    def test_returns_404_for_missing(self, client):
        r = client.get("/api/merch/99999")
        assert r.status_code == 404
        assert r.get_json()["status"] == "error"

    def test_image_url_none_when_not_set(self, client, merch_record):
        product = client.get(
            f"/api/merch/{merch_record.id}"
        ).get_json()["data"]["product"]
        assert product["image_url"] is None


# ------------------------------------------------------------------ #
# POST /api/merch                                                      #
# ------------------------------------------------------------------ #

class TestCreateMerch:
    """Tests for the authenticated product creation endpoint."""

    def test_unauthenticated_returns_401(self, client):
        r = client.post(
            "/api/merch",
            json={"product_name": "T-Shirt", "price": 19.99},
        )
        assert r.status_code == 401

    def test_missing_product_name_returns_400(self, artist_client):
        r = artist_client.post("/api/merch", json={"price": 10.0})
        assert r.status_code == 400

    def test_missing_price_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/merch", json={"product_name": "Hat"}
        )
        assert r.status_code == 400

    def test_empty_product_name_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/merch", json={"product_name": "", "price": 5.0}
        )
        assert r.status_code == 400

    def test_negative_price_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/merch",
            json={"product_name": "Hoodie", "price": -5.0},
        )
        assert r.status_code == 400

    def test_negative_inventory_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/merch",
            json={
                "product_name": "Poster",
                "price": 10.0,
                "inventory_quantity": -1,
            },
        )
        assert r.status_code == 400

    def test_zero_price_accepted(self, artist_client):
        """Free items (price=0) must be allowed."""
        r = artist_client.post(
            "/api/merch",
            json={"product_name": "Free Sticker", "price": 0},
        )
        assert r.status_code == 201

    def test_create_minimal_product(self, artist_client, artist_record):
        r = artist_client.post(
            "/api/merch",
            json={"product_name": "Cap", "price": 24.99},
        )
        assert r.status_code == 201
        product = r.get_json()["data"]["product"]
        assert product["product_name"] == "Cap"
        assert product["price"] == 24.99
        assert product["artist_id"] == artist_record.id
        assert product["inventory_quantity"] is None  # unlimited by default

    def test_create_full_product(self, artist_client):
        r = artist_client.post(
            "/api/merch",
            json={
                "product_name": "Vinyl Record",
                "description": "Limited edition 12-inch.",
                "price": 34.99,
                "image_url": "https://example.com/vinyl.jpg",
                "inventory_quantity": 50,
            },
        )
        assert r.status_code == 201
        product = r.get_json()["data"]["product"]
        assert product["description"] == "Limited edition 12-inch."
        assert product["inventory_quantity"] == 50
        assert product["image_url"] == "https://example.com/vinyl.jpg"

    def test_null_inventory_means_unlimited(self, artist_client):
        """Explicit null inventory_quantity must be stored as NULL."""
        r = artist_client.post(
            "/api/merch",
            json={
                "product_name": "Digital Download",
                "price": 9.99,
                "inventory_quantity": None,
            },
        )
        assert r.status_code == 201
        assert r.get_json()["data"]["product"]["inventory_quantity"] is None

    def test_zero_inventory_means_out_of_stock(self, artist_client):
        r = artist_client.post(
            "/api/merch",
            json={"product_name": "Sold Out Item", "price": 99.99,
                  "inventory_quantity": 0},
        )
        assert r.status_code == 201
        assert r.get_json()["data"]["product"]["inventory_quantity"] == 0

    def test_invalid_image_url_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/merch",
            json={"product_name": "Bad URL Item", "price": 5.0,
                  "image_url": "not-a-url"},
        )
        assert r.status_code == 400

    def test_artist_id_from_session(self, artist_client, artist_record):
        r = artist_client.post(
            "/api/merch",
            json={"product_name": "Ownership Check", "price": 1.0},
        )
        assert r.status_code == 201
        assert r.get_json()["data"]["product"]["artist_id"] == (
            artist_record.id
        )


# ------------------------------------------------------------------ #
# PUT /api/merch/<id>                                                  #
# ------------------------------------------------------------------ #

class TestUpdateMerch:
    """Tests for the authenticated product update endpoint."""

    def test_unauthenticated_returns_401(self, client, merch_record):
        r = client.put(
            f"/api/merch/{merch_record.id}",
            json={"product_name": "Hack"},
        )
        assert r.status_code == 401

    def test_update_own_product(self, artist_client, merch_record):
        r = artist_client.put(
            f"/api/merch/{merch_record.id}",
            json={"product_name": "Updated Tee", "price": 39.99},
        )
        assert r.status_code == 200
        product = r.get_json()["data"]["product"]
        assert product["product_name"] == "Updated Tee"
        assert product["price"] == 39.99

    def test_partial_update_preserves_other_fields(
        self, artist_client, merch_record
    ):
        original_name = merch_record.product_name
        r = artist_client.put(
            f"/api/merch/{merch_record.id}",
            json={"description": "New description only."},
        )
        assert r.status_code == 200
        product = r.get_json()["data"]["product"]
        assert product["product_name"] == original_name
        assert product["description"] == "New description only."

    def test_update_inventory_to_zero(self, artist_client, merch_record):
        """Updating inventory to 0 (out of stock) must be persisted."""
        r = artist_client.put(
            f"/api/merch/{merch_record.id}",
            json={"inventory_quantity": 0},
        )
        assert r.status_code == 200
        assert r.get_json()["data"]["product"]["inventory_quantity"] == 0

    def test_update_another_artists_product_returns_403(
        self, app, merch_record, db_
    ):
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other_merch@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.commit()

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_user_id"] = other.get_id()
                sess["_fresh"] = True
            r = c.put(
                f"/api/merch/{merch_record.id}",
                json={"product_name": "Stolen"},
            )
        assert r.status_code == 403

    def test_update_nonexistent_returns_404(self, artist_client):
        r = artist_client.put(
            "/api/merch/99999", json={"product_name": "Ghost"}
        )
        assert r.status_code == 404

    def test_empty_body_accepted(self, artist_client, merch_record):
        r = artist_client.put(
            f"/api/merch/{merch_record.id}", json={}
        )
        assert r.status_code == 200

    def test_negative_price_update_returns_400(
        self, artist_client, merch_record
    ):
        r = artist_client.put(
            f"/api/merch/{merch_record.id}", json={"price": -1.0}
        )
        assert r.status_code == 400


# ------------------------------------------------------------------ #
# DELETE /api/merch/<id>                                               #
# ------------------------------------------------------------------ #

class TestDeleteMerch:
    """Tests for the authenticated product deletion endpoint."""

    def test_unauthenticated_returns_401(self, client, merch_record):
        r = client.delete(f"/api/merch/{merch_record.id}")
        assert r.status_code == 401

    def test_delete_own_product_returns_200(
        self, artist_client, merch_record
    ):
        r = artist_client.delete(f"/api/merch/{merch_record.id}")
        assert r.status_code == 200
        assert r.get_json()["data"]["message"] == "Product deleted."

    def test_deleted_product_is_gone(self, artist_client, merch_record):
        artist_client.delete(f"/api/merch/{merch_record.id}")
        r = artist_client.get(f"/api/merch/{merch_record.id}")
        assert r.status_code == 404

    def test_delete_another_artists_product_returns_403(
        self, app, merch_record, db_
    ):
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other_merch2@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.commit()

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_user_id"] = other.get_id()
                sess["_fresh"] = True
            r = c.delete(f"/api/merch/{merch_record.id}")
        assert r.status_code == 403

    def test_delete_nonexistent_returns_404(self, artist_client):
        r = artist_client.delete("/api/merch/99999")
        assert r.status_code == 404


# ------------------------------------------------------------------ #
# GET /api/artists/<id>/merch  (nested endpoint)                      #
# ------------------------------------------------------------------ #

class TestListArtistMerch:
    """Tests for the artist-scoped merch nested endpoint."""

    def test_returns_200_for_known_artist(self, client, artist_record):
        r = client.get(f"/api/artists/{artist_record.id}/merch")
        assert r.status_code == 200

    def test_empty_for_artist_with_no_products(
        self, client, artist_record
    ):
        data = client.get(
            f"/api/artists/{artist_record.id}/merch"
        ).get_json()["data"]
        assert data["total"] == 0
        assert data["products"] == []

    def test_returns_products_for_artist(
        self, client, artist_record, merch_record
    ):
        data = client.get(
            f"/api/artists/{artist_record.id}/merch"
        ).get_json()["data"]
        assert data["total"] == 1
        assert data["products"][0]["id"] == merch_record.id

    def test_only_returns_own_products(
        self, client, db_, merch_record
    ):
        """A second artist's products must not appear in the first's list."""
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other_merch3@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.flush()
        other_product = MerchProduct(
            artist_id=other.id,
            product_name="Other Merch",
            price=15.0,
        )
        db_.session.add(other_product)
        db_.session.commit()

        data = client.get(
            f"/api/artists/{merch_record.artist_id}/merch"
        ).get_json()["data"]
        ids = [p["id"] for p in data["products"]]
        assert merch_record.id in ids
        assert other_product.id not in ids

    def test_returns_404_for_unknown_artist(self, client):
        r = client.get("/api/artists/99999/merch")
        assert r.status_code == 404

    def test_pagination_keys_present(self, client, artist_record):
        data = client.get(
            f"/api/artists/{artist_record.id}/merch"
        ).get_json()["data"]
        for key in ("products", "total", "page", "per_page", "pages"):
            assert key in data
