"""Regression tests for #106 — one logical watchlist membership per (user, movie).

Covers both duplicate directions (manual add ↔ ambiguous-import resolution),
repeated calls, cross-source import reuse, movie/series identity separation,
and concurrent inserts on real PostgreSQL (TransactionTestCase).
"""

import threading
from unittest.mock import MagicMock, patch

from django.db import connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from tracker.models import Movie, WatchlistItem
from tracker.services.letterboxd import LetterboxdRow, import_rows
from tracker.services.membership import get_or_create_watchlist_membership
from tracker.services.tmdb import TMDbItem


def _mock_tmdb_details(mock_cls, tmdb_id, title, year, content_type="movie"):
    mock_cls.return_value.details.return_value = TMDbItem(
        tmdb_id=tmdb_id, title=title, year=year, content_type=content_type
    )


class MembershipServiceTests(TestCase):
    def test_creates_membership_when_absent(self):
        movie = Movie.objects.create(tmdb_id=680, title="Pulp Fiction", year=1994)
        item, created = get_or_create_watchlist_membership(movie, source="manual")
        self.assertTrue(created)
        self.assertEqual(item.source, "manual")
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_reuses_existing_row_regardless_of_source_and_preserves_provenance(self):
        movie = Movie.objects.create(tmdb_id=680, title="Pulp Fiction", year=1994)
        original = WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
        for source in ("manual", "letterboxd_resolved", "myanimelist"):
            item, created = get_or_create_watchlist_membership(movie, source=source)
            self.assertFalse(created)
            self.assertEqual(item.pk, original.pk)
            self.assertEqual(item.source, "letterboxd")
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_movie_and_series_with_same_tmdb_id_are_distinct_memberships(self):
        movie = Movie.objects.create(tmdb_id=100, title="Same ID Movie", content_type="movie")
        series = Movie.objects.create(tmdb_id=100, title="Same ID Series", content_type="series")
        m_item, m_created = get_or_create_watchlist_membership(movie)
        s_item, s_created = get_or_create_watchlist_membership(series)
        self.assertTrue(m_created)
        self.assertTrue(s_created)
        self.assertNotEqual(m_item.pk, s_item.pk)
        self.assertEqual(WatchlistItem.objects.count(), 2)


class AddResolveIdempotencyTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _add(self, tmdb_id=680, title="Pulp Fiction", year=1994, content_type="movie"):
        with patch("tracker.services.enrichment.TMDbClient") as mock_cls:
            _mock_tmdb_details(mock_cls, tmdb_id, title, year, content_type)
            return self.client.post(
                "/api/watchlist/add/",
                data={"tmdb_id": tmdb_id, "content_type": content_type},
                content_type="application/json",
            )

    def _resolve(self, tmdb_id=680, content_type="movie"):
        return self.client.post(
            "/api/watchlist/resolve/",
            data={"selections": [{"tmdb_id": tmdb_id, "content_type": content_type}]},
            content_type="application/json",
        )

    def test_manual_then_resolve_keeps_single_membership(self):
        resp = self._add()
        self.assertEqual(resp.status_code, 201, resp.content)
        resp = self._resolve()
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["resolved_count"], 1)
        self.assertEqual(resp.json()["created_count"], 0)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertEqual(WatchlistItem.objects.get().source, "manual")

    def test_resolve_then_manual_keeps_single_membership(self):
        resp = self._resolve()
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["created_count"], 1)
        resp = self._add()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["message"], "Movie already in watchlist")
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertEqual(WatchlistItem.objects.get().source, "letterboxd_resolved")

    def test_repeated_manual_add_is_idempotent(self):
        self.assertEqual(self._add().status_code, 201)
        second = self._add()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["message"], "Movie already in watchlist")
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_repeated_resolve_is_idempotent(self):
        first = self._resolve()
        self.assertEqual(first.json()["created_count"], 1)
        second = self._resolve()
        self.assertEqual(second.json()["created_count"], 0)
        self.assertEqual(second.json()["resolved_count"], 1)
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_manual_add_reuses_cross_source_import_row(self):
        movie = Movie.objects.create(tmdb_id=680, title="Pulp Fiction", year=1994)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
        resp = self._add()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertEqual(WatchlistItem.objects.get().source, "letterboxd")

    def test_importer_reuses_manual_membership(self):
        movie = Movie.objects.create(tmdb_id=680, title="Pulp Fiction", year=1994)
        manual = WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        pick = MagicMock()
        pick.tmdb_id = 680
        pick.title = "Pulp Fiction"
        pick.year = 1994
        pick.poster_path = ""
        pick.overview = ""
        pick.content_type = "movie"
        client = MagicMock()
        client.search.return_value = [pick]
        result = import_rows(
            [LetterboxdRow(title="Pulp Fiction", year=1994)], client=client
        )
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(result.resolved[0].pk, manual.pk)
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_watchlist_list_shows_one_entry_after_add_and_resolve(self):
        self._add()
        self._resolve()
        resp = self.client.get("/api/watchlist/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_series_and_movie_same_tmdb_id_both_listed_once(self):
        self._add(tmdb_id=100, title="Movie", content_type="movie")
        self._add(tmdb_id=100, title="Series", content_type="series")
        self._resolve(tmdb_id=100, content_type="movie")
        self._resolve(tmdb_id=100, content_type="series")
        self.assertEqual(WatchlistItem.objects.count(), 2)
        resp = self.client.get("/api/watchlist/")
        self.assertEqual(len(resp.json()), 2)


class ConcurrentMembershipTests(TransactionTestCase):
    """Real-commit concurrency on disposable PostgreSQL (no SQLite shim)."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=680, title="Pulp Fiction", year=1994)

    def test_concurrent_mixed_source_calls_create_single_row(self):
        barrier = threading.Barrier(8)
        results = []
        errors = []

        def worker(i):
            try:
                barrier.wait(timeout=10)
                item, created = get_or_create_watchlist_membership(
                    self.movie, source="manual" if i % 2 else "letterboxd_resolved"
                )
                results.append((item.pk, created))
            except Exception as exc:  # pragma: no cover - surfaced via assert
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertEqual(len({pk for pk, _ in results}), 1)
        self.assertEqual(sum(1 for _, created in results if created), 1)

    def test_concurrent_resolve_endpoint_creates_single_row(self):
        barrier = threading.Barrier(4)
        statuses = []
        errors = []

        def worker():
            client = APIClient()
            try:
                barrier.wait(timeout=10)
                resp = client.post(
                    "/api/watchlist/resolve/",
                    data={"selections": [{"tmdb_id": 680}]},
                    content_type="application/json",
                )
                statuses.append(resp.status_code)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [201] * 4)
        self.assertEqual(WatchlistItem.objects.count(), 1)
