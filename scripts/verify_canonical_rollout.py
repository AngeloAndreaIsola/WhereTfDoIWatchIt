#!/usr/bin/env python3
"""Snapshot/verify a restored, migrated canonical_* PostgreSQL clone only.

Run snapshot before import, snapshot after import, replay, then verify. This
checks the actual rows, including every pending discovery, rather than relying
on command counters. No network requests, migrations or writes to DB occur.
"""
import argparse
import json
import os
from pathlib import Path
import sys

backend = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(backend if (backend / 'manage.py').exists() else Path.cwd()))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--imported', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('phase', choices=('snapshot', 'verify'))
    args = parser.parse_args()
    import django
    django.setup()
    from django.db import connection, transaction
    from tracker.services.canonical_integrity import snapshot, verify
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        actual = cursor.fetchone()[0]
    if (connection.vendor != 'postgresql' or not args.database.startswith('canonical_')
            or actual != args.database or connection.settings_dict['HOST'] != args.host):
        parser.error('Require the exact expected disposable PostgreSQL clone')
    if args.output.exists():
        parser.error('Output already exists; preserve audit artifacts')
    result = snapshot(connection, transaction)
    if args.phase == 'verify':
        if not all((args.before, args.imported, args.manifest)):
            parser.error('verify requires --before --imported --manifest')
        result = verify(json.loads(args.before.read_text()), json.loads(args.imported.read_text()),
                        result, json.loads(args.manifest.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    if args.phase == 'verify' and not result['passed']:
        raise SystemExit('Clone verification failed; inspect the saved report')


if __name__ == '__main__':
    main()
