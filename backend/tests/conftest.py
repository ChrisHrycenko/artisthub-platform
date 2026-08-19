"""
tests/conftest.py

Shared pytest fixtures for the ArtistHub test suite.

All fixtures are session- or function-scoped as noted. The key design
principle: every test gets a fresh in-memory SQLite database so tests
are fully isolated and can run in any order.

Fixture hierarchy:
    app            — Flask application configured for testing
    db_            — initialised database within the app context
    client         — unauthenticated Flask test client
    artist_record  — a persisted Artist row (no session)
    artist_client  — test client with an active Artist session cookie
    release_record — a persisted MusicRelease row owned by artist_record
    post_record    — a persisted SocialPost row owned by artist_record
    merch_record   — a persisted MerchProduct row owned by artist_record
    fan_record     — a persisted Fan row (no session)
    fan_client     — test client with an active Fan session cookie

Adding new fixtures:
    Follow the artist_record / artist_client pattern.
    Use db_.session.add() + db_.session.commit() to persist fixtures.
    Use client.post("/api/auth/...") to log in once auth is implemented.
"""

import pytest
from app import create_app
from app.extensions import db as _db, bcrypt as _bcrypt
from app.models.artist import Artist
from app.models.fan import Fan
from app.models.merchandise import MerchProduct
from app.models.post import SocialPost
from app.models.release import MusicRelease


@pytest.fixture(scope="session")
def app():
    """
    Create a Flask application instance configured for testing.

    scope="session" means one app is created for the entire test run.
    The in-memory SQLite database is reset for each test via the db_
    fixture (which is function-scoped).
    """
    flask_app = create_app("testing")

    # Establish an application context for the duration of the test session.
    with flask_app.app_context():
        yield flask_app


@pytest.fixture(scope="function")
def db_(app):
    """
    Provide a clean database for each test function.

    Creates all tables before the test and drops them after, ensuring
    complete isolation. Named db_ (with underscore) to avoid shadowing
    the built-in `db` import in test files.
    """
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture(scope="function")
def client(app, db_):
    """
    Provide an unauthenticated Flask test client.

    All requests made through this client have no session cookie.
    Use this for testing public endpoints (GET /api/health, etc.).
    """
    return app.test_client()


@pytest.fixture(scope="function")
def artist_record(db_):
    """
    Persist a single Artist row and return it.

    This fixture provides a real database record for tests that need
    to query an artist by ID, test profile pages, etc. It does NOT
    log the artist in — use artist_client for authenticated requests.

    The email/password values are fixed so tests can reference them.
    """
    pw_hash = _bcrypt.generate_password_hash("password123").decode("utf-8")
    artist = Artist(
        email="fixture@artist.com",
        password_hash=pw_hash,
        display_name="Fixture Artist",
        genre="Indie",
        location="Toronto, CA",
        bio="Test artist for the pytest fixture suite.",
    )
    db_.session.add(artist)
    db_.session.commit()
    return artist


@pytest.fixture(scope="function")
def artist_client(app, artist_record):
    """
    Provide a Flask test client with an active Artist session.

    Logs in via the Flask-Login test client helper so that
    @login_required endpoints recognise current_user as the
    artist_record fixture.

    Phase 2 note: once POST /api/auth/artist/login exists, replace
    the direct login_user() call here with an HTTP login request so
    the fixture tests the actual auth flow end-to-end.
    """
    from flask_login import login_user

    with app.test_client() as c:
        with app.app_context():
            # Log in the fixture artist directly via Flask-Login internals.
            # This is the standard pattern for Flask test suites before the
            # auth endpoints are implemented.
            with c.session_transaction() as sess:
                # Flask-Login stores the user_id in the session under '_user_id'.
                # Using get_id() ensures we use the 'artist-<id>' prefixed form
                # that our user_loader expects.
                sess["_user_id"] = artist_record.get_id()
                sess["_fresh"] = True
        yield c


@pytest.fixture(scope="function")
def release_record(db_, artist_record):
    """
    Persist a single MusicRelease row owned by ``artist_record``.

    Provides a real DB record for read/update/delete tests.
    Does NOT authenticate — use ``artist_client`` for write tests.
    """
    release = MusicRelease(
        artist_id=artist_record.id,
        title="Fixture Single",
        release_type="Single",
        genre="Indie",
        description="A test release for the pytest fixture suite.",
        streaming_url="https://open.spotify.com/track/fixture",
        release_date=None,
    )
    db_.session.add(release)
    db_.session.commit()
    return release


@pytest.fixture(scope="function")
def post_record(db_, artist_record):
    """
    Persist a single SocialPost row owned by ``artist_record``.

    Provides a real DB record for read/delete tests.
    Does NOT authenticate — use ``artist_client`` for write tests.
    """
    post = SocialPost(
        artist_id=artist_record.id,
        body="This is a fixture post for the pytest suite.",
        image_url=None,
    )
    db_.session.add(post)
    db_.session.commit()
    return post


@pytest.fixture(scope="function")
def merch_record(db_, artist_record):
    """
    Persist a single MerchProduct row owned by ``artist_record``.

    Provides a real DB record for read/update/delete tests.
    Does NOT authenticate — use ``artist_client`` for write tests.
    """
    product = MerchProduct(
        artist_id=artist_record.id,
        product_name="Fixture T-Shirt",
        description="A test merch item for the pytest fixture suite.",
        price=29.99,
        image_url=None,
        inventory_quantity=100,
    )
    db_.session.add(product)
    db_.session.commit()
    return product


@pytest.fixture(scope="function")
def fan_record(db_):
    """
    Persist a single Fan row and return it.

    Provides a real DB record for read and follow tests.
    Does NOT log the fan in — use fan_client for authenticated requests.
    """
    pw_hash = _bcrypt.generate_password_hash("fanpass123").decode("utf-8")
    fan = Fan(
        email="fixture@fan.com",
        password_hash=pw_hash,
        username="fixturefan",
    )
    db_.session.add(fan)
    db_.session.commit()
    return fan


@pytest.fixture(scope="function")
def fan_client(app, fan_record):
    """
    Provide a Flask test client with an active Fan session.

    Uses the same session_transaction pattern as artist_client.
    The 'fan-<id>' prefix in get_id() is required by the user_loader
    in create_app() to distinguish Fan from Artist sessions.
    """
    with app.test_client() as c:
        with app.app_context():
            with c.session_transaction() as sess:
                sess["_user_id"] = fan_record.get_id()
                sess["_fresh"] = True
        yield c
