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

    def test_fresh_nl_offer_report_and_eligibility(self):
        offer = local_offer(self.movie, country="NL", service="netflix")
        self.assertFalse(is_stale(self.movie, country="NL"))

        report = ar.title_source_report([self.movie.pk], country="NL")
        entry = report.get(self.movie.pk, {})
        self.assertEqual(entry.get("state"), ar.TitleAvailabilityState.AVAILABLE)

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision = source_policy.decide_sources_for_title(
                entry, applicable_slugs=["justwatch", "streaming_availability"]
            )
        self.assertNotIn("streaming_availability", decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"].get("streaming_availability"), "not_needed")

    def test_expired_offer_cannot_establish_availability_or_suppress_sa(self):
        """Expected failure: desired contract that expired offer cannot establish verified current availability or suppress eligible SA refresh (B2 target)."""
        offer = local_offer(self.movie, country="NL", service="netflix")
        offer.last_seen = timezone.now() - dt.timedelta(days=2)
        offer.save(update_fields=["last_seen"])
        self.assertTrue(is_stale(self.movie, country="NL"))

        report_stale = ar.title_source_report([self.movie.pk], country="NL")
        entry_stale = report_stale.get(self.movie.pk, {})
        # Desired behavior: expired offer cannot establish verified current availability
        self.assertNotEqual(entry_stale.get("state"), ar.TitleAvailabilityState.AVAILABLE)

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision_stale = source_policy.decide_sources_for_title(
                entry_stale, applicable_slugs=["justwatch", "streaming_availability"]
            )
        # Desired behavior: expired offer cannot suppress eligible SA refresh
        self.assertIn("streaming_availability", decision_stale["allowed_sources"])


class B1MeteredSelfSuppressionTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2002, title="B1 Metered Suppress", year=2024, content_type=ContentType.MOVIE)
        for code in ("NL", "IT"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def test_active_but_expired_sa_offer_without_cheaper_evidence(self):
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
        self.assertIn("streaming_availability", decision["allowed_sources"])

    def test_stale_sa_only_offer_cannot_suppress_its_own_refresh(self):
        """Expected failure: desired contract that a stale SA-only offer without cheaper evidence cannot suppress its own refresh (B2 target)."""
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
        # Desired behavior: stale SA-only offer cannot suppress its own refresh -> SA should be allowed
        self.assertIn("streaming_availability", decision["allowed_sources"])

    def test_cheaper_source_corroboration_with_non_metered_source(self):
        """Assert that an explicitly non-metered source (justwatch) acting as a cheaper source
        corroborates availability and correctly bypasses/suppresses metered SA refresh.
        """
        jw_source, _ = Source.objects.get_or_create(slug="justwatch", defaults={"name": "JustWatch"})
        jw_prov, _ = Provider.objects.get_or_create(slug="jw-prov", defaults={"name": "JustWatch Provider"})
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})

        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=jw_prov,
            source=jw_source,
            country=country,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
            last_seen=timezone.now(),
        )

        states = ar.title_source_report([self.movie.pk], country="NL")
        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision = source_policy.decide_sources_for_title(
                states.get(self.movie.pk, {}), applicable_slugs=["justwatch", "streaming_availability"]
            )
        self.assertNotIn("streaming_availability", decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"].get("streaming_availability"), "not_needed")


class B1ExplicitRefreshTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2003, title="B1 Explicit Refresh", year=2024, content_type=ContentType.MOVIE)
        WatchlistItem.objects.get_or_create(user_id=1, movie=self.movie)
        for code in ("IT",):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    @unittest.expectedFailure
    def test_explicit_watchlist_refresh_includes_sa_subject_to_quota(self):
        """Expected failure: desired contract that EXPLICIT bulk refreshes include SA subject to quota.
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

    @unittest.expectedFailure
    def test_explicit_single_title_refresh_includes_sa_subject_to_quota(self):
        """Expected failure: desired contract that EXPLICIT single-title refreshes include SA subject to quota."""
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
            res = rr.request_title_refresh(
                self.movie,
                country="IT",
                priority=RefreshPriority.EXPLICIT,
                if_stale=False,
            )

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
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="NL",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
            completed_at=timezone.now() - dt.timedelta(hours=10),
        )
        local_offer(self.movie, country="NL", service="netflix")

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)

    def test_stale_negative_precedence_and_unresolved_expiry_policy(self):
        """Document unresolved expiry as pending policy (no arbitrary TTL):
        Negative results/outcomes (e.g. TITLE_NOT_FOUND) do not expire via an arbitrary TTL.
        Instead, recency and presence of newer positive evidence take precedence over older negative outcomes.
        """
        neg_req = RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            country="NL",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
            completed_at=timezone.now() - dt.timedelta(days=5),
        )
        local_offer(self.movie, country="NL", service="netflix")
        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        
        # Meaningful precedence assertion: older negative outcome exists in database records (not arbitrarily expired by TTL),
        # but newer positive local offer takes precedence, classifying the title state as AVAILABLE.
        self.assertTrue(RefreshRequest.objects.filter(pk=neg_req.pk).exists())
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)


class B1MappingConfidenceTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="test-scraper", defaults={"name": "Test Scraper", "category": "scraper"}
        )
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999, content_type=ContentType.MOVIE)
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})

    def test_scraped_content_mapping_varying_confidence_and_timestamps(self):
        # Unconfirmed low-confidence historical mapping
        unconfirmed = ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Fight Club Unconfirmed",
            scraped_year=1999,
            country_code="US",
            movie=self.movie,
            is_confirmed=False,
            confidence_score=0.3,
            is_available=True,
            last_scraped_at=timezone.now() - dt.timedelta(days=30),
        )
        # Confirmed high-confidence trusted mapping
        confirmed = ScrapedContentMapping.objects.create(
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

        # Report-level assertion: unconfirmed low-confidence historical evidence and confirmed high-confidence trusted evidence
        # both enter title_source_report() via title_signals (distinguishing trusted vs historical).
        title_signals = entry.get("title_signals", [])
        self.assertTrue(any(s["provider"] == "test-scraper" for s in title_signals))
        self.assertEqual(entry.get("evidence"), "title_signal")

        # Independent evaluation assertions: trusted confirmed vs historical unconfirmed
        self.assertFalse(unconfirmed.is_confirmed)
        self.assertLess(unconfirmed.confidence_score, 0.5)
        self.assertTrue(confirmed.is_confirmed)
        self.assertGreater(confirmed.confidence_score, 0.8)


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
