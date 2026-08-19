"""
tests/test_auth.py

Test suite for the Auth Blueprint.

Endpoints under test
--------------------
POST /api/auth/artist/register
POST /api/auth/artist/login
POST /api/auth/fan/login
POST /api/auth/logout
GET  /api/auth/me
"""

import pytest


# ================================================================== #
# Artist Registration                                                  #
# ================================================================== #

class TestArtistRegister:
    """POST /api/auth/artist/register"""

    def test_register_returns_201(self, client):
        r = client.post("/api/auth/artist/register", json={
            "email": "newartist@example.com",
            "password": "supersecret",
        })
        assert r.status_code == 201

    def test_register_sets_session(self, client):
        r = client.post("/api/auth/artist/register", json={
            "email": "session@example.com",
            "password": "supersecret",
        })
        assert r.status_code == 201
        # After registration, GET /api/auth/me must return the user.
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.get_json()["data"]["role"] == "artist"

    def test_register_returns_artist_in_response(self, client):
        r = client.post("/api/auth/artist/register", json={
            "email": "returned@example.com",
            "password": "supersecret",
            "display_name": "Test Artist",
        })
        data = r.get_json()["data"]
        assert data["artist"]["email"] == "returned@example.com"
        assert data["artist"]["display_name"] == "Test Artist"

    def test_register_password_not_in_response(self, client):
        r = client.post("/api/auth/artist/register", json={
            "email": "nopasswd@example.com",
            "password": "supersecret",
        })
        text = r.get_data(as_text=True)
        assert "password" not in text
        assert "supersecret" not in text

    def test_register_missing_email_returns_400(self, client):
        r = client.post("/api/auth/artist/register", json={
            "password": "supersecret",
        })
        assert r.status_code == 400

    def test_register_missing_password_returns_400(self, client):
        r = client.post("/api/auth/artist/register", json={
            "email": "nopw@example.com",
        })
        assert r.status_code == 400

    def test_register_short_password_returns_400(self, client):
        r = client.post("/api/auth/artist/register", json={
            "email": "shortpw@example.com",
            "password": "abc",
        })
        assert r.status_code == 400

    def test_register_duplicate_email_returns_400(self, client):
        payload = {
            "email": "dup@example.com",
            "password": "supersecret",
        }
        client.post("/api/auth/artist/register", json=payload)
        r = client.post("/api/auth/artist/register", json=payload)
        assert r.status_code == 400

    def test_register_display_name_defaults_to_email(self, client):
        r = client.post("/api/auth/artist/register", json={
            "email": "noname@example.com",
            "password": "supersecret",
        })
        data = r.get_json()["data"]
        assert data["artist"]["display_name"] == "noname@example.com"


# ================================================================== #
# Artist Login                                                         #
# ================================================================== #

class TestArtistLogin:
    """POST /api/auth/artist/login"""

    def test_login_returns_200(self, client, artist_record):
        r = client.post("/api/auth/artist/login", json={
            "email": artist_record.email,
            "password": "password123",
        })
        assert r.status_code == 200

    def test_login_returns_artist_data(self, client, artist_record):
        r = client.post("/api/auth/artist/login", json={
            "email": artist_record.email,
            "password": "password123",
        })
        data = r.get_json()["data"]
        assert data["artist"]["id"] == artist_record.id

    def test_login_wrong_password_returns_401(self, client, artist_record):
        r = client.post("/api/auth/artist/login", json={
            "email": artist_record.email,
            "password": "wrongpassword",
        })
        assert r.status_code == 401

    def test_login_unknown_email_returns_401(self, client):
        r = client.post("/api/auth/artist/login", json={
            "email": "ghost@example.com",
            "password": "doesntmatter",
        })
        assert r.status_code == 401

    def test_login_missing_email_returns_400(self, client):
        r = client.post("/api/auth/artist/login", json={
            "password": "password123",
        })
        assert r.status_code == 400

    def test_login_missing_password_returns_400(self, client):
        r = client.post("/api/auth/artist/login", json={
            "email": "whoever@example.com",
        })
        assert r.status_code == 400

    def test_login_sets_session(self, client, artist_record):
        client.post("/api/auth/artist/login", json={
            "email": artist_record.email,
            "password": "password123",
        })
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.get_json()["data"]["id"] == artist_record.id

    def test_password_not_in_response(self, client, artist_record):
        r = client.post("/api/auth/artist/login", json={
            "email": artist_record.email,
            "password": "password123",
        })
        assert "password" not in r.get_data(as_text=True)


# ================================================================== #
# Fan Login                                                            #
# ================================================================== #

class TestFanLogin:
    """POST /api/auth/fan/login"""

    def test_login_returns_200(self, client, fan_record):
        r = client.post("/api/auth/fan/login", json={
            "email": fan_record.email,
            "password": "fanpass123",
        })
        assert r.status_code == 200

    def test_login_returns_fan_data(self, client, fan_record):
        r = client.post("/api/auth/fan/login", json={
            "email": fan_record.email,
            "password": "fanpass123",
        })
        data = r.get_json()["data"]
        assert data["fan"]["id"] == fan_record.id

    def test_login_wrong_password_returns_401(self, client, fan_record):
        r = client.post("/api/auth/fan/login", json={
            "email": fan_record.email,
            "password": "wrongpassword",
        })
        assert r.status_code == 401

    def test_login_sets_session(self, client, fan_record):
        client.post("/api/auth/fan/login", json={
            "email": fan_record.email,
            "password": "fanpass123",
        })
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.get_json()["data"]["id"] == fan_record.id

    def test_fan_role_in_me_response(self, client, fan_record):
        client.post("/api/auth/fan/login", json={
            "email": fan_record.email,
            "password": "fanpass123",
        })
        me = client.get("/api/auth/me")
        assert me.get_json()["data"]["role"] == "fan"


# ================================================================== #
# Logout                                                               #
# ================================================================== #

class TestLogout:
    """POST /api/auth/logout"""

    def test_logout_returns_200(self, artist_client):
        r = artist_client.post("/api/auth/logout")
        assert r.status_code == 200

    def test_logout_clears_session(self, artist_client):
        artist_client.post("/api/auth/logout")
        me = artist_client.get("/api/auth/me")
        assert me.status_code == 401

    def test_logout_unauthenticated_returns_401(self, client):
        r = client.post("/api/auth/logout")
        assert r.status_code == 401


# ================================================================== #
# GET /api/auth/me                                                     #
# ================================================================== #

class TestMe:
    """GET /api/auth/me"""

    def test_unauthenticated_returns_401(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_artist_session_returns_role(self, artist_client):
        r = artist_client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.get_json()["data"]["role"] == "artist"

    def test_fan_session_returns_role(self, fan_client):
        r = fan_client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.get_json()["data"]["role"] == "fan"

    def test_artist_me_contains_id(self, artist_client, artist_record):
        r = artist_client.get("/api/auth/me")
        assert r.get_json()["data"]["id"] == artist_record.id

    def test_fan_me_contains_id(self, fan_client, fan_record):
        r = fan_client.get("/api/auth/me")
        assert r.get_json()["data"]["id"] == fan_record.id

    def test_password_not_in_me_response(self, artist_client):
        r = artist_client.get("/api/auth/me")
        assert "password" not in r.get_data(as_text=True)
