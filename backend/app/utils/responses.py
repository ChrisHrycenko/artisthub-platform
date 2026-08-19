"""
responses.py

Standardised JSON response helpers for all ArtistHub API endpoints.

Every route MUST return one of these two helpers — never call jsonify()
directly in a route. This enforces a consistent response envelope that
the frontend api.js wrapper can rely on unconditionally.

Success envelope:
    {
        "status": "success",
        "data": { ... }
    }

Error envelope:
    {
        "status": "error",
        "error": "Human-readable message"
    }

Usage:
    from app.utils.responses import success, error

    return success({"artist": artist.to_dict()}, 200)
    return error("Artist not found.", 404)
"""

from flask import jsonify
from typing import Any


def success(data: Any = None, status: int = 200):
    """
    Return a standardised success JSON response.

    Args:
        data:   The payload to include under the "data" key.
                Pass None for responses with no body (e.g. 204-style).
        status: HTTP status code (default 200).

    Returns:
        A Flask Response object with Content-Type: application/json.
    """
    return jsonify({"status": "success", "data": data}), status


def error(message: str, status: int = 400):
    """
    Return a standardised error JSON response.

    Args:
        message: Human-readable description of what went wrong.
        status:  HTTP status code (default 400).

    Returns:
        A Flask Response object with Content-Type: application/json.
    """
    return jsonify({"status": "error", "error": message}), status
