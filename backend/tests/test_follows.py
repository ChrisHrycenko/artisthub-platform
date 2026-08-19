"""
tests/test_follows.py

Unit tests for the Follows Blueprint and follower count on artist profiles.

Endpoints under test:
    POST   /api/follows                — follow_artist
    DELETE /api/follows/<artist_id>    — unfollow_artist
    GET    /api/follows                — list_following

Also tests:
    - follower_count on GET /api/artists/<id>
    - follower_count on GET /api/artists (list)

Test categories:
    - Fan can follow and unfollow an artist
    - Duplicate follow returns 409
    - Unfollow non-followed artist returns 404
    - Artist trying to use follow endpoints gets 403
    - Unauthenticated requests get 401
    - Follower count is accurate before and after follow/unfollow
    - List following is scoped to the authenticated fan
"""

from app.models.follow import Follow
from app.models.artist import Artist
from app.extensions import db as _db, bcrypt as _bcrypt


# ------------------------------------------------------------------ #
# POST /api/follows                                                    #
# ------------------------------------------------------------------ #

class TestFollowArtist:
    """Tests for POST /api/follows."""

    def test_unauthenticated_returns_401(self, client, artist_record):
        r = client.post("/api/follows", json={"artist_id": artist_record.id})
        assert r.status_code == 401

    def test_artist_session_returns_403(
        self, artist_client, artist_record
    ):
        """An artist cannot follow another artist."""
        r = artist_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        assert r.status_code == 403
        assert "fans" in r.get_json()["error"].lower()

    def test_fan_can_follow_artist(
        self, fan_client, artist_record
    ):
        r = fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["status"] == "success"
        assert body["data"]["follow"]["artist_id"] == artist_record.id

    def test_follow_response_fields(
        self, fan_client, fan_record, artist_record
    ):
        r = fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        follow = r.get_json()["data"]["follow"]
        assert follow["fan_id"] == fan_record.id
        assert follow["artist_id"] == artist_record.id
        assert "created_at" in follow

    def test_missing_artist_id_returns_400(self, fan_client):
        r = fan_client.post("/api/follows", json={})
        assert r.status_code == 400

    def test_nonexistent_artist_returns_404(self, fan_client):
        r = fan_client.post("/api/follows", json={"artist_id": 99999})
        assert r.status_code == 404

    def test_duplicate_follow_returns_409(
        self, fan_client, artist_record
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        r = fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        assert r.status_code == 409
        assert r.get_json()["status"] == "error"

    def test_follow_is_persisted_in_db(
        self, fan_client, fan_record, artist_record, db_
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        row = Follow.query.filter_by(
            fan_id=fan_record.id,
            artist_id=artist_record.id,
        ).first()
        assert row is not None


# ------------------------------------------------------------------ #
# DELETE /api/follows/<artist_id>                                     #
# ------------------------------------------------------------------ #

class TestUnfollowArtist:
    """Tests for DELETE /api/follows/<artist_id>."""

    def test_unauthenticated_returns_401(self, client, artist_record):
        r = client.delete(f"/api/follows/{artist_record.id}")
        assert r.status_code == 401

    def test_artist_session_returns_403(
        self, artist_client, artist_record
    ):
        r = artist_client.delete(f"/api/follows/{artist_record.id}")
        assert r.status_code == 403

    def test_unfollow_not_followed_returns_404(
        self, fan_client, artist_record
    ):
        """Trying to unfollow someone not yet followed returns 404."""
        r = fan_client.delete(f"/api/follows/{artist_record.id}")
        assert r.status_code == 404

    def test_fan_can_unfollow_artist(
        self, fan_client, artist_record
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        r = fan_client.delete(f"/api/follows/{artist_record.id}")
        assert r.status_code == 200
        assert r.get_json()["data"]["message"] == "Unfollowed."

    def test_follow_removed_from_db_after_unfollow(
        self, fan_client, fan_record, artist_record, db_
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        fan_client.delete(f"/api/follows/{artist_record.id}")
        row = Follow.query.filter_by(
            fan_id=fan_record.id,
            artist_id=artist_record.id,
        ).first()
        assert row is None

    def test_refollowing_after_unfollow_succeeds(
        self, fan_client, artist_record
    ):
        """A fan should be able to follow → unfollow → follow again."""
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        fan_client.delete(f"/api/follows/{artist_record.id}")
        r = fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        assert r.status_code == 201


# ------------------------------------------------------------------ #
# GET /api/follows                                                     #
# ------------------------------------------------------------------ #

class TestListFollowing:
    """Tests for GET /api/follows."""

    def test_unauthenticated_returns_401(self, client):
        r = client.get("/api/follows")
        assert r.status_code == 401

    def test_artist_session_returns_403(self, artist_client):
        r = artist_client.get("/api/follows")
        assert r.status_code == 403

    def test_empty_when_not_following_anyone(self, fan_client):
        data = fan_client.get("/api/follows").get_json()["data"]
        assert data["total"] == 0
        assert data["following"] == []

    def test_lists_followed_artist(
        self, fan_client, artist_record
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        data = fan_client.get("/api/follows").get_json()["data"]
        assert data["total"] == 1
        assert data["following"][0]["id"] == artist_record.id

    def test_does_not_list_unfollowed_artists(
        self, fan_client, artist_record
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        fan_client.delete(f"/api/follows/{artist_record.id}")
        data = fan_client.get("/api/follows").get_json()["data"]
        assert data["total"] == 0

    def test_scoped_to_current_fan(self, app, db_, artist_record):
        """A second fan's follows must not appear in the first fan's list."""
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        from app.models.fan import Fan
        other_fan = Fan(
            email="other@fan.com",
            password_hash=pw,
            username="otherfan",
        )
        db_.session.add(other_fan)
        db_.session.commit()

        # other_fan follows artist_record
        other_follow = Follow(
            fan_id=other_fan.id,
            artist_id=artist_record.id,
        )
        db_.session.add(other_follow)
        db_.session.commit()

        # fan_client (fixturefan) has NOT followed anyone
        from app.models.fan import Fan as _Fan
        pw2 = _bcrypt.generate_password_hash("fanpass123").decode("utf-8")
        my_fan = _Fan(
            email="mine@fan.com",
            password_hash=pw2,
            username="minefan",
        )
        db_.session.add(my_fan)
        db_.session.commit()

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_user_id"] = my_fan.get_id()
                sess["_fresh"] = True
            data = c.get("/api/follows").get_json()["data"]
        assert data["total"] == 0

    def test_pagination_keys_present(self, fan_client):
        data = fan_client.get("/api/follows").get_json()["data"]
        for key in ("following", "total", "page", "per_page", "pages"):
            assert key in data


# ------------------------------------------------------------------ #
# Follower count on artist profile                                     #
# ------------------------------------------------------------------ #

class TestFollowerCount:
    """
    Tests that follower_count is accurate on artist profile endpoints.

    These tests verify the count field added to Artist.to_dict() via
    the dynamic ``followers`` backref on the Follow model.
    """

    def test_follower_count_zero_by_default(self, client, artist_record):
        body = client.get(
            f"/api/artists/{artist_record.id}"
        ).get_json()
        assert body["data"]["artist"]["follower_count"] == 0

    def test_follower_count_increments_on_follow(
        self, client, fan_client, artist_record
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        body = client.get(
            f"/api/artists/{artist_record.id}"
        ).get_json()
        assert body["data"]["artist"]["follower_count"] == 1

    def test_follower_count_decrements_on_unfollow(
        self, client, fan_client, artist_record
    ):
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        fan_client.delete(f"/api/follows/{artist_record.id}")
        body = client.get(
            f"/api/artists/{artist_record.id}"
        ).get_json()
        assert body["data"]["artist"]["follower_count"] == 0

    def test_follower_count_in_list_response(
        self, client, fan_client, artist_record
    ):
        """follower_count must also appear in the paginated list."""
        fan_client.post(
            "/api/follows", json={"artist_id": artist_record.id}
        )
        data = client.get("/api/artists").get_json()["data"]
        assert "follower_count" in data["artists"][0]
        assert data["artists"][0]["follower_count"] == 1

    def test_multiple_followers_counted(
        self, client, app, artist_record, db_
    ):
        """Each unique fan follow should increment the count."""
        pw = _bcrypt.generate_password_hash("pass").decode("utf-8")
        from app.models.fan import Fan
        fans = [
            Fan(email=f"f{i}@fan.com", password_hash=pw, username=f"fan{i}")
            for i in range(3)
        ]
        db_.session.add_all(fans)
        db_.session.commit()

        for fan in fans:
            follow = Follow(fan_id=fan.id, artist_id=artist_record.id)
            db_.session.add(follow)
        db_.session.commit()

        body = client.get(
            f"/api/artists/{artist_record.id}"
        ).get_json()
        assert body["data"]["artist"]["follower_count"] == 3
