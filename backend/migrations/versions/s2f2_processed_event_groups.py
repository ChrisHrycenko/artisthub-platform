"""Repair group-scoped deduplication with verified historical attribution.

Revision ID: s2f2_processed_event_groups
Revises: 78e93118283f
"""

import hashlib
import json
import logging
import os
import stat

from alembic import context, op
import sqlalchemy as sa

revision = 's2f2_processed_event_groups'
down_revision = '78e93118283f'
branch_labels = None
depends_on = None

BASE = {
    'event_id': ('VARCHAR(36)', False),
    'event_type': ('VARCHAR(100)', False),
    'topic': ('VARCHAR(255)', False),
    'partition': ('INTEGER', False),
    'offset': ('INTEGER', False),
    'artist_id': ('INTEGER', True),
    'processed_at': ('TIMESTAMP WITHOUT TIME ZONE', False),
}
TRANSITION_INDEX = 'ix_processed_event_event_group'
LOG = logging.getLogger('alembic.runtime.migration')


def reject(code: str) -> None:
    """Expose fixed diagnostics, never event data or manifest contents."""
    raise RuntimeError('processed_event repair: ' + code)


def shape(bind: sa.Connection) -> str:
    """Accept only the baseline, target and documented partial states."""
    inspector = sa.inspect(bind)
    if not inspector.has_table('processed_event', schema='public'):
        reject('unsupported_schema')
    columns = {c['name']: c for c in inspector.get_columns(
        'processed_event', schema='public')}
    if set(columns) not in (set(BASE), set(BASE) | {'consumer_group'}):
        reject('unsupported_columns')
    for name, (kind, nullable) in BASE.items():
        column = columns[name]
        if (column['type'].compile(dialect=bind.dialect) != kind
                or column['nullable'] != nullable or column['default']
                or column.get('computed') or column.get('identity')):
            reject('unsupported_column_definition')
    group = columns.get('consumer_group')
    if group is not None and (
            group['type'].compile(dialect=bind.dialect) != 'VARCHAR(100)'
            or group['default'] or group.get('computed') or group.get('identity')):
        reject('unsupported_group_definition')
    pk = inspector.get_pk_constraint('processed_event', schema='public')
    if pk['constrained_columns'] == ['event_id']:
        state = 'legacy' if group is None else 'partial'
        if group is not None and not group['nullable']:
            reject('unsupported_partial_not_null')
    elif pk['constrained_columns'] == ['event_id', 'consumer_group']:
        if group is None or group['nullable']:
            reject('unsupported_primary_key')
        state = 'correct'
    else:
        reject('unsupported_primary_key')
    if (inspector.get_unique_constraints('processed_event', schema='public')
            or inspector.get_check_constraints('processed_event', schema='public')
            or inspector.get_foreign_keys('processed_event', schema='public')):
        reject('unsupported_constraints')
    for index in inspector.get_indexes('processed_event', schema='public'):
        if (state != 'partial' or index['name'] != TRANSITION_INDEX
                or index['column_names'] != ['event_id', 'consumer_group']
                or not index['unique']
                or index.get('dialect_options', {}) not in (
                    {}, {'postgresql_include': []})):
            reject('unsupported_indexes')
    # Reject dependent keys in any schema, including non-public schemas.
    constraints = sa.table('pg_constraint', sa.column('confrelid'),
                           sa.column('contype'), schema='pg_catalog')
    target = sa.func.to_regclass('public.processed_event')
    relation = sa.table('pg_class', sa.column('oid'), sa.column('relkind'),
                        sa.column('relrowsecurity'), sa.column('relforcerowsecurity'),
                        sa.column('relhasrules'), sa.column('relhassubclass'),
                        sa.column('relispartition'), schema='pg_catalog')
    details = bind.execute(sa.select(relation).where(relation.c.oid == target)).one()
    if details.relkind != 'r' or any((details.relrowsecurity,
                                     details.relforcerowsecurity, details.relhasrules,
                                     details.relhassubclass, details.relispartition)):
        reject('unsupported_table_features')
    triggers = sa.table('pg_trigger', sa.column('tgrelid'),
                        sa.column('tgisinternal'), schema='pg_catalog')
    inheritance = sa.table('pg_inherits', sa.column('inhrelid'), schema='pg_catalog')
    if (bind.scalar(sa.select(sa.func.count()).select_from(triggers).where(
            triggers.c.tgrelid == target, triggers.c.tgisinternal.is_(False)))
            or bind.scalar(sa.select(sa.func.count()).select_from(inheritance).where(
                inheritance.c.inhrelid == target))):
        reject('unsupported_table_features')
    if bind.scalar(sa.select(sa.func.count()).select_from(constraints).where(
            constraints.c.confrelid == target, constraints.c.contype == 'f')):
        reject('unsupported_incoming_foreign_key')
    return state


def snapshot(bind: sa.Connection) -> tuple[dict, list[dict]]:
    """Read a private, canonical snapshot for operator approval and recheck."""
    state = shape(bind)
    table = sa.Table('processed_event', sa.MetaData(), schema='public',
                     autoload_with=bind)
    rows = [dict(row) for row in bind.execute(
        sa.select(table).order_by(*table.primary_key.columns)).mappings()]
    normalized = [dict(row, consumer_group=row.get('consumer_group'))
                  for row in rows]
    encoded = json.dumps(normalized, sort_keys=True, separators=(',', ':'),
                         default=lambda value: value.isoformat()).encode()
    database, started = bind.execute(sa.select(
        sa.func.current_database(),
        sa.func.pg_postmaster_start_time().cast(sa.String))).one()
    databases = sa.table('pg_database', sa.column('oid'), sa.column('datname'),
                         schema='pg_catalog')
    oid = bind.scalar(sa.select(databases.c.oid).where(
        databases.c.datname == database))
    return {
        'database': database, 'database_oid': oid, 'server_started_at': started,
        'schema': 'public', 'shape': state, 'row_count': len(rows),
        'records_sha256': hashlib.sha256(encoded).hexdigest(),
    }, rows


def unique_object(pairs: list) -> dict:
    """Reject JSON duplicate keys instead of overwriting their values."""
    result = {}
    for key, value in pairs:
        if key in result:
            reject('invalid_manifest')
        result[key] = value
    return result


def attribution(path: str, binding: dict, rows: list[dict]) -> dict:
    """Read a private regular file and require exact verified coverage."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'r', encoding='utf-8') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > 10_000_000):
                reject('unsafe_manifest_file')
            document = json.load(handle, object_pairs_hook=unique_object)
        if (not isinstance(document, dict) or set(document) != {
                'version', 'binding', 'verified_by', 'approval_reference', 'entries'}
                or document['version'] != 1 or document['binding'] != binding):
            reject('stale_or_invalid_manifest')
        for field in ('verified_by', 'approval_reference'):
            if not isinstance(document[field], str) or not document[field].strip():
                reject('unverified_manifest')
        if not isinstance(document['entries'], list):
            reject('invalid_manifest')
        expected = {row['event_id'] for row in rows
                    if row.get('consumer_group') is None}
        mapping = {}
        for entry in document['entries']:
            if not isinstance(entry, dict) or set(entry) != {
                    'event_id', 'consumer_group', 'evidence_reference'}:
                reject('invalid_attribution')
            event_id, group, evidence = (entry['event_id'], entry['consumer_group'],
                                         entry['evidence_reference'])
            if (not isinstance(event_id, str) or event_id not in expected
                    or event_id in mapping or not isinstance(group, str)
                    or not group.strip() or len(group) > 100 or '\x00' in group
                    or not isinstance(evidence, str) or not evidence.strip()):
                reject('conflicting_or_invalid_attribution')
            mapping[event_id] = group
        if set(mapping) != expected:
            reject('incomplete_attribution')
        return mapping
    except RuntimeError:
        raise
    except Exception:
        reject('unreadable_or_invalid_manifest')


def repair() -> None:
    """Repair within Alembic's existing single PostgreSQL DDL transaction."""
    if context.is_offline_mode():
        reject('postgresql_online_required')
    bind = op.get_bind()
    bind.engine.hide_parameters = True
    if bind.dialect.name != 'postgresql':
        reject('postgresql_online_required')
    arguments = context.get_x_argument(as_dictionary=True)
    if not arguments.get('pe_pause_verified', '').strip():
        reject('consumer_pause_attestation_required')
    for key, value in [('lock_timeout', '5s'), ('statement_timeout', '60s')]:
        bind.execute(sa.select(sa.func.set_config(key, value, True)))
    # Fixed, qualified DDL: PostgreSQL has no ORM equivalent for table locks.
    bind.execute(sa.schema.DDL(
        'LOCK TABLE public.processed_event IN ACCESS EXCLUSIVE MODE'))
    binding, before = snapshot(bind)
    for row in before:
        group = row.get('consumer_group')
        if group is not None and (not group.strip() or '\x00' in group):
            reject('invalid_existing_group')
    missing = sum(row.get('consumer_group') is None for row in before)
    LOG.info('processed_event repair: shape=%s rows=%d unattributed=%d',
             binding['shape'], len(before), missing)
    path = arguments.get('pe_manifest')
    if missing and not path:
        reject('verified_attribution_required')
    mapping = attribution(path, binding, before) if path else {}
    if binding['shape'] == 'correct':
        return
    if binding['shape'] == 'legacy':
        op.add_column('processed_event', sa.Column(
            'consumer_group', sa.String(100), nullable=True), schema='public')
    table = sa.Table('processed_event', sa.MetaData(), schema='public',
                     autoload_with=bind)
    for event_id, group in mapping.items():
        bind.execute(table.update().where(
            table.c.event_id == event_id, table.c.consumer_group.is_(None)
        ).values(consumer_group=group))
    after = [dict(row) for row in bind.execute(sa.select(table)).mappings()]
    originals = {row['event_id']: row for row in before}
    if len(after) != len(before):
        reject('record_preservation_failed')
    for row in after:
        old = originals[row['event_id']]
        if any(row[name] != old[name] for name in BASE):
            reject('metadata_preservation_failed')
        expected = old.get('consumer_group') or mapping.get(row['event_id'])
        if row['consumer_group'] is None or row['consumer_group'] != expected:
            reject('attribution_preservation_failed')
    inspector = sa.inspect(bind)
    pk = inspector.get_pk_constraint('processed_event', schema='public')
    op.drop_constraint(pk['name'], 'processed_event', type_='primary',
                       schema='public')
    op.alter_column('processed_event', 'consumer_group', nullable=False,
                    existing_type=sa.String(100), schema='public')
    op.create_primary_key('processed_event_pkey', 'processed_event',
                          ['event_id', 'consumer_group'], schema='public')
    if inspector.get_indexes('processed_event', schema='public'):
        op.drop_index(TRANSITION_INDEX, table_name='processed_event', schema='public')
    if shape(bind) != 'correct':
        reject('final_schema_validation_failed')


def upgrade() -> None:
    """Sanitize exceptions without swallowing failures or committing."""
    try:
        repair()
    except RuntimeError as exc:
        if str(exc).startswith('processed_event repair: '):
            raise RuntimeError(str(exc)) from None
        raise RuntimeError('processed_event repair: operation_failed') from None
    except Exception:
        raise RuntimeError('processed_event repair: operation_failed') from None


def downgrade() -> None:
    """The old key cannot safely represent multi-group processing history."""
    reject('automatic_downgrade_refused')
