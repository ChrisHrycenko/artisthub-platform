"""
tests/test_fans.py

Unit tests for the Fans Blueprint.

Endpoints under test:
    POST /api/fans/register    — register_fan
    GET  /api/fans/<id>        — get_fan

Test categories:
    - Registration happy path
    - Validation errors (missing fields, short password, bad email)
    - Duplicate email / username (409)
    - Public profile retrieval
    - Password never exposed in any response
"""


class TestFanRegister:
    """Tests for POST /api/fans/register."""

    def test_returns_201_on_success(self, client):
        r = client.post("/api/fans/register", json={
            "username": "rockfan",
            "email": "rockfan@example.com",
            "password": "securepass",
        })
        assert r.status_code == 201

    def test_envelope_shape(self, client):
        r = client.post("/api/fans/register", json={
            "username": "fan2",
            "email": "fan2@example.com",
            "password": "securepass",
        })
        body = r.get_json()
        assert body["status"] == "success"
        assert "fan" in body["data"]

    def test_response_contains_expected_fields(self, client):
        r = client.post("/api/fans/register", json={
            "username": "fan3",
            "email": "fan3@example.com",
            "password": "securepass",
        })
        fan = r.get_json()["data"]["fan"]
        assert fan["username"] == "fan3"
        assert fan["email"] == "fan3@example.com"
        assert fan["role"] == "fan"
        assert "created_at" in fan

    def test_password_not_in_response(self, client):
        """password_hash must NEVER appear in any response."""
        r = client.post("/api/fans/register", json={
            "username": "fan4",
            "email": "fan4@example.com",
            "password": "securepass",
        })
        body = r.get_json()
        assert "password" not in body["data"]["fan"]
        assert "password_hash" not in body["data"]["fan"]

    def test_missing_username_returns_400(self, client):
        r = client.post("/api/fans/register", json={
            "email": "nousername@example.com",
            "password": "securepass",
        })
        assert r.status_code == 400

    def test_missing_email_returns_400(self, client):
        r = client.post("/api/fans/register", json={
            "username": "noemail",
            "password": "securepass",
        })
        assert r.status_code == 400

    def test_missing_password_returns_400(self, client):
        r = client.post("/api/fans/register", json={
            "username": "nopass",
            "email": "nopass@example.com",
        })
        assert r.status_code == 400

    def test_password_too_short_returns_400(self, client):
        """Password must be at least 8 characters."""
        r = client.post("/api/fans/register", json={
            "username": "shortpass",
            "email": "shortpass@example.com",
            "password": "abc",
        })
        assert r.status_code == 400

    def test_invalid_email_returns_400(self, client):
        r = client.post("/api/fans/register", json={
            "username": "bademail",
            "email": "not-an-email",
            "password": "securepass",
        })
        assert r.status_code == 400

    def test_duplicate_email_returns_409(self, client, fan_record):
        r = client.post("/api/fans/register", json={
            "username": "differentname",
            "email": fan_record.email,   # already in DB
            "password": "securepass",
        })
        assert r.status_code == 409
        assert r.get_json()["status"] == "error"

    def test_duplicate_username_returns_409(self, client, fan_record):
        r = client.post("/api/fans/register", json={
            "username": fan_record.username,  # already in DB
            "email": "unique@example.com",
            "password": "securepass",
        })
        assert r.status_code == 409


class TestGetFan:
    """Tests for GET /api/fans/<id>."""

    def test_returns_200_for_existing(self, client, fan_record):
        r = client.get(f"/api/fans/{fan_record.id}")
        assert r.status_code == 200

    def test_returns_fan_data(self, client, fan_record):
        body = client.get(f"/api/fans/{fan_record.id}").get_json()
        fan = body["data"]["fan"]
        assert fan["id"] == fan_record.id
        assert fan["username"] == fan_record.username
        assert fan["role"] == "fan"

    def test_returns_404_for_missing(self, client):
        r = client.get("/api/fans/99999")
        assert r.status_code == 404
        assert r.get_json()["status"] == "error"

    def test_password_not_in_response(self, client, fan_record):
        body = client.get(f"/api/fans/{fan_record.id}").get_json()
        assert "password_hash" not in body["data"]["fan"]
