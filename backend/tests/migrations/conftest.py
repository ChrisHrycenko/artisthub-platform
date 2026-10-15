"""Alembic-only databases: no shared model-created schema fixtures."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import create_app
from app.config import TestingConfig, config_map, normalize_database_uri
from app.extensions import db

BASE = '78e93118283f'
HEAD = 's2f2_processed_event_groups'
PAUSE = 'pe_pause_verified=isolated-test-no-consumers'
DIRECTORY = Path(__file__).resolve().parents[2] / 'migrations'


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """Create and clean a unique localhost PostgreSQL database per test."""
    uri = os.environ.get('ARTISTHUB_TEST_POSTGRES_URL')
    if not uri:
        pytest.skip('Disposable PostgreSQL URL not configured')
    url = sa.make_url(normalize_database_uri(uri))
    if (url.get_backend_name() != 'postgresql'
            or url.host not in ('localhost', '127.0.0.1')
            or not url.database.endswith('_test')):
        pytest.fail('Migration tests require localhost PostgreSQL *_test')
    name = 'pe_' + uuid4().hex + '_test'
    admin = sa.create_engine(url.set(database='postgres'),
                             isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(sa.schema.DDL('CREATE DATABASE ' + name))
    app = None
    try:
        class MigrationConfig(TestingConfig):
            """PostgreSQL bypasses the application's SQLite creation path."""

            SQLALCHEMY_DATABASE_URI = url.set(database=name).render_as_string(
                hide_password=False)

        def forbidden(*args, **kwargs) -> None:
            """Fail if any test or application attempts metadata DDL."""
            pytest.fail('Model schema creation/deletion prohibited')

        monkeypatch.setitem(config_map, 'migration_testing', MigrationConfig)
        monkeypatch.setattr(db, 'create_all', forbidden)
        monkeypatch.setattr(db, 'drop_all', forbidden)
        app = create_app('migration_testing')
        spec = importlib.util.spec_from_file_location(
            'repair', DIRECTORY / 'versions' / (HEAD + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with app.app_context():
            def migrate(revision: str = HEAD, args: list | None = None) -> None:
                """Execute the actual online revision without CLI exit wrapping."""
                db.session.remove()
                config = app.extensions['migrate'].migrate.get_config(
                    str(DIRECTORY), x_arg=[PAUSE] if args is None else args)
                command.upgrade(config, revision)

            def reverse() -> None:
                """Attempt the unsafe automatic downgrade."""
                db.session.remove()
                command.downgrade(app.extensions['migrate'].migrate.get_config(
                    str(DIRECTORY)), BASE)

            def alter(callback) -> None:
                """Construct partial/unknown states in this disposable DB."""
                db.session.remove()
                with db.engine.begin() as connection:
                    callback(Operations(MigrationContext.configure(connection)))

            def state() -> tuple:
                """Read complete history, snapshot binding and actual revision."""
                with db.engine.connect() as connection:
                    binding, rows = module.snapshot(connection)
                    version = sa.Table('alembic_version', sa.MetaData(),
                                       autoload_with=connection)
                    return binding, rows, connection.scalar(sa.select(version))

            def manifest() -> Path:
                """Write a complete verified attribution fixture, mode 0600."""
                binding, rows, _ = state()
                document = {
                    'version': 1, 'binding': binding, 'verified_by': 'operator',
                    'approval_reference': 'verified-fixture-evidence',
                    'entries': [{'event_id': row['event_id'],
                                 'consumer_group': 'artisthub.analytics.v1',
                                 'evidence_reference': 'fixture-processing-log'}
                                for row in rows
                                if row.get('consumer_group') is None],
                }
                path = tmp_path / 'attribution.json'
                path.write_text(json.dumps(document))
                path.chmod(0o600)
                return path

            try:
                migrate(BASE)
                yield SimpleNamespace(db=db, app=app, repair=module,
                                      migrate=migrate, reverse=reverse,
                                      alter=alter, state=state, manifest=manifest)
            finally:
                db.session.remove()
                db.engine.dispose()
    finally:
        with admin.connect() as connection:
            activity = sa.table('pg_stat_activity', sa.column('pid'),
                                sa.column('datname'), schema='pg_catalog')
            connection.execute(sa.select(sa.func.pg_terminate_backend(
                activity.c.pid)).where(activity.c.datname == name))
            connection.execute(sa.schema.DDL('DROP DATABASE ' + name))
        admin.dispose()
