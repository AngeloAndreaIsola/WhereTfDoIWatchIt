from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase

from tracker.models import Movie, MovieWatchEvent, UserMovieData, WatchlistItem
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
        self.assertEqual(response.status_code, 409)
        self.movie.refresh_from_db()
        self.series.refresh_from_db()
        self.assertEqual((self.movie.title, self.series.title), ('A film', 'A series'))

    @patch('tracker.views.request_availability_refresh')
    def test_watched_mutations_select_the_typed_row_and_preserve_personal_data(self, refresh):
        for movie in (self.movie, self.series):
            WatchlistItem.objects.create(movie=movie, user_id=1)
        UserMovieData.objects.create(movie=self.movie, user_id=1, is_watched=True, rating=4.5)
        response = self.client.post(
            '/api/watched/42/?content_type=series',
            {'watched_date': '2026-01-01', 'rating': 3.5, 'review': 'Series history'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(refresh.call_args.args[0], self.series)
        self.assertEqual(self.client.post('/api/unwatched/42/').status_code, 409)
        self.assertEqual(self.client.post('/api/unwatched/42/?content_type=invalid').status_code, 400)
        self.assertEqual(self.client.post('/api/unwatched/42/?content_type=series').status_code, 200)
        self.assertEqual(self.client.delete('/api/watchlist/remove/42/?content_type=series').status_code, 200)
        self.assertTrue(UserMovieData.objects.get(movie=self.movie, user_id=1).is_watched)
        self.assertEqual(UserMovieData.objects.get(movie=self.movie, user_id=1).rating, 4.5)
        series_data = UserMovieData.objects.get(movie=self.series, user_id=1)
        self.assertFalse(series_data.is_watched)
        self.assertEqual(series_data.rating, 3.5)
        event = MovieWatchEvent.objects.get(movie=self.series, user_id=1)
        self.assertEqual((event.rating, event.review), (3.5, 'Series history'))
        self.assertFalse(MovieWatchEvent.objects.filter(movie=self.movie).exists())
        self.assertEqual(list(WatchlistItem.objects.values_list('movie_id', flat=True)), [self.movie.pk])

    @patch('tracker.views.request_availability_refresh')
    def test_watchlist_selection_deduplicates_the_full_identity(self, refresh):
        response = self.client.post('/api/watchlist/resolve/', {'selections': [
            {'tmdb_id': 42, 'content_type': 'movie'},
            {'tmdb_id': 42, 'content_type': 'series'},
            {'tmdb_id': 42, 'content_type': 'movie'},
        ]}, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(WatchlistItem.objects.values_list('movie_id', flat=True)),
                         {self.movie.pk, self.series.pk})
        self.assertEqual(refresh.call_count, 2)
