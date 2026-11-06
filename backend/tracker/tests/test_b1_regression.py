"""B1 regression tests (offline, deterministic)."""
from __future__ import annotations

import datetime as dt
import os
import unittest
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
    ProviderCategory,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRequestOutcome,
    WatchlistItem,
    RefreshPriority,
    ScrapedContentMapping,
)
from tracker.services import availability_report as ar
from tracker.services import rate_limit, refresh_requests as rr, source_policy
from tracker.services.bulk_refresh import is_stale
from tracker.tests.availability_factories import local_offer


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

    def test_fresh_vs_expired_nl_offer_report_and_eligibility(self):
        offer = local_offer(self.movie, country="NL", service="netflix")
        self.assertFalse(is_stale(self.movie, country="NL"))

        # Assert title_source_report() state and decide_sources_for_title() metered eligibility for fresh offer
        report = ar.title_source_report([self.movie.pk], country="NL")
        entry = report.get(self.movie.pk, {})
        self.assertEqual(entry.get("state"), ar.TitleAvailabilityState.AVAILABLE)

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision = source_policy.decide_sources_for_title(
                entry, applicable_slugs=["justwatch", "streaming_availability"]
            )
        self.assertNotIn("streaming_availability", decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"].get("streaming_availability"), "not_needed")

        # Expire the offer (older than 24h freshness window)
        offer.last_seen = timezone.now() - dt.timedelta(days=2)
        offer.save(update_fields=["last_seen"])
        self.assertTrue(is_stale(self.movie, country="NL"))

        # Assert title_source_report() state and decide_sources_for_title() for expired/stale positive
        report_stale = ar.title_source_report([self.movie.pk], country="NL")
        entry_stale = report_stale.get(self.movie.pk, {})
        self.assertEqual(entry_stale.get("state"), ar.TitleAvailabilityState.AVAILABLE)

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision_stale = source_policy.decide_sources_for_title(
                entry_stale, applicable_slugs=["justwatch", "streaming_availability"]
            )
        self.assertIn("streaming_availability", decision_stale["skipped_sources"])


class B1MeteredSelfSuppressionTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2002, title="B1 Metered Suppress", year=2024, content_type=ContentType.MOVIE)
        for code in ("NL", "IT"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def test_active_but_expired_sa_offer_without_cheaper_evidence(self):
        # Construct actual active-but-expired SA Offer with no cheaper evidence
        sa_source, _ = Source.objects.get_or_create(slug="streaming_availability", defaults={"name": "SA"})
        prov, _ = Provider.objects.get_or_create(slug="sa-prov", defaults={"name": "SA Provider"})
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})

        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=prov,
            source=sa_source,
            country=country,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
            last_seen=timezone.now() - dt.timedelta(days=3),
        )
        states = ar.title_source_report([self.movie.pk], country="NL")
        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision = source_policy.decide_sources_for_title(
                states.get(self.movie.pk, {}), applicable_slugs=["justwatch", "streaming_availability"]
            )
        # Assert desired metered scheduling outcome
        self.assertNotIn("streaming_availability", decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"].get("streaming_availability"), "not_needed")


class B1ExplicitRefreshTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2003, title="B1 Explicit Refresh", year=2024, content_type=ContentType.MOVIE)
        WatchlistItem.objects.get_or_create(user_id=1, movie=self.movie)
        for code in ("IT",):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    @unittest.expectedFailure
    def test_explicit_refresh_includes_sa_subject_to_quota(self):
        """Expected failure: desired contract that EXPLICIT single-title and bulk refreshes include SA subject to quota.
        B2 removal instructions: Remove this expectedFailure decorator and update assertion once Phase B2 explicit refresh override is deployed.
        """
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

        self.assertIn("streaming_availability", res["sources"])

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

    def test_classifier_with_older_negatives_and_newer_positives(self):
        # Older negative request
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="NL",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
            completed_at=timezone.now() - dt.timedelta(hours=10),
        )
        # Newer same-source positive offer
        local_offer(self.movie, country="NL", service="netflix")

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)

    def test_stale_negatives_vs_fresh(self):
        # Stale negative request
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="NL",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
            completed_at=timezone.now() - dt.timedelta(days=5),
        )
        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        sa_row = next((r for r in entry["sources"] if r["source"] == "streaming_availability"), None)
        self.assertIsNotNone(sa_row)


class B1MappingConfidenceTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="test-scraper", defaults={"name": "Test Scraper", "category": "scraper"}
        )
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999, content_type=ContentType.MOVIE)
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})

    def test_scraped_content_mapping_varying_confidence_and_timestamps(self):
        # Create actual ScrapedContentMapping records with varying is_confirmed/confidence/timestamps
        ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Fight Club Unconfirmed",
            scraped_year=1999,
            country_code="US",
            movie=self.movie,
            is_confirmed=False,
            confidence_score=0.3,
            is_available=True,
            last_scraped_at=timezone.now() - dt.timedelta(days=10),
        )
        ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Fight Club",
            scraped_year=1999,
            country_code="US",
            movie=self.movie,
            is_confirmed=True,
            confidence_score=0.95,
            is_available=True,
            last_scraped_at=timezone.now(),
        )

        report = ar.title_source_report([self.movie.pk], country="US")
        entry = report.get(self.movie.pk, {})
        self.assertIn("test-scraper", entry.get("available_on", []))


class B1ContentIdentityTests(TestCase):
    def setUp(self):
        for code in ("NL", "IT", "JP"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def test_movie_vs_series_identity_collision(self):
        for idx, code in enumerate(("NL", "IT", "JP"), start=3001):
            movie_row = Movie.objects.create(tmdb_id=idx, title=f"Identity Test {code}", year=2024, content_type=ContentType.MOVIE)
            series_collision = Movie(
                tmdb_id=idx,
                title=f"Identity Test Series {code}",
                year=2024,
                content_type=ContentType.SERIES,
            )

            local_offer(movie_row, country=code)
            self.assertFalse(is_stale(movie_row, country=code))
            self.assertTrue(is_stale(series_collision, country=code))

    def test_country_scope_isolation_nl_it_jp(self):
        movie = Movie.objects.create(tmdb_id=3002, title="Country Scope", year=2024, content_type=ContentType.MOVIE)
        local_offer(movie, country="NL")

        self.assertFalse(is_stale(movie, country="NL"))
        self.assertTrue(is_stale(movie, country="IT"))
        self.assertTrue(is_stale(movie, country="JP"))
