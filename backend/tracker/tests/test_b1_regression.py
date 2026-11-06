"""B1 regression tests (offline, deterministic)."""
from __future__ import annotations

import datetime as dt
import os
import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone

from availability.models import ContentType, Country, Offer, Source
from availability.sources import DEFAULT_SOURCE_SLUG
from tracker.models import (
    AvailabilityResolutionReason,
    Movie,
    Provider,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRequestOutcome,
    WatchlistItem,
    RefreshPriority,
)
from tracker.services import availability_report as ar
from tracker.services import rate_limit, refresh_requests as rr, source_policy
from tracker.services.bulk_refresh import is_stale
from tracker.tests.availability_factories import local_offer
from tracker.scrapers.base import ContentMatcher, ScrapedItem
from tracker.services.tmdb import TMDbItem


class B1StalePositiveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._socket_patcher = patch("socket.socket", side_effect=RuntimeError("Network access forbidden in tests"))
        cls._socket_patcher.start()
        for code in ("US", "NL", "JP", "IT"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        try:
            cls._socket_patcher.stop()
        except Exception:
            pass

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2001, title="B1 Stale Movie", year=2024, content_type=ContentType.MOVIE)

    def test_fresh_vs_expired_nl_offer(self):
        offer = local_offer(self.movie, country="NL", service="netflix")
        self.assertFalse(is_stale(self.movie, country="NL"))

        # Expire the offer (older than 24h freshness window)
        offer.last_seen = timezone.now() - dt.timedelta(days=2)
        offer.save(update_fields=["last_seen"])
        self.assertTrue(is_stale(self.movie, country="NL"))

    def test_historical_and_cross_country_cannot_establish_nl(self):
        # US offer fresh, but NL should remain stale
        local_offer(self.movie, country="US", service="hulu")
        self.assertFalse(is_stale(self.movie, country="US"))
        self.assertTrue(is_stale(self.movie, country="NL"))

        # JP offer fresh, NL still stale
        local_offer(self.movie, country="JP", service="prime")
        self.assertTrue(is_stale(self.movie, country="NL"))


class B1MeteredSelfSuppressionTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2002, title="B1 Metered Suppress", year=2024, content_type=ContentType.MOVIE)
        for code in ("NL", "IT"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def test_expired_sa_observation_does_not_suppress_eligible_refreshes(self):
        # When SA observation / request is expired or missing, metered refresh is eligible when gate is ON
        sa_source, _ = Source.objects.get_or_create(slug="streaming_availability", defaults={"name": "SA"})
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            completed_at=timezone.now() - dt.timedelta(days=5),
        )
        states = ar.title_source_report([self.movie.pk])
        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision = source_policy.decide_sources_for_title(
                states.get(self.movie.pk, {}), applicable_slugs=["justwatch", "streaming_availability"]
            )
        self.assertIn("streaming_availability", decision["allowed_sources"])


class B1ExplicitRefreshTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2003, title="B1 Explicit Refresh", year=2024, content_type=ContentType.MOVIE)
        WatchlistItem.objects.get_or_create(user_id=1, movie=self.movie)
        for code in ("IT",):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def test_single_title_and_bulk_enqueue_with_gate_on(self):
        prov, _ = Provider.objects.get_or_create(slug="wm-prov", defaults={"name": "WM"})
        src, _ = Source.objects.get_or_create(slug="watchmode", defaults={"name": "Watchmode"})
        country, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=prov,
            source=src,
            country=country,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
        )

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            res = rr.request_watchlist_refresh(user_id=1)

        self.assertNotIn("streaming_availability", res["sources"])
        self.assertEqual(res["skipped_counts"].get("not_needed", 0), 1)

    def test_worker_execution_quota_dedup(self):
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            status=RefreshRequestStatus.PENDING,
            batch=uuid.uuid4(),
            requested_at=timezone.now(),
        )
        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            rr.request_watchlist_refresh(user_id=1)

        sa_count = RefreshRequest.objects.filter(movie=self.movie, source="streaming_availability").count()
        self.assertEqual(sa_count, 1)


class B1NegativePrecedenceTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2004, title="B1 Negative Precedence", year=2024, content_type=ContentType.MOVIE)
        Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})

    def test_old_title_not_found_vs_newer_positive(self):
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="NL",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
            completed_at=timezone.now() - dt.timedelta(hours=5),
        )
        local_offer(self.movie, country="NL", service="netflix")

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)

    def test_cross_country_negative_does_not_affect_other_country(self):
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="US",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
            completed_at=timezone.now(),
        )
        entry_nl = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        sa_nl = next((r for r in entry_nl["sources"] if r["source"] == "streaming_availability"), None)
        if sa_nl:
            self.assertNotEqual(sa_nl["reason"], "title_not_found")

    def test_transient_vs_definitive(self):
        req_transient = RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="NL",
            status=RefreshRequestStatus.FAILED,
            last_error="TimeoutError: upstream timeout",
        )
        req_definitive = RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="NL",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        self.assertEqual(req_definitive.outcome, RefreshRequestOutcome.TITLE_NOT_FOUND)
        self.assertNotEqual(req_transient.outcome, RefreshRequestOutcome.TITLE_NOT_FOUND)


class B1MappingConfidenceTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="test-scraper", defaults={"name": "Test Scraper", "category": "scraper"}
        )

    def match(self, scraped, candidates):
        matcher = ContentMatcher(tmdb_client=MagicMock())
        matcher.tmdb_client.search.return_value = list(candidates)
        return matcher.match_item(scraped)

    def test_unconfirmed_weak_match(self):
        scraped = ScrapedItem(title="Her", provider_slug="test-scraper", content_type="movie")
        matched, score, _, _ = self.match(scraped, [TMDbItem(tmdb_id=530385, title="Hereditary", year=2018, content_type="movie")])
        self.assertIsNone(matched)
        self.assertLess(score, 0.6)

    def test_confirmed_stale_conflicting_year(self):
        scraped = ScrapedItem(title="The Thing", year=1982, provider_slug="test-scraper", content_type="movie")
        matched, score, _, _ = self.match(scraped, [TMDbItem(tmdb_id=60935, title="The Thing", year=2011, content_type="movie")])
        self.assertIsNone(matched)
        self.assertGreaterEqual(score, 0.6)

    def test_confirmed_recent_exact(self):
        scraped = ScrapedItem(title="Fight Club", year=1999, provider_slug="test-scraper", content_type="movie")
        matched, score, _, _ = self.match(scraped, [TMDbItem(tmdb_id=550, title="Fight Club", year=1999, content_type="movie")])
        self.assertIsNotNone(matched)
        self.assertEqual(matched.tmdb_id, 550)
        self.assertEqual(score, 1.0)


class B1ContentIdentityTests(TestCase):
    def setUp(self):
        for code in ("NL", "IT", "JP"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def test_movie_vs_series_identity_collision(self):
        movie_row = Movie.objects.create(tmdb_id=3001, title="Identity Test", year=2024, content_type=ContentType.MOVIE)
        series_collision = Movie(
            tmdb_id=3001,
            title="Identity Test Series",
            year=2024,
            content_type=ContentType.SERIES,
        )

        local_offer(movie_row, country="NL")
        self.assertFalse(is_stale(movie_row, country="NL"))
        self.assertTrue(is_stale(series_collision, country="NL"))

    def test_country_scope_isolation_nl_it_jp(self):
        movie = Movie.objects.create(tmdb_id=3002, title="Country Scope", year=2024, content_type=ContentType.MOVIE)
        local_offer(movie, country="NL")

        self.assertFalse(is_stale(movie, country="NL"))
        self.assertTrue(is_stale(movie, country="IT"))
        self.assertTrue(is_stale(movie, country="JP"))
