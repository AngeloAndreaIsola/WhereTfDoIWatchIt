"""Read-only, exact-target checks for live preflight, migration and import integrity."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from tracker.services.canonical_integrity import capture_state, verify, verify_migrations


class Command(BaseCommand):
    help = 'Read-only snapshot/verification. Requires exact database/host; never migrates or reconciles.'

    def add_arguments(self, parser):
        parser.add_argument('phase', choices=('snapshot', 'migrations', 'import', 'unchanged'))
        parser.add_argument('--expected-database', required=True)
        parser.add_argument('--expected-host', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--before')
        parser.add_argument('--imported')
        parser.add_argument('--manifest')

    def handle(self, *args, **options):
        if (connection.vendor != 'postgresql' or connection.settings_dict['NAME'] != options['expected_database']
                or connection.settings_dict['HOST'] != options['expected_host']):
            raise CommandError('Expected PostgreSQL database/host differs from configured target')
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database()')
            if cursor.fetchone()[0] != options['expected_database']:
                raise CommandError('Connected database differs from expected target')
            # This management process performs reads only, including ORM verification.
            cursor.execute('SET default_transaction_read_only = on')
        output = Path(options['output'])
        if output.exists():
            raise CommandError('Output exists; preserve audit artifacts')
        state = capture_state(connection, transaction)
        result = state
        phase = options['phase']
        if phase != 'snapshot':
            if not options['before']:
                raise CommandError('Verification requires --before')
            before = json.loads(Path(options['before']).read_text())
            if phase == 'migrations':
                result = verify_migrations(before, state)
            elif phase == 'unchanged':
                result = {'passed': before == state, 'checks': {'all_rows_schema_history_unchanged': before == state}}
            else:
                if not options['imported'] or not options['manifest']:
                    raise CommandError('Import verification requires --imported and --manifest')
                imported = json.loads(Path(options['imported']).read_text())
                manifest = json.loads(Path(options['manifest']).read_text())
                result = verify(before['tables'], imported['tables'], state['tables'], manifest)
                result['checks']['schema_and_history_unchanged'] = before['schema'] == imported['schema'] == state['schema']
                result['passed'] = all(result['checks'].values())
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, default=str) + '\n')
        self.stdout.write(json.dumps({'phase': phase, 'passed': result.get('passed'), 'schema_ready': state['schema']['ready']}))
        if result.get('passed') is False:
            raise CommandError('Integrity verification failed; stop and inspect saved report')
