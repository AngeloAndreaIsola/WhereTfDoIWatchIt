import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from availability.models import ContentMapping, Offer
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

