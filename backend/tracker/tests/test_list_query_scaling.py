"""Query-count regression coverage for catalogue list reads.

The data setup is deliberately outside each capture block.  The assertion
measures only the request read path, not fixture creation.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from tracker.models import Movie, Preference
from tracker.tests.availability_factories import local_offer


class CatalogueListQueryScalingTests(TestCase):
    def setUp(self):
        Preference.objects.create(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            current_country="US",
        )

    def _add_titles(self, start: int, count: int) -> None:
        for tmdb_id in range(start, start + count):
            movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"Title {tmdb_id}")
            local_offer(movie, country="US", service="prime")

    def _query_count(self) -> int:
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/catalog/?page_size=100")
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_catalogue_queries_are_bounded_from_20_to_1000_titles(self):
        self._add_titles(10_000, 20)
        count_20 = self._query_count()

        self._add_titles(20_000, 180)
        count_200 = self._query_count()
        self._add_titles(30_000, 800)
        count_1000 = self._query_count()

        # Reconciliation performs a fixed batch of observation/mapping/claim/
        # sync/price reads; classification shares subscriptions and providers.
        self.assertLessEqual(count_20, 12)
        self.assertEqual(count_20, count_200)
        self.assertEqual(count_20, count_1000)
