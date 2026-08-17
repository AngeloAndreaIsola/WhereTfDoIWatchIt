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