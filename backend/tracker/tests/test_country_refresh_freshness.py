"""Country-aware refresh freshness regressions at the tracker boundary."""
from __future__ import annotations

from unittest.mock import MagicMock

from django.test import TestCase

from availability.domain.enums import ContentType
from availability.sources import DEFAULT_SOURCE_SLUG
from tracker.models import (
    Movie,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services.bulk_refresh import is_stale
from tracker.services.refresh_requests import (
    process_refresh_queue,
    recently_attempted,
    request_availability_refresh,
    request_title_refresh,
    request_watchlist_refresh,
)
from tracker.tests.availability_factories import local_offer


class CountryAwareRefreshTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=9101, title="Country Scope", year=2024, content_type=ContentType.MOVIE
        )
        self.series_collision = Movie.objects.create(
            tmdb_id=9101,
            title="Country Scope Series",
            year=2024,
            content_type=ContentType.SERIES,
        )

    def test_fresh_us_evidence_does_not_suppress_nl_refresh(self):
        local_offer(self.movie, country="US")

        self.assertFalse(is_stale(self.movie, country="US"))
        self.assertTrue(is_stale(self.movie, country="NL"))

    def test_fresh_nl_evidence_satisfies_nl_refresh(self):
        local_offer(self.movie, country="NL")

        self.assertFalse(is_stale(self.movie, country="NL"))

    def test_recent_jp_evidence_does_not_suppress_nl_refresh(self):
        local_offer(self.movie, country="JP")

        self.assertTrue(is_stale(self.movie, country="NL"))

    def test_movie_series_identity_collision_cannot_cross_contaminate(self):
        local_offer(self.movie, country="NL")

        self.assertFalse(is_stale(self.movie, country="NL"))
        self.assertTrue(is_stale(self.series_collision, country="NL"))

    def test_global_behavior_is_only_used_when_country_is_omitted(self):
        local_offer(self.movie, country="US")

        self.assertFalse(is_stale(self.movie))
        self.assertTrue(is_stale(self.movie, country="NL"))


class CountryAwareEnqueueTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=9102, title="Queued Scope", year=2024)

    def test_enqueue_deduplicates_only_same_movie_source_and_country(self):
        us = request_availability_refresh(self.movie, source=DEFAULT_SOURCE_SLUG, country="us")
        nl = request_availability_refresh(self.movie, source=DEFAULT_SOURCE_SLUG, country="NL")
        us_again = request_availability_refresh(self.movie, source=DEFAULT_SOURCE_SLUG, country="US")

        self.assertEqual(us.pk, us_again.pk)
        self.assertNotEqual(us.pk, nl.pk)
        self.assertEqual(
            set(RefreshRequest.objects.values_list("country", flat=True)), {"US", "NL"}
        )

    def test_recent_attempt_in_us_does_not_suppress_nl_enqueue(self):
        RefreshRequest.objects.create(
            movie=self.movie, source=DEFAULT_SOURCE_SLUG, country="US",
            status=RefreshRequestStatus.DONE,
        )
        self.assertTrue(recently_attempted(self.movie, DEFAULT_SOURCE_SLUG, "US"))
        self.assertFalse(recently_attempted(self.movie, DEFAULT_SOURCE_SLUG, "NL"))

    def test_title_and_watchlist_fanout_preserve_country(self):
        request_title_refresh(self.movie, country="NL", sources=[DEFAULT_SOURCE_SLUG])
        self.assertEqual(RefreshRequest.objects.get().country, "NL")

        RefreshRequest.objects.all().delete()
        WatchlistItem.objects.create(user_id=1, movie=self.movie)
        request_watchlist_refresh(country="NL", sources=[DEFAULT_SOURCE_SLUG])
        self.assertEqual(RefreshRequest.objects.get().country, "NL")

    def test_us_title_not_found_does_not_suppress_nl_request(self):
        RefreshRequest.objects.create(
            movie=self.movie, source=DEFAULT_SOURCE_SLUG, country="US",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        request_availability_refresh(
            self.movie, source=DEFAULT_SOURCE_SLUG, country="NL", if_stale=False,
        )

        service = MagicMock()
        process_refresh_queue(budget=5, service=service)

        service.refresh_content.assert_called_once_with(self.movie, country="NL")

    def test_unknown_country_title_not_found_does_not_suppress_country_request(self):
        RefreshRequest.objects.create(
            movie=self.movie, source=DEFAULT_SOURCE_SLUG, country="",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        request_availability_refresh(
            self.movie, source=DEFAULT_SOURCE_SLUG, country="NL", if_stale=False,
        )

        service = MagicMock()
        process_refresh_queue(budget=5, service=service)

        service.refresh_content.assert_called_once_with(self.movie, country="NL")
