"""
tests/test_posts.py

Unit tests for the Posts Blueprint.

Endpoints under test:
    GET    /api/posts               — list_posts  (global feed)
    GET    /api/posts/<id>          — get_post
    POST   /api/posts               — create_post
    DELETE /api/posts/<id>          — delete_post
    GET    /api/artists/<id>/posts  — list_artist_posts (nested)

Test categories per endpoint:
    - Public access (unauthenticated GET)
    - Validation (missing/invalid fields)
    - Ownership enforcement (403 for wrong artist)
    - Happy-path CRUD
    - Immutability (no PUT endpoint)
"""

from app.models.artist import Artist
from app.models.post import SocialPost
from app.extensions import db as _db, bcrypt as _bcrypt


# ------------------------------------------------------------------ #
# GET /api/posts  (global feed)                                        #
# ------------------------------------------------------------------ #

class TestListPosts:
    """Tests for the public global post feed."""

    def test_returns_200(self, client):
        assert client.get("/api/posts").status_code == 200

    def test_envelope_shape(self, client):
        body = client.get("/api/posts").get_json()
        assert body["status"] == "success"
        assert "data" in body

    def test_pagination_keys_present(self, client):
        data = client.get("/api/posts").get_json()["data"]
        for key in ("posts", "total", "page", "per_page", "pages"):
            assert key in data

    def test_empty_feed(self, client):
        data = client.get("/api/posts").get_json()["data"]
        assert data["posts"] == []
        assert data["total"] == 0

    def test_lists_created_post(self, client, post_record):
        data = client.get("/api/posts").get_json()["data"]
        assert data["total"] == 1
        assert data["posts"][0]["id"] == post_record.id

    def test_post_contains_artist_id(self, client, post_record):
        data = client.get("/api/posts").get_json()["data"]
        assert data["posts"][0]["artist_id"] == post_record.artist_id

    def test_per_page_capped_at_50(self, client):
        data = client.get("/api/posts?per_page=999").get_json()["data"]
        assert data["per_page"] <= 50

    def test_newest_first_ordering(self, client, db_, artist_record):
        """Posts should be returned newest first."""
        p1 = SocialPost(artist_id=artist_record.id, body="First post")
        p2 = SocialPost(artist_id=artist_record.id, body="Second post")
        db_.session.add_all([p1, p2])
        db_.session.commit()

        posts = client.get("/api/posts").get_json()["data"]["posts"]
        # p2 was inserted last, so it should appear first.
        assert posts[0]["body"] == "Second post"


# ------------------------------------------------------------------ #
# GET /api/posts/<id>                                                  #
# ------------------------------------------------------------------ #

class TestGetPost:
    """Tests for the public single-post detail endpoint."""

    def test_returns_200_for_existing(self, client, post_record):
        assert client.get(f"/api/posts/{post_record.id}").status_code == 200

    def test_returns_post_data(self, client, post_record):
        body = client.get(f"/api/posts/{post_record.id}").get_json()
        post = body["data"]["post"]
        assert post["id"] == post_record.id
        assert post["body"] == post_record.body
        assert post["artist_id"] == post_record.artist_id
        assert "created_at" in post

    def test_returns_404_for_missing(self, client):
        r = client.get("/api/posts/99999")
        assert r.status_code == 404
        assert r.get_json()["status"] == "error"

    def test_image_url_is_none_when_not_set(self, client, post_record):
        post = client.get(
            f"/api/posts/{post_record.id}"
        ).get_json()["data"]["post"]
        assert post["image_url"] is None


# ------------------------------------------------------------------ #
# POST /api/posts                                                      #
# ------------------------------------------------------------------ #

class TestCreatePost:
    """Tests for the authenticated post creation endpoint."""

    def test_unauthenticated_returns_401(self, client):
        r = client.post("/api/posts", json={"body": "Hello"})
        assert r.status_code == 401

    def test_missing_body_returns_400(self, artist_client):
        r = artist_client.post("/api/posts", json={})
        assert r.status_code == 400
        assert r.get_json()["status"] == "error"

    def test_empty_body_returns_400(self, artist_client):
        r = artist_client.post("/api/posts", json={"body": ""})
        assert r.status_code == 400

    def test_body_too_long_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/posts", json={"body": "x" * 2001}
        )
        assert r.status_code == 400

    def test_create_text_only_post(self, artist_client, artist_record):
        r = artist_client.post(
            "/api/posts",
            json={"body": "Just a text post."},
        )
        assert r.status_code == 201
        post = r.get_json()["data"]["post"]
        assert post["body"] == "Just a text post."
        assert post["image_url"] is None
        assert post["artist_id"] == artist_record.id

    def test_create_post_with_image_url(self, artist_client):
        r = artist_client.post(
            "/api/posts",
            json={
                "body": "Check out this photo!",
                "image_url": "https://example.com/photo.jpg",
            },
        )
        assert r.status_code == 201
        post = r.get_json()["data"]["post"]
        assert post["image_url"] == "https://example.com/photo.jpg"

    def test_invalid_image_url_returns_400(self, artist_client):
        r = artist_client.post(
            "/api/posts",
            json={"body": "Bad URL", "image_url": "not-a-url"},
        )
        assert r.status_code == 400

    def test_artist_id_set_from_session(self, artist_client, artist_record):
        """artist_id must come from current_user, not the request body."""
        r = artist_client.post(
            "/api/posts",
            json={"body": "Ownership test."},
        )
        assert r.status_code == 201
        assert r.get_json()["data"]["post"]["artist_id"] == artist_record.id

    def test_response_contains_created_at(self, artist_client):
        r = artist_client.post(
            "/api/posts", json={"body": "Timestamp check."}
        )
        assert r.status_code == 201
        assert "created_at" in r.get_json()["data"]["post"]

    def test_no_put_endpoint(self, artist_client, post_record):
        """Posts are immutable — PUT must return 405 Method Not Allowed."""
        r = artist_client.put(
            f"/api/posts/{post_record.id}",
            json={"body": "Edit attempt"},
        )
        assert r.status_code == 405


# ------------------------------------------------------------------ #
# DELETE /api/posts/<id>                                               #
# ------------------------------------------------------------------ #

class TestDeletePost:
    """Tests for the authenticated post deletion endpoint."""

    def test_unauthenticated_returns_401(self, client, post_record):
        r = client.delete(f"/api/posts/{post_record.id}")
        assert r.status_code == 401

    def test_delete_own_post_returns_200(self, artist_client, post_record):
        r = artist_client.delete(f"/api/posts/{post_record.id}")
        assert r.status_code == 200
        assert r.get_json()["data"]["message"] == "Post deleted."

    def test_deleted_post_is_gone(self, artist_client, post_record):
        artist_client.delete(f"/api/posts/{post_record.id}")
        r = artist_client.get(f"/api/posts/{post_record.id}")
        assert r.status_code == 404

    def test_delete_another_artists_post_returns_403(
        self, app, post_record, db_
    ):
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other_posts@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.commit()

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_user_id"] = other.get_id()
                sess["_fresh"] = True
            r = c.delete(f"/api/posts/{post_record.id}")
        assert r.status_code == 403
        assert r.get_json()["status"] == "error"

    def test_delete_nonexistent_returns_404(self, artist_client):
        r = artist_client.delete("/api/posts/99999")
        assert r.status_code == 404


# ------------------------------------------------------------------ #
# GET /api/artists/<id>/posts  (nested endpoint)                      #
# ------------------------------------------------------------------ #

class TestListArtistPosts:
    """Tests for the artist-scoped posts nested endpoint."""

    def test_returns_200_for_known_artist(self, client, artist_record):
        r = client.get(f"/api/artists/{artist_record.id}/posts")
        assert r.status_code == 200

    def test_empty_for_artist_with_no_posts(self, client, artist_record):
        data = client.get(
            f"/api/artists/{artist_record.id}/posts"
        ).get_json()["data"]
        assert data["total"] == 0
        assert data["posts"] == []

    def test_returns_posts_for_artist(
        self, client, artist_record, post_record
    ):
        data = client.get(
            f"/api/artists/{artist_record.id}/posts"
        ).get_json()["data"]
        assert data["total"] == 1
        assert data["posts"][0]["id"] == post_record.id

    def test_only_returns_own_posts(self, client, db_, post_record):
        """A second artist's posts must not appear in the first's listing."""
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        other = Artist(
            email="other_posts2@artist.com",
            password_hash=pw,
            display_name="Other Artist",
        )
        db_.session.add(other)
        db_.session.flush()
        other_post = SocialPost(
            artist_id=other.id,
            body="Other artist post.",
        )
        db_.session.add(other_post)
        db_.session.commit()

        data = client.get(
            f"/api/artists/{post_record.artist_id}/posts"
        ).get_json()["data"]
        ids = [p["id"] for p in data["posts"]]
        assert post_record.id in ids
        assert other_post.id not in ids

    def test_returns_404_for_unknown_artist(self, client):
        r = client.get("/api/artists/99999/posts")
        assert r.status_code == 404

    def test_pagination_keys_present(self, client, artist_record):
        data = client.get(
            f"/api/artists/{artist_record.id}/posts"
        ).get_json()["data"]
        for key in ("posts", "total", "page", "per_page", "pages"):
            assert key in data
