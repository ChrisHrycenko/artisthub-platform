"""
tests/test_analytics.py

Test suite for GET /api/artists/<id>/analytics.

Each test class covers one slice of the contract:
  - TestAnalyticsShape      envelope structure and required keys
  - TestAnalyticsZeroCounts artist with no content returns zeros
  - TestAnalyticsCounts     counts reflect actual DB rows
  - TestAnalytics404        non-existent artist returns 404

Design notes
------------
All fixtures (artist_record, release_record, post_record, merch_record,
fan_record) are provided by conftest.py.  The Follow model is exercised
via the fan_client fixture, which already injects a valid Fan session, so
we create Follow rows directly in the DB rather than going through the API.
"""

import pytest
from app.models.follow import Follow
from app.extensions import db as _db


URL = "/api/artists/{}/analytics"


# ================================================================== #
# Envelope shape                                                       #
# ================================================================== #

class TestAnalyticsShape:
    """The response envelope must conform to the standard pattern."""

    def test_returns_200(self, client, artist_record):
        r = client.get(URL.format(artist_record.id))
        assert r.status_code == 200

    def test_envelope_status_is_success(self, client, artist_record):
        r = client.get(URL.format(artist_record.id))
        assert r.get_json()["status"] == "success"

    def test_analytics_key_present(self, client, artist_record):
        r = client.get(URL.format(artist_record.id))
        assert "analytics" in r.get_json()["data"]

    def test_required_keys_present(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        a = data["analytics"]
        for key in (
            "artist_id",
            "display_name",
            "follower_count",
            "release_count",
            "post_count",
            "merch_count",
            "generated_at",
        ):
            assert key in a, f"Missing key: {key}"

    def test_artist_id_matches(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["artist_id"] == artist_record.id

    def test_display_name_matches(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["display_name"] == artist_record.display_name

    def test_generated_at_is_string(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert isinstance(data["analytics"]["generated_at"], str)

    def test_generated_at_ends_with_z(self, client, artist_record):
        """Confirm the UTC marker is present so JS Date() parses correctly."""
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["generated_at"].endswith("Z")

    def test_no_password_in_response(self, client, artist_record):
        r = client.get(URL.format(artist_record.id))
        assert "password" not in r.get_data(as_text=True)

    def test_public_no_auth_required(self, client, artist_record):
        """Analytics endpoint is public — unauthenticated requests succeed."""
        r = client.get(URL.format(artist_record.id))
        assert r.status_code == 200


# ================================================================== #
# Zero counts — fresh artist, no content                              #
# ================================================================== #

class TestAnalyticsZeroCounts:
    """An artist with no releases, posts, merch or followers returns zeros."""

    def test_follower_count_zero(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["follower_count"] == 0

    def test_release_count_zero(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["release_count"] == 0

    def test_post_count_zero(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["post_count"] == 0

    def test_merch_count_zero(self, client, artist_record):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["merch_count"] == 0


# ================================================================== #
# Counts reflect real DB rows                                          #
# ================================================================== #

class TestAnalyticsCounts:
    """Each metric increments correctly when rows are added."""

    def test_release_count_increments(
        self, client, artist_record, release_record
    ):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["release_count"] == 1

    def test_post_count_increments(
        self, client, artist_record, post_record
    ):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["post_count"] == 1

    def test_merch_count_increments(
        self, client, artist_record, merch_record
    ):
        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["merch_count"] == 1

    def test_follower_count_increments(
        self, client, db_, artist_record, fan_record
    ):
        """Follow row added directly to DB — mirrors what POST /api/follows does."""
        follow = Follow(fan_id=fan_record.id, artist_id=artist_record.id)
        _db.session.add(follow)
        _db.session.commit()

        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        assert data["analytics"]["follower_count"] == 1

    def test_all_counts_together(
        self,
        client,
        db_,
        artist_record,
        release_record,
        post_record,
        merch_record,
        fan_record,
    ):
        """All four counts are accurate when all content types exist."""
        follow = Follow(fan_id=fan_record.id, artist_id=artist_record.id)
        _db.session.add(follow)
        _db.session.commit()

        data = client.get(URL.format(artist_record.id)).get_json()["data"]
        a = data["analytics"]
        assert a["release_count"] == 1
        assert a["post_count"] == 1
        assert a["merch_count"] == 1
        assert a["follower_count"] == 1

    def test_counts_scoped_to_artist(
        self, client, db_, artist_record, release_record
    ):
        """Content belonging to a different artist does not inflate counts."""
        from app.models.artist import Artist
        from app.extensions import bcrypt as _bcrypt

        pw = _bcrypt.generate_password_hash("pw").decode("utf-8")
        other = Artist(
            email="other@artist.com",
            password_hash=pw,
            display_name="Other",
        )
        _db.session.add(other)
        _db.session.commit()

        # release_record belongs to artist_record, not `other`.
        data = client.get(URL.format(other.id)).get_json()["data"]
        assert data["analytics"]["release_count"] == 0


# ================================================================== #
# 404 handling                                                         #
# ================================================================== #

class TestAnalytics404:
    """Requesting analytics for an unknown artist returns 404."""

    def test_unknown_artist_returns_404(self, client, db_):
        r = client.get(URL.format(99999))
        assert r.status_code == 404

    def test_error_message_present(self, client, db_):
        r = client.get(URL.format(99999))
        assert r.get_json()["status"] == "error"
