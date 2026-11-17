"""Offline rollout rejects stale/ambiguous evidence and rolls back partial imports."""
from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from availability.models import ContentMapping, Offer, ProviderContent, Source
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie, Provider
from tracker.services.canonical_rollout import apply_manifest, observation, observation_digest


class CanonicalRolloutTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get_or_create(slug='hydrahd', defaults={'name': 'HydraHD', 'category': 'scraper'})[0]
        self.record = self.record_for('Example Film', '91')
        self.row = self.proof(self.record, 991)
        self.manifest = {'schema_version': 1, 'associations': [self.row]}

    def record_for(self, title, native, kind='movie'):
        url = f'https://provider.example/{kind}/{native}'
        return DiscoveredCatalogueRecord.objects.create(
            provider=self.provider, provider_native_id=native, title=title, source_url=url,
            identity_url=url, source_urls_seen=[url], original_metadata={
                'catalogue_partition': 'movies' if kind == 'movie' else 'tv-shows', 'year': 2020})

    def proof(self, record, tmdb_id):
        media = 'movie' if record.original_metadata['catalogue_partition'] == 'movies' else 'series'
        details = {'tmdb_id': tmdb_id, 'title': record.title, 'original_title': record.title,
                   'year': 2020, 'content_type': media}
        from availability.services.candidate_gateway import TMDbCandidateGateway
        return {'source_record_id': record.pk, 'source_observation': observation(record),
                'source_sha256': observation_digest(record), 'provider_content_id': record.provider_native_id,
                'canonical_id': {'tmdb_id': tmdb_id, 'content_type': media}, 'media_type': media,
                'verification_evidence': {'method': 'exact_title_year_type',
                    'search': {'query': TMDbCandidateGateway._normalize_query(record.title), 'year': 2020,
                               'content_type': media, 'results': [deepcopy(details)],
                               'expires_at': str(timezone.now() + timedelta(days=1))},
                    'details': {'payload': details, 'expires_at': str(timezone.now() + timedelta(days=1))}}}

    def test_offline_import_preserves_observations_and_replay_all_rows(self):
        source = observation(self.record)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')):
            dry = apply_manifest(self.manifest)
            self.assertEqual(dry['associations_validated'], 1)
            self.assertFalse(Movie.objects.exists())
            first = apply_manifest(self.manifest, apply=True)
            self.assertEqual(first['changes'], {'canonical': 1, 'evidence': 1, 'mappings': 1, 'provider_content': 1})
            models = (Movie, DiscoveredCatalogueRecord, CanonicalBootstrapEvidence, ContentMapping, ProviderContent)
            before = [list(m.objects.order_by('pk').values()) for m in models]
            replay = apply_manifest(self.manifest, apply=True)
            self.assertEqual([list(m.objects.order_by('pk').values()) for m in models], before)
            self.assertTrue(all(v == 0 for v in replay['changes'].values()))
        self.record.refresh_from_db()
        self.assertEqual(observation(self.record), source)
        self.assertFalse(ProviderContent.objects.get().currently_available)
        self.assertFalse(Offer.objects.exists())

    def test_rejects_changed_source_metadata_even_if_title_and_year_match(self):
        self.record.original_metadata['new_evidence'] = 'changed'
        self.record.save()
        with self.assertRaisesMessage(CommandError, 'source observation changed'):
            apply_manifest(self.manifest, apply=True)
        self.assertFalse(Movie.objects.exists())

    def test_rejects_remake_wrong_type_and_wrong_identifier_details(self):
        for field, value in [('year', 1990), ('content_type', 'series'), ('tmdb_id', 999), ('title', 'Unrelated Film')]:
            with self.subTest(field=field):
                manifest = deepcopy(self.manifest)
                payload = manifest['associations'][0]['verification_evidence']['details']['payload']
                payload[field] = value
                if field == 'title':
                    payload['original_title'] = value
                with self.assertRaisesMessage(CommandError, 'details contradict'):
                    apply_manifest(manifest, apply=True)
                self.assertFalse(Movie.objects.exists())

    def test_rejects_multiple_exact_candidates_truncated_search_and_expired_evidence(self):
        for change in ('multiple', 'truncated', 'expired'):
            with self.subTest(change=change):
                manifest = deepcopy(self.manifest)
                proof = manifest['associations'][0]['verification_evidence']
                if change == 'multiple':
                    proof['search']['results'].append({**proof['search']['results'][0], 'tmdb_id': 999})
                elif change == 'truncated':
                    proof['search']['results'][0]['search_truncated'] = True
                else:
                    proof['details']['expires_at'] = str(timezone.now() - timedelta(seconds=1))
                with self.assertRaises(CommandError):
                    apply_manifest(manifest, apply=True)
        self.assertFalse(Movie.objects.exists())

    def test_rejects_new_competing_source_release(self):
        self.record_for('Example Film', '92')
        with self.assertRaisesMessage(CommandError, 'competing source releases'):
            apply_manifest(self.manifest, apply=True)

    def test_media_namespaces_coexist_and_curated_metadata_is_preserved(self):
        series = self.record_for('Example Film', 'series-91', 'series')
        Movie.objects.create(tmdb_id=991, title='Curated title', year=2020, content_type='movie', overview='Keep me')
        manifest = deepcopy(self.manifest)
        manifest['associations'].append(self.proof(series, 991))
        apply_manifest(manifest, apply=True)
        self.assertEqual(Movie.objects.filter(tmdb_id=991).count(), 2)
        self.assertEqual(Movie.objects.get(tmdb_id=991, content_type='movie').overview, 'Keep me')
        self.assertEqual(ContentMapping.objects.get(provider_content_id='series-91').content_type, 'series')

    def test_conflicting_provider_mapping_or_content_is_not_overwritten(self):
        content = ProviderContent.objects.create(provider=self.provider, provider_content_id='91',
            content_type='series', title='Some Series', provider_url=self.record.source_url)
        with self.assertRaisesMessage(CommandError, 'provider identity already belongs'):
            apply_manifest(self.manifest, apply=True)
        self.assertFalse(Movie.objects.exists())
        self.assertEqual(ProviderContent.objects.get(pk=content.pk).content_type, 'series')

    def test_duplicate_manifest_provider_identity_rejected(self):
        duplicate = deepcopy(self.row)
        duplicate['source_record_id'] += 1
        with self.assertRaisesMessage(CommandError, 'Duplicate source or provider identity'):
            apply_manifest({'schema_version': 1, 'associations': [self.row, duplicate]}, apply=True)

    def test_entire_batch_rolls_back_if_application_fails_after_first_insert(self):
        other = self.record_for('Other Film', '92')
        manifest = deepcopy(self.manifest)
        manifest['associations'].append(self.proof(other, 992))
        from tracker.services.canonical_rollout import _apply_classification
        def fail_second(record, *args):
            if record.pk == other.pk:
                raise CommandError('Simulated application failure')
            return _apply_classification(record, *args)
        with patch('tracker.services.canonical_rollout._apply_classification', side_effect=fail_second):
            with self.assertRaisesMessage(CommandError, 'Simulated application failure'):
                apply_manifest(manifest, apply=True)
        for model in (Movie, CanonicalBootstrapEvidence, ContentMapping, ProviderContent, Offer):
            self.assertFalse(model.objects.exists())
        self.assertFalse(DiscoveredCatalogueRecord.objects.filter(classification_state='CLASSIFIED').exists())

    def test_cli_rejects_unconfirmed_apply_before_any_mutation(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps(self.manifest))
            with self.assertRaisesMessage(CommandError, 'exact PostgreSQL target'):
                call_command('apply_canonical_manifest', '--manifest', str(path), '--apply', stdout=StringIO())
        self.assertFalse(Movie.objects.exists())

    def test_conflicting_operator_mapping_is_preserved_without_partial_canonical_insert(self):
        source = Source.objects.create(slug='manual-fixture', name='Manual fixture')
        mapping = ContentMapping.objects.create(provider=self.provider, source=source,
            provider_content_id='91', content_type='movie', tmdb_id=999, match_method='manual')
        before = list(ContentMapping.objects.values())
        with self.assertRaisesMessage(CommandError, 'existing provider mapping conflicts'):
            apply_manifest(self.manifest, apply=True)
        self.assertEqual(list(ContentMapping.objects.values()), before)
        self.assertFalse(Movie.objects.exists())

    def test_cli_rejects_wrong_checksum_even_with_exact_database_confirmation(self):
        import tempfile
        from pathlib import Path
        from django.db import connection
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps(self.manifest))
            name, host = connection.settings_dict['NAME'], connection.settings_dict['HOST']
            with self.assertRaisesMessage(CommandError, 'reviewed manifest SHA-256'):
                call_command('apply_canonical_manifest', '--manifest', str(path), '--apply',
                             '--expected-database', str(name), '--expected-host', host,
                             '--confirm-target', f'{name}@{host}', '--confirm-manifest-sha256', 'wrong',
                             stdout=StringIO())
        self.assertFalse(Movie.objects.exists())
