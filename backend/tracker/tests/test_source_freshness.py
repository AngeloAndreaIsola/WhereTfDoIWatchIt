"""Per-source freshness through the refresh paths (Phase D).

``availability.freshness`` owns *when an observation becomes stale*; these
tests pin how that policy flows through the refresh infrastructure: the
``is_stale`` guard, the background sweep's source selection, the event-driven
drain's settle-without-spending rule, and the enqueue guard. None of them
share a global timeout anymore — each source scope answers for itself.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from availability.domain.enums import OfferType, SourceType
from availability.freshness import (
    FRESHNESS_INTERVAL_KEY,
    TITLE_REFRESH_INTERVAL_KEY,
    declared_freshness,
    freshness_for,
    source_configuration_snapshot,
    source_is_stale,
)
from availability.models import Country, Offer, Source
from availability.sources import DEFAULT_SOURCE_SLUG
from tracker.models import (
    Movie,
    Provider,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services.bulk_refresh import (
    is_stale,
    process_watchlist_refresh,
    refreshable_source_slugs,
    stale_refresh_scopes,
)
from tracker.services.rate_limit import RateLimiter
from tracker.services.refresh_requests import request_availability_refresh
from tracker.services.refresh_requests import process_refresh_queue
from tracker.tests.availability_factories import local_offer, reviewed_test_sources

PRIME = "prime_video"
CATALOGUE_SLUGS = ("cbc_gem", "bbc_iplayer", "tubi", PRIME, "rai_play", "pluto_tv", "pbs", "abc_iview")


def _scope_offer(movie, slug, *, age, tag, currently_available=True):
    """One canonical observation of *slug* for *movie*, last_seen *age* ago."""
    source, _ = Source.objects.get_or_create(
        slug=slug, defaults={"name": slug, "type": SourceType.API}
    )
    provider, _ = Provider.objects.get_or_create(
        slug=f"freshness-{slug}", defaults={"name": slug}
    )
    country, _ = Country.objects.get_or_create(code="US", defaults={"name": "US"})
    row = Offer.objects.create(
        provider=provider,
        country=country,
        source=source,
        content_type=movie.content_type,
        tmdb_id=movie.tmdb_id,
        offer_type=OfferType.SUBSCRIPTION,
        provider_content_id=tag,
        currently_available=currently_available,
    )
    Offer.objects.filter(pk=row.pk).update(last_seen=timezone.now() - age)
    return row


def _backdate_default_scope(movie, *, age):
    """Age the default scope's observations of *movie* by *age*."""
    Offer.objects.filter(
        tmdb_id=movie.tmdb_id, source__slug=DEFAULT_SOURCE_SLUG
    ).update(last_seen=timezone.now() - age)


class ScopedStalenessTests(TestCase):
    """``is_stale`` answers per source scope, never across sources."""

    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=801, title="Scoped", year=2021
        )

    def test_scopes_go_stale_independently(self):
        local_offer(self.movie)  # default scope, just observed
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=25), tag="prime-old"
        )

        self.assertFalse(is_stale(self.movie))  # default scope: fresh
        self.assertTrue(
            is_stale(self.movie, source=PRIME)
        )  # prime's own 24h window has closed

    def test_a_fresh_catalogue_observation_never_masks_a_stale_default_scope(self):
        local_offer(self.movie)
        _backdate_default_scope(self.movie, age=dt.timedelta(hours=48))
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=1), tag="prime-fresh"
        )

        self.assertTrue(is_stale(self.movie))  # default scope is stale anyway
        self.assertFalse(is_stale(self.movie, source=PRIME))

    def test_the_default_scope_boundary_is_its_own_window(self):
        local_offer(self.movie)
        observed = timezone.now()
        Offer.objects.filter(
            tmdb_id=self.movie.tmdb_id, source__slug=DEFAULT_SOURCE_SLUG
        ).update(last_seen=observed)
        window = freshness_for(DEFAULT_SOURCE_SLUG)

        self.assertFalse(
            is_stale(self.movie, now=observed + window - dt.timedelta(microseconds=1))
        )  # just inside the window
        self.assertFalse(
            is_stale(self.movie, now=observed + window)
        )  # exactly at the boundary
        self.assertTrue(
            is_stale(
                self.movie,
                now=observed + window + dt.timedelta(microseconds=1),
            )
        )  # just past it


class ConfiguredFreshnessSchedulingTests(TestCase):
    """Schedulers use the same persisted source window as read verdicts."""

    def setUp(self):
        reviewed_test_sources()
        self.movie = Movie.objects.create(
            tmdb_id=8210, title="Configured Window", year=2022
        )
        self.source, _ = Source.objects.get_or_create(
            slug="justwatch",
            defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR},
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "US"}
        )

    def _set_interval(self, seconds):
        self.source.configuration = {**self.source.configuration, TITLE_REFRESH_INTERVAL_KEY: seconds}
        self.source.save(update_fields=["configuration"])

    def _justwatch_offer(self, *, age=dt.timedelta(hours=2), content_type="movie"):
        provider, _ = Provider.objects.get_or_create(
            slug="freshness-justwatch-provider",
            defaults={"name": "Freshness JustWatch Provider"},
        )
        offer = Offer.objects.create(
            provider=provider,
            country=self.country,
            source=self.source,
            content_type=content_type,
            tmdb_id=self.movie.tmdb_id,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id=f"jw-{content_type}",
        )
        Offer.objects.filter(pk=offer.pk).update(last_seen=timezone.now() - age)
        return offer

    def test_one_hour_override_makes_two_hour_evidence_stale(self):
        self._set_interval(3600)
        self._justwatch_offer()

        self.assertTrue(
            is_stale(self.movie, source="justwatch", country="US")
        )
        self.assertIn(
            "justwatch",
            stale_refresh_scopes(
                self.movie.tmdb_id,
                country="US",
                content_type="movie",
                source_configurations=source_configuration_snapshot(),
            ),
        )

    def test_forty_eight_hour_override_keeps_two_hour_evidence_fresh(self):
        self._set_interval(48 * 3600)
        self._justwatch_offer()

        self.assertFalse(
            is_stale(self.movie, source="justwatch", country="US")
        )
        self.assertNotIn(
            "justwatch",
            stale_refresh_scopes(
                self.movie.tmdb_id,
                country="US",
                content_type="movie",
                source_configurations=source_configuration_snapshot(),
            ),
        )

    def test_effective_window_keeps_title_media_source_and_country_scoped(self):
        self._set_interval(48 * 3600)
        self._justwatch_offer()
        configuration = self.source.configuration

        self.assertFalse(
            is_stale(
                self.movie,
                source="justwatch",
                country="US",
                configuration=configuration,
            )
        )
        self.assertTrue(
            is_stale(
                self.movie,
                source="justwatch",
                country="NL",
                configuration=configuration,
            )
        )
        self.assertTrue(
            source_is_stale(
                self.movie.tmdb_id,
                "justwatch",
                country="US",
                content_type="series",
                configuration=configuration,
            ),
            "a movie observation must not make a same-ID series scope fresh",
        )
        self.assertTrue(
            is_stale(
                self.movie,
                source="streaming_availability",
                country="US",
                configuration=configuration,
            )
        )
        other_title = Movie.objects.create(
            tmdb_id=8211, title="Other", year=2022
        )
        self.assertTrue(
            is_stale(
                other_title,
                source="justwatch",
                country="US",
                configuration=configuration,
            )
        )

    def test_invalid_or_missing_configuration_uses_documented_default(self):
        self._justwatch_offer()
        default_window = freshness_for("justwatch")
        self.source.configuration = {FRESHNESS_INTERVAL_KEY: "invalid"}
        self.source.save(update_fields=["configuration"])
        self.assertFalse(is_stale(self.movie, source="justwatch", country="US"))
        self.assertEqual(default_window, dt.timedelta(hours=12))

        self.source.configuration = {}
        self.source.save(update_fields=["configuration"])
        self.assertFalse(is_stale(self.movie, source="justwatch", country="US"))

    def test_unrepresentable_or_zero_length_override_uses_default(self):
        default_window = freshness_for("justwatch")
        for invalid in (float("inf"), float("nan"), 10**100, 1e-7, 0):
            with self.subTest(invalid=invalid):
                self.assertEqual(
                    declared_freshness(
                        "justwatch", {FRESHNESS_INTERVAL_KEY: invalid}
                    ),
                    default_window,
                )

    def test_configuration_snapshot_is_one_query_for_a_title_batch(self):
        self._set_interval(3600)
        self._justwatch_offer()
        second = Movie.objects.create(tmdb_id=8212, title="Second", year=2022)
        third = Movie.objects.create(tmdb_id=8213, title="Third", year=2022)

        with CaptureQueriesContext(connection) as queries:
            configurations = source_configuration_snapshot()
            for movie in (self.movie, second, third):
                stale_refresh_scopes(
                    movie.tmdb_id,
                    country="US",
                    content_type="movie",
                    source_configurations=configurations,
                )

        source_reads = [
            query for query in queries
            if (
                '"availability_source"' in query["sql"].lower()
                and '"configuration"' in query["sql"].lower()
            )
        ]
        self.assertEqual(len(source_reads), 1)

    def test_snapshot_does_not_create_or_enable_sources(self):
        disabled_slug = "disabled-freshness-fixture"
        Source.objects.create(
            slug=disabled_slug,
            name="Disabled Fixture",
            type=SourceType.API,
            enabled=False,
        )

        snapshot = source_configuration_snapshot(
            [disabled_slug, "never-configured-fixture"]
        )

        self.assertIn(disabled_slug, snapshot)
        self.assertNotIn("never-configured-fixture", snapshot)
        self.assertFalse(Source.objects.get(slug=disabled_slug).enabled)
        self.assertFalse(
            Source.objects.filter(slug="never-configured-fixture").exists()
        )

    def test_config_change_to_longer_window_settles_queued_work_without_refresh(self):
        self._set_interval(3600)
        self._justwatch_offer()
        request = request_availability_refresh(
            self.movie,
            source="justwatch",
            country="US",
            if_stale=True,
        )
        self.assertIsNotNone(request)

        self._set_interval(48 * 3600)
        service = MagicMock()
        result = process_refresh_queue(budget=5, service=service)

        self.assertEqual(result["requests_drained"], 1)
        self.assertEqual(result["skipped_cached"], 1)
        self.assertEqual(result["refreshed"], 0)
        service.refresh_content.assert_not_called()
        self.assertEqual(
            RefreshRequest.objects.get(pk=request.pk).status,
            RefreshRequestStatus.DONE,
        )
        self.assertIsNone(
            request_availability_refresh(
                self.movie,
                source="justwatch",
                country="US",
                if_stale=True,
            ),
            "fresh evidence after the configuration change must not be requeued",
        )


class SweepScopeSelectionTests(TestCase):
    """The sweep refreshes stale refreshable scopes, and only those."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=802, title="Sweep", year=2020)
        WatchlistItem.objects.create(user_id=1, movie=self.movie)
        self.refresh_service = MagicMock()
        self.limiter = MagicMock(spec=RateLimiter)
        self.limiter.remaining.return_value = 100

    def _run(self, budget=10):
        return process_watchlist_refresh(
            budget=budget,
            user_id=1,
            refresh_service=self.refresh_service,
            limiter=self.limiter,
        )

    def test_only_title_targeted_sources_are_refreshable_by_the_sweep(self):
        self.assertIn(DEFAULT_SOURCE_SLUG, refreshable_source_slugs())
        for slug in CATALOGUE_SLUGS:
            self.assertNotIn(slug, refreshable_source_slugs())

    def test_stale_refresh_scopes_selects_the_stale_refreshable_source(self):
        local_offer(self.movie)  # default scope observed, then aged
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=48), tag="prime-stale"
        )
        _backdate_default_scope(self.movie, age=dt.timedelta(hours=48))
        now = timezone.now()  # after all writes: both scopes are >24h old

        self.assertEqual(
            stale_refresh_scopes(self.movie.tmdb_id, now=now),
            [DEFAULT_SOURCE_SLUG],
        )

        # A stale catalogue source is visible as stale, but it is not this
        # run's to refresh: its refresh belongs to the catalogue job.
        Offer.objects.filter(
            tmdb_id=self.movie.tmdb_id, source__slug=DEFAULT_SOURCE_SLUG
        ).update(last_seen=timezone.now())  # default scope fresh again
        now = timezone.now()
        self.assertEqual(stale_refresh_scopes(self.movie.tmdb_id, now=now), [])

    def test_a_stale_default_scope_refreshes_while_a_fresh_source_is_left_alone(self):
        local_offer(self.movie)
        _backdate_default_scope(self.movie, age=dt.timedelta(hours=48))
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=1), tag="prime-fresh"
        )

        summary = self._run()

        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["skipped_cached"], 0)
        self.assertEqual(
            [
                call.args[0].tmdb_id
                for call in self.refresh_service.refresh_content.call_args_list
            ],
            [self.movie.tmdb_id],
        )

    def test_a_stale_catalogue_source_does_not_refresh_a_fresh_default_scope(self):
        local_offer(self.movie)  # default scope fresh
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=48), tag="prime-stale"
        )

        summary = self._run()

        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["skipped_cached"], 1)
        self.refresh_service.refresh_content.assert_not_called()


class DrainScopeTests(TestCase):
    """The drain settles requests on their own scope's freshness."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=803, title="Drained", year=2019)
        WatchlistItem.objects.create(user_id=1, movie=self.movie)
        self.refresh_service = MagicMock()
        self.limiter = MagicMock(spec=RateLimiter)
        self.limiter.remaining.return_value = 100

    def _run(self, budget=5):
        return process_watchlist_refresh(
            budget=budget,
            user_id=1,
            refresh_service=self.refresh_service,
            limiter=self.limiter,
        )

    def test_request_refreshes_when_its_own_scope_is_stale_despite_a_fresh_source(self):
        local_offer(self.movie)
        _backdate_default_scope(self.movie, age=dt.timedelta(hours=48))
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=1), tag="prime-fresh"
        )
        request_availability_refresh(
            self.movie, priority=RefreshPriority.RECENTLY_VIEWED
        )

        summary = self._run()

        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["skipped_cached"], 0)
        self.assertEqual(summary["requests_drained"], 1)
        self.refresh_service.refresh_content.assert_called_once_with(self.movie, country=None)

    def test_a_stale_other_source_does_not_block_settling_a_fresh_scope(self):
        local_offer(self.movie)  # default scope fresh -> settle for free
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=48), tag="prime-stale"
        )
        # Bypass the enqueue guard: the request was made while stale and the
        # scope has since been refreshed.
        request_availability_refresh(
            self.movie, priority=RefreshPriority.RECENTLY_VIEWED, if_stale=False
        )

        summary = self._run()

        self.assertEqual(summary["requests_drained"], 1)
        self.assertEqual(summary["skipped_cached"], 1)
        self.assertEqual(summary["refreshed"], 0)
        self.refresh_service.refresh_content.assert_not_called()

    def test_a_failed_refresh_does_not_make_the_scope_fresh(self):
        local_offer(self.movie)
        _backdate_default_scope(self.movie, age=dt.timedelta(hours=48))
        request_availability_refresh(
            self.movie, priority=RefreshPriority.RECENTLY_VIEWED
        )
        self.refresh_service.refresh_content.side_effect = RuntimeError(
            "provider exploded"
        )

        summary = self._run()

        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["errors"], 1)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.attempts, 1)
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)

        # The failed call wrote nothing: the scope still reads as stale, so a
        # later run retries instead of treating the title as fresh.
        self.assertTrue(is_stale(self.movie))


class EnqueueScopeTests(TestCase):
    """The enqueue guard checks the scope the caller asked to refresh."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=804, title="Enqueued", year=2022)

    def test_enqueue_guard_reads_the_requested_scope(self):
        local_offer(self.movie)  # default scope fresh

        self.assertIsNone(request_availability_refresh(self.movie))

        # The prime scope never observed this title: still enqueueable even
        # though the default scope is fresh.
        request = request_availability_refresh(self.movie, source=PRIME)
        self.assertIsNotNone(request)
        # Canonical offers exist for the title, so the title-level NEW bump
        # (Phase B priority semantics) stays untouched.
        self.assertEqual(request.priority, RefreshPriority.STALE)

    def test_enqueue_guard_skips_when_the_requested_scope_is_fresh(self):
        _scope_offer(
            self.movie, PRIME, age=dt.timedelta(hours=1), tag="prime-fresh"
        )

        self.assertIsNone(
            request_availability_refresh(self.movie, source=PRIME)
        )
        # The default scope has never observed the title -> still stale.
        self.assertIsNotNone(request_availability_refresh(self.movie))
