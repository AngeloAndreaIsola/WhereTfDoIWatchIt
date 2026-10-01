"""Phase B: event-driven availability refresh requests (enqueue + drain).

Covers both halves of the contract:

- **Enqueue**: watchlist adds, letterboxd/Google imports, and title-detail
  views create at most one pending ``RefreshRequest`` per (movie, source)
  scope — a local insert only, never an external fetch — with priority and
  the import rule ("entering or never fetched") applied.
- **Drain**: ``process_watchlist_refresh`` settles pending requests first,
  in priority order, sharing the run's budget, rate limiter, freshness
  policy and ``RefreshRun`` audit row with the background sweep; failures
  are isolated and retried at most ``MAX_ATTEMPTS`` times.

No test in this module makes live HTTP requests to TMDb or any availability
provider.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services.bulk_refresh import process_watchlist_refresh
from tracker.services.rate_limit import RateLimitExceeded, RateLimiter
from tracker.services.refresh_requests import (
    MAX_ATTEMPTS,
    request_availability_refresh,
    request_import_refresh,
)
from tracker.tests.availability_factories import local_offer

REFRESH_CONTENT = "availability.services.refresh.AvailabilityRefreshService.refresh_content"


def _backdate_offers(movie, days=2):
    """Make the canonical offers of *movie* read as stale."""
    from availability.models import Offer

    Offer.objects.filter(tmdb_id=movie.tmdb_id).update(
        last_seen=timezone.now() - dt.timedelta(days=days)
    )


class EnqueueServiceTests(TestCase):
    """Direct behavior of request_availability_refresh / request_import_refresh."""

    def test_never_fetched_title_is_bumped_to_new_priority(self):
        movie = Movie.objects.create(tmdb_id=101, title="Never", year=2020)
        request = request_availability_refresh(movie, priority=RefreshPriority.RECENTLY_VIEWED)
        self.assertIsNotNone(request)
        self.assertEqual(request.priority, RefreshPriority.NEW)
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)

    def test_fresh_title_is_not_enqueued(self):
        movie = Movie.objects.create(tmdb_id=102, title="Fresh", year=2020)
        local_offer(movie)
        self.assertIsNone(request_availability_refresh(movie))
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_stale_title_keeps_requested_priority(self):
        movie = Movie.objects.create(tmdb_id=103, title="Stale", year=2020)
        local_offer(movie)
        _backdate_offers(movie)
        request = request_availability_refresh(movie, priority=RefreshPriority.RECENTLY_VIEWED)
        self.assertEqual(request.priority, RefreshPriority.RECENTLY_VIEWED)

    def test_enqueue_is_idempotent_and_never_downgrades(self):
        movie = Movie.objects.create(tmdb_id=104, title="Repeat", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        first = request_availability_refresh(movie, priority=RefreshPriority.STALE)
        upgraded = request_availability_refresh(movie, priority=RefreshPriority.RECENTLY_VIEWED)
        downgraded = request_availability_refresh(
            movie, priority=RefreshPriority.BACKGROUND
        )

        self.assertEqual(RefreshRequest.objects.count(), 1)
        self.assertEqual(first.pk, upgraded.pk)
        self.assertEqual(upgraded.pk, downgraded.pk)
        self.assertEqual(
            RefreshRequest.objects.get().priority, RefreshPriority.RECENTLY_VIEWED
        )

    def test_if_stale_false_enqueues_even_a_fresh_title(self):
        movie = Movie.objects.create(tmdb_id=105, title="Forced", year=2020)
        local_offer(movie)
        request = request_availability_refresh(
            movie, priority=RefreshPriority.RECENTLY_VIEWED, if_stale=False
        )
        self.assertIsNotNone(request)
        self.assertEqual(RefreshRequest.objects.count(), 1)

    def test_enqueue_failure_never_raises(self):
        movie = Movie.objects.create(tmdb_id=106, title="Boom", year=2020)
        with patch(
            "tracker.models.RefreshRequest.objects.get_or_create",
            side_effect=RuntimeError("db down"),
        ):
            result = request_availability_refresh(movie)
        self.assertIsNone(result)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_import_rule_skips_seen_stale_titles(self):
        movie = Movie.objects.create(tmdb_id=107, title="Seen", year=2020)
        local_offer(movie)
        _backdate_offers(movie)
        self.assertIsNone(
            request_import_refresh(movie, priority=RefreshPriority.WATCHLIST)
        )
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_import_rule_requests_when_never_fetched(self):
        movie = Movie.objects.create(tmdb_id=108, title="Unseen", year=2020)
        request = request_import_refresh(
            movie, priority=RefreshPriority.WATCHLIST, entering=False
        )
        self.assertIsNotNone(request)
        self.assertEqual(request.priority, RefreshPriority.NEW)

    def test_import_rule_entering_stale_title_keeps_priority(self):
        movie = Movie.objects.create(tmdb_id=109, title="Entering", year=2020)
        local_offer(movie)
        _backdate_offers(movie)
        request = request_import_refresh(
            movie, priority=RefreshPriority.WATCHLIST, entering=True
        )
        self.assertIsNotNone(request)
        self.assertEqual(request.priority, RefreshPriority.WATCHLIST)

    def test_import_rule_entering_fresh_title_is_skipped(self):
        movie = Movie.objects.create(tmdb_id=110, title="EnteringFresh", year=2020)
        local_offer(movie)
        self.assertIsNone(
            request_import_refresh(
                movie, priority=RefreshPriority.WATCHLIST, entering=True
            )
        )

    def test_explicit_action_keeps_top_priority_on_never_fetched_titles(self):
        movie = Movie.objects.create(tmdb_id=111, title="Confirmed", year=2020)
        request = request_availability_refresh(movie, priority=RefreshPriority.EXPLICIT)
        self.assertIsNotNone(request)
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_explicit_upgrades_a_recently_viewed_request_and_never_downgrades(self):
        movie = Movie.objects.create(tmdb_id=112, title="Upgrade", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        first = request_availability_refresh(
            movie, priority=RefreshPriority.RECENTLY_VIEWED
        )
        upgraded = request_availability_refresh(movie, priority=RefreshPriority.EXPLICIT)
        downgraded = request_availability_refresh(
            movie, priority=RefreshPriority.WATCHLIST
        )

        self.assertEqual(RefreshRequest.objects.count(), 1)
        self.assertEqual(first.pk, upgraded.pk)
        self.assertEqual(upgraded.pk, downgraded.pk)
        self.assertEqual(
            RefreshRequest.objects.get().priority, RefreshPriority.EXPLICIT
        )


class PriorityHierarchyTests(TestCase):
    """The single priority ladder encodes the user-intent hierarchy."""

    def test_ladder_is_strictly_ordered_by_intent(self):
        ladder = [
            RefreshPriority.EXPLICIT,
            RefreshPriority.NEW,
            RefreshPriority.RECENTLY_VIEWED,
            RefreshPriority.VISIBLE,
            RefreshPriority.WATCHLIST,
            RefreshPriority.WATCHED,
            RefreshPriority.STALE,
            RefreshPriority.BACKGROUND,
        ]
        values = [int(level) for level in ladder]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), len(values))

    def test_no_caller_enqueues_visible_yet(self):
        """VISIBLE is a reserved slot: nothing enqueues it until the
        frontend has a reliable visibility signal to send."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in root.rglob("*.py"):
            if path.name == "models.py":
                continue
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            if "RefreshPriority.VISIBLE" in path.read_text():
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])


class WatchlistAddEndpointTests(TestCase):
    """POST /api/watchlist/add/ queues a refresh without any provider call."""

    @patch("tracker.services.enrichment.TMDbClient")
    def test_add_requests_refresh_without_external_fetch(self, mock_cls):
        from tracker.services.tmdb import TMDbItem

        mock_cls.return_value.details.return_value = TMDbItem(
            tmdb_id=680, title="Pulp Fiction", year=1994, content_type="movie"
        )

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError(
                "watchlist add triggered availability refresh"
            )
            resp = self.client.post(
                "/api/watchlist/add/",
                data={"tmdb_id": 680},
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 201, resp.content)
        refresh_content.assert_not_called()
        request = RefreshRequest.objects.get()
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.NEW)  # never fetched

    @patch("tracker.services.enrichment.TMDbClient")
    def test_add_of_fresh_title_creates_no_request(self, mock_cls):
        from tracker.services.tmdb import TMDbItem

        mock_cls.return_value.details.return_value = TMDbItem(
            tmdb_id=681, title="Fresh One", year=2021, content_type="movie"
        )
        movie = Movie.objects.create(tmdb_id=681, title="Fresh One", year=2021)
        local_offer(movie)

        resp = self.client.post(
            "/api/watchlist/add/",
            data={"tmdb_id": 681},
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 0)


class WatchlistResolveEndpointTests(TestCase):
    """POST /api/watchlist/resolve/ (Letterboxd manual matching)."""

    def test_resolve_requests_refresh_for_new_titles(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={"selections": [{"original_title": "Inception", "year": 2010, "tmdb_id": 27205}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

        request = RefreshRequest.objects.get()
        self.assertEqual(request.movie.tmdb_id, 27205)
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.NEW)  # never fetched

    def test_resolve_skips_titles_with_fresh_offers(self):
        movie = Movie.objects.create(tmdb_id=27205, title="Inception", year=2010)
        local_offer(movie)

        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={"selections": [{"original_title": "Inception", "year": 2010, "tmdb_id": 27205}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_repeat_resolves_collapse_into_one_pending_request(self):
        payload = {"selections": [{"original_title": "Tenet", "year": 2020, "tmdb_id": 724089}]}
        first = self.client.post(
            "/api/watchlist/resolve/", data=payload, content_type="application/json"
        )
        second = self.client.post(
            "/api/watchlist/resolve/", data=payload, content_type="application/json"
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(RefreshRequest.objects.filter(status="pending").count(), 1)


class MarkWatchedEndpointTests(TestCase):
    """POST /api/watched/<tmdb_id>/ queues a freshness-guarded WATCHED refresh."""

    def _mark_watched(self, tmdb_id):
        return self.client.post(
            f"/api/watched/{tmdb_id}/",
            data={},
            content_type="application/json",
        )

    def test_mark_watched_stale_title_queues_watched_refresh_without_fetching(self):
        movie = Movie.objects.create(tmdb_id=401, title="SeenStale", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("mark-watched fetched")
            resp = self._mark_watched(movie.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        refresh_content.assert_not_called()
        request = RefreshRequest.objects.get()
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.WATCHED)

    def test_mark_watched_fresh_title_queues_nothing(self):
        movie = Movie.objects.create(tmdb_id=402, title="SeenFresh", year=2020)
        local_offer(movie)

        resp = self._mark_watched(movie.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_mark_watched_never_fetched_title_bumps_to_new(self):
        movie = Movie.objects.create(tmdb_id=403, title="SeenUnseen", year=2020)

        resp = self._mark_watched(movie.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.priority, RefreshPriority.NEW)


class RefreshOnViewTests(TestCase):
    """Detail views queue an async refresh but never fetch inline."""

    @patch("tracker.views.enrich_movie_from_tmdb")
    def test_movie_detail_stale_title_queues_recently_viewed_refresh(self, enrich):
        movie = Movie.objects.create(tmdb_id=301, title="Stale", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("detail read fetched")
            resp = self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        refresh_content.assert_not_called()
        request = RefreshRequest.objects.get()
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.RECENTLY_VIEWED)

    @patch("tracker.views.enrich_movie_from_tmdb")
    def test_movie_detail_never_fetched_title_queues_new_priority(self, enrich):
        movie = Movie.objects.create(tmdb_id=302, title="Unseen", year=2020)

        resp = self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.priority, RefreshPriority.NEW)

    @patch("tracker.views.enrich_movie_from_tmdb")
    def test_movie_detail_fresh_title_queues_nothing(self, enrich):
        movie = Movie.objects.create(tmdb_id=303, title="Fresh", year=2020)
        local_offer(movie)

        resp = self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_prime_detail_stale_title_queues_without_fetching(self, enrich):
        movie = Movie.objects.create(tmdb_id=304, title="PrimeStale", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("prime read fetched")
            resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        refresh_content.assert_not_called()
        request = RefreshRequest.objects.get()
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.RECENTLY_VIEWED)


class LetterboxdImportEnqueueTests(TestCase):
    """import_rows enqueues only per the import rule, idempotently."""

    def _client_resolving(self, title, tmdb_movie):
        client = MagicMock()
        client.search.side_effect = lambda query, year=None: (
            [tmdb_movie] if query == title else []
        )
        return client

    def test_new_watchlist_row_requests_refresh(self):
        from tracker.services.letterboxd import LetterboxdRow, import_rows
        from tracker.services.tmdb import TMDbMovie

        client = self._client_resolving(
            "Inception", TMDbMovie(tmdb_id=27205, title="Inception", year=2010)
        )
        rows = [LetterboxdRow(title="Inception", year=2010)]

        with patch(REFRESH_CONTENT) as refresh_content:
            import_rows(rows, client=client)

        refresh_content.assert_not_called()  # import only enqueues
        request = RefreshRequest.objects.get()
        self.assertEqual(request.movie.tmdb_id, 27205)
        self.assertEqual(request.priority, RefreshPriority.NEW)  # never fetched

    def test_reimport_does_not_duplicate_pending_request(self):
        from tracker.services.letterboxd import LetterboxdRow, import_rows
        from tracker.services.tmdb import TMDbMovie

        client = self._client_resolving(
            "Inception", TMDbMovie(tmdb_id=27205, title="Inception", year=2010)
        )
        rows = [LetterboxdRow(title="Inception", year=2010)]

        import_rows(rows, client=client)
        import_rows(rows, client=client)

        self.assertEqual(RefreshRequest.objects.count(), 1)

    def test_reimport_of_fresh_title_requests_nothing(self):
        from tracker.services.letterboxd import LetterboxdRow, import_rows
        from tracker.services.tmdb import TMDbMovie

        client = self._client_resolving(
            "Inception", TMDbMovie(tmdb_id=27205, title="Inception", year=2010)
        )
        rows = [LetterboxdRow(title="Inception", year=2010)]

        import_rows(rows, client=client)
        movie = Movie.objects.get(tmdb_id=27205)
        local_offer(movie)  # canonical path has now seen it

        import_rows(rows, client=client)
        self.assertEqual(RefreshRequest.objects.count(), 1)  # only the first one

    def test_seen_stale_title_left_to_the_scheduled_sweep(self):
        from tracker.services.letterboxd import LetterboxdRow, import_rows

        movie = Movie.objects.create(tmdb_id=603, title="The Matrix", year=1999)
        local_offer(movie)
        _backdate_offers(movie)  # stale but already fetched -> sweep's job
        WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")

        rows = [LetterboxdRow(title="The Matrix", year=1999)]
        import_rows(rows, client=MagicMock())  # cache hit, client never used

        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_new_watched_row_requests_refresh(self):
        from tracker.services.letterboxd import LetterboxdRow, import_rows
        from tracker.services.tmdb import TMDbMovie

        client = self._client_resolving(
            "Heat", TMDbMovie(tmdb_id=949, title="Heat", year=1995)
        )
        rows = [LetterboxdRow(title="Heat", year=1995, file_type="watched")]

        import_rows(rows, client=client)

        request = RefreshRequest.objects.get()
        self.assertEqual(request.movie.tmdb_id, 949)
        self.assertEqual(request.priority, RefreshPriority.NEW)  # never fetched

    def test_watched_row_for_catalogued_title_requests_nothing(self):
        from tracker.services.letterboxd import LetterboxdRow, import_rows

        movie = Movie.objects.create(tmdb_id=949, title="Heat", year=1995)
        local_offer(movie)
        _backdate_offers(movie)  # seen (stale) -> sweep, not the import

        rows = [LetterboxdRow(title="Heat", year=1995, file_type="watched")]
        import_rows(rows, client=MagicMock())

        self.assertEqual(RefreshRequest.objects.count(), 0)


class GoogleInterestsImportEnqueueTests(TestCase):
    """import_interests follows the same rule and dedup contract."""

    def test_new_row_requests_refresh_and_reimport_dedupes(self):
        from tracker.services.google_interests import (
            GoogleInterestRow,
            import_interests,
        )
        from tracker.services.tmdb import TMDbMovie

        class FakeClient:
            def search(self, query, year=None, content_type=None):
                if query == "Heat":
                    return [TMDbMovie(tmdb_id=949, title="Heat", year=1995)]
                return []

        rows = [GoogleInterestRow(title="Heat", source="google_interests")]
        import_interests(rows, client=FakeClient())
        import_interests(rows, client=FakeClient())

        request = RefreshRequest.objects.get()  # exactly one, after two imports
        self.assertEqual(request.movie.tmdb_id, 949)
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.NEW)


class DrainTests(TestCase):
    """process_watchlist_refresh drains RefreshRequest rows first, budgeted."""

    def setUp(self):
        self.refresh_service = MagicMock()
        self.limiter = MagicMock(spec=RateLimiter)
        self.limiter.remaining.return_value = 100

    def _run(self, **kwargs):
        kwargs.setdefault("refresh_service", self.refresh_service)
        kwargs.setdefault("limiter", self.limiter)
        kwargs.setdefault("budget", 10)
        kwargs.setdefault("user_id", 1)
        return process_watchlist_refresh(**kwargs)

    @staticmethod
    def _request(movie, priority, source=""):
        return RefreshRequest.objects.create(
            movie=movie, priority=priority, source=source
        )

    def test_drains_in_priority_order(self):
        intent = Movie.objects.create(tmdb_id=901, title="Viewed", year=2020)
        new = Movie.objects.create(tmdb_id=902, title="BrandNew", year=2021)
        watch = Movie.objects.create(tmdb_id=903, title="Watchlisted", year=2022)
        self._request(intent, RefreshPriority.RECENTLY_VIEWED)
        self._request(new, RefreshPriority.NEW)
        self._request(watch, RefreshPriority.WATCHLIST)

        summary = self._run()

        order = [
            call.args[0].tmdb_id
            for call in self.refresh_service.refresh_content.call_args_list
        ]
        self.assertEqual(order, [902, 903, 901])  # NEW, WATCHLIST, RECENTLY_VIEWED
        self.assertEqual(summary["refreshed"], 3)
        self.assertEqual(summary["requests_drained"], 3)
        self.assertEqual(summary["requests_pending"], 0)
        for request in RefreshRequest.objects.all():
            self.assertEqual(request.status, RefreshRequestStatus.DONE)
            self.assertIsNotNone(request.completed_at)

    def test_budget_exhaustion_leaves_requests_pending(self):
        first = Movie.objects.create(tmdb_id=911, title="First", year=2020)
        second = Movie.objects.create(tmdb_id=912, title="Second", year=2021)
        self._request(first, RefreshPriority.NEW)
        self._request(second, RefreshPriority.WATCHLIST)

        summary = self._run(budget=1)

        refreshed = [
            call.args[0].tmdb_id
            for call in self.refresh_service.refresh_content.call_args_list
        ]
        self.assertEqual(refreshed, [911])
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["remaining_budget"], 0)
        self.assertEqual(summary["skipped_budget_exhausted"], 1)
        self.assertEqual(summary["requests_pending"], 1)
        self.assertEqual(
            RefreshRequest.objects.get(movie=second).status,
            RefreshRequestStatus.PENDING,
        )

    def _stale_backlog_and_fresh_event(self):
        """An old BACKGROUND request plus a brand-new RECENTLY_VIEWED one."""
        backlog = Movie.objects.create(tmdb_id=971, title="Backlog", year=2019)
        event = Movie.objects.create(tmdb_id=972, title="Event", year=2020)
        local_offer(backlog)
        _backdate_offers(backlog)
        local_offer(event)
        _backdate_offers(event)
        old = self._request(backlog, RefreshPriority.BACKGROUND)
        # Make the backlog request the oldest waiting row by a wide margin.
        RefreshRequest.objects.filter(pk=old.pk).update(
            requested_at=timezone.now() - dt.timedelta(days=30)
        )
        self._request(event, RefreshPriority.RECENTLY_VIEWED)
        return backlog, event

    @staticmethod
    def _refreshed_ids(refresh_service):
        return [
            call.args[0].tmdb_id
            for call in refresh_service.refresh_content.call_args_list
        ]

    def test_fairness_slot_drains_the_oldest_backlog_request_last(self):
        self._stale_backlog_and_fresh_event()

        summary = self._run(budget=2)

        # Strict priority first (the event), then the reserved slot (the
        # longest-waiting request) with the held-back budget unit.
        self.assertEqual(self._refreshed_ids(self.refresh_service), [972, 971])
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["remaining_budget"], 0)
        self.assertEqual(summary["requests_pending"], 0)

    def test_fairness_slot_never_overrides_priority_when_budget_is_one(self):
        self._stale_backlog_and_fresh_event()

        summary = self._run(budget=1)

        self.assertEqual(self._refreshed_ids(self.refresh_service), [972])
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["skipped_budget_exhausted"], 1)
        self.assertEqual(
            RefreshRequest.objects.get(movie__tmdb_id=971).status,
            RefreshRequestStatus.PENDING,
        )

    def test_fairness_slot_returns_the_unit_when_reserved_request_is_fresh(self):
        backlog = Movie.objects.create(tmdb_id=973, title="FreshBacklog", year=2019)
        event = Movie.objects.create(tmdb_id=974, title="StaleEvent", year=2020)
        local_offer(backlog)  # fresh: the reserved pass settles it for free
        local_offer(event)
        _backdate_offers(event)
        old = self._request(backlog, RefreshPriority.BACKGROUND)
        RefreshRequest.objects.filter(pk=old.pk).update(
            requested_at=timezone.now() - dt.timedelta(days=30)
        )
        self._request(event, RefreshPriority.RECENTLY_VIEWED)

        summary = self._run(budget=2)

        self.assertEqual(self._refreshed_ids(self.refresh_service), [974])
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["skipped_cached"], 1)
        # The held-back unit was not spent, so it goes back to the run.
        self.assertEqual(summary["remaining_budget"], 1)
        self.assertEqual(summary["requests_pending"], 0)

    def test_rate_limit_is_never_bypassed(self):
        self.limiter.acquire.side_effect = RateLimitExceeded("daily quota")
        first = Movie.objects.create(tmdb_id=921, title="A", year=2020)
        second = Movie.objects.create(tmdb_id=922, title="B", year=2021)
        self._request(first, RefreshPriority.NEW)
        self._request(second, RefreshPriority.STALE)

        summary = self._run()

        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["skipped_rate_limited"], 2)
        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["requests_pending"], 2)
        self.refresh_service.refresh_content.assert_not_called()
        self.limiter.acquire.assert_called_once()  # stop after first refusal

    def test_upfront_exhausted_quota_blocks_the_drain(self):
        self.limiter.remaining.return_value = 0
        movie = Movie.objects.create(tmdb_id=923, title="C", year=2020)
        self._request(movie, RefreshPriority.NEW)

        summary = self._run()

        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["requests_pending"], 1)
        self.refresh_service.refresh_content.assert_not_called()

    def test_fresh_pending_request_settles_without_spending_budget(self):
        movie = Movie.objects.create(tmdb_id=931, title="Fresh", year=2020)
        local_offer(movie)
        self._request(movie, RefreshPriority.STALE)

        summary = self._run(budget=5)

        request = RefreshRequest.objects.get()
        self.assertEqual(request.status, RefreshRequestStatus.DONE)
        self.assertEqual(summary["skipped_cached"], 1)
        self.assertEqual(summary["remaining_budget"], 5)
        self.assertEqual(summary["requests_drained"], 1)
        self.refresh_service.refresh_content.assert_not_called()

    def test_drain_failure_is_isolated_and_retried_until_failed(self):
        movie = Movie.objects.create(tmdb_id=941, title="Failing", year=2020)
        self._request(movie, RefreshPriority.NEW)
        self.refresh_service.refresh_content.side_effect = RuntimeError(
            "provider exploded"
        )

        first = self._run()  # must not raise
        self.assertEqual(first["errors"], 1)
        self.assertEqual(first["requests_drained"], 1)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.attempts, 1)
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertIn("RuntimeError", request.last_error)

        self._run()
        self._run()  # third attempt reaches the cap
        request.refresh_from_db()
        self.assertEqual(request.attempts, MAX_ATTEMPTS)
        self.assertEqual(request.status, RefreshRequestStatus.FAILED)

        calls_so_far = self.refresh_service.refresh_content.call_count
        fourth = self._run()
        # The request is dead: the drain never touches it again...
        self.assertEqual(fourth["requests_drained"], 0)
        self.assertEqual(
            RefreshRequest.objects.get().status, RefreshRequestStatus.FAILED
        )
        # ...while background sweep maintenance keeps its own schedule (its
        # failures are logged, budget-guarded, and freshness-driven).
        self.assertEqual(fourth["errors"], 1)
        self.assertEqual(
            self.refresh_service.refresh_content.call_count, calls_so_far + 1
        )

    def test_drained_titles_are_not_refreshed_again_by_the_sweep(self):
        movie = Movie.objects.create(tmdb_id=951, title="WatchedOnce", year=2020)
        WatchlistItem.objects.create(user_id=1, movie=movie)
        self._request(movie, RefreshPriority.WATCHLIST)

        summary = self._run()

        self.assertEqual(self.refresh_service.refresh_content.call_count, 1)
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(summary["requests_drained"], 1)
        self.assertEqual(summary["requests_pending"], 0)

    def test_source_scoped_request_uses_its_own_source_service(self):
        scoped_movie = Movie.objects.create(tmdb_id=961, title="Tubi", year=2020)
        default_movie = Movie.objects.create(tmdb_id=962, title="Default", year=2021)
        self._request(scoped_movie, RefreshPriority.NEW, source="tubi")
        self._request(default_movie, RefreshPriority.WATCHLIST, source="")

        with patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            scoped_service = MagicMock()
            service_cls.return_value = scoped_service
            summary = self._run()

        service_cls.assert_called_once()
        self.assertEqual(
            service_cls.call_args.kwargs["source"].slug, "tubi"
        )  # provider-vs-source scope intact
        scoped_service.refresh_content.assert_called_once_with(scoped_movie)
        self.refresh_service.refresh_content.assert_called_once_with(default_movie)
        self.assertEqual(summary["requests_drained"], 2)
