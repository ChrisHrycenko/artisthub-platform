"""
tests/test_releases.py

Unit tests for the Releases Blueprint.

Endpoints under test:
    GET    /api/releases                  — list_releases
    GET    /api/releases/<id>             — get_release
    POST   /api/releases                  — create_release
    PUT    /api/releases/<id>             — update_release
    DELETE /api/releases/<id>             — delete_release
    GET    /api/artists/<id>/releases     — list_artist_releases (nested)

Test categories per endpoint:
    - Public access (unauthenticated GET)
    - Validation (missing/invalid fields)
    - Ownership enforcement (403 for wrong artist)
    - Happy-path CRUD
    - Relationships (artist_id carried through, nested endpoint)
"""

from app.models.artist import Artist
from app.models.release import MusicRelease, RELEASE_TYPES
from app.extensions import db as _db, bcrypt as _bcrypt


# ------------------------------------------------------------------ #
# GET /api/releases                                                    #
# ------------------------------------------------------------------ #

class TestListReleases:
    """Tests for the public releases browse endpoint."""

    def test_returns_200(self, client):
        """Empty database still returns 200."""
        assert client.get("/api/releases").status_code == 200

    def test_envelope_shape(self, client):
        """Response must use the standard success envelope."""
        body = client.get("/api/releases").get_json()
        assert body["status"] == "success"
        assert "data" in body

    def test_pagination_keys_present(self, client):
        """Response data must include pagination metadata."""
        data = client.get("/api/releases").get_json()["data"]
        for key in ("releases", "total", "page", "per_page", "pages"):
            assert key in data

    def test_empty_list(self, client):
        """No releases in DB → empty list, total=0."""
        data = client.get("/api/releases").get_json()["data"]
        assert data["releases"] == []
        assert data["total"] == 0

    def test_lists_created_release(self, client, release_record):
        """After inserting one release the list should contain it."""
        data = client.get("/api/releases").get_json()["data"]
        assert data["total"] == 1
        assert data["releases"][0]["id"] == release_record.id

    def test_genre_filter_matches(self, client, release_record):
        """?genre= performs a case-insensitive substring match."""
        data = client.get(
            "/api/releases?genre=indie"
        ).get_json()["data"]
        assert data["total"] == 1

    def test_genre_filter_no_match(self, client, release_record):
        """?genre= that matches nothing returns empty list."""
        data = client.get(
            "/api/releases?genre=nonexistentgenre"
        ).get_json()["data"]
        assert data["total"] == 0

    def test_per_page_capped_at_50(self, client):
        """per_page cannot exceed MAX_PER_PAGE=50."""
        data = client.get("/api/releases?per_page=999").get_json()["data"]
        assert data["per_page"] <= 50

    def test_release_contains_artist_id(self, client, release_record):
        """Each release in the list must carry its artist_id."""
        data = client.get("/api/releases").get_json()["data"]
        assert data["releases"][0]["artist_id"] == release_record.artist_id


# ------------------------------------------------------------------ #
# GET /api/releases/<id>                                               #
# ------------------------------------------------------------------ #

class TestGetRelease:
    """Tests for the public single-release detail endpoint."""

    def test_returns_200_for_existing(self, client, release_record):
        r = client.get(f"/api/releases/{release_record.id}")
        assert r.status_code == 200

    def test_returns_release_data(self, client, release_record):
        body = client.get(
            f"/api/releases/{release_record.id}"
        ).get_json()
        rel = body["data"]["release"]
        assert rel["id"] == release_record.id
        assert rel["title"] == release_record.title
        assert rel["release_type"] == "Single"
        assert rel["genre"] == "Indie"
        assert rel["artist_id"] == release_record.artist_id

    def test_returns_404_for_missing(self, client):
        r = client.get("/api/releases/99999")
        assert r.status_code == 404
        assert client.get(
            "/api/releases/99999"
        ).get_json()["status"] == "error"

    def test_streaming_url_present(self, client, release_record):
        """Streaming URL should be returned as-is (external link)."""
        body = client.get(
            f"/api/releases/{release_record.id}"
        ).get_json()
        assert body["data"]["release"]["streaming_url"] == (
            "https://open.spotify.com/track/fixture"
        )


# ------------------------------------------------------------------ #
# POST /api/releases                                                   #
# ------------------------------------------------------------------ #

class TestCreateRelease:
    """Tests for the authenticated release creation endpoint."""

    def test_unauthenticated_returns_401(self, client):
        r = client.post("/api/releases", json={"title": "Test"})
        assert r.status_code == 401

    def test_missing_title_returns_400(self, artist_client):
        r = artist_client.post("/api/releases", json={})
        assert r.status_code == 400
        assert artist_client.post(
            "/api/releases", json={}
        ).get_json()["status"] == "error"

    def test_empty_title_returns_400(self, artist_client):
        r = artist_client.post("/api/releases", json={"title": ""})
        assert r.status_code == 400

    def test_create_minimal_release(self, artist_client, artist_record):
        """Only title is required; defaults fill the rest."""
        r = artist_client.post(
            "/api/releases",
            json={"title": "My Single"},
        )
        assert r.status_code == 201
        rel = r.get_json()["data"]["release"]
        assert rel["title"] == "My Single"
        assert rel["release_type"] == "Single"   # default
        assert rel["artist_id"] == artist_record.id

    def test_create_full_release(self, artist_client, artist_record):
        """All fields can be provided and are persisted."""
        payload = {
            "title": "Full Album",
            "release_type": "Album",
            "genre": "Jazz",
            "description": "A complete album.",
            "artwork_url": "https://example.com/art.jpg",
            "streaming_url": "https://open.spotify.com/album/abc",
            "release_date": "2024-06-01",
        }
        r = artist_client.post("/api/releases", json=payload)
        assert r.status_code == 201
        rel = r.get_json()["data"]["release"]
        assert rel["title"] == "Full Album"
        assert rel["release_type"] == "Album"
        assert rel["genre"] == "Jazz"
        assert rel["release_date"] == "2024-06-01"
        assert rel["streaming_url"] == "https://open.spotify.com/album/abc"

    def test_invalid_release_type_returns_400(self, artist_client):
        """release_type must be one of the accepted values."""
        r = artist_client.post(
            "/api/releases",
            json={"title": "Bad Type", "release_type": "Podcast"},
        )
        assert r.status_code == 400

    def test_all_valid_release_types_accepted(self, artist_client):
        """Every value in RELEASE_TYPES must be accepted."""
        for rt in RELEASE_TYPES:
            r = artist_client.post(
                "/api/releases",
                json={"title": f"A {rt}", "release_type": rt},
            )
            assert r.status_code == 201, (
                f"Expected 201 for release_type={rt!r}, got {r.status_code}"
            )

    def test_invalid_streaming_url_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/releases",
            json={"title": "Bad URL", "streaming_url": "not-a-url"},
        )
        assert r.status_code == 400

    def test_artist_id_set_from_session(self, artist_client, artist_record):
        """artist_id is taken from current_user — caller cannot override."""
        r = artist_client.post(
            "/api/releases",
            json={"title": "Ownership Test"},
        )
        assert r.status_code == 201
        assert r.get_json()["data"]["release"]["artist_id"] == (
            artist_record.id
        )


# ------------------------------------------------------------------ #
# PUT /api/releases/<id>                                               #
# ------------------------------------------------------------------ #

class TestUpdateRelease:
    """Tests for the authenticated release update endpoint."""

    def test_unauthenticated_returns_401(self, client, release_record):
        r = client.put(
            f"/api/releases/{release_record.id}",
            json={"title": "Hack"},
        )
        assert r.status_code == 401

    def test_update_own_release(self, artist_client, release_record):
        """Owner can update any field of their release."""
        r = artist_client.put(
            f"/api/releases/{release_record.id}",
            json={"title": "Updated Title", "genre": "Electronic"},
        )
        assert r.status_code == 200
        rel = r.get_json()["data"]["release"]
        assert rel["title"] == "Updated Title"
        assert rel["genre"] == "Electronic"

    def test_partial_update_preserves_other_fields(
        self, artist_client, release_record
    ):
        """Updating one field must not wipe others."""
        original_title = release_record.title
        r = artist_client.put(
            f"/api/releases/{release_record.id}",
            json={"genre": "Pop"},
        )
        assert r.status_code == 200
        rel = r.get_json()["data"]["release"]
        assert rel["title"] == original_title   # unchanged
        assert rel["genre"] == "Pop"

    def test_update_another_artists_release_returns_403(
        self, app, release_record, db_
    ):
        """A different artist cannot edit another artist's release."""
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other2@artist.com",
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
                f"/api/releases/{release_record.id}",
                json={"title": "Stolen Title"},
            )
        assert r.status_code == 403

    def test_update_nonexistent_returns_404(self, artist_client):
        r = artist_client.put(
            "/api/releases/99999",
            json={"title": "Ghost"},
        )
        assert r.status_code == 404

    def test_empty_body_is_accepted(self, artist_client, release_record):
        """Empty JSON body is a valid no-op update."""
        r = artist_client.put(
            f"/api/releases/{release_record.id}", json={}
        )
        assert r.status_code == 200

    def test_invalid_release_type_in_update(
        self, artist_client, release_record
    ):
        """Updating with a bad release_type is rejected."""
        r = artist_client.put(
            f"/api/releases/{release_record.id}",
            json={"release_type": "Podcast"},
        )
        assert r.status_code == 400

    def test_update_release_date(self, artist_client, release_record):
        """release_date can be set via ISO string."""
        r = artist_client.put(
            f"/api/releases/{release_record.id}",
            json={"release_date": "2025-01-15"},
        )
        assert r.status_code == 200
        assert r.get_json()["data"]["release"]["release_date"] == "2025-01-15"


# ------------------------------------------------------------------ #
# DELETE /api/releases/<id>                                            #
# ------------------------------------------------------------------ #

class TestDeleteRelease:
    """Tests for the authenticated release deletion endpoint."""

    def test_unauthenticated_returns_401(self, client, release_record):
        r = client.delete(f"/api/releases/{release_record.id}")
        assert r.status_code == 401

    def test_delete_own_release_returns_200(
        self, artist_client, release_record
    ):
        r = artist_client.delete(f"/api/releases/{release_record.id}")
        assert r.status_code == 200
        assert r.get_json()["data"]["message"] == "Release deleted."

    def test_deleted_release_is_gone(self, artist_client, release_record):
        """After deletion, a GET should return 404."""
        artist_client.delete(f"/api/releases/{release_record.id}")
        r = artist_client.get(f"/api/releases/{release_record.id}")
        assert r.status_code == 404

    def test_delete_another_artists_release_returns_403(
        self, app, release_record, db_
    ):
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other3@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.commit()

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_user_id"] = other.get_id()
                sess["_fresh"] = True
            r = c.delete(f"/api/releases/{release_record.id}")
        assert r.status_code == 403

    def test_delete_nonexistent_returns_404(self, artist_client):
        r = artist_client.delete("/api/releases/99999")
        assert r.status_code == 404


# ------------------------------------------------------------------ #
# GET /api/artists/<id>/releases  (nested endpoint)                   #
# ------------------------------------------------------------------ #

class TestListArtistReleases:
    """Tests for the artist-scoped releases nested endpoint."""

    def test_returns_200_for_known_artist(self, client, artist_record):
        r = client.get(f"/api/artists/{artist_record.id}/releases")
        assert r.status_code == 200

    def test_empty_for_artist_with_no_releases(self, client, artist_record):
        data = client.get(
            f"/api/artists/{artist_record.id}/releases"
        ).get_json()["data"]
        assert data["total"] == 0
        assert data["releases"] == []

    def test_returns_releases_for_artist(
        self, client, artist_record, release_record
    ):
        data = client.get(
            f"/api/artists/{artist_record.id}/releases"
        ).get_json()["data"]
        assert data["total"] == 1
        assert data["releases"][0]["id"] == release_record.id

    def test_only_returns_own_releases(self, client, db_, release_record):
        """A second artist's releases must not appear in the first's listing."""
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other4@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.flush()
        other_release = MusicRelease(
            artist_id=other.id,
            title="Other Artist Release",
            release_type="EP",
        )
        db_.session.add(other_release)
        db_.session.commit()

        # Fixture artist should only see their own release.
        data = client.get(
            f"/api/artists/{release_record.artist_id}/releases"
        ).get_json()["data"]
        ids = [r["id"] for r in data["releases"]]
        assert release_record.id in ids
        assert other_release.id not in ids

    def test_returns_404_for_unknown_artist(self, client):
        r = client.get("/api/artists/99999/releases")
        assert r.status_code == 404

    def test_pagination_keys_present(self, client, artist_record):
        data = client.get(
            f"/api/artists/{artist_record.id}/releases"
        ).get_json()["data"]
        for key in ("releases", "total", "page", "per_page", "pages"):
            assert key in data
