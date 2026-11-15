"""Tests for TMDb client (HTTP mocked)."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from tracker.services.tmdb import TMDbClient


class TMDbClientTests(TestCase):
    def _client(self):
        return TMDbClient(api_key="test-key")

    @patch("tracker.services.tmdb.requests.get")
    def test_search_parses_results(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id": 1, "title": "Foo", "release_date": "2010-05-01"},
                {"id": 2, "title": "Foo 2", "release_date": ""},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = self._client().search("Foo")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].year, 2010)
        self.assertIsNone(results[1].year)

    @patch("tracker.services.tmdb.requests.get")
    def test_search_defaults_to_movie_endpoint_only(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [{"id": 1, "title": "Foo", "release_date": "2010-05-01"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = self._client().search("Foo")
        self.assertEqual(mock_get.call_count, 1)
        self.assertIn("/search/movie", mock_get.call_args[0][0])
        self.assertEqual(results[0].content_type, "movie")

    @patch("tracker.services.tmdb.requests.get")
    def test_search_series_hits_tv_endpoint_and_labels_series(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [{"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20"}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = self._client().search("Breaking Bad", content_type="series")
        self.assertEqual(mock_get.call_count, 1)
        self.assertIn("/search/tv", mock_get.call_args[0][0])
        self.assertEqual(results[0].content_type, "series")
        self.assertEqual(results[0].year, 2008)

    @patch("tracker.services.tmdb.requests.get")
    def test_search_all_queries_both_endpoints_and_tags_results(self, mock_get):
        def respond(url, *args, **kwargs):
            resp = MagicMock()
            if "/search/movie" in url:
                resp.json.return_value = {"results": [{"id": 10, "title": "The Matrix", "release_date": "1999-03-31"}]}
            else:
                resp.json.return_value = {"results": [{"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20"}]}
            resp.raise_for_status = MagicMock()
            return resp

        mock_get.side_effect = respond

        results = self._client().search("matrix", content_type="all")
        called_urls = [c[0][0] for c in mock_get.call_args_list]
        self.assertEqual(len(called_urls), 2)
        self.assertTrue(any("/search/movie" in u for u in called_urls))
        self.assertTrue(any("/search/tv" in u for u in called_urls))
        by_type = {r.content_type for r in results}
        self.assertEqual(by_type, {"movie", "series"})

    @patch("tracker.services.tmdb.requests.get")
    def test_details_returns_full_metadata(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "id": 99,
            "title": "Bar",
            "release_date": "2021-08-15",
            "overview": "hello",
            "poster_path": "/x.jpg",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        movie = self._client().details(99)
        self.assertEqual(movie.tmdb_id, 99)
        self.assertEqual(movie.year, 2021)
        self.assertEqual(movie.poster_path, "/x.jpg")

    @patch("tracker.services.tmdb.requests.get")
    def test_resolve_returns_first_match(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [{"id": 5, "title": "Z", "release_date": "2000-01-01"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = self._client().resolve("Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.tmdb_id, 5)

    @patch("tracker.services.tmdb.requests.get")
    def test_resolve_returns_none_on_empty(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        self.assertIsNone(self._client().resolve("Nothing"))