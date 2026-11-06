"""Country-aware refresh freshness regressions at the tracker boundary."""
from __future__ import annotations

from django.test import TestCase

from availability.domain.enums import ContentType
from tracker.models import Movie
from tracker.services.bulk_refresh import is_stale
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
