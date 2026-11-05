"""Unit and integration tests for metered gating and Watchmode budget safety.

Tests the explicit truth table of source availability vs entitlement vs
resolution state vs skip/consult decision, without making any external or
paid network calls.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.models import ContentType, Country, Offer, ProviderContent, Source
from tracker.models import (
    ApiCallCounter,
    ApiCallLedger,
    AvailabilityResolutionReason,
    BlockedReason,
    Movie,
    Provider,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services import availability_report as ar
from tracker.services import rate_limit, refresh_requests as rr, source_policy
from tracker.services.bulk_refresh import BulkRefreshSummary

SA = "streaming_availability"
WM = "watchmode"
JW = "justwatch"
COLLECTOR = "mediaset_infinity"


class MeteredGateTruthTableTests(TestCase):
    """Pin the full truth table specified in docs/METERED_GATE_POLICY_AUDIT.md."""

    def setUp(self):
        self.applicable = [COLLECTOR, JW, WM, SA]
        self.country_it, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
        self.country_us, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})

    def _movie(self, tmdb_id: int, title: str) -> Movie:
        return Movie.objects.create(tmdb_id=tmdb_id, title=title, year=2020)

    def _offer(
        self,
        movie: Movie,
        source_slug: str,
        *,
        provider_slug: str | None = None,
        country: Country | None = None,
        offer_type: str = "subscription",
        available: bool = True,
    ) -> Offer:
        prov_slug = provider_slug or f"{source_slug}-prov"
        provider, _ = Provider.objects.get_or_create(
            slug=prov_slug, defaults={"name": prov_slug, "category": "stream"}
        )
        source, _ = Source.objects.get_or_create(
            slug=source_slug, defaults={"name": source_slug}
        )
        return Offer.objects.create(
            tmdb_id=movie.tmdb_id,
            provider=provider,
            country=country or self.country_it,
            source=source,
            content_type=ContentType.MOVIE,
            offer_type=offer_type,
            currently_available=available,
        )

    def _mark_done_no_offer(self, movie: Movie, slug: str) -> None:
        RefreshRequest.objects.update_or_create(
            movie=movie,
            source=slug,
            status=RefreshRequestStatus.DONE,
            defaults={"completed_at": timezone.now(), "last_error": ""},
        )

    def _decide(self, movie: Movie, *, gate: bool) -> dict:
        entry = ar.title_source_report([movie.pk]).get(movie.pk, {})
        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1" if gate else "0"}):
            return source_policy.decide_sources_for_title(
                entry, applicable_slugs=self.applicable
            )

    def test_t01_collector_positive_skips_sa_when_gate_on(self):
        movie = self._movie(101, "T01")
        self._offer(movie, COLLECTOR)

        dec_off = self._decide(movie, gate=False)
        self.assertIn(SA, dec_off["allowed_sources"])
        self.assertNotIn(SA, dec_off["skipped_sources"])

        dec_on = self._decide(movie, gate=True)
        self.assertNotIn(SA, dec_on["allowed_sources"])
        self.assertEqual(dec_on["skipped_sources"].get(SA), "not_needed")

    def test_t02_provider_coverage_is_not_user_entitlement(self):
        """Title is resolved in catalog even if user entitlement is separate."""
        movie = self._movie(102, "T02")
        self._offer(movie, JW, provider_slug="netflix")

        dec_on = self._decide(movie, gate=True)
        self.assertNotIn(SA, dec_on["allowed_sources"])
        self.assertEqual(dec_on["skipped_sources"].get(SA), "not_needed")

    def test_t03_paid_rental_or_addon_still_resolves_title_availability(self):
        movie = self._movie(103, "T03")
        self._offer(movie, JW, offer_type="rent")

        dec_on = self._decide(movie, gate=True)
        self.assertNotIn(SA, dec_on["allowed_sources"])
        self.assertEqual(dec_on["skipped_sources"].get(SA), "not_needed")

    def test_t05_all_cheaper_sources_negative_skips_sa(self):
        """All cheaper sources answering no-offer resolves title as unavailable."""
        movie = self._movie(105, "T05")
        for slug in ar._title_level_slugs():
            self._mark_done_no_offer(movie, slug)

        entry = ar.title_source_report([movie.pk]).get(movie.pk, {})
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNAVAILABLE)

        dec_on = self._decide(movie, gate=True)
        self.assertNotIn(SA, dec_on["allowed_sources"])
        self.assertEqual(dec_on["skipped_sources"].get(SA), "not_needed")

    def test_t06_cheaper_source_unanswered_keeps_sa_consult(self):
        """If any cheaper source is not_checked or queued, title is unknown and SA is consulted."""
        movie = self._movie(106, "T06")
        self._mark_done_no_offer(movie, JW)
        self._mark_done_no_offer(movie, WM)
        # COLLECTOR has not been checked, so title is unknown.
        entry = ar.title_source_report([movie.pk]).get(movie.pk, {})
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)

        dec_on = self._decide(movie, gate=True)
        self.assertIn(SA, dec_on["allowed_sources"])
        self.assertNotIn(SA, dec_on["skipped_sources"])

    def test_t07_missing_language_metadata_does_not_prevent_title_resolution(self):
        """Language compatibility UNKNOWN does not treat title as unresolved."""
        movie = self._movie(107, "T07")
        self._offer(movie, JW)

        entry = ar.title_source_report([movie.pk]).get(movie.pk, {})
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)
        dec_on = self._decide(movie, gate=True)
        self.assertNotIn(SA, dec_on["allowed_sources"])

    def test_t08_withdrawn_or_inactive_offer_keeps_sa_consult(self):
        movie = self._movie(108, "T08")
        self._offer(movie, JW, available=False)

        entry = ar.title_source_report([movie.pk]).get(movie.pk, {})
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)
        dec_on = self._decide(movie, gate=True)
        self.assertIn(SA, dec_on["allowed_sources"])

    def test_t12_explicit_refresh_bypasses_drain_gate(self):
        """User manual EXPLICIT priority forces consult even if title resolved."""
        movie = self._movie(112, "T12")
        self._offer(movie, JW, available=True)

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            is_unnecessary = rr._metered_source_is_unnecessary(
                movie, SA, RefreshPriority.EXPLICIT, explicitly_named_source=True
            )
            self.assertFalse(is_unnecessary, "EXPLICIT priority must bypass gating")


class StreamingAvailabilityGateTests(TestCase):
    """Verify Streaming Availability skip vs consult behavior in execution paths."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=88801, title="SA Gating Test", year=2021)
        WatchlistItem.objects.create(user_id=1, movie=self.movie)

    def test_enqueue_skips_sa_when_gate_on_and_title_resolved(self):
        # Resolve via Watchmode offer
        prov, _ = Provider.objects.get_or_create(slug="wm-p", defaults={"name": "wm-p"})
        src, _ = Source.objects.get_or_create(slug=WM, defaults={"name": WM})
        country, _ = Country.objects.get_or_create(code="IT", defaults={"name": "IT"})
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

        self.assertNotIn(SA, res["sources"])
        self.assertEqual(res["skipped_counts"].get("not_needed", 0), 1)
        sa_requests = RefreshRequest.objects.filter(movie=self.movie, source=SA)
        self.assertEqual(sa_requests.count(), 0)

    def test_enqueue_includes_sa_when_gate_off_even_if_title_resolved(self):
        prov, _ = Provider.objects.get_or_create(slug="wm-p2", defaults={"name": "wm-p2"})
        src, _ = Source.objects.get_or_create(slug=WM, defaults={"name": WM})
        country, _ = Country.objects.get_or_create(code="IT", defaults={"name": "IT"})
        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=prov,
            source=src,
            country=country,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
        )

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "0"}):
            res = rr.request_watchlist_refresh(user_id=1)

        self.assertIn(SA, res["sources"])
        sa_requests = RefreshRequest.objects.filter(movie=self.movie, source=SA)
        self.assertEqual(sa_requests.count(), 1)


class WatchmodeBudgetSafetyTests(TestCase):
    """Verify that Watchmode is treated with finite budget semantics and safe exhaustion."""

    def test_watchmode_has_finite_monthly_limit_in_rate_limiter(self):
        limiter = rate_limit.rate_limiter_for(WM)
        self.assertTrue(limiter.metered)
        self.assertIsNone(limiter.daily_limit, "Watchmode has no daily quota")
        self.assertEqual(limiter.monthly_limit, rate_limit.WATCHMODE_MONTHLY_BUDGET_DEFAULT)
        self.assertEqual(limiter.monthly_limit, 2200)

    def test_watchmode_budget_exhaustion_trips_circuit_and_blocks_safely(self):
        circuit = rate_limit.SourceQuotaCircuit()
        self.assertFalse(circuit.is_tripped(WM))

        diag = "SourceQuotaExceeded(kind=plan_quota, service=watchmode): Over plan quota on this API Key."
        circuit.trip(WM, diag)
        self.assertTrue(circuit.is_tripped(WM))
        self.assertEqual(circuit.diagnostic(WM), diag)

    def test_empty_ledger_is_conservative_lower_bound_not_inflated(self):
        """An empty ledger does not invent free quota if counter is ahead or days missing."""
        limiter = rate_limit.RateLimiter(WM, daily_limit=None, monthly_limit=2200)
        today = dt.date.today()
        ApiCallLedger.objects.filter(service=WM).delete()
        ApiCallCounter.objects.filter(service=WM).delete()

        used = limiter.monthly_used()
        self.assertEqual(used, 0)
        self.assertFalse(limiter.is_exhausted())

        # Seed counter with usage exceeding cap
        ApiCallCounter.objects.create(service=WM, call_date=today, count=2200)
        used_with_counter = limiter.monthly_used()
        self.assertEqual(used_with_counter, 2200)
        self.assertTrue(limiter.is_exhausted())


class CounterfactualEvaluationTests(TestCase):
    """Verify counterfactual projections without network calls."""

    def test_counterfactual_estimates_savings_safely(self):
        # 3 titles:
        # Title 1: resolved by JW & SA -> still resolved if SA gated
        # Title 2: resolved only by SA -> loses answer if SA gated
        # Title 3: unavailable (state != 'available') -> not affected
        title_states = {
            1: "available",
            2: "available",
            3: "unavailable",
        }
        resolved_sources = {
            1: {JW, SA},
            2: {SA},
            3: set(),
        }
        cf = source_policy.counterfactual(title_states, resolved_sources)
        self.assertEqual(cf["titles_still_resolved"], 1)
        self.assertEqual(cf["titles_losing_their_answer"], 1)
        self.assertIn("Streaming Availability", [ar.source_label(s) for s in cf["metered_sources"]])
        self.assertIn("Counterfactual only", cf["note"])
