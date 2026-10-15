"""Verify PostgreSQL startup selects the driver supplied by this project."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("config_name", ["development", "docker", "production", "pg_testing"])
@pytest.mark.parametrize("explicit_driver", [False, True])
def test_postgres_startup_uses_installed_driver(config_name: str, explicit_driver: bool) -> None:
    """Initialize every PostgreSQL app configuration without a database call."""
    scheme = "postgresql+psycopg2" if explicit_driver else "postgresql"
    uri = f"{scheme}://user:p%40ss@127.0.0.1:65431/driver_test?sslmode=require"
    environment = os.environ.copy()
    environment.update({
        "DATABASE_URL": uri,
        "ARTISTHUB_TEST_POSTGRES_URL": uri,
        "SECRET_KEY": "driver-regression-test-only",
        "WATSONX_MOCK": "false",
    })
    code = '''
import sys
from app import create_app
from app.extensions import db
app = create_app(sys.argv[1])
with app.app_context():
    engine = db.engine
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg2"
    assert engine.dialect.dbapi.__name__ == "psycopg2"
    assert engine.url.username == "user"
    assert engine.url.password == "p@ss"
    assert engine.url.host == "127.0.0.1"
    assert engine.url.port == 65431
    assert engine.url.database == "driver_test"
    assert engine.url.query["sslmode"] == "require"
'''
    result = subprocess.run(
        [sys.executable, "-c", code, config_name], env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
