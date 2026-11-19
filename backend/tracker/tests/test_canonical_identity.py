from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase

from tracker.models import Movie
from tracker.services.unified_catalogue import _movies_by_identity
from tracker.views import _load_content_item, _resolve_watch_item, _unique_movie_for_tmdb


class CanonicalIdentityTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=42, content_type='movie', title='A film', year=2000)
        self.series = Movie.objects.create(tmdb_id=42, content_type='series', title='A series', year=2010)

    def test_database_rejects_duplicates_within_the_same_namespace(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Movie.objects.create(tmdb_id=42, content_type='movie', title='Duplicate')
        self.assertEqual(Movie.objects.filter(tmdb_id=42).count(), 2)

    def test_typed_reads_choose_the_right_identity_and_untyped_reads_fail_closed(self):
        with patch('tracker.views.resolve_or_create_movie', side_effect=AssertionError('network forbidden')):
            self.assertEqual(_load_content_item(42, 'movie'), self.movie)
            self.assertEqual(_load_content_item(42, 'series'), self.series)
            self.assertIsNone(_resolve_watch_item(42))
        self.assertIsNone(_unique_movie_for_tmdb(42))
        resolved = _movies_by_identity([self.movie, self.series])
        self.assertEqual(resolved[(42, 'movie')], self.movie)
        self.assertEqual(resolved[(42, 'series')], self.series)

    def test_legacy_mutations_cannot_choose_one_of_two_numeric_identifiers(self):
        response = self.client.post('/api/watched/42/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.movie.refresh_from_db()
        self.series.refresh_from_db()
        self.assertEqual((self.movie.title, self.series.title), ('A film', 'A series'))

    @patch('tracker.views.request_availability_refresh')
    def test_watchlist_selection_deduplicates_the_full_identity(self, refresh):
        from tracker.models import WatchlistItem
        response = self.client.post('/api/watchlist/resolve/', {'selections': [
            {'tmdb_id': 42, 'content_type': 'movie'},
            {'tmdb_id': 42, 'content_type': 'series'},
            {'tmdb_id': 42, 'content_type': 'movie'},
        ]}, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(WatchlistItem.objects.values_list('movie_id', flat=True)),
                         {self.movie.pk, self.series.pk})
        self.assertEqual(refresh.call_count, 2)
