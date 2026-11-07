"""Database-backed regression tests for B2.3b negative-evidence precedence."""

from django.test import TestCase

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
        # ensure ordering
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
