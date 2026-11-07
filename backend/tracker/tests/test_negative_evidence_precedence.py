"""Database-backed regression tests for B2.3b negative-evidence precedence."""

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    Movie,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
)
from tracker.services import availability_report as ar


def make_movie(tmdb_id: int) -> Movie:
    return Movie.objects.create(tmdb_id=tmdb_id, title="T", year=2000)


class NegativeEvidencePrecedenceDbTests(TestCase):
    def test_done_unknown_does_not_supersede_older_permanent_miss(self):
        movie = make_movie(1)
        older = RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        newer = RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.UNKNOWN,
        )
        self.assertLess(older.id, newer.id)
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertIn("streaming_availability", misses[movie.id])

    def test_done_no_offer_supersedes_older_permanent_miss(self):
        movie = make_movie(2)
        RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.NO_OFFER,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))

    def test_offers_found_supersedes_older_permanent_miss(self):
        movie = make_movie(3)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.OFFERS_FOUND,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))

    def test_failed_upstream_title_not_found_is_permanent(self):
        movie = make_movie(4)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.FAILED, outcome=RefreshRequestOutcome.UNKNOWN,
            last_error="UpstreamTitleNotFound: no record",
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertIn("streaming_availability", misses[movie.id])

    def test_transient_404_not_permanent(self):
        movie = make_movie(5)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.FAILED, outcome=RefreshRequestOutcome.UNKNOWN,
            last_error="HTTP 404 not found (transient)",
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))

    def test_country_isolation_NL_vs_IT(self):
        movie = make_movie(6)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses_nl = ar._permanent_miss_sources([movie.id], country="NL")
        misses_it = ar._permanent_miss_sources([movie.id], country="IT")
        self.assertIn("streaming_availability", misses_nl[movie.id])
        self.assertNotIn("streaming_availability", misses_it.get(movie.id, set()))

    def test_source_isolation(self):
        movie = make_movie(7)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="source_a",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertIn("source_a", misses[movie.id])
        self.assertNotIn("source_b", misses.get(movie.id, set()))

    def test_batch_isolation(self):
        m1 = make_movie(8)
        m2 = make_movie(9)
        RefreshRequest.objects.create(
            movie=m1, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses = ar._permanent_miss_sources([m1.id, m2.id], country="NL")
        self.assertIn("streaming_availability", misses[m1.id])
        self.assertNotIn("streaming_availability", misses.get(m2.id, set()))

    def test_no_country_filter_aggregates(self):
        movie = make_movie(10)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses = ar._permanent_miss_sources([movie.id])
        self.assertIn("streaming_availability", misses[movie.id])

    def test_unknown_does_not_manufacture_no_offer(self):
        # Cached DONE/UNKNOWN must not be treated as a definitive negative.
        movie = make_movie(11)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.UNKNOWN,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))
