"""Per-source freshness policy (Phase D).

One module decides when an observation from a given ``AvailabilitySource``
becomes stale (``availability.freshness``); refresh priority stays the
separate concern of ``tracker.RefreshPriority``. These tests pin the policy
side: a declared window per source, the configuration override, boundary
semantics, and the per-source selection helper the scheduler reads.
"""
from __future__ import annotations

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType, OfferType, FreshnessState, SourceType
from availability.freshness import (
    DEFAULT_FRESHNESS,
    FRESHNESS_INTERVAL_KEY,
    SOURCE_FRESHNESS,
    declared_freshness,
    freshness_for,
    freshness_state,
    source_is_stale,
    source_last_seen,
    stale_sources,
)
from availability.models import (
    LEGACY_SCRAPER_SOURCE_SLUG,
    Country,
    Offer,
    Source,
)
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry
from tracker.models import Provider


class DeclaredFreshnessTests(TestCase):
    """Configuration overrides the policy; the policy covers every source."""

    def test_every_source_the_application_writes_under_declares_a_policy(self):
        # A newly registered source must pick a window, not silently inherit
        # somebody else's. Legacy scraper rows are written by the ingestion
        # bridge, so they are covered even though the slug is unregistered.
        for slug in source_registry.slugs():
            self.assertIn(slug, SOURCE_FRESHNESS, slug)
        self.assertIn(LEGACY_SCRAPER_SOURCE_SLUG, SOURCE_FRESHNESS)

    def test_configuration_overrides_the_declared_policy(self):
        self.assertEqual(
            declared_freshness(DEFAULT_SOURCE_SLUG), dt.timedelta(hours=24)
        )
        self.assertEqual(
            declared_freshness(
                DEFAULT_SOURCE_SLUG, {FRESHNESS_INTERVAL_KEY: 3600}
            ),
            dt.timedelta(hours=1),
        )
        # The override drives refresh decisions the same way it drives the
        # read side: freshness_for resolves it too.
        self.assertEqual(
            freshness_for(DEFAULT_SOURCE_SLUG, {FRESHNESS_INTERVAL_KEY: 3600}),
            dt.timedelta(hours=1),
        )

    def test_invalid_configuration_falls_back_to_the_policy(self):
        for bad in (0, -1, True, "3600", "soon", [], {}):
            with self.subTest(bad=bad):
                self.assertEqual(
                    declared_freshness(
                        DEFAULT_SOURCE_SLUG, {FRESHNESS_INTERVAL_KEY: bad}
                    ),
                    dt.timedelta(hours=24),
                )

    def test_undeclared_slug_is_unknown_for_reads_but_answered_for_refreshes(self):
        # Reads must not pretend an unknown source is fresh...
        self.assertIsNone(declared_freshness("unregistered-source"))
        self.assertIsNone(declared_freshness("unregistered-source", {}))
        self.assertEqual(
            freshness_state(
                "unregistered-source", timezone.now() - dt.timedelta(hours=1)
            ),
            FreshnessState.UNKNOWN,
        )
        # ...while a refresh decision still has to pick a window.
        self.assertEqual(freshness_for("unregistered-source"), DEFAULT_FRESHNESS)

    def test_freshness_state_counts_the_boundary_as_fresh(self):
        now = timezone.now()
        window = freshness_for(DEFAULT_SOURCE_SLUG)
        for age, expected in (
            (window - dt.timedelta(seconds=1), FreshnessState.FRESH),
            (window, FreshnessState.FRESH),
            (window + dt.timedelta(seconds=1), FreshnessState.STALE),
        ):
            with self.subTest(age=age):
                self.assertEqual(
                    freshness_state(
                        DEFAULT_SOURCE_SLUG, now - age, now=now
                    ),
                    expected,
                )

    def test_naive_observation_times_are_accepted(self):
        now = timezone.now()
        naive = timezone.make_naive(now - dt.timedelta(hours=1))
        self.assertEqual(
            freshness_state(DEFAULT_SOURCE_SLUG, naive, now=now),
            FreshnessState.FRESH,
        )


class SourceObservationTests(TestCase):
    """Database-backed staleness: per-scope ``last_seen`` and selection."""

    def setUp(self):
        self.tmdb_id = 777
        self.provider, _ = Provider.objects.get_or_create(
            slug="freshness-provider", defaults={"name": "Freshness Provider"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "US"}
        )

    def _observe(self, slug, *, age, tag, tmdb_id=None, currently_available=True):
        """One canonical observation of *slug*, last_seen *age* ago."""
        source, _ = Source.objects.get_or_create(
            slug=slug, defaults={"name": slug, "type": SourceType.API}
        )
        row = Offer.objects.create(
            provider=self.provider,
            country=self.country,
            source=source,
            content_type=ContentType.MOVIE,
            tmdb_id=tmdb_id or self.tmdb_id,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id=f"{slug}-{tag}",
            currently_available=currently_available,
        )
        Offer.objects.filter(pk=row.pk).update(
            last_seen=timezone.now() - age
        )
        return row

    def test_each_source_is_measured_against_its_own_window(self):
        now = timezone.now()
        # 13h old: already past cbc_gem's 6h window, still inside
        # bbc_iplayer's 24h one — same age, different verdicts.
        self._observe("cbc_gem", age=dt.timedelta(hours=13), tag="cbc")
        self._observe("bbc_iplayer", age=dt.timedelta(hours=13), tag="bbc")

        self.assertEqual(
            stale_sources(self.tmdb_id, sources=["cbc_gem", "bbc_iplayer"], now=now),
            ["cbc_gem"],
        )

    def test_equal_windows_still_go_stale_at_different_times(self):
        now = timezone.now()
        self._observe("prime_video", age=dt.timedelta(hours=25), tag="old")
        self._observe("bbc_iplayer", age=dt.timedelta(hours=13), tag="fresh")

        self.assertEqual(
            stale_sources(self.tmdb_id, sources=["prime_video", "bbc_iplayer"], now=now),
            ["prime_video"],
        )

    def test_boundary_is_measured_against_that_sources_window(self):
        now = timezone.now()
        window = freshness_for("prime_video")
        self._observe("prime_video", age=window, tag="at-boundary")
        self.assertFalse(source_is_stale(self.tmdb_id, "prime_video", now=now))

        Offer.objects.update(last_seen=now - window - dt.timedelta(seconds=1))
        self.assertTrue(source_is_stale(self.tmdb_id, "prime_video", now=now))

    def test_a_scope_that_never_observed_the_title_is_stale(self):
        self.assertTrue(source_is_stale(self.tmdb_id, DEFAULT_SOURCE_SLUG))
        self.assertIsNone(source_last_seen(self.tmdb_id, DEFAULT_SOURCE_SLUG))

    def test_the_default_scope_is_always_a_selection_candidate(self):
        now = timezone.now()
        self._observe("prime_video", age=dt.timedelta(hours=1), tag="fresh")

        # Prime is fresh; the default scope has never been observed, so it is
        # the stale source the scheduler may still need to fetch.
        self.assertEqual(stale_sources(self.tmdb_id, now=now), [DEFAULT_SOURCE_SLUG])

        self._observe(DEFAULT_SOURCE_SLUG, age=dt.timedelta(hours=1), tag="fresh")
        self.assertEqual(stale_sources(self.tmdb_id, now=now), [])

    def test_availability_state_does_not_affect_freshness(self):
        now = timezone.now()
        # Freshly observed but no longer available: the observation is fresh.
        self._observe(
            DEFAULT_SOURCE_SLUG,
            age=dt.timedelta(hours=1),
            tag="fresh-unavailable",
            tmdb_id=778,
            currently_available=False,
        )
        self.assertFalse(source_is_stale(778, DEFAULT_SOURCE_SLUG, now=now))

        # Stale observation of an unavailable offer: staleness still applies.
        self._observe(
            DEFAULT_SOURCE_SLUG,
            age=dt.timedelta(hours=48),
            tag="old-unavailable",
            tmdb_id=779,
            currently_available=False,
        )
        self.assertTrue(source_is_stale(779, DEFAULT_SOURCE_SLUG, now=now))

    def test_newest_observation_of_the_scope_decides(self):
        now = timezone.now()
        old = self._observe(
            DEFAULT_SOURCE_SLUG, age=dt.timedelta(hours=48), tag="old"
        )
        self.assertTrue(source_is_stale(self.tmdb_id, DEFAULT_SOURCE_SLUG, now=now))

        Offer.objects.filter(pk=old.pk).update(last_seen=now)
        self.assertFalse(source_is_stale(self.tmdb_id, DEFAULT_SOURCE_SLUG, now=now))
        self.assertEqual(
            source_last_seen(self.tmdb_id, DEFAULT_SOURCE_SLUG), now
        )
