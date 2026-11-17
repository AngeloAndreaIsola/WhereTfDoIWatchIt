"""Watchlist "Refresh all availability" — bulk explicit enqueue + progress.

Covers the two endpoints behind the watchlist page's bulk refresh action:

- ``POST /api/watchlist/refresh-explicit/`` enqueues every watchlisted title
  at ``EXPLICIT`` priority in one local pass — enqueue-only (no provider
  I/O), deduplicated against pending event rows, freshness bypassed — and
  reports how many titles are queued. Each run owns a ``batch`` uuid stamped
  onto every request it touches, and stale catalogue-capable sources get a
  ``CatalogueRefreshRequest`` marker alongside the title queue;
- ``GET /api/watchlist/refresh-status/?since=<epoch>[&batch=<uuid>]`` reports
  that run's progress: pending/refreshed/failed counts scoped to the
  watchlist's default source scope — to completions at or after the run's
  start (legacy), or to the run's own batch — plus the shared daily quota
  (``rate_limited``/``quota_*``/``resets_at``);
- ``refresh_states_for`` derives the read-side evidence (pending/checked/
  failed/never_checked) the report turns into 🔵/⚫/⚪ badges, and
  ``stale_catalogue_sources``/``process_pending_catalogue_refresh`` drive
  the marker the worker runs after draining titles.

The drain itself is the existing worker path (already covered in
``test_event_driven_refresh``); one end-to-end test here asserts a bulk
enqueue is drained by ``process_refresh_queue`` and then reads back through
the status endpoint.

No test in this module makes live HTTP requests to any availability
provider.
"""
from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from availability.freshness import freshness_for
from availability.models import Source
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry
from availability.tests.source_stubs import StubSource
from tracker.models import (
    ApiCallCounter,
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
    WatchlistItem,
)
from tracker.services.bulk_refresh import (
    process_pending_catalogue_refresh,
    stale_catalogue_sources,
    title_level_source_slugs,
)
from tracker.services.rate_limit import (
    SOURCE_DAILY_LIMITS,
    SOURCE_MONTHLY_LIMITS,
    RateLimiter,
)
from tracker.services.refresh_requests import (
    process_refresh_queue,
    refresh_states_for,
    request_availability_refresh,
    request_catalogue_refresh,
    request_watchlist_refresh,
)
from tracker.services.watchability import RefreshState
from tracker.services.access import AccessVerdict, _empty_result
from tracker.tests.availability_factories import local_offer
from tracker.tests.test_refresh_provider_catalogue import catalogue_slugs

REFRESH_CONTENT = (
    "availability.services.refresh.AvailabilityRefreshService.refresh_content"
)
REGISTRY = "tracker.services.bulk_refresh.source_registry"
CATALOGUE_RUN = "tracker.services.bulk_refresh.process_catalogue_refresh"


def _watchlist_movie(tmdb_id, title="Title", **kwargs):
    movie = Movie.objects.create(tmdb_id=tmdb_id, title=title, year=2020, **kwargs)
    WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
    return movie


def gate_off(test):
    """Pin the metered-source gate OFF for a test that predates it.

    The bulk-refresh tests below assert the full ``titles x sources`` fan-out.
    That is the **default** contract and it stays pinned exactly as it was —
    but with the metered gate enabled those assertions are meant to change,
    because a title already resolved by a free source deliberately gets no
    metered row.

    So rather than weaken the old assertions to "whatever the policy says",
    each pre-existing test states the configuration it is asserting. That
    keeps the old contract verifiable *and* lets the whole suite run green
    under ``AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED=1`` — which is
    the only way to know the flag did not break unrelated behaviour. The
    gate-on path is asserted directly in
    ``test_refresh_source_policy_enqueue.py``.

    Decorator rather than ``setUp`` so the intent is visible at each call
    site rather than buried in a base class.
    """

    def wrapper(*args, **kwargs):
        import os

        import tracker.services.source_policy as sp

        original = os.environ.get(sp.LADDER_ENV_VAR)
        os.environ[sp.LADDER_ENV_VAR] = "0"
        try:
            return test(*args, **kwargs)
        finally:
            if original is None:
                os.environ.pop(sp.LADDER_ENV_VAR, None)
            else:
                os.environ[sp.LADDER_ENV_VAR] = original

    wrapper.__name__ = test.__name__
    wrapper.__doc__ = test.__doc__
    return wrapper


class BulkEnqueueServiceTests(TestCase):
    """request_watchlist_refresh: one local pass, per-title contracts intact.

    The unit of work is now one request per (title, source) across every
    registered title-level source, so the expected counts are
    ``titles x len(title_level_source_slugs())`` — never a bare title count.
    """

    @gate_off
    def test_queues_every_watchlisted_title_at_explicit_priority(self):
        movie_a = _watchlist_movie(101)
        _watchlist_movie(102, content_type="series")
        local_offer(movie_a)  # fresh: if_stale=False must still enqueue

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("enqueue fetched")
            result = request_watchlist_refresh(user_id=1)

        refresh_content.assert_not_called()
        sources = title_level_source_slugs()
        expected = 2 * len(sources)
        self.assertEqual(result["queued"], expected)
        self.assertEqual(result["total_watchlist"], 2)
        self.assertEqual(result["checks_per_title"], len(sources))
        self.assertEqual(sorted(result["sources"]), sorted(sources))
        uuid.UUID(result["batch"])
        self.assertEqual(RefreshRequest.objects.count(), expected)
        for request in RefreshRequest.objects.all():
            self.assertEqual(request.status, RefreshRequestStatus.PENDING)
            self.assertEqual(request.priority, RefreshPriority.EXPLICIT)
            # Every explicitly queued request names its source: no request
            # may be born in the legacy empty-scope bucket.
            self.assertIn(request.source, sources)

    def test_one_request_per_title_per_source(self):
        _watchlist_movie(111)
        _watchlist_movie(112)

        result = request_watchlist_refresh(user_id=1)

        pairs = set(
            RefreshRequest.objects.values_list("movie_id", "source")
        )
        self.assertEqual(len(pairs), result["queued"])
        for source in title_level_source_slugs():
            self.assertEqual(
                sum(1 for _movie_id, slug in pairs if slug == source), 2
            )

    def test_titles_outside_the_watchlist_are_untouched(self):
        Movie.objects.create(tmdb_id=999, title="Not watched", year=1999)

        result = request_watchlist_refresh(user_id=1)

        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["total_watchlist"], 0)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    @gate_off
    def test_repeated_bulk_calls_stay_deduplicated_and_upgrade(self):
        movie = _watchlist_movie(103)
        local_offer(movie)
        # A pre-existing default-scope row: this is the legacy spelling of the
        # streaming_availability scope, so the fan-out must *adopt* it rather
        # than queue a second row for the same source.
        request_availability_refresh(movie, priority=RefreshPriority.WATCHLIST)

        first = request_watchlist_refresh(user_id=1)
        second = request_watchlist_refresh(user_id=1)

        sources = title_level_source_slugs()
        # One pending row per (movie, source): the adopted legacy row now
        # counts as the streaming_availability check, so the total is exactly
        # the number of title-level sources — not that plus one.
        expected = len(sources)
        self.assertEqual(first["queued"], expected)
        self.assertEqual(second["queued"], expected)
        self.assertEqual(second["total_watchlist"], 1)
        self.assertNotEqual(first["batch"], second["batch"])
        self.assertEqual(RefreshRequest.objects.count(), expected)

        scopes = sorted(
            RefreshRequest.objects.values_list("source", flat=True)
        )
        self.assertEqual(scopes, sorted(sources))
        for request in RefreshRequest.objects.all():
            request.refresh_from_db()
            self.assertEqual(request.priority, RefreshPriority.EXPLICIT)
            # Last click wins: the pending row now belongs to the newest run.
            self.assertEqual(request.batch, uuid.UUID(second["batch"]))

    def test_repeated_refresh_all_never_duplicates_a_pending_pair(self):
        """Repeated clicks must not multiply pending (movie, source) work."""
        movie = _watchlist_movie(114)
        for _ in range(3):
            request_watchlist_refresh(user_id=1)

        pairs = list(RefreshRequest.objects.values_list("movie_id", "source"))
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(
            len(pairs), len(title_level_source_slugs())
        )

    def test_empty_watchlist_reports_zero(self):
        result = request_watchlist_refresh(user_id=1)

        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["total_watchlist"], 0)
        uuid.UUID(result["batch"])


class BulkEnqueueEndpointTests(TestCase):
    """POST /api/watchlist/refresh-explicit/ — enqueue-only, answers 202."""

    def _post(self):
        return self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )

    @gate_off
    def test_queues_all_titles_without_provider_io(self):
        _watchlist_movie(201)
        local_offer(_watchlist_movie(202))

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("endpoint fetched")
            resp = self._post()

        refresh_content.assert_not_called()
        expected = 2 * len(title_level_source_slugs())
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["queued"], expected)
        self.assertEqual(resp.data["total_watchlist"], 2)
        self.assertIsInstance(resp.data["now"], float)
        self.assertEqual(
            RefreshRequest.objects.filter(
                status=RefreshRequestStatus.PENDING,
                priority=RefreshPriority.EXPLICIT,
            ).count(),
            expected,
        )

    @gate_off
    def test_endpoint_upgrades_a_lower_priority_pending_request(self):
        movie = _watchlist_movie(203)
        local_offer(movie)
        existing = request_availability_refresh(
            movie, priority=RefreshPriority.WATCHLIST, if_stale=False, country="US"
        )
        self.assertIsNotNone(existing)
        self.assertEqual(existing.priority, RefreshPriority.WATCHLIST)
        existing_pk = existing.pk

        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        # The legacy default-scope row is adopted into the explicit
        # streaming_availability scope and upgraded in place, never
        # duplicated: one pending row per (movie, source).
        self.assertEqual(
            RefreshRequest.objects.count(), len(title_level_source_slugs())
        )
        request = RefreshRequest.objects.get(pk=existing_pk)
        self.assertEqual(request.source, DEFAULT_SOURCE_SLUG)
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_empty_watchlist_returns_202_with_zero_counts(self):
        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["queued"], 0)
        self.assertEqual(resp.data["total_watchlist"], 0)

    def test_total_failure_surfaces_instead_of_looking_queued(self):
        _watchlist_movie(204)

        with patch(
            "tracker.views.request_watchlist_refresh",
            return_value={"queued": 0, "total_watchlist": 1},
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 500)
        self.assertIn("error", resp.data)

    def test_checks_per_title_stays_exact_as_rows_settle(self):
        """Fan-out width must not drift downward mid-run.

        Dividing total by titles would report 2.98 once a couple of rows
        settle, implying the run had fewer sources per title than it does.
        """
        _watchlist_movie(511)
        _watchlist_movie(512)
        resp = self._post()
        batch = resp.data["batch"]
        sources = len(title_level_source_slugs())

        before = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": resp.data["now"], "batch": batch},
        )
        self.assertEqual(before.data["checks_per_title"], sources)

        # Settle some rows and delete others, as a real run does over time.
        # (Slicing a queryset forbids ``.update()``, so take PKs first.)
        pks = list(
            RefreshRequest.objects.filter(batch=batch)
            .order_by("source")
            .values_list("pk", flat=True)
        )
        RefreshRequest.objects.filter(pk__in=pks[:2]).update(
            status=RefreshRequestStatus.DONE
        )
        RefreshRequest.objects.filter(pk__in=pks[2:3]).delete()

        after = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": resp.data["now"], "batch": batch},
        )
        self.assertEqual(after.data["checks_per_title"], sources)
        self.assertEqual(after.data["titles"], 2)


class RefreshStatusEndpointTests(TestCase):
    """GET /api/watchlist/refresh-status/ — progress scoped to one run."""

    def setUp(self):
        self.since = (timezone.now() - dt.timedelta(seconds=60)).timestamp()

    def _get(self, since=None, batch=None):
        params = {"since": self.since if since is None else since}
        if batch is not None:
            params["batch"] = batch
        return self.client.get("/api/watchlist/refresh-status/", params)

    def test_counts_pending_refreshed_failed_only_for_this_run(self):
        pending_movie = _watchlist_movie(301)
        refreshed_movie = _watchlist_movie(302)
        finished_before_run = _watchlist_movie(303)
        failed_movie = _watchlist_movie(304)
        non_watchlist = Movie.objects.create(tmdb_id=305, title="Other", year=2001)

        now = timezone.now()
        RefreshRequest.objects.create(
            movie=pending_movie, status=RefreshRequestStatus.PENDING
        )
        RefreshRequest.objects.create(
            movie=refreshed_movie,
            status=RefreshRequestStatus.DONE,
            completed_at=now,
        )
        RefreshRequest.objects.create(
            movie=finished_before_run,
            status=RefreshRequestStatus.DONE,
            completed_at=now - dt.timedelta(hours=2),
        )
        RefreshRequest.objects.create(
            movie=failed_movie,
            status=RefreshRequestStatus.FAILED,
            completed_at=now,
        )
        RefreshRequest.objects.create(
            movie=non_watchlist,
            status=RefreshRequestStatus.DONE,
            completed_at=now,
        )

        resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["pending"], 1)
        self.assertEqual(resp.data["refreshed"], 1)
        self.assertEqual(resp.data["failed"], 1)

    def test_other_source_scopes_are_counted_in_their_own_batch(self):
        """A source-scoped row is now real work, not something to hide.

        The old endpoint filtered to ``source=""``, which would make a
        source-fanned-out batch report zero progress while the worker drained
        it. Unbatched reads still report it (there is no batch to scope to).
        """
        movie = _watchlist_movie(306)
        batch = uuid.uuid4()
        RefreshRequest.objects.create(
            movie=movie,
            source="youtube",
            status=RefreshRequestStatus.PENDING,
            batch=batch,
        )

        resp = self._get(batch=str(batch))

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["pending"], 1)
        self.assertEqual(resp.data["total"], 1)

    def test_a_legacy_default_scope_batch_still_reports_progress(self):
        """Pre-fan-out rows (source="") keep counting instead of vanishing."""
        movie = _watchlist_movie(307)
        batch = uuid.uuid4()
        RefreshRequest.objects.create(
            movie=movie,
            source="",
            status=RefreshRequestStatus.PENDING,
            batch=batch,
        )

        resp = self._get(batch=str(batch))

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["pending"], 1)

    def test_missing_or_malformed_since_is_rejected(self):
        self.assertEqual(
            self.client.get("/api/watchlist/refresh-status/").status_code, 400
        )
        self.assertEqual(
            self.client.get(
                "/api/watchlist/refresh-status/", {"since": "not-a-timestamp"}
            ).status_code,
            400,
        )


class BulkDrainEndToEndTests(TestCase):
    """Bulk enqueue -> worker drain -> status endpoint reads the progress back.

    The drained unit is a (title, source) check, so both titles are checked
    against every title-level source — ``2 x sources`` refreshes — and the
    status endpoint reports the same aggregate.
    """

    @gate_off
    def test_worker_drains_bulk_queue_and_status_reports_it(self):
        movie_a = _watchlist_movie(401)
        movie_b = _watchlist_movie(402, content_type="series")
        local_offer(movie_a)  # fresh: EXPLICIT must still be refreshed

        resp = self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 202, resp.content)
        since = resp.data["now"]
        expected = 2 * len(title_level_source_slugs())

        before = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": since, "batch": resp.data["batch"]},
        )
        self.assertEqual(before.data["pending"], expected)
        self.assertEqual(before.data["refreshed"], 0)

        # Every request, whatever its source, must resolve to a mock: a
        # source-scoped request builds its own refresh service from the
        # registry, and the default-scope one uses the injected service, so
        # patching only the injected service would let streaming_availability
        # reach the real API.
        default_service = MagicMock()
        with patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            service_cls.return_value = MagicMock()
            summary = process_refresh_queue(budget=99, service=default_service)

        self.assertEqual(summary["refreshed"], expected)
        # Every source-scoped request — including the explicit
        # streaming_availability one, which is no longer the legacy ``""`` row
        # — resolves its collector through the registry rather than through the
        # injected default service.
        sources_used = {
            call.kwargs["source"].slug for call in service_cls.call_args_list
        }
        self.assertEqual(sources_used, set(title_level_source_slugs()))
        default_service.refresh_content.assert_not_called()

        after = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": since, "batch": resp.data["batch"]},
        )
        self.assertEqual(after.data["pending"], 0)
        self.assertEqual(after.data["refreshed"], expected)
        self.assertEqual(after.data["failed"], 0)


class BatchStampingTests(TestCase):
    """One uuid per run, stamped onto every request the run touches."""

    def test_service_returns_a_batch_id_and_stamps_its_rows(self):
        _watchlist_movie(411)
        _watchlist_movie(412)

        result = request_watchlist_refresh(user_id=1)

        batch = uuid.UUID(result["batch"])
        for request in RefreshRequest.objects.all():
            self.assertEqual(request.batch, batch)

    def test_endpoint_returns_the_batch_it_stamped(self):
        _watchlist_movie(413)

        resp = self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 202, resp.content)
        batch = uuid.UUID(resp.data["batch"])
        self.assertEqual(
            set(
                RefreshRequest.objects.values_list("batch", flat=True)
            ),
            {batch},
        )

    @gate_off
    def test_a_row_opened_by_another_event_joins_the_run(self):
        movie = _watchlist_movie(414)
        local_offer(movie)
        existing = request_availability_refresh(
            movie, priority=RefreshPriority.WATCHLIST, if_stale=False
        )
        existing_pk = existing.pk
        self.assertIsNone(existing.batch)

        request_watchlist_refresh(user_id=1)

        batch = RefreshRequest.objects.get(pk=existing_pk).batch
        self.assertIsNotNone(batch)
        # Every row the run owns carries that one batch id — including the
        # fan-out rows and the legacy row it adopted.
        self.assertEqual(
            set(RefreshRequest.objects.values_list("batch", flat=True)), {batch}
        )

    def test_batch_scoped_status_reads_this_runs_progress_end_to_end(self):
        _watchlist_movie(415)
        _watchlist_movie(416)

        resp = self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )
        batch = resp.data["batch"]
        expected = 2 * len(title_level_source_slugs())

        before = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": resp.data["now"], "batch": batch},
        )
        self.assertEqual(before.data["pending"], expected)
        self.assertEqual(before.data["total"], expected)
        self.assertEqual(before.data["refreshed"], 0)

        # Patch the registry resolution too: without it, source-scoped
        # requests build their own service and hit the real API.
        with patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            service_cls.return_value = MagicMock()
            # The drain must actually execute here, so give the metered
            # source today's budget back: a developer machine can have it
            # spent, which would correctly defer those requests instead.
            ApiCallCounter.objects.filter(service=DEFAULT_SOURCE_SLUG).delete()
            process_refresh_queue(budget=99, service=MagicMock())

        after = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": resp.data["now"], "batch": batch},
        )
        self.assertEqual(after.data["pending"], 0)
        self.assertEqual(after.data["refreshed"], expected)
        self.assertEqual(after.data["total"], expected)
        self.assertEqual(after.data["titles"], 2)

class RefreshStatusBatchTests(TestCase):
    """batch= scopes every count to the run's own stamped backlog."""

    def setUp(self):
        self.since = (timezone.now() - dt.timedelta(seconds=60)).timestamp()
        self.batch = uuid.uuid4()

    def _get(self, **extra):
        params = {"since": self.since, **extra}
        return self.client.get("/api/watchlist/refresh-status/", params)

    def test_pending_counts_only_the_batches_own_rows(self):
        mine = _watchlist_movie(421)
        theirs = _watchlist_movie(422)
        RefreshRequest.objects.create(
            movie=mine,
            status=RefreshRequestStatus.PENDING,
            batch=self.batch,
        )
        RefreshRequest.objects.create(
            movie=theirs,
            status=RefreshRequestStatus.PENDING,
            batch=uuid.uuid4(),
        )

        resp = self._get(batch=str(self.batch))

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["pending"], 1)
        self.assertEqual(resp.data["batch"], str(self.batch))

    def test_batch_scoping_ignores_the_since_cutoff(self):
        movie = _watchlist_movie(423)
        RefreshRequest.objects.create(
            movie=movie,
            status=RefreshRequestStatus.DONE,
            completed_at=timezone.now() - dt.timedelta(hours=2),
            batch=self.batch,
        )

        legacy = self._get()
        scoped = self._get(batch=str(self.batch))

        self.assertEqual(legacy.data["refreshed"], 0)
        self.assertEqual(scoped.data["refreshed"], 1)

    def test_invalid_batch_is_rejected(self):
        resp = self._get(batch="not-a-uuid")

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("batch", resp.data["error"])


class RefreshStatusQuotaTests(TestCase):
    """The status endpoint reports per-source quota, not one shared one.

    Only sources that declare a quota appear: unmetered collectors get no
    fabricated numbers, and one exhausted source is named rather than
    presented as the whole watchlist being blocked.
    """

    def setUp(self):
        self.since = (timezone.now() - dt.timedelta(seconds=60)).timestamp()

    def _get(self):
        return self.client.get(
            "/api/watchlist/refresh-status/", {"since": self.since}
        )

    def test_quota_fields_are_reported(self):
        resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertGreater(resp.data["resets_at"], timezone.now().timestamp())
        # Fresh day, no calls recorded: quota untouched, nothing blocked.
        self.assertFalse(resp.data["rate_limited"])
        self.assertEqual(resp.data["rate_limited_sources"], [])
        quotas = resp.data["source_quotas"]
        self.assertEqual(quotas[DEFAULT_SOURCE_SLUG]["limit"], 100)
        self.assertEqual(quotas[DEFAULT_SOURCE_SLUG]["remaining"], 100)
        self.assertFalse(quotas[DEFAULT_SOURCE_SLUG]["exhausted"])
        self.assertEqual(quotas[DEFAULT_SOURCE_SLUG]["monthly_limit"], 1000)
        self.assertEqual(quotas[DEFAULT_SOURCE_SLUG]["monthly_remaining"], 1000)
        self.assertEqual(quotas[DEFAULT_SOURCE_SLUG]["monthly_used"], 0)
        self.assertEqual(quotas[DEFAULT_SOURCE_SLUG]["monthly_window_days"], 31)
        self.assertIsNone(
            quotas[DEFAULT_SOURCE_SLUG]["monthly_accounting_since"]
        )

    def test_unmetered_sources_get_no_quota_invented(self):
        resp = self._get()

        quotas = resp.data["source_quotas"]
        for slug in title_level_source_slugs():
            if slug not in SOURCE_DAILY_LIMITS and slug not in SOURCE_MONTHLY_LIMITS:
                self.assertNotIn(
                    slug, quotas, f"{slug} has no declared quota to report"
                )

    def test_exhausted_quota_names_only_that_source(self):
        """Only the exhausted source is reported blocked.

        Unmetered sources answer ``remaining() is None``; mocking a flat 0
        for every source would falsely report unmetered collectors as
        quota-limited, so the fake mirrors the real per-source contract.
        Watchmode declares only a monthly budget, so it is *reported* but not
        named as exhausted by this daily-only fake.
        """
        real_remaining = RateLimiter.remaining

        def fake_remaining(self):
            if self.daily_limit is None:
                return None  # no declared daily budget (Watchmode: monthly only)
            return 0  # the daily-metered source has spent today's budget

        with patch.object(RateLimiter, "remaining", fake_remaining):
            resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["rate_limited"])
        self.assertEqual(
            resp.data["rate_limited_sources"], [DEFAULT_SOURCE_SLUG]
        )
        self.assertCountEqual(
            list(resp.data["source_quotas"]),
            [DEFAULT_SOURCE_SLUG, "watchmode"],
        )


class StaleCatalogueSourcesTests(TestCase):
    """stale_catalogue_sources: evidence is the last error-free sync per slug."""

    STUB = "stub_catalogue"

    def _stale(self):
        with patch(REGISTRY) as registry:
            registry.slugs.return_value = [self.STUB]
            registry.get.return_value.capabilities.supports_catalogue_sync = True
            return stale_catalogue_sources()

    def test_a_source_that_never_synced_reads_stale(self):
        self.assertEqual(self._stale(), [self.STUB])

    def test_a_recent_successful_run_is_fresh(self):
        RefreshRun.objects.create(
            service=self.STUB, error="", finished_at=timezone.now()
        )

        self.assertEqual(self._stale(), [])

    def test_a_failed_run_is_not_fresh_evidence(self):
        RefreshRun.objects.create(
            service=self.STUB, error="boom", finished_at=timezone.now()
        )

        self.assertEqual(self._stale(), [self.STUB])

    def test_a_run_older_than_the_window_reads_stale(self):
        RefreshRun.objects.create(
            service=self.STUB,
            error="",
            finished_at=timezone.now()
            - freshness_for(self.STUB)
            - dt.timedelta(hours=1),
        )

        self.assertEqual(self._stale(), [self.STUB])

    def test_disabled_sources_are_skipped(self):
        Source.objects.create(slug=self.STUB, name="Stub", enabled=False)

        self.assertEqual(self._stale(), [])


class CatalogueMarkerEnqueueTests(TestCase):
    """The button's enqueue side queues the catalogue marker alongside titles."""

    def _post(self):
        return self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )

    def test_never_synced_catalogue_sources_are_queued(self):
        expected = stale_catalogue_sources()
        self.assertTrue(expected)

        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["catalogue_queued"], expected)
        marker = CatalogueRefreshRequest.objects.get(
            status=CatalogueRefreshStatus.PENDING
        )
        self.assertEqual(marker.sources, expected)

    def test_fresh_catalogue_sources_are_not_queued(self):
        now = timezone.now()
        for slug in catalogue_slugs():
            RefreshRun.objects.create(
                service=slug, error="", finished_at=now
            )

        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["catalogue_queued"], [])
        self.assertEqual(CatalogueRefreshRequest.objects.count(), 0)

    def test_repeated_clicks_collapse_into_one_pending_marker(self):
        first = request_catalogue_refresh(["src_b"])
        second = request_catalogue_refresh(["src_a", "src_b"])

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            CatalogueRefreshRequest.objects.filter(
                status=CatalogueRefreshStatus.PENDING
            ).count(),
            1,
        )
        self.assertEqual(second.sources, ["src_a", "src_b"])


class CatalogueMarkerWorkerTests(TestCase):
    """process_pending_catalogue_refresh: one marker, one pass over its slugs."""

    def test_no_pending_marker_returns_none(self):
        self.assertIsNone(process_pending_catalogue_refresh())

    def test_marker_runs_every_source_then_completes(self):
        marker = request_catalogue_refresh(["src_b", "src_a"])

        with patch(CATALOGUE_RUN) as run:
            run.return_value = {"errors": 0}
            result = process_pending_catalogue_refresh()

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            {call.kwargs["source_slug"] for call in run.call_args_list},
            {"src_a", "src_b"},
        )
        marker.refresh_from_db()
        self.assertEqual(marker.status, CatalogueRefreshStatus.DONE)
        self.assertIsNotNone(marker.completed_at)
        self.assertEqual(result["status"], CatalogueRefreshStatus.DONE)
        self.assertEqual(result["failed_sources"], [])

        # Consumed: the next tick sees nothing to do.
        self.assertIsNone(process_pending_catalogue_refresh())

    def test_a_failing_source_marks_the_marker_failed_and_continues(self):
        marker = request_catalogue_refresh(["src_a", "src_b"])

        with patch(CATALOGUE_RUN) as run:
            run.side_effect = [{"errors": 1}, {"errors": 0}]
            result = process_pending_catalogue_refresh()

        self.assertEqual(run.call_count, 2)
        marker.refresh_from_db()
        self.assertEqual(marker.status, CatalogueRefreshStatus.FAILED)
        self.assertEqual(marker.last_error, "src_a")
        self.assertEqual(result["failed_sources"], ["src_a"])

    def test_an_exception_is_isolated_to_its_source(self):
        marker = request_catalogue_refresh(["src_a", "src_b"])

        with patch(CATALOGUE_RUN) as run:
            run.side_effect = [RuntimeError("source exploded"), {"errors": 0}]
            result = process_pending_catalogue_refresh()

        self.assertEqual(run.call_count, 2)
        marker.refresh_from_db()
        self.assertEqual(marker.status, CatalogueRefreshStatus.FAILED)
        self.assertEqual(result["failed_sources"], ["src_a"])


class RefreshStateEvidenceTests(TestCase):
    """refresh_states_for: the read side's pending/checked/failed/never map."""

    def test_states_are_derived_from_request_history(self):
        never = _watchlist_movie(431)
        pending = _watchlist_movie(432)
        checked = _watchlist_movie(433)
        failed = _watchlist_movie(434)
        request_availability_refresh(pending)
        RefreshRequest.objects.create(
            movie=checked, status=RefreshRequestStatus.DONE
        )
        RefreshRequest.objects.create(
            movie=failed, status=RefreshRequestStatus.FAILED
        )

        states = refresh_states_for(
            {never.id, pending.id, checked.id, failed.id}
        )

        self.assertEqual(states[never.id], RefreshState.NEVER_CHECKED)
        self.assertEqual(states[pending.id], RefreshState.PENDING)
        self.assertEqual(states[checked.id], RefreshState.CHECKED)
        self.assertEqual(states[failed.id], RefreshState.FAILED)

    def test_pending_beats_completed_evidence_while_queued(self):
        movie = _watchlist_movie(435)
        RefreshRequest.objects.create(
            movie=movie, status=RefreshRequestStatus.DONE
        )
        request_availability_refresh(movie, if_stale=False)

        states = refresh_states_for({movie.id})

        self.assertEqual(states[movie.id], RefreshState.PENDING)

    def test_done_in_any_source_scope_counts_as_checked(self):
        # A title checked through ANY source scope is checked. Refresh All
        # fans each title out over every title-level source, so a ``youtube``
        # (or mediaset_infinity) DONE row is just as much proof that the title
        # was looked at as a streaming_availability one — the read path must
        # not report ⚪ Unknown for a title a collector already answered.
        #
        # This assertion previously inverted the contract (it demanded
        # NEVER_CHECKED for a DONE row in a non-default scope), which is what
        # made almost every real watchlist title read "Unknown" despite having
        # been fetched. See ``refresh_states_for``'s docstring.
        movie = _watchlist_movie(436)
        RefreshRequest.objects.create(
            movie=movie,
            source="youtube",
            status=RefreshRequestStatus.DONE,
        )

        states = refresh_states_for({movie.id})

        self.assertEqual(states[movie.id], RefreshState.CHECKED)

    def test_cross_country_success_does_not_prove_current_market_unavailable(self):
        movie = _watchlist_movie(43601)
        RefreshRequest.objects.create(
            movie=movie, source="watchmode", country="US",
            status=RefreshRequestStatus.DONE,
        )
        RefreshRequest.objects.create(
            movie=movie, source="justwatch", country="NL",
            status=RefreshRequestStatus.FAILED,
        )

        state = refresh_states_for({movie.id})[movie.id]
        verdict = _empty_result(movie, state)

        self.assertEqual(state, RefreshState.CHECKED)
        self.assertEqual(verdict.verdict, AccessVerdict.UNKNOWN)

    def test_pending_in_any_scope_beats_a_done_row(self):
        # Precedence is evaluated across scopes too: a queued check anywhere
        # still reads 🔵, so a title being re-checked never flickers to ⚫.
        movie = _watchlist_movie(4361)
        RefreshRequest.objects.create(
            movie=movie, source="youtube", status=RefreshRequestStatus.DONE,
        )
        RefreshRequest.objects.create(
            movie=movie,
            source="streaming_availability",
            status=RefreshRequestStatus.PENDING,
        )

        states = refresh_states_for({movie.id})

        self.assertEqual(states[movie.id], RefreshState.PENDING)

    def test_failed_in_any_scope_counts_as_failed(self):
        movie = _watchlist_movie(4362)
        RefreshRequest.objects.create(
            movie=movie,
            source="mediaset_infinity",
            status=RefreshRequestStatus.FAILED,
        )

        states = refresh_states_for({movie.id})

        self.assertEqual(states[movie.id], RefreshState.FAILED)

    def test_report_payload_carries_the_refresh_state(self):
        queued = _watchlist_movie(437)
        never = _watchlist_movie(438)
        request_availability_refresh(queued)

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        by_tmdb = {
            row["movie"]["tmdb_id"]: row["watchability"]
            for row in resp.data["report"]
        }
        self.assertEqual(
            by_tmdb[queued.tmdb_id]["status"], "checking"
        )
        self.assertEqual(
            by_tmdb[queued.tmdb_id]["refresh_state"], RefreshState.PENDING
        )
        self.assertEqual(
            by_tmdb[never.tmdb_id]["status"], "unknown"
        )
        self.assertEqual(
            by_tmdb[never.tmdb_id]["refresh_state"],
            RefreshState.NEVER_CHECKED,
        )
