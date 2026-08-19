"""
tests/test_artists.py

Unit tests for the Artists Blueprint.

Covers all four endpoints:
    GET  /api/artists          — list_artists
    GET  /api/artists/<id>     — get_artist
    POST /api/artists          — create_artist
    PUT  /api/artists/<id>     — update_artist

Test categories:
    - Public access (no auth required for GET)
    - Validation errors (missing / invalid fields)
    - Ownership enforcement (cannot update another artist's profile)
    - Happy-path CRUD
"""

import pytest
from app.models.artist import Artist
from app.extensions import db as _db, bcrypt as _bcrypt


# ------------------------------------------------------------------ #
# GET /api/artists                                                     #
# ------------------------------------------------------------------ #

class TestListArtists:
    """Tests for GET /api/artists (public, paginated)."""

    def test_returns_200(self, client):
        """Empty database still returns 200 with an empty list."""
        r = client.get("/api/artists")
        assert r.status_code == 200

    def test_envelope_shape(self, client):
        """Response must use the standard success envelope."""
        body = client.get("/api/artists").get_json()
        assert body["status"] == "success"
        assert "data" in body

    def test_pagination_keys_present(self, client):
        """Response data must include pagination metadata."""
        data = client.get("/api/artists").get_json()["data"]
        for key in ("artists", "total", "page", "per_page", "pages"):
            assert key in data, f"Missing key: {key}"

    def test_empty_list(self, client):
        """No artists in DB → empty list, total=0."""
        data = client.get("/api/artists").get_json()["data"]
        assert data["artists"] == []
        assert data["total"] == 0

    def test_lists_created_artist(self, client, artist_record):
        """After inserting one artist the list should contain it."""
        data = client.get("/api/artists").get_json()["data"]
        assert data["total"] == 1
        assert data["artists"][0]["id"] == artist_record.id

    def test_pagination_defaults(self, client):
        """Default page=1, per_page=20."""
        data = client.get("/api/artists").get_json()["data"]
        assert data["page"] == 1
        assert data["per_page"] == 20

    def test_per_page_capped_at_50(self, client):
        """per_page cannot exceed MAX_PER_PAGE=50."""
        data = client.get("/api/artists?per_page=999").get_json()["data"]
        assert data["per_page"] <= 50

    def test_response_excludes_password_hash(self, client, artist_record):
        """password_hash must never appear in any artist response."""
        data = client.get("/api/artists").get_json()["data"]
        for artist in data["artists"]:
            assert "password_hash" not in artist


# ------------------------------------------------------------------ #
# GET /api/artists/<id>                                                #
# ------------------------------------------------------------------ #

class TestGetArtist:
    """Tests for GET /api/artists/<id> (public)."""

    def test_returns_200_for_existing(self, client, artist_record):
        """Known artist ID returns 200."""
        r = client.get(f"/api/artists/{artist_record.id}")
        assert r.status_code == 200

    def test_returns_artist_data(self, client, artist_record):
        """Response data contains the correct artist fields."""
        body = client.get(
            f"/api/artists/{artist_record.id}"
        ).get_json()
        artist = body["data"]["artist"]
        assert artist["id"] == artist_record.id
        assert artist["display_name"] == artist_record.display_name
        assert artist["genre"] == artist_record.genre
        assert artist["location"] == artist_record.location

    def test_returns_404_for_missing(self, client):
        """Non-existent artist ID returns 404 with error envelope."""
        r = client.get("/api/artists/99999")
        assert r.status_code == 404
        body = r.get_json()
        assert body["status"] == "error"
        assert "not found" in body["error"].lower()

    def test_response_excludes_password_hash(self, client, artist_record):
        """password_hash must not appear in single-artist response."""
        body = client.get(
            f"/api/artists/{artist_record.id}"
        ).get_json()
        assert "password_hash" not in body["data"]["artist"]

    def test_role_field_is_artist(self, client, artist_record):
        """The role field must be 'artist' for Artist records."""
        body = client.get(
            f"/api/artists/{artist_record.id}"
        ).get_json()
        assert body["data"]["artist"]["role"] == "artist"


# ------------------------------------------------------------------ #
# POST /api/artists                                                    #
# ------------------------------------------------------------------ #

class TestCreateArtist:
    """Tests for POST /api/artists (requires Artist session)."""

    def test_unauthenticated_returns_401(self, client):
        """No session → 401 Unauthorized."""
        r = client.post(
            "/api/artists",
            json={"display_name": "Ghost"},
        )
        assert r.status_code == 401

    def test_missing_display_name_returns_400(self, artist_client):
        """display_name is required — omitting it returns 400."""
        r = artist_client.post("/api/artists", json={})
        assert r.status_code == 400
        body = r.get_json()
        assert body["status"] == "error"

    def test_empty_display_name_returns_400(self, artist_client):
        """Empty string for display_name fails min-length validation."""
        r = artist_client.post(
            "/api/artists",
            json={"display_name": ""},
        )
        assert r.status_code == 400

    def test_updates_own_profile_returns_201(self, artist_client, artist_record):
        """Valid payload updates the authenticated artist and returns 201."""
        r = artist_client.post(
            "/api/artists",
            json={
                "display_name": "Updated Name",
                "bio": "New bio text.",
                "genre": "Jazz",
            },
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["status"] == "success"
        artist = body["data"]["artist"]
        assert artist["display_name"] == "Updated Name"
        assert artist["bio"] == "New bio text."
        assert artist["genre"] == "Jazz"

    def test_optional_fields_not_required(self, artist_client):
        """Only display_name is required; other fields may be omitted."""
        r = artist_client.post(
            "/api/artists",
            json={"display_name": "Minimal Artist"},
        )
        assert r.status_code == 201

    def test_invalid_profile_image_url_returns_400(self, artist_client):
        """A non-URL value for profile_image_url is rejected."""
        r = artist_client.post(
            "/api/artists",
            json={
                "display_name": "Artist",
                "profile_image_url": "not-a-url",
            },
        )
        assert r.status_code == 400


# ------------------------------------------------------------------ #
# PUT /api/artists/<id>                                                #
# ------------------------------------------------------------------ #

class TestUpdateArtist:
    """Tests for PUT /api/artists/<id> (requires owner session)."""

    def test_unauthenticated_returns_401(self, client, artist_record):
        """No session → 401."""
        r = client.put(
            f"/api/artists/{artist_record.id}",
            json={"bio": "New bio"},
        )
        assert r.status_code == 401

    def test_update_own_profile_returns_200(
        self, artist_client, artist_record
    ):
        """Owner can update their own profile."""
        r = artist_client.put(
            f"/api/artists/{artist_record.id}",
            json={"bio": "Updated bio.", "location": "Berlin, DE"},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "success"
        artist = body["data"]["artist"]
        assert artist["bio"] == "Updated bio."
        assert artist["location"] == "Berlin, DE"

    def test_update_another_artist_returns_403(
        self, app, artist_record, db_
    ):
        """An artist cannot update a different artist's profile."""
        # Create a second artist in the DB.
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.commit()

        # Log in as `other`, try to update `artist_record`'s profile.
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_user_id"] = other.get_id()
                sess["_fresh"] = True

            r = c.put(
                f"/api/artists/{artist_record.id}",
                json={"bio": "Hacked bio"},
            )
        assert r.status_code == 403
        body = r.get_json()
        assert body["status"] == "error"
        assert "own" in body["error"].lower()

    def test_update_nonexistent_artist_returns_404(self, artist_client):
        """Updating an artist that does not exist returns 404."""
        r = artist_client.put(
            "/api/artists/99999",
            json={"bio": "Doesn't matter"},
        )
        assert r.status_code == 404

    def test_partial_update_preserves_other_fields(
        self, artist_client, artist_record
    ):
        """Updating one field must not overwrite other existing fields."""
        # Record the original display_name.
        original_name = artist_record.display_name

        r = artist_client.put(
            f"/api/artists/{artist_record.id}",
            json={"bio": "Only bio changed"},
        )
        assert r.status_code == 200
        artist = r.get_json()["data"]["artist"]
        # display_name should be unchanged.
        assert artist["display_name"] == original_name
        assert artist["bio"] == "Only bio changed"

    def test_empty_body_is_accepted(self, artist_client, artist_record):
        """An empty JSON body is a valid no-op update."""
        r = artist_client.put(
            f"/api/artists/{artist_record.id}",
            json={},
        )
        assert r.status_code == 200

    def test_display_name_too_long_returns_400(
        self, artist_client, artist_record
    ):
        """display_name exceeding 100 chars is rejected."""
        r = artist_client.put(
            f"/api/artists/{artist_record.id}",
            json={"display_name": "x" * 101},
        )
        assert r.status_code == 400
