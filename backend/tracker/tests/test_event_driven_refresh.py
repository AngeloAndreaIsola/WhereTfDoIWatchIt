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
    BlockedReason,
    CatalogueRefreshRequest,
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services.bulk_refresh import (
    process_watchlist_refresh,
    title_level_source_slugs,
)
from tracker.services.rate_limit import RateLimitExceeded, RateLimiter
from tracker.services.streaming_api import UpstreamTitleNotFound
from tracker.services.refresh_requests import (
    MAX_ATTEMPTS,
    process_refresh_queue,
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


def _fresh_every_title_level_scope(movie):
    """Give *movie* a fresh observation in **every** title-level source scope.

    Per-source freshness means a title can be fresh in one scope and stale in
    another, so "fresh title" now has to mean fresh everywhere — otherwise a
    source that has never observed the title is (correctly) still stale for its
    own scope and gets queued.
    """
    from availability.domain.enums import SourceType
    from availability.models import Country, Offer, Source
    from tracker.models import Provider

    provider, _ = Provider.objects.get_or_create(
        slug="netflix", defaults={"name": "Netflix"}
    )
    country, _ = Country.objects.get_or_create(code="US", defaults={"name": "US"})
    for slug in title_level_source_slugs():
        source, _ = Source.objects.get_or_create(
            slug=slug, defaults={"name": slug, "type": SourceType.API}
        )
        Offer.objects.create(
            provider=provider,
            country=country,
            source=source,
            content_type=movie.content_type,
            tmdb_id=movie.tmdb_id,
            offer_type="subscription",
            provider_content_id=f"{slug}-fresh",
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
    """Detail views fan out an async refresh but never fetch inline.

    Opening a title asks **every** eligible title-level source (capability-
    driven), not just the default scope: one check cannot answer a market only
    an aggregator like JustWatch covers. Every request is still the regular
    per-(title, source) row — idempotent, priority-upgraded, never an inline
    fetch.
    """

    @patch("tracker.views.enrich_movie_from_tmdb")
    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=False,
    )
    def test_movie_detail_stale_title_queues_recently_viewed_refresh(
        self, _gate, enrich
    ):
        """With the ladder off, every title-level source — including the
        default scope — is queued for a stale title. This is the pre-existing
        default-scope behaviour, preserved."""
        movie = Movie.objects.create(tmdb_id=301, title="Stale", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("detail read fetched")
            resp = self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        refresh_content.assert_not_called()
        requests = RefreshRequest.objects.filter(movie=movie)
        self.assertEqual(
            set(requests.values_list("source", flat=True)),
            set(title_level_source_slugs()),
        )
        self.assertTrue(
            all(r.status == RefreshRequestStatus.PENDING for r in requests)
        )
        self.assertTrue(
            all(r.priority == RefreshPriority.RECENTLY_VIEWED for r in requests)
        )

    @patch("tracker.views.enrich_movie_from_tmdb")
    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=True,
    )
    def test_movie_detail_resolved_title_skips_the_metered_scope_on_the_ladder(
        self, _gate, enrich
    ):
        """With the ladder on, an already-resolved title does not spend the
        metered scope — but the free title-level sources still run."""
        movie = Movie.objects.create(tmdb_id=306, title="Resolved", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        resp = self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        queued = set(
            RefreshRequest.objects.filter(movie=movie).values_list(
                "source", flat=True
            )
        )
        self.assertNotIn("streaming_availability", queued)
        self.assertIn("justwatch", queued)
        self.assertIn("watchmode", queued)

    @patch("tracker.views.enrich_movie_from_tmdb")
    def test_movie_detail_never_fetched_title_queues_new_priority(self, enrich):
        movie = Movie.objects.create(tmdb_id=302, title="Unseen", year=2020)

        resp = self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        requests = RefreshRequest.objects.filter(movie=movie)
        self.assertEqual(
            set(requests.values_list("source", flat=True)),
            set(title_level_source_slugs()),
        )
        self.assertTrue(
            all(r.priority == RefreshPriority.NEW for r in requests)
        )

    @patch("tracker.views.enrich_movie_from_tmdb")
    def test_movie_detail_fresh_title_queues_nothing(self, enrich):
        movie = Movie.objects.create(tmdb_id=303, title="Fresh", year=2020)
        _fresh_every_title_level_scope(movie)

        resp = self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    @patch("tracker.views.enrich_movie_from_tmdb")
    def test_repeated_opens_do_not_multiply_requests(self, enrich):
        """The fan-out is idempotent: opening twice must not duplicate rows."""
        movie = Movie.objects.create(tmdb_id=305, title="Reopened", year=2020)

        self.client.get(f"/api/movie/{movie.pk}/")
        first = set(
            RefreshRequest.objects.filter(movie=movie).values_list(
                "source", flat=True
            )
        )
        self.client.get(f"/api/movie/{movie.pk}/")

        self.assertEqual(
            set(
                RefreshRequest.objects.filter(movie=movie).values_list(
                    "source", flat=True
                )
            ),
            first,
        )
        self.assertEqual(
            RefreshRequest.objects.filter(movie=movie).count(), len(first)
        )

    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_prime_detail_stale_title_queues_without_fetching(self, enrich):
        """Superseded by :class:`PrimeDetailFanOutTests`, kept as a light
        guard that the real offer pipeline path still enqueues and never
        fetches inline."""
        movie = Movie.objects.create(tmdb_id=304, title="PrimeStale", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("prime read fetched")
            resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        refresh_content.assert_not_called()
        requests = RefreshRequest.objects.filter(movie=movie)
        self.assertTrue(requests.exists())
        self.assertTrue(
            all(r.status == RefreshRequestStatus.PENDING for r in requests)
        )
        self.assertTrue(
            all(r.priority == RefreshPriority.RECENTLY_VIEWED for r in requests)
        )


class PrimeDetailFanOutTests(TestCase):
    """Both Prime detail routes fan out exactly like ``movie_detail``.

    ``prime_movie`` and ``prime_series`` share ``_prime_detail_response``, so a
    single change there covers both entry points. The point of the tests is
    parity: reaching a title through Prime must schedule the same targeted
    lookups as reaching it through ``/api/movie/<pk>/`` — otherwise the
    availability a user sees would depend on which route they used.
    """

    def _prime_movie(self, tmdb_id, **extra):
        return Movie.objects.create(
            tmdb_id=tmdb_id, title=f"Movie {tmdb_id}", year=2020,
            content_type="movie", **extra,
        )

    def _prime_series(self, tmdb_id, **extra):
        return Movie.objects.create(
            tmdb_id=tmdb_id, title=f"Series {tmdb_id}", year=2020,
            content_type="series", **extra,
        )

    def _get(self, kind, tmdb_id):
        return self.client.get(f"/api/prime/{kind}/{tmdb_id}/")

    def _queued(self, movie):
        return set(
            RefreshRequest.objects.filter(movie=movie).values_list(
                "source", flat=True
            )
        )

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=False,
    )
    def test_prime_movie_fans_out_to_every_title_level_source(
        self, _gate, pipeline_cls
    ):
        pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])
        movie = self._prime_movie(1301)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("prime read fetched")
            resp = self._get("movie", movie.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        refresh_content.assert_not_called()
        self.assertEqual(self._queued(movie), set(title_level_source_slugs()))

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=False,
    )
    def test_prime_series_fans_out_to_every_title_level_source(
        self, _gate, pipeline_cls
    ):
        pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])
        series = self._prime_series(1302)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("prime read fetched")
            resp = self._get("series", series.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        refresh_content.assert_not_called()
        self.assertEqual(self._queued(series), set(title_level_source_slugs()))

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=True,
    )
    def test_prime_movie_resolved_title_skips_the_metered_scope_on_the_ladder(
        self, _gate, pipeline_cls
    ):
        pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])
        movie = self._prime_movie(1303)
        local_offer(movie)
        _backdate_offers(movie)

        resp = self._get("movie", movie.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        queued = self._queued(movie)
        self.assertNotIn("streaming_availability", queued)
        self.assertIn("justwatch", queued)
        self.assertIn("watchmode", queued)

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=True,
    )
    def test_prime_series_resolved_title_skips_the_metered_scope_on_the_ladder(
        self, _gate, pipeline_cls
    ):
        pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])
        series = self._prime_series(1304)
        local_offer(series)
        _backdate_offers(series)

        resp = self._get("series", series.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        queued = self._queued(series)
        self.assertNotIn("streaming_availability", queued)
        self.assertIn("justwatch", queued)

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    def test_prime_routes_never_create_a_catalogue_refresh_job(self, pipeline_cls):
        pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])
        movie = self._prime_movie(1305)
        series = self._prime_series(1306)

        self._get("movie", movie.tmdb_id)
        self._get("series", series.tmdb_id)

        self.assertEqual(CatalogueRefreshRequest.objects.count(), 0)
        self.assertNotIn("prime_video", self._queued(movie))
        self.assertNotIn("prime_video", self._queued(series))

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    def test_prime_detail_fresh_title_queues_nothing(self, pipeline_cls):
        pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])
        movie = self._prime_movie(1307)
        _fresh_every_title_level_scope(movie)

        resp = self._get("movie", movie.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    def test_repeated_prime_opens_do_not_multiply_requests(self, pipeline_cls):
        """The Prime fan-out is idempotent, like the movie-detail one."""
        pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])
        movie = self._prime_movie(1308)

        self._get("movie", movie.tmdb_id)
        first = self._queued(movie)
        self._get("movie", movie.tmdb_id)

        self.assertEqual(self._queued(movie), first)
        self.assertEqual(
            RefreshRequest.objects.filter(movie=movie).count(), len(first)
        )

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    def test_prime_detail_keeps_cached_offers_while_refresh_runs(
        self, pipeline_cls
    ):
        """The response still carries the cached offers and a pending flag —
        the fan-out never blocks or clears them."""
        pipeline_cls.return_value.run.return_value = MagicMock(
            offers_out=[{"service": "prime", "country": "US", "url": "https://x"}]
        )
        movie = self._prime_movie(1309)

        resp = self._get("movie", movie.tmdb_id)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()["offers"]), 1)
        self.assertTrue(resp.json()["refresh_pending"])


class ExplicitRefreshEndpointTests(TestCase):
    """POST /api/movie/<tmdb_id>/refresh/ — the manual "Refresh availability" action.

    Enqueue-only: the HTTP request creates (or upgrades, deduplicated) a
    pending ``RefreshRequest`` at ``EXPLICIT`` priority with
    ``if_stale=False`` — even for a title whose offers still read as fresh —
    and never contacts a provider; the existing worker performs the actual
    refresh. Movies and series share the endpoint and the mechanism.
    """

    def _post(self, tmdb_id):
        return self.client.post(
            f"/api/movie/{tmdb_id}/refresh/",
            data={},
            content_type="application/json",
        )

    def test_queues_pending_explicit_request_without_provider_io(self):
        movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        local_offer(movie)  # fresh: if_stale=False must still enqueue

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("endpoint fetched")
            resp = self._post(movie.tmdb_id)

        self.assertEqual(resp.status_code, 202, resp.content)
        refresh_content.assert_not_called()
        request = RefreshRequest.objects.get()
        self.assertEqual(request.movie, movie)
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)
        self.assertTrue(resp.data["refresh_pending"])
        self.assertEqual(resp.data["refresh_status"], RefreshRequestStatus.PENDING)
        self.assertEqual(resp.data["priority"], int(RefreshPriority.EXPLICIT))

    def test_worker_performs_the_refresh_for_a_fresh_title(self):
        """EXPLICIT outranks the freshness short-circuit at drain time too.

        A fresh title's pending request normally settles as ``done`` without
        spending budget — but the manual button means "check now", so the
        worker must perform the new availability check.
        """
        movie = Movie.objects.create(tmdb_id=551, title="Fresh", year=2000)
        local_offer(movie)

        resp = self._post(movie.tmdb_id)
        self.assertEqual(resp.status_code, 202, resp.content)

        service = MagicMock()
        summary = process_refresh_queue(budget=5, service=service)

        service.refresh_content.assert_called_once_with(movie, country=None)
        self.assertEqual(summary["refreshed"], 1)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.status, RefreshRequestStatus.DONE)
        self.assertIsNotNone(request.completed_at)

    def test_upgrades_a_lower_priority_pending_request(self):
        movie = Movie.objects.create(tmdb_id=552, title="Stale", year=2001)
        local_offer(movie)
        _backdate_offers(movie)
        first = request_availability_refresh(
            movie, priority=RefreshPriority.RECENTLY_VIEWED
        )

        resp = self._post(movie.tmdb_id)

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 1)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.pk, first.pk)
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_repeated_clicks_stay_deduplicated(self):
        movie = Movie.objects.create(tmdb_id=553, title="Repeat", year=2002)
        local_offer(movie)

        first = self._post(movie.tmdb_id)
        second = self._post(movie.tmdb_id)

        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(second.status_code, 202, second.content)
        self.assertEqual(RefreshRequest.objects.filter(status="pending").count(), 1)
        self.assertEqual(
            RefreshRequest.objects.get().priority, RefreshPriority.EXPLICIT
        )

    def test_never_fetched_title_keeps_explicit_priority(self):
        movie = Movie.objects.create(tmdb_id=554, title="Unseen", year=2003)

        resp = self._post(movie.tmdb_id)

        self.assertEqual(resp.status_code, 202, resp.content)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_series_title_shares_the_same_refresh_path(self):
        movie = Movie.objects.create(
            tmdb_id=1396,
            title="Breaking Bad",
            year=2008,
            content_type="series",
        )
        local_offer(movie)

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("endpoint fetched")
            resp = self._post(movie.tmdb_id)

        self.assertEqual(resp.status_code, 202, resp.content)
        refresh_content.assert_not_called()
        request = RefreshRequest.objects.get()
        self.assertEqual(request.movie.content_type, "series")
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)
        self.assertEqual(resp.data["content_type"], "series")

    def test_unknown_title_returns_404(self):
        resp = self._post(999999)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_enqueue_failure_surfaces_instead_of_looking_queued(self):
        movie = Movie.objects.create(tmdb_id=555, title="Doomed", year=2004)

        with patch("tracker.views.request_availability_refresh", return_value=None):
            resp = self._post(movie.tmdb_id)

        self.assertEqual(resp.status_code, 500)
        self.assertIn("error", resp.data)


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

    def test_budget_exhaustion_leaves_the_remainder_pending(self):
        """A spent *run* budget defers work; it never fakes source exhaustion.

        A run budget limits *work performed*, not future eligibility. The worker
        spent its one unit on the higher-priority request, so the other is
        deferred — left ``PENDING`` for the next tick, not settled. Recording it
        as ``blocked``/``quota_exhausted`` conflated the run's own execution
        budget with a source running dry; on the live watchlist a single
        10-unit tick stranded ~181 requests per source that way, and 22 titles
        read as ``not_applicable`` unknowns purely because the worker never
        reached them.

        The budget itself is honoured exactly as before: one upstream call, and
        the deferral is still counted — in ``skipped_budget_exhausted``, never
        in ``blocked_by_reason``.
        """
        first = Movie.objects.create(tmdb_id=911, title="First", year=2020)
        second = Movie.objects.create(tmdb_id=912, title="Second", year=2021)
        self._request(first, RefreshPriority.NEW)
        self._request(second, RefreshPriority.WATCHLIST)

        summary = self._run(budget=1)

        refreshed = [
            call.args[0].tmdb_id
            for call in self.refresh_service.refresh_content.call_args_list
        ]
        # The budget is honoured exactly as before: one call, not two.
        self.assertEqual(refreshed, [911])
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["remaining_budget"], 0)
        self.assertEqual(summary["skipped_budget_exhausted"], 1)
        # A run budget is never recorded as a source-quota block.
        self.assertEqual(
            summary["blocked_by_reason"].get(BlockedReason.QUOTA_EXHAUSTED, 0), 0
        )
        self.assertEqual(summary["requests_drained"], 1)
        # The unrun request is deferred, not settled: eligible next tick.
        self.assertEqual(summary["requests_pending"], 1)
        deferred = RefreshRequest.objects.get(movie=second)
        self.assertEqual(deferred.status, RefreshRequestStatus.PENDING)
        self.assertEqual(deferred.blocked_reason, "")
        self.assertIsNone(deferred.completed_at)

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
        """Priority still wins the single unit; the loser is deferred.

        The priority half is unchanged and is the point of the test: the
        high-priority event gets the one unit of budget. The loser is left
        ``PENDING`` — deferred by the run budget, not settled as a source-quota
        block.
        """
        self._stale_backlog_and_fresh_event()

        summary = self._run(budget=1)

        self.assertEqual(self._refreshed_ids(self.refresh_service), [972])
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["skipped_budget_exhausted"], 1)
        loser = RefreshRequest.objects.get(movie__tmdb_id=971)
        self.assertEqual(loser.status, RefreshRequestStatus.PENDING)
        self.assertEqual(loser.blocked_reason, "")

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
        """Zero upstream calls when the limiter refuses, and no row left queued.

        The contract that matters — the limiter is honoured and no source call
        is made — is unchanged. Only the requests' terminal state moved: the
        drain has decided it cannot spend the source, so the rows settle as
        `blocked`/`quota_exhausted` rather than staying `pending`, which would
        claim a worker still intended to run them.
        """
        self.limiter.acquire.side_effect = RateLimitExceeded("daily quota")
        first = Movie.objects.create(tmdb_id=921, title="A", year=2020)
        second = Movie.objects.create(tmdb_id=922, title="B", year=2021)
        self._request(first, RefreshPriority.NEW)
        self._request(second, RefreshPriority.STALE)

        summary = self._run()

        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["skipped_rate_limited"], 2)
        # No quota bypass: the limiter refused and not one upstream call was
        # made. The second source refused again when the sweep reached it, which
        # is why `attempted` is 2 while `refreshed` stays 0.
        self.refresh_service.refresh_content.assert_not_called()
        self.limiter.acquire.assert_called_once()  # stop after first refusal

        # The queue drain settles the row it can actually reach; the other is
        # filtered out in SQL by the anti-starvation exclusion, so only the
        # settlement boundary can close it. Both end up terminal either way, and
        # neither is left queued.
        self.assertEqual(summary["requests_drained"], 1)
        self.assertEqual(
            summary["blocked_by_reason"][BlockedReason.QUOTA_EXHAUSTED], 1
        )
        self.assertEqual(summary["requests_pending"], 1, "one row awaits the boundary")

        # Exactly one of the two rows is settled by the drain; the other is excluded
        # in SQL by the anti-starvation filter and is therefore owned by the
        # settlement boundary instead — that half is covered in
        # test_refresh_failure_settles.py, where requests carry a run's batch,
        # because `settle_unexecutable_requests` deliberately scopes to a run
        # and these queue-only rows belong to no run.
        rows = {
            row.status
            for row in RefreshRequest.objects.filter(movie__in=[first, second])
        }
        self.assertEqual(
            rows, {RefreshRequestStatus.BLOCKED, RefreshRequestStatus.PENDING}
        )
        self.assertEqual(
            RefreshRequest.objects.filter(
                movie__in=[first, second],
                status=RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            ).count(),
            1,
            "the settled row must record why it could not run",
        )

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

    def test_upstream_404_is_permanent_and_never_retried(self):
        # A 404 means the upstream catalogue does not carry the title at all —
        # a fact that cannot change on a retry. It must fail on the FIRST
        # attempt instead of consuming MAX_ATTEMPTS metered calls to relearn
        # the same 404.
        movie = Movie.objects.create(tmdb_id=942, title="Absent", year=2020)
        self._request(movie, RefreshPriority.NEW)
        self.refresh_service.refresh_content.side_effect = UpstreamTitleNotFound(
            "Streaming Availability has no record of movie 942 (404)"
        )

        first = self._run()
        # A permanently absent title is not a run failure: it lands on its own
        # counter so a healthy run's ``errors`` stays meaningful.
        self.assertEqual(first["errors"], 0)
        self.assertEqual(first["skipped_upstream_missing"], 1)
        self.assertEqual(first["requests_drained"], 1)
        request = RefreshRequest.objects.get()
        # Failed immediately, having attempted exactly once — not MAX_ATTEMPTS.
        self.assertEqual(request.attempts, 1)
        self.assertEqual(request.status, RefreshRequestStatus.FAILED)
        self.assertIn("UpstreamTitleNotFound", request.last_error)

        calls_so_far = self.refresh_service.refresh_content.call_count
        second = self._run()
        # Dead on arrival: the request is never picked up again...
        self.assertEqual(second["requests_drained"], 0)
        self.assertEqual(
            RefreshRequest.objects.get().status, RefreshRequestStatus.FAILED
        )
        # ...and the title costs no further metered calls. The permanent 404 is
        # remembered as evidence (a failed UpstreamTitleNotFound request), so
        # neither the queue drain nor the background sweep re-fetches it — an
        # absent title writes no Offer row and would otherwise read as stale
        # forever, burning one call per sweep run, every run.
        self.assertLessEqual(
            self.refresh_service.refresh_content.call_count,
            calls_so_far,
        )

    def test_transient_error_still_retries(self):
        # The counterpart guard: only a 404 is permanent. A 5xx/timeout/429 must
        # keep the normal retry ladder, or a temporary upstream blip would
        # permanently mark a perfectly good title as failed.
        movie = Movie.objects.create(tmdb_id=943, title="Blip", year=2020)
        self._request(movie, RefreshPriority.NEW)
        self.refresh_service.refresh_content.side_effect = RuntimeError("503")

        self._run()
        request = RefreshRequest.objects.get()
        self.assertEqual(request.attempts, 1)
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)

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
        scoped_service.refresh_content.assert_called_once_with(scoped_movie, country=None)
        self.refresh_service.refresh_content.assert_called_once_with(default_movie, country=None)
        self.assertEqual(summary["requests_drained"], 2)
