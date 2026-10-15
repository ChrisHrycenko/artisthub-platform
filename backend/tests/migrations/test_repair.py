"""Forward-only repair, preservation and fail-closed regression tests."""

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

BASE = '78e93118283f'
HEAD = 's2f2_processed_event_groups'
PAUSE = 'pe_pause_verified=isolated-test-no-consumers'


def seed(lab, count: int = 2) -> list[dict]:
    """Persist original legacy records and every metadata field."""
    rows = [dict(event_id=f'private-event-{i}', event_type='artist.release.created',
                 topic='private-topic', partition=0, offset=i, artist_id=7,
                 processed_at=datetime(2026, 1, 2, 3, 4, 5, 123456))
            for i in range(count)]
    with lab.db.engine.begin() as connection:
        table = sa.Table('processed_event', sa.MetaData(), autoload_with=connection)
        connection.execute(table.insert(), rows)
    return rows


def group_column(operations) -> None:
    """Emulate an interrupted nullable-column repair."""
    operations.add_column('processed_event', sa.Column(
        'consumer_group', sa.String(100), nullable=True), schema='public')


def test_empty_upgrade_and_downgrade_refusal(lab) -> None:
    """Empty databases become model-compatible without unsafe reversal."""
    lab.migrate()
    before = lab.state()
    assert before[0]['shape'] == 'correct'
    assert before[1] == []
    assert before[2] == HEAD
    with pytest.raises(RuntimeError, match='automatic_downgrade_refused'):
        lab.reverse()
    assert lab.state() == before


def test_pause_required_even_when_empty(lab) -> None:
    """A lock never substitutes for independently verified consumer pause."""
    before = lab.state()
    with pytest.raises(RuntimeError, match='pause_attestation_required'):
        lab.migrate(args=[])
    assert lab.state() == before


def test_populated_requires_attribution(lab) -> None:
    """All history remains intact if attribution has not been approved."""
    seed(lab)
    before = lab.state()
    with pytest.raises(RuntimeError, match='verified_attribution_required'):
        lab.migrate()
    assert lab.state() == before


def test_complete_verified_attribution(lab) -> None:
    """Only the previously absent group is added to each original record."""
    original = seed(lab)
    path = lab.manifest()
    lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    _, rows, version = lab.state()
    assert version == HEAD
    assert len(rows) == len(original)
    for row, old in zip(rows, original):
        assert {key: row[key] for key in old} == old
        assert row['consumer_group'] == 'artisthub.analytics.v1'


@pytest.mark.parametrize('defect', [
    'incomplete', 'conflicting', 'unknown', 'stale', 'unverified', 'blank',
    'too_long', 'duplicate_json_key', 'unsafe_mode', 'symlink', 'malformed',
])
def test_invalid_manifest_preserves_all_data(lab, defect) -> None:
    """Incomplete, ambiguous, stale or unsafe attribution fails atomically."""
    seed(lab)
    path = lab.manifest()
    document = json.loads(path.read_text())
    if defect == 'incomplete':
        document['entries'].pop()
    elif defect == 'conflicting':
        document['entries'].append(dict(document['entries'][0],
                                       consumer_group='different'))
    elif defect == 'unknown':
        document['entries'][0]['event_id'] = 'unknown-event'
    elif defect == 'stale':
        document['binding']['records_sha256'] = '0' * 64
    elif defect == 'unverified':
        document['approval_reference'] = ''
    elif defect == 'blank':
        document['entries'][0]['consumer_group'] = ' '
    elif defect == 'too_long':
        document['entries'][0]['consumer_group'] = 'x' * 101
    path.write_text(json.dumps(document))
    if defect == 'duplicate_json_key':
        path.write_text('{"version":1,"version":1}')
    elif defect == 'unsafe_mode':
        path.chmod(0o644)
    elif defect == 'symlink':
        link = path.with_suffix('.link')
        link.symlink_to(path)
        path = link
    elif defect == 'malformed':
        path.write_text('invalid JSON')
    before = lab.state()
    with pytest.raises(RuntimeError, match='processed_event repair:') as error:
        lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    assert 'private-event' not in str(error.value)
    assert lab.state() == before


@pytest.mark.parametrize('with_index', [False, True])
def test_partial_preserves_nonnull_groups(lab, with_index) -> None:
    """Support both allowlisted partial states without overwriting groups."""
    seed(lab)
    lab.alter(group_column)
    if with_index:
        lab.alter(lambda operations: operations.create_index(
            'ix_processed_event_event_group', 'processed_event',
            ['event_id', 'consumer_group'], unique=True, schema='public'))
    with lab.db.engine.begin() as connection:
        table = sa.Table('processed_event', sa.MetaData(), autoload_with=connection)
        connection.execute(table.update().where(
            table.c.event_id == 'private-event-0').values(consumer_group='existing'))
    before = lab.state()[1]
    path = lab.manifest()
    lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    after = lab.state()[1]
    assert len(after) == len(before)
    assert after[0] == before[0]
    assert after[1]['consumer_group'] == 'artisthub.analytics.v1'
    assert lab.state()[0]['shape'] == 'correct'


def test_cannot_overwrite_existing_attribution(lab) -> None:
    """Even an approved manifest cannot alter a non-null group."""
    seed(lab)
    lab.alter(group_column)
    with lab.db.engine.begin() as connection:
        table = sa.Table('processed_event', sa.MetaData(), autoload_with=connection)
        connection.execute(table.update().values(consumer_group='existing'))
    path = lab.manifest()
    document = json.loads(path.read_text())
    document['entries'] = [{'event_id': 'private-event-0',
                            'consumer_group': 'replacement',
                            'evidence_reference': 'conflict'}]
    path.write_text(json.dumps(document))
    before = lab.state()
    with pytest.raises(RuntimeError, match='conflicting_or_invalid'):
        lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    assert lab.state() == before


def test_already_correct_multigroup_history(lab) -> None:
    """Model-built historical schemas can be adopted without changing data."""
    def correct(operations) -> None:
        """Construct the existing correct shape using Alembic operations."""
        group_column(operations)
        operations.drop_constraint('processed_event_pkey', 'processed_event',
                                   type_='primary')
        operations.alter_column('processed_event', 'consumer_group', nullable=False)
        operations.create_primary_key('processed_event_pkey', 'processed_event',
                                      ['event_id', 'consumer_group'])
    lab.alter(correct)
    with lab.db.engine.begin() as connection:
        table = sa.Table('processed_event', sa.MetaData(), autoload_with=connection)
        row = dict(event_id='same-event', event_type='release', topic='test',
                   partition=0, offset=1, processed_at=datetime(2026, 1, 1))
        connection.execute(table.insert(), [dict(row, consumer_group=g)
                                            for g in ['analytics', 'notifications']])
    before = lab.state()
    lab.migrate()
    assert lab.state()[:2] == before[:2]
    assert lab.state()[2] == HEAD


@pytest.mark.parametrize('change', ['column', 'unique', 'type', 'key', 'index',
                                   'nonnull_partial'])
def test_unknown_shapes_rejected(lab, change) -> None:
    """Unsupported schemas are rejected without advancing the revision."""
    def alter(operations) -> None:
        """Construct one unsupported disposable schema."""
        if change == 'column':
            operations.add_column('processed_event', sa.Column('extra', sa.Text))
        elif change == 'unique':
            operations.create_unique_constraint('extra_unique', 'processed_event',
                                                ['topic'])
        elif change == 'type':
            operations.alter_column('processed_event', 'topic', type_=sa.String(500))
        elif change == 'key':
            operations.drop_constraint('processed_event_pkey', 'processed_event',
                                       type_='primary')
        elif change == 'nonnull_partial':
            group_column(operations)
            operations.alter_column('processed_event', 'consumer_group', nullable=False)
        else:
            operations.create_index('unexpected', 'processed_event', ['topic'])
    lab.alter(alter)
    with pytest.raises(RuntimeError, match='unsupported_'):
        lab.migrate()
    with lab.db.engine.connect() as connection:
        version = sa.Table('alembic_version', sa.MetaData(), autoload_with=connection)
        assert connection.scalar(sa.select(version)) == BASE


def test_lock_timeout(lab) -> None:
    """Contention rolls back within the fixed lock timeout."""
    before = lab.state()
    with lab.db.engine.connect() as blocker:
        transaction = blocker.begin()
        table = sa.Table('processed_event', sa.MetaData(), autoload_with=blocker)
        blocker.execute(sa.select(table))
        start = time.monotonic()
        try:
            with pytest.raises(RuntimeError, match='operation_failed'):
                lab.migrate()
            assert 4 <= time.monotonic() - start < 15
        finally:
            transaction.rollback()
    assert lab.state() == before


def test_failure_after_backfill_rolls_back_everything(lab) -> None:
    """DDL, row updates and revision all roll back after a late error."""
    seed(lab)
    path = lab.manifest()
    before = lab.state()

    def fail(connection, cursor, statement, parameters, context, many) -> None:
        """Fail after backfill, immediately before installing the new key."""
        if 'ADD CONSTRAINT processed_event_pkey PRIMARY KEY' in statement:
            raise RuntimeError('private failure')
    sa.event.listen(lab.db.engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='operation_failed'):
            lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    finally:
        sa.event.remove(lab.db.engine, 'before_cursor_execute', fail)
    assert lab.state() == before


def test_composite_key_constraints(lab) -> None:
    """Duplicate/null groups fail; different groups for one event succeed."""
    lab.migrate()
    with lab.db.engine.begin() as connection:
        table = sa.Table('processed_event', sa.MetaData(), autoload_with=connection)
        row = dict(event_id='event', consumer_group='one', event_type='test',
                   topic='test', partition=0, offset=1,
                   processed_at=datetime(2026, 1, 1))
        connection.execute(table.insert().values(**row))
        connection.execute(table.insert().values(**dict(row, consumer_group='two')))
        for group in ['one', None]:
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(table.insert().values(**dict(
                        row, consumer_group=group)))
        assert connection.scalar(sa.select(sa.func.count()).select_from(table)) == 2


@pytest.mark.parametrize('change', ['metadata', 'rows', 'shape'])
def test_changed_snapshot_rejected(lab, change) -> None:
    """A real change since export invalidates prior attribution approval."""
    seed(lab)
    path = lab.manifest()
    if change == 'shape':
        lab.alter(group_column)
    else:
        with lab.db.engine.begin() as connection:
            table = sa.Table('processed_event', sa.MetaData(), autoload_with=connection)
            if change == 'metadata':
                connection.execute(table.update().values(offset=999))
            else:
                connection.execute(table.insert().values(
                    event_id='new-event', event_type='test', topic='test', partition=0,
                    offset=99, processed_at=datetime(2026, 1, 1)))
    before = lab.state()
    with pytest.raises(RuntimeError, match='stale_or_invalid_manifest'):
        lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    assert lab.state() == before


def test_exporter_private_unverified_and_read_only(lab, tmp_path) -> None:
    """An export preserves data and cannot pass as verified attribution."""
    seed(lab)
    script = Path(__file__).resolve().parents[3] / 'scripts' / (
        'prepare_processed_event_manifest.py')
    spec = importlib.util.spec_from_file_location('exporter', script)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    before = lab.state()
    path = tmp_path / 'export.json'
    with patch.dict(os.environ, {'DATABASE_URL': lab.db.engine.url.render_as_string(
            hide_password=False)}):
        exporter.prepare(str(path))
        with pytest.raises(FileExistsError):
            exporter.prepare(str(path))
    assert path.stat().st_mode & 0o777 == 0o600
    document = json.loads(path.read_text())
    assert document['binding'] == before[0]
    assert document['verified_by'] == ''
    assert all(e['consumer_group'] is None for e in document['entries'])
    with pytest.raises(RuntimeError, match='unverified_manifest'):
        lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    assert lab.state() == before


def test_cli_failure_redacts_private_values(lab) -> None:
    """The actual Flask CLI reports fixed codes without manifest contents."""
    seed(lab)
    path = lab.manifest()
    document = json.loads(path.read_text())
    document['entries'][0]['consumer_group'] = ' '
    path.write_text(json.dumps(document))
    result = lab.app.test_cli_runner().invoke(args=[
        'db', 'upgrade', '-d', str(Path(__file__).resolve().parents[2] / 'migrations'),
        '-x', PAUSE, '-x', f'pe_manifest={path}'])
    assert result.exit_code != 0
    assert 'conflicting_or_invalid_attribution' in result.output
    assert 'private-event' not in result.output
    assert 'private-topic' not in result.output


def test_unrelated_event_data_preserved(lab) -> None:
    """The migration leaves all existing outbox and projection fields intact."""
    from app.models import AnalyticsState, Notification, OutboxEvent
    seed(lab)
    lab.db.session.add_all([
        AnalyticsState(artist_id=7, release_count=3),
        Notification(event_id='existing-notification', fan_id=2, artist_id=7,
                     notification_type='release', subject='existing', message='preserve'),
        OutboxEvent(event_id='existing-outbox', event_type='test', topic='test',
                    message_key='7', payload='{"preserve":true}'),
    ])
    lab.db.session.commit()

    def existing() -> dict:
        """Capture every field of the unrelated preexisting records."""
        with lab.db.engine.connect() as connection:
            return {model.__tablename__: [dict(row) for row in connection.execute(
                sa.select(model.__table__)).mappings()]
                for model in [AnalyticsState, Notification, OutboxEvent]}
    before = existing()
    path = lab.manifest()
    lab.migrate(args=[PAUSE, f'pe_manifest={path}'])
    assert existing() == before


def test_row_security_is_unsupported(lab) -> None:
    """Policies must never hide records from preservation/attribution checks."""
    lab.alter(lambda operations: operations.execute(sa.schema.DDL(
        'ALTER TABLE public.processed_event ENABLE ROW LEVEL SECURITY')))
    with pytest.raises(RuntimeError, match='unsupported_table_features'):
        lab.migrate()


def test_incoming_foreign_key_is_unsupported(lab) -> None:
    """Dependent tables require separate review before replacing the key."""
    lab.alter(lambda operations: operations.create_table(
        'marker_reference', sa.Column('event_id', sa.String(36)),
        sa.ForeignKeyConstraint(['event_id'], ['processed_event.event_id'])))
    with pytest.raises(RuntimeError, match='unsupported_incoming_foreign_key'):
        lab.migrate()


def test_correct_shape_with_legacy_uniqueness_rejected(lab) -> None:
    """An old unique event-ID guard must not silently block independent groups."""
    lab.migrate()
    lab.alter(lambda operations: operations.create_unique_constraint(
        'legacy_event_unique', 'processed_event', ['event_id']))
    with lab.db.engine.connect() as connection:
        with pytest.raises(RuntimeError, match='unsupported_constraints'):
            lab.repair.snapshot(connection)
