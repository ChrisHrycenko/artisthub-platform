"""Explicit PostgreSQL catalog parity, including keys Alembic misses."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import sqlalchemy as sa


def test_complete_schema_parity(lab) -> None:
    """Match model metadata except one precisely identified outbox duplicate."""
    lab.migrate()
    with lab.db.engine.connect() as connection:
        inspector = sa.inspect(connection)
        metadata = lab.db.metadata
        assert set(inspector.get_table_names()) == set(metadata.tables) | {
            'alembic_version'}
        for name, table in metadata.tables.items():
            columns = {c['name']: c for c in inspector.get_columns(name)}
            assert set(columns) == set(table.columns.keys())
            for column in table.columns:
                actual = columns[column.name]
                assert actual['nullable'] == column.nullable
                assert (actual['type'].compile(dialect=connection.dialect)
                        == column.type.compile(dialect=connection.dialect))
                if column is table._autoincrement_column:
                    assert actual['default'].startswith('nextval(')
                else:
                    assert actual['default'] is None
                assert column.server_default is None
            assert inspector.get_pk_constraint(name)['constrained_columns'] == (
                list(table.primary_key.columns.keys()))
            expected_unique = {tuple(c.columns.keys()) for c in table.constraints
                               if isinstance(c, sa.UniqueConstraint)}
            actual_unique = {tuple(c['column_names']) for c in
                             inspector.get_unique_constraints(name)}
            if name == 'event_outbox':
                # Exact historical redundancy; no other uniqueness is ignored.
                assert actual_unique - expected_unique == {('event_id',)}
                actual_unique.remove(('event_id',))
            assert actual_unique == expected_unique
            assert {(i['name'], tuple(i['column_names']), i['unique'])
                    for i in inspector.get_indexes(name)
                    if not i.get('duplicates_constraint')} == {
                        (i.name, tuple(i.columns.keys()), i.unique)
                        for i in table.indexes}
            expected_fks = {
                (tuple(c.parent.name for c in fk.elements),
                 tuple(c.column.name for c in fk.elements),
                 next(iter(fk.elements)).column.table.name, fk.ondelete)
                for fk in table.foreign_key_constraints}
            assert {(tuple(fk['constrained_columns']), tuple(fk['referred_columns']),
                     fk['referred_table'], fk['options'].get('ondelete'))
                    for fk in inspector.get_foreign_keys(name)} == expected_fks
            assert inspector.get_check_constraints(name) == []
        differences = compare_metadata(MigrationContext.configure(
            connection, opts={'compare_type': True, 'compare_server_default': True}),
            metadata)
        assert len(differences) == 1
        operation, constraint = differences[0]
        assert operation == 'remove_constraint'
        assert constraint.table.name == 'event_outbox'
        assert list(constraint.columns.keys()) == ['event_id']
