"""Unit tests for discover_provider_candidates management command, flag combinations, and strict boundaries."""
import io
import json
from unittest.mock import MagicMock, patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    ProviderContentCandidate,
    Source,
    SyncCursor,
    TmdbSearchCache,
)
from tracker.models import Movie, Provider


class CandidateCommandTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.filter(slug="tubi").first() or Provider.objects.create(
            name="Tubi", slug="tubi"
        )
        self.source = Source.objects.filter(slug="tubi").first() or Source.objects.create(
            slug="tubi", name="Tubi Source"
        )
        # Clean any existing provider content for this provider in test DB
        ProviderContent.objects.filter(provider=self.provider).delete()
        self.unmapped_content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="tubi-unmapped-test-1",
            content_type=ContentType.MOVIE,
            title="Unmapped Film",
        )
        self.mapped_content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="tubi-mapped-test-1",
            content_type=ContentType.MOVIE,
            title="Mapped Film",
        )
        # Create an existing manual mapping for mapped_content
        ContentMapping.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="tubi-mapped-test-1",
            content_type=ContentType.MOVIE,
            tmdb_id=888,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

    def test_flag_contradiction_preview_with_authorize_or_write_fails(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("discover_provider_candidates", preview=True, authorize_network=True, budget=5)
        self.assertIn("cannot be combined", str(ctx.exception))

        with self.assertRaises(CommandError) as ctx:
            call_command("discover_provider_candidates", preview=True, write=True)
        self.assertIn("cannot be combined", str(ctx.exception))

    def test_write_without_authorize_network_fails(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("discover_provider_candidates", write=True)
        self.assertIn("--write requires --authorize-network", str(ctx.exception))

    def test_authorize_network_without_positive_budget_fails(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("discover_provider_candidates", authorize_network=True)
        self.assertIn("requires an explicit positive integer --budget", str(ctx.exception))

        with self.assertRaises(CommandError) as ctx:
            call_command("discover_provider_candidates", authorize_network=True, budget=0)
        self.assertIn("requires an explicit positive integer --budget", str(ctx.exception))

    def test_default_offline_preview_makes_zero_requests_and_zero_writes(self):
        out = io.StringIO()
        call_command("discover_provider_candidates", stdout=out)
        output = out.getvalue()
        self.assertIn("Candidate discovery command finished", output)
        self.assertEqual(ProviderContentCandidate.objects.count(), 0)
        self.assertEqual(TmdbSearchCache.objects.count(), 0)

    def test_manual_mapping_precedence_excludes_mapped_provider_content(self):
        out = io.StringIO()
        call_command("discover_provider_candidates", preview=True, stdout=out)
        # Parse the JSON summary
        json_start = out.getvalue().find("{")
        json_end = out.getvalue().rfind("}") + 1
        summary = json.loads(out.getvalue()[json_start:json_end])

        self.assertEqual(summary["stats"]["processed"], 1)
        preview_sample = summary.get("preview_sample", [])
        self.assertEqual(len(preview_sample), 1)
        self.assertEqual(preview_sample[0]["content_id"], "tubi-unmapped-test-1")

    @patch("availability.services.candidate_gateway.requests.Session.get")
    def test_authorized_network_lookup_with_write_persists_candidates(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": 12345,
                    "title": "Unmapped Film",
                    "original_title": "Unmapped Film",
                    "release_date": "2020-01-01",
                }
            ]
        }
        mock_get.return_value = mock_resp

        out = io.StringIO()
        call_command(
            "discover_provider_candidates",
            authorize_network=True,
            budget=5,
            write=True,
            stdout=out,
        )

        self.assertEqual(ProviderContentCandidate.objects.count(), 1)
        cand = ProviderContentCandidate.objects.get()
        self.assertEqual(cand.tmdb_id, 12345)
        self.assertEqual(cand.title, "Unmapped Film")
        self.assertEqual(cand.provider_content, self.unmapped_content)

        # Negative / Positive cache was created
        self.assertEqual(TmdbSearchCache.objects.count(), 1)

    @patch("availability.services.candidate_gateway.requests.Session.get")
    def test_strict_persistence_boundary_never_mutates_forbidden_tables(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"id": 12345, "title": "Unmapped Film"}]
        }
        mock_get.return_value = mock_resp

        # Snapshot counts before run
        m_count = Movie.objects.count()
        cm_count = ContentMapping.objects.count()
        off_count = Offer.objects.count()
        ev_count = OfferEvidence.objects.count()
        pr_count = OfferPrice.objects.count()
        cur_count = SyncCursor.objects.count()

        call_command(
            "discover_provider_candidates",
            authorize_network=True,
            budget=5,
            write=True,
        )

        # Assert zero mutations to forbidden models
        self.assertEqual(Movie.objects.count(), m_count)
        self.assertEqual(ContentMapping.objects.count(), cm_count)
        self.assertEqual(Offer.objects.count(), off_count)
        self.assertEqual(OfferEvidence.objects.count(), ev_count)
        self.assertEqual(OfferPrice.objects.count(), pr_count)
        self.assertEqual(SyncCursor.objects.count(), cur_count)
