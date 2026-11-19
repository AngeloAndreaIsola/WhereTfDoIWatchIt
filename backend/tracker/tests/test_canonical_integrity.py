"""Migration checks reject collateral changes and falsely claimed schema readiness."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase

from tracker.services.canonical_integrity import REQUIRED_MIGRATIONS, ADDED_COLUMNS, schema_status, verify_migrations


class MigrationPreservationTests(SimpleTestCase):
    def states(self):
        counts = {'tracker_discoveredcataloguerecord': 5095, 'tracker_cataloguediscoverystate': 5,
                  'availability_contentmapping': 0, 'tracker_movie': 0,
                  'tracker_scrapedcontentmapping': 3300, 'tracker_provider': 50,
                  'availability_offer': 0, 'django_migrations': 88,
                  'auth_permission': 1, 'django_content_type': 1, 'discovery_identity': 5095}
        before = {'tables': {table: {'count': count, 'sha256': f'original:{table}'} for table, count in counts.items()},
                  'old_column_projections': {**{k: f'old:{k}' for k in ADDED_COLUMNS}, 'last_seen_expected': 'source-timestamps'},
                  'migration_history': [['tracker', '0053_catalogue_url_identity_provenance']],
                  'framework_identity_rows': {'django_content_type': [{'id': 1, 'app_label': 'tracker', 'model': 'movie'}],
                                             'auth_permission': [{'id': 1, 'content_type_id': 1, 'codename': 'view_movie'}]}}
        after = deepcopy(before)
        after['schema'] = {'ready': True}
        after['migration_history'].extend([list(m) for m in REQUIRED_MIGRATIONS])
        after['tables']['tracker_canonicalbootstrapevidence'] = {'count': 0, 'sha256': 'empty'}
        after['tables']['django_migrations']['count'] += 4
        after['tables']['django_content_type']['count'] += 1
        after['tables']['auth_permission']['count'] += 4
        after['old_column_projections']['last_seen_actual'] = 'source-timestamps'
        after['framework_identity_rows']['django_content_type'].append({'id': 2, 'app_label': 'tracker', 'model': 'canonicalbootstrapevidence'})
        after['framework_identity_rows']['auth_permission'].extend(
            {'id': i, 'content_type_id': 2, 'codename': f'{verb}_canonicalbootstrapevidence'}
            for i, verb in enumerate(('add', 'change', 'delete', 'view'), 2))
        return before, after

    def test_expected_schema_metadata_and_backfill_are_accepted(self):
        self.assertTrue(verify_migrations(*self.states())['passed'])

    def test_missing_availability_migration_is_rejected(self):
        before, after = self.states()
        after['migration_history'].remove(list(REQUIRED_MIGRATIONS[0]))
        self.assertFalse(verify_migrations(before, after)['checks']['exactly_four_expected_migrations_added'])

    def test_legacy_fields_provider_settings_checkpoint_and_backfill_changes_are_rejected(self):
        for change in ('legacy', 'provider', 'checkpoint', 'backfill'):
            with self.subTest(change=change):
                before, after = self.states()
                if change == 'legacy':after['tables']['tracker_scrapedcontentmapping']['sha256'] = 'changed'
                elif change == 'provider':after['tables']['tracker_provider']['sha256'] = 'changed'
                elif change == 'checkpoint':after['old_column_projections']['tracker_cataloguediscoverystate'] = 'changed'
                else:after['old_column_projections']['last_seen_actual'] = 'wrong'
                self.assertFalse(verify_migrations(before, after)['passed'])

    def test_unrelated_permission_changes_and_unexpected_insertions_are_rejected(self):
        for change in ('existing', 'extra', 'wrong_target'):
            with self.subTest(change=change):
                before, after = self.states()
                permissions = after['framework_identity_rows']['auth_permission']
                if change == 'existing':permissions[0]['codename'] = 'changed'
                elif change == 'extra':permissions.append({'id': 99, 'content_type_id': 2, 'codename': 'extra'})
                else:permissions[-1]['content_type_id'] = 1
                self.assertFalse(verify_migrations(before, after)['passed'])


@skipUnless(connection.vendor == 'postgresql', 'Physical schema tests require PostgreSQL')
class CanonicalPhysicalSchemaTests(TestCase):
    def test_migrated_history_and_physical_schema_are_ready(self):
        self.assertTrue(schema_status(connection)['ready'])

    def test_fake_ready_history_does_not_hide_a_missing_typed_constraint(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('ALTER TABLE tracker_movie DROP CONSTRAINT uniq_movie_tmdb_content_type')
            status = schema_status(connection)
            self.assertFalse(status['ready'])
            self.assertIn('missing typed Movie unique constraint', status['missing_requirements'])
            transaction.set_rollback(True)
        self.assertTrue(schema_status(connection)['ready'])

    def test_manifest_import_fails_before_writes_when_schema_prerequisites_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps({'schema_version': 1, 'associations': []}))
            with patch('tracker.management.commands.apply_canonical_manifest.schema_status', return_value={
                'ready': False, 'missing_requirements': ['unapplied availability.0016_contentmapping_match_evidence']}), patch(
                    'tracker.management.commands.apply_canonical_manifest.apply_manifest') as apply:
                with self.assertRaisesMessage(CommandError, 'Schema not ready'):
                    call_command('apply_canonical_manifest', '--manifest', str(path), stdout=StringIO())
                apply.assert_not_called()

    def test_read_only_audit_rejects_wrong_database_target(self):
        with self.assertRaisesMessage(CommandError, 'differs from configured target'):
            call_command('audit_canonical_rollout', 'snapshot', '--expected-database', 'wrong_database',
                         '--expected-host', connection.settings_dict['HOST'], '--output', '/tmp/never-created-canonical.json',
                         stdout=StringIO())
