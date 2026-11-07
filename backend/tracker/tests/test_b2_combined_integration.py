"""Combined B2 sweep regressions and PostgreSQL query measurements."""
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from availability.models import Source
from tracker.models import (
    Movie,
    Provider,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
    ScrapedContentMapping,
    WatchlistItem,
)
from tracker.services import availability_report as ar, bulk_refresh as bulk, source_policy
from tracker.tests.availability_factories import local_offer
from tracker.tests.test_refresh_source_policy_drain import unlimited

SA = "streaming_availability"


class CombinedSweepTests(TestCase):
    def test_sweep_retries_superseded_negative_only_in_its_market(self):
        movie = Movie.objects.create(tmdb_id=976001, title="Superseded negative")
        WatchlistItem.objects.create(user_id=1, movie=movie)
        RefreshRequest.objects.create(movie=movie, country="NL", source=SA,
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
        RefreshRequest.objects.create(movie=movie, country="NL", source=SA,
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.NO_OFFER)
        RefreshRequest.objects.create(movie=movie, country="IT", source=SA,
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
        for country, expected in (("NL", 1), ("IT", 0), ("JP", 1)):
            with self.subTest(country=country):
                service = MagicMock()
                with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=True), \
                     patch.object(bulk, "resolve_source_country", return_value=country), \
                     patch.object(bulk, "stale_refresh_scopes", return_value=[SA]):
                    result = bulk.process_watchlist_refresh(budget=5, refresh_service=service, limiter=unlimited())
                self.assertEqual(service.refresh_content.call_count, expected)
                self.assertEqual(result["refreshed"], expected)

    def test_trust_metadata_and_blocked_provenance_coexist(self):
        movie = Movie.objects.create(tmdb_id=976003, title="Combined evidence")
        provider = Provider.objects.create(slug="combined-fixture", name="Combined fixture")
        ScrapedContentMapping.objects.create(
            movie=movie, provider=provider, scraped_title=movie.title,
            country_code="NL", is_available=True, is_confirmed=True,
            last_scraped_at=timezone.now(),
        )
        RefreshRequest.objects.create(
            movie=movie, country="NL", source=SA,
            status=RefreshRequestStatus.BLOCKED, blocked_reason="quota_exhausted",
        )
        with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=True):
            entry = ar.title_source_report([movie.pk], country="NL")[movie.pk]
            [signal] = entry["title_signals"]
            self.assertTrue(signal["trusted"])
            self.assertEqual(signal["trust"], "confirmed_mapping")
            self.assertFalse(signal["playable"])
            self.assertIn(provider.slug, entry["available_on"])
            self.assertEqual(entry["state"], "unknown")
            row = next(row for row in entry["sources"] if row["source"] == SA)
            self.assertEqual(row["blocked_reason"], "quota_exhausted")
            self.assertEqual(row["reason"], "quota_exhausted")
            self.assertIn(SA, source_policy.decide_sources_for_title(entry)["allowed_sources"])

    def test_compatibility_negative_reader_uses_shared_supersession(self):
        movie = Movie.objects.create(tmdb_id=976004, title="Legacy scope")
        RefreshRequest.objects.create(movie=movie, country="NL", source="",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
        self.assertEqual(bulk.permanently_absent_scopes(movie.tmdb_id, country="NL"), {SA})
        RefreshRequest.objects.create(movie=movie, country="NL", source=SA,
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.NO_OFFER)
        self.assertEqual(bulk.permanently_absent_scopes(movie.tmdb_id, country="NL"), set())

    def test_background_sweep_query_counts_gate_on(self):
        self.assertEqual(connection.vendor, "postgresql")
        for size in (1, 100, 200):
            with self.subTest(size=size):
                movies = [Movie.objects.create(tmdb_id=977000 + size * 1000 + i, title=f"Sweep {size}/{i}") for i in range(size)]
                WatchlistItem.objects.bulk_create([WatchlistItem(user_id=size, movie=m) for m in movies])
                wm, _ = Source.objects.get_or_create(slug="watchmode", defaults={"name": "Watchmode"})
                for movie in movies:
                    offer = local_offer(movie, country="NL")
                    offer.source = wm
                    offer.save(update_fields=["source"])
                self.assertFalse(RefreshRequest.objects.filter(status=RefreshRequestStatus.PENDING).exists())
                with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=True), \
                     patch.object(bulk, "resolve_source_country", return_value="NL"), \
                     patch.object(bulk, "rate_limiter_for", side_effect=lambda slug: unlimited()), \
                     patch.object(bulk.AvailabilityRefreshService, "refresh_content", return_value=None) as fetch:
                    # Count through the execution wrapper: Django's bounded
                    # queries_log can silently truncate a 200-title sweep.
                    count = [0]
                    def record(execute, sql, params, many, context):
                        count[0] += 1
                        return execute(sql, params, many, context)
                    with connection.execute_wrapper(record):
                        result = bulk.process_watchlist_refresh(budget=size * 10, user_id=size, limiter=unlimited())
                self.assertEqual(result["attempted"], size)
                self.assertEqual(result["skipped_policy_gated"], size)
                self.assertEqual(result["requests_drained"], 0)
                self.assertEqual(result["errors"], 0)
                self.assertFalse(RefreshRequest.objects.filter(status=RefreshRequestStatus.PENDING).exists())
                print(f"BACKGROUND_SWEEP_QUERIES titles={size} total={count[0]} policy_gated={result['skipped_policy_gated']} mocked_fetches={fetch.call_count} queue_drained=0")
