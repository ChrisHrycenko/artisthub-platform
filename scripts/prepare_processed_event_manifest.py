"""Export an UNVERIFIED private template using a read-only PG transaction."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

import sqlalchemy as sa

REVISION = (Path(__file__).resolve().parents[1] / 'backend' / 'migrations'
            / 'versions' / 's2f2_processed_event_groups.py')


def prepare(output: str) -> None:
    """Create an owner-private file; never print private rows or overwrite."""
    uri = os.environ.get('DATABASE_URL', '')
    if uri.startswith('postgresql://'):
        uri = 'postgresql+psycopg2://' + uri[len('postgresql://'):]
    url = sa.make_url(uri)
    if url.get_backend_name() != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    spec = importlib.util.spec_from_file_location('repair', REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine(url, hide_parameters=True)
    try:
        with engine.connect().execution_options(
                isolation_level='REPEATABLE READ', postgresql_readonly=True
        ) as connection:
            with connection.begin():
                binding, rows = module.snapshot(connection)
        document = {
            'version': 1, 'binding': binding, 'verified_by': '',
            'approval_reference': '',
            'entries': [{'event_id': row['event_id'], 'consumer_group': None,
                         'evidence_reference': ''} for row in rows
                        if row.get('consumer_group') is None],
        }
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(document, handle, indent=2)
            handle.write('\n')
    finally:
        engine.dispose()


def main() -> int:
    """Require pause attestation and emit only sanitized status messages."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--pause-reference', required=True)
    args = parser.parse_args()
    if not args.pause_reference.strip():
        parser.error('Verified pause reference required')
    try:
        prepare(args.output)
    except Exception:
        print('Manifest preparation failed; use restricted operator diagnostics.',
              file=sys.stderr)
        return 1
    print('Private UNVERIFIED template created; reconciliation required.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
