import json
import tempfile
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from availability.models import ContentMapping, Offer
from availability.services.candidate_gateway import CandidateDiscoveryBudgetExceeded
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie, Provider


class CanonicalBootstrapTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get_or_create(
            slug="hydrahd", defaults={"name": "HydraHD", "category": "scraper"}
        )[0]
        self.record = DiscoveredCatalogueRecord.objects.create(
            provider=self.provider, provider_native_id="provider-91", title="Example Film",
            source_url="https://hydrahd.example/movie/example-film",
            identity_url="https://hydrahd.example/movie/example-film",
            original_metadata={"catalogue_partition": "movies", "year": 2020,
                               "listing_content_type_hint": "movie"},
        )

    def gateway(self, *, details=None):
        return patch("tracker.management.commands.bootstrap_canonical_catalogue.TMDbCandidateGateway",
                     return_value=type("Gateway", (), {
                         "search": lambda _self, *args, **kwargs: [{"tmdb_id": 991, "title": "Example Film",
                                                                      "year": 2020, "content_type": "movie"}],
                         "get_details": lambda _self, *args, **kwargs: details or {
                             "tmdb_id": 991, "title": "Example Film", "year": 2020,
                             "content_type": "movie", "overview": "A fixture.",
                         },
                     })())

    def test_dry_run_is_read_only_and_apply_bootstraps_with_provenance(self):
        details = {"tmdb_id": 991, "title": "Example Film", "year": 2020,
                   "content_type": "movie", "overview": "A fixture."}
        with self.gateway(details=details), patch("tracker.management.commands.bootstrap_canonical_catalogue.settings.TMDB_API_KEY", "test-key"):
            output = StringIO()
            call_command("bootstrap_canonical_catalogue", "--record-id", str(self.record.pk),
                         "--allow-network", stdout=output)
            dry = json.loads(output.getvalue())
            self.assertEqual(dry["statistics"]["verified"], 1)
            self.assertFalse(Movie.objects.exists())
            self.assertFalse(CanonicalBootstrapEvidence.objects.exists())

            output = StringIO()
            call_command("bootstrap_canonical_catalogue", "--record-id", str(self.record.pk),
                         "--allow-network", "--apply", stdout=output)
            applied = json.loads(output.getvalue())
        movie = Movie.objects.get(tmdb_id=991)
        evidence = CanonicalBootstrapEvidence.objects.get(discovered_record=self.record)
        self.assertEqual(movie.content_type, "movie")
        self.assertEqual(evidence.movie, movie)
        self.assertEqual(evidence.evidence["provider_native_id"], "provider-91")
        self.assertEqual(applied["statistics"]["applied"], 1)
        self.assertFalse(ContentMapping.objects.exists())
        self.assertFalse(Offer.objects.exists())

    def test_apply_replay_is_idempotent_and_does_not_overwrite_existing_metadata(self):
        Movie.objects.create(tmdb_id=991, title="Curated title", year=2020,
                             content_type="movie", overview="Keep this metadata")
        details = {"tmdb_id": 991, "title": "Example Film", "year": 2020,
                   "content_type": "movie", "overview": "TMDb update"}
        with self.gateway(details=details), patch("tracker.management.commands.bootstrap_canonical_catalogue.settings.TMDB_API_KEY", "test-key"):
            for _ in range(2):
                call_command("bootstrap_canonical_catalogue", "--record-id", str(self.record.pk),
                             "--allow-network", "--apply", stdout=StringIO())
        movie = Movie.objects.get(tmdb_id=991)
        self.assertEqual(movie.title, "Curated title")
        self.assertEqual(movie.overview, "Keep this metadata")
        self.assertEqual(CanonicalBootstrapEvidence.objects.filter(discovered_record=self.record).count(), 1)

    def test_conflicting_detail_is_not_applied(self):
        details = {"tmdb_id": 991, "title": "Different Film", "year": 2020,
                   "content_type": "movie"}
        with self.gateway(details=details), patch("tracker.management.commands.bootstrap_canonical_catalogue.settings.TMDB_API_KEY", "test-key"):
            output = StringIO()
            call_command("bootstrap_canonical_catalogue", "--record-id", str(self.record.pk),
                         "--allow-network", "--apply", stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["statistics"]["conflicting"], 1)
        self.assertFalse(Movie.objects.exists())
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertFalse(Offer.objects.exists())

    def test_movie_and_series_can_share_numeric_tmdb_id_without_collision(self):
        series_record = DiscoveredCatalogueRecord.objects.create(
            provider=self.provider, provider_native_id="provider-series-91",
            title="Example Film", source_url="https://hydrahd.example/tv/example-film",
            identity_url="https://hydrahd.example/tv/example-film",
            original_metadata={"catalogue_partition": "tv-shows", "year": 2020,
                               "listing_content_type_hint": "series"},
        )
        gateway = type("Gateway", (), {
            "search": lambda _self, title, *, content_type, **kwargs: [
                {"tmdb_id": 991, "title": title, "year": 2020, "content_type": content_type}
            ],
            "get_details": lambda _self, tmdb_id, *, content_type, **kwargs: {
                "tmdb_id": tmdb_id, "title": "Example Film", "year": 2020,
                "content_type": content_type,
            },
        })
        with patch("tracker.management.commands.bootstrap_canonical_catalogue.TMDbCandidateGateway",
                   return_value=gateway()), patch(
                       "tracker.management.commands.bootstrap_canonical_catalogue.settings.TMDB_API_KEY",
                       "test-key"):
            call_command("bootstrap_canonical_catalogue", "--record-id", str(self.record.pk),
                         "--record-id", str(series_record.pk), "--limit", "2",
                         "--allow-network", "--apply", stdout=StringIO())
        self.assertEqual(Movie.objects.filter(tmdb_id=991).count(), 2)
        self.assertEqual(set(Movie.objects.filter(tmdb_id=991).values_list("content_type", flat=True)),
                         {"movie", "series"})
        self.assertEqual(CanonicalBootstrapEvidence.objects.filter(movie__tmdb_id=991).count(), 2)

    def test_checkpoint_resumes_after_last_fully_evaluated_record(self):
        first = DiscoveredCatalogueRecord.objects.create(
            provider=self.provider, title="No Year",
            source_url="https://hydrahd.example/movie/no-year",
            original_metadata={"catalogue_partition": "movies"},
        )
        second = DiscoveredCatalogueRecord.objects.create(
            provider=self.provider, title="Example Film",
            source_url="https://hydrahd.example/movie/example-film",
            original_metadata={"catalogue_partition": "movies", "year": 2020},
        )
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "batch.json"
            exhausted_gateway = type("Gateway", (), {
                "search": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    CandidateDiscoveryBudgetExceeded(limit=1, attempted=1)
                ),
            })
            with patch("tracker.management.commands.bootstrap_canonical_catalogue.TMDbCandidateGateway",
                       return_value=exhausted_gateway()):
                call_command("bootstrap_canonical_catalogue", "--record-id", str(first.pk),
                             "--record-id", str(second.pk), "--limit", "2", "--request-budget", "1",
                             "--checkpoint-file", str(checkpoint), stdout=StringIO())
            state = json.loads(checkpoint.read_text())
            self.assertEqual(state["last_completed_record_id"], first.pk)
            successful_gateway = type("Gateway", (), {
                "search": lambda _self, *args, **kwargs: [{"tmdb_id": 991, "title": "Example Film",
                                                            "year": 2020, "content_type": "movie"}],
                "get_details": lambda _self, *args, **kwargs: {
                    "tmdb_id": 991, "title": "Example Film", "year": 2020,
                    "content_type": "movie",
                },
            })
            with patch("tracker.management.commands.bootstrap_canonical_catalogue.TMDbCandidateGateway",
                       return_value=successful_gateway()):
                output = StringIO()
                call_command("bootstrap_canonical_catalogue", "--record-id", str(first.pk),
                             "--record-id", str(second.pk), "--limit", "2", "--request-budget", "5",
                             "--checkpoint-file", str(checkpoint), stdout=output)
            report = json.loads(output.getvalue())
            self.assertEqual([row["record_id"] for row in report["records"]], [second.pk])
            self.assertEqual(json.loads(checkpoint.read_text())["last_completed_record_id"], second.pk)


    def run_bootstrap(self, *args):
        output = StringIO()
        call_command('bootstrap_canonical_catalogue', *args, stdout=output)
        return json.loads(output.getvalue())

    def test_real_gateway_budget_interruption_recovers_cached_search(self):
        from availability.tests.test_candidate_gateway import FakeResponse
        from availability.models import TmdbSearchCache
        with tempfile.TemporaryDirectory() as directory:
            args = ['--record-id', str(self.record.pk), '--allow-network', '--apply',
                    '--checkpoint-file', str(Path(directory) / 'checkpoint.json'), '--interval', '0']
            search = FakeResponse(200, {'results': [{'id': 991, 'title': 'Example Film', 'release_date': '2020-01-01'}]})
            details = FakeResponse(200, {'id': 991, 'title': 'Example Film', 'release_date': '2020-01-01'})
            with patch('requests.Session.get', return_value=search), patch(
                    'tracker.management.commands.bootstrap_canonical_catalogue.settings.TMDB_API_KEY', 'fixture'):
                interrupted = self.run_bootstrap(*args, '--request-budget', '1')
            self.assertEqual(interrupted['records'][0]['status'], 'BUDGET_EXHAUSTED')
            self.assertEqual(interrupted['batch_completed'], 0)
            self.assertEqual(TmdbSearchCache.objects.count(), 1)
            self.assertFalse(Movie.objects.exists())
            with patch('requests.Session.get', return_value=details) as request, patch(
                    'tracker.management.commands.bootstrap_canonical_catalogue.settings.TMDB_API_KEY', 'fixture'):
                resumed = self.run_bootstrap(*args, '--request-budget', '1')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(resumed['tmdb_budget']['cache_hits'], 1)
            self.assertEqual(resumed['statistics']['applied'], 1)
            with patch('tracker.management.commands.bootstrap_canonical_catalogue.settings.TMDB_API_KEY', 'fixture'):
                replay = self.run_bootstrap(*args)  # complete checkpoint dispatches no network
            self.assertEqual(replay['statistics']['evaluated'], 0)

    def test_checkpoint_freezes_selection_and_rejects_identity_mutation(self):
        from django.core.management.base import CommandError
        self.record.original_metadata.pop('year')
        self.record.save()
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'checkpoint.json'
            args = ['--limit', '1', '--checkpoint-file', str(checkpoint)]
            self.run_bootstrap(*args)
            DiscoveredCatalogueRecord.objects.create(provider=self.provider, title='Later discovery',
                source_url='https://hydrahd.example/movie/later', original_metadata={'catalogue_partition': 'movies'})
            self.assertEqual(self.run_bootstrap(*args)['batch_selected'], 1)
            self.assertEqual(self.run_bootstrap(*args)['statistics']['evaluated'], 0)
            self.record.title = 'Changed observation'
            self.record.save()
            with self.assertRaises(CommandError):
                self.run_bootstrap(*args)
            with self.assertRaises(CommandError):
                self.run_bootstrap(*args, '--apply')

    def test_commit_before_checkpoint_crash_replays_without_network(self):
        from tracker.management.commands.bootstrap_canonical_catalogue import write_checkpoint
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoint.json'
            calls = []
            def crash_after_commit(checkpoint, state):
                calls.append(True)
                if len(calls) == 2:
                    raise OSError('simulated interruption after DB commit')
                write_checkpoint(checkpoint, state)
            args = ['--record-id', str(self.record.pk), '--apply', '--checkpoint-file', str(path)]
            with self.gateway(), patch('tracker.management.commands.bootstrap_canonical_catalogue.write_checkpoint',
                                       side_effect=crash_after_commit):
                with self.assertRaises(OSError):
                    self.run_bootstrap(*args)
            self.assertEqual(CanonicalBootstrapEvidence.objects.count(), 1)
            with patch('requests.Session.get', side_effect=AssertionError('network forbidden')):
                replay = self.run_bootstrap(*args)
            self.assertEqual(replay['statistics']['verified'], 1)
            self.assertEqual(replay['statistics']['created'], 0)
            self.assertEqual(replay['batch_remaining'], 0)

    def test_competing_source_releases_are_not_automatically_associated(self):
        DiscoveredCatalogueRecord.objects.create(provider=self.provider, title=self.record.title,
            provider_native_id='other-release', source_url='https://hydrahd.example/movie/other',
            original_metadata=self.record.original_metadata)
        with self.gateway():
            report = self.run_bootstrap('--record-id', str(self.record.pk), '--apply')
        self.assertEqual(report['statistics']['ambiguous'], 1)
        self.assertFalse(Movie.objects.exists())

    def test_trusted_identifier_skips_search_but_still_requires_details_agreement(self):
        from unittest.mock import MagicMock
        self.record.original_metadata.update(external_ids={'tmdb': 991}, external_ids_trusted=True)
        self.record.save()
        gateway = MagicMock()
        gateway.get_details.return_value = {'tmdb_id': 991, 'content_type': 'movie', 'title': 'Remake', 'year': 2020}
        with patch('tracker.management.commands.bootstrap_canonical_catalogue.TMDbCandidateGateway', return_value=gateway):
            report = self.run_bootstrap('--record-id', str(self.record.pk), '--apply')
        gateway.search.assert_not_called()
        self.assertEqual(report['statistics']['conflicting'], 1)
        self.assertFalse(Movie.objects.exists())
