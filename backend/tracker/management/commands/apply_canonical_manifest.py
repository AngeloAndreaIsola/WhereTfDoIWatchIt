"""Apply only an exact, checksummed and reviewed offline canonical manifest."""
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from tracker.services.canonical_rollout import apply_manifest
from tracker.services.canonical_integrity import schema_status


class Command(BaseCommand):
    help = 'Validate a canonical manifest offline; writes require --apply and exact target/hash confirmation.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--expected-database')
        parser.add_argument('--expected-host')
        parser.add_argument('--confirm-target')
        parser.add_argument('--confirm-manifest-sha256')

    def handle(self, *args, **options):
        payload = Path(options['manifest']).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if options['apply']:
            name, host = connection.settings_dict['NAME'], connection.settings_dict['HOST']
            if (connection.vendor != 'postgresql' or name != options['expected_database']
                    or host != options['expected_host'] or options['confirm_target'] != f'{name}@{host}'
                    or options['confirm_manifest_sha256'] != digest):
                raise CommandError('Apply requires exact PostgreSQL target and reviewed manifest SHA-256 confirmation')
            with connection.cursor() as cursor:
                cursor.execute('SELECT current_database()')
                if cursor.fetchone()[0] != name:
                    raise CommandError('Connected database differs from expected target')
        if connection.vendor == 'postgresql':
            schema = schema_status(connection)
            if not schema['ready']:
                raise CommandError('Schema not ready; separately authorize guarded migrations: '
                                   + '; '.join(schema['missing_requirements']))
        try:
            result = apply_manifest(json.loads(payload), apply=options['apply'])
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(f'Invalid manifest: {exc}') from exc
        self.stdout.write(json.dumps({**result, 'manifest_sha256': digest}, indent=2))
