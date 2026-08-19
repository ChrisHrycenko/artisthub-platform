"""
tests/test_health.py

Tests for the GET /api/health endpoint.

This endpoint is the first smoke test for the entire application — if
it passes, the app factory, extensions, database connection, and response
envelope are all wired correctly.
"""


def test_health_returns_200(client):
    """GET /api/health should respond with HTTP 200."""
    response = client.get("/api/health")
    assert response.status_code == 200


def test_health_returns_json(client):
    """GET /api/health should return Content-Type: application/json."""
    response = client.get("/api/health")
    assert response.content_type == "application/json"


def test_health_envelope_shape(client):
    """Response must use the standard success envelope: {status, data}."""
    response = client.get("/api/health")
    body = response.get_json()
    assert body["status"] == "success"
    assert "data" in body


def test_health_data_fields(client):
    """Response data must include app name, status, environment, and database fields."""
    body = client.get("/api/health").get_json()
    data = body["data"]
    assert data["app"] == "ArtistHub"
    assert data["status"] == "ok"
    assert data["database"] == "ok"
    assert "environment" in data


def test_health_database_ok(client):
    """Database connectivity probe should report 'ok' during tests."""
    body = client.get("/api/health").get_json()
    assert body["data"]["database"] == "ok"
