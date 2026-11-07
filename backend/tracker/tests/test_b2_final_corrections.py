"""Final B2 quota/evidence regressions; PostgreSQL and mocked providers only."""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TestCase

from availability.sources.base import SourceFetchError, SourceQuotaExceeded
from tracker.models import (
    ApiCallCounter, ApiCallLedger, Movie, RefreshPriority, RefreshRequest,
    RefreshRequestOutcome as Outcome, RefreshRequestStatus as Status,
    RefreshRun, WatchlistItem,
)
from tracker.services import availability_report as report
from tracker.services import bulk_refresh as bulk, refresh_requests as rr
from tracker.services.rate_limit import RateLimiter, RateLimitExceeded, budget_is_reserved
from tracker.services.streaming_api import UpstreamTitleNotFound

SA = "streaming_availability"
WM = "watchmode"


class RunBudgetCases:
    """Exercise both public entry points with real daily/monthly ledgers."""
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        ApiCallCounter.objects.filter(service__in=[SA, WM]).delete()
        ApiCallLedger.objects.filter(service__in=[SA, WM]).delete()
        self.limiters = {
            slug: RateLimiter(slug, daily_limit=1000, monthly_limit=2000)
            for slug in (SA, WM)
        }
        self.services = {slug: MagicMock() for slug in (SA, WM)}
        self.movies = []
        self.scopes = {}
        self.calls = []
        for slug, service in self.services.items():
            service.refresh_content.side_effect = self.provider(slug)

    def provider(self, slug):
        def fetch(movie, *, country):
            # Real clients honour this context instead of reserving twice.
            self.assertTrue(budget_is_reserved(slug))
            self.calls.append((movie.pk, slug, country))
            outcome = self.scopes[movie.tmdb_id][1]
            if isinstance(outcome, Exception):
                raise outcome
        return fetch

    def add(self, outcome=None, *, source=SA, priority=RefreshPriority.STALE):
        movie = Movie.objects.create(tmdb_id=986000 + len(self.movies), title="Budget fixture")
        self.movies.append(movie)
        self.scopes[movie.tmdb_id] = (source, outcome)
        if self.queue:
            RefreshRequest.objects.create(movie=movie, source=source, country="NL", priority=priority)
        else:
            WatchlistItem.objects.create(user_id=1, movie=movie)
        return movie

    def run_refresh(self, *, fresh=(), gated=()):
        with ExitStack() as stack:
            stack.enter_context(patch.object(rr, "is_stale", side_effect=lambda movie, **kw: movie.pk not in fresh))
            stack.enter_context(patch.object(bulk, "stale_refresh_scopes", side_effect=lambda tmdb_id, **kw: [] if self.movies_by_tmdb(tmdb_id).pk in fresh else [self.scopes[tmdb_id][0]]))
            stack.enter_context(patch.object(bulk, "resolve_source_country", return_value="NL"))
            stack.enter_context(patch.object(rr, "_metered_source_is_unnecessary", side_effect=lambda movie, *a, **kw: movie.pk in gated))
            for module in (bulk, rr):
                stack.enter_context(patch.object(module, "rate_limiter_for", side_effect=lambda slug: self.limiters.get(slug, RateLimiter(slug, daily_limit=None))))
            stack.enter_context(patch.object(rr, "_service_for_scope", side_effect=lambda source, default: self.services[source or SA]))
            stack.enter_context(patch.object(bulk, "AvailabilityRefreshService", return_value=self.services[WM]))
            if self.queue:
                return rr.process_refresh_queue(budget=self.budget, service=self.services[SA], limiter=self.limiters[SA])
            return bulk.process_watchlist_refresh(budget=self.budget, refresh_service=self.services[SA], limiter=self.limiters[SA])

    def movies_by_tmdb(self, tmdb_id):
        return next(movie for movie in self.movies if movie.tmdb_id == tmdb_id)

    def assert_spend(self, result, calls):
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(result["remaining_budget"], self.budget - calls)
        for slug in (SA, WM):
            expected = sum(source == slug for _, source, _ in self.calls)
            counter = ApiCallCounter.objects.filter(service=slug).first()
            ledger = ApiCallLedger.objects.filter(service=slug).first()
            self.assertEqual(counter.count if counter else 0, expected)
            self.assertEqual(ledger.count if ledger else 0, expected)

    def test_success_is_bounded_and_charged_once(self):
        for _ in range(4):
            self.add()
        result = self.run_refresh()
        self.assert_spend(result, self.budget)
        self.assertEqual(result["refreshed"], self.budget)
        self.assertGreater(result["skipped_budget_exhausted"], 0)

    def test_permanent_misses_are_bounded(self):
        for _ in range(4):
            self.add(UpstreamTitleNotFound("fixture miss"))
        result = self.run_refresh()
        self.assert_spend(result, self.budget)
        self.assertEqual(result["skipped_upstream_missing"], self.budget)
        self.assertEqual(RefreshRequest.objects.filter(outcome=Outcome.TITLE_NOT_FOUND).count(), self.budget)

    def test_timeouts_are_bounded_and_never_permanent(self):
        for _ in range(4):
            self.add(TimeoutError("fixture timeout"))
        result = self.run_refresh()
        self.assert_spend(result, self.budget)
        self.assertEqual(result["errors"], self.budget)
        self.assertFalse(report._permanent_miss_sources([m.pk for m in self.movies], country="NL"))

    def test_provider_exceptions_are_bounded(self):
        for _ in range(4):
            self.add(SourceFetchError("fixture provider failure"))
        result = self.run_refresh()
        self.assert_spend(result, self.budget)
        self.assertEqual(result["errors"], self.budget)

    def test_upstream_rate_refusals_consume_attempt_budget(self):
        for _ in range(4):
            self.add(SourceQuotaExceeded("fixture rate window", kind="rate_limit"))
        result = self.run_refresh()
        self.assert_spend(result, self.budget)
        self.assertEqual(result["skipped_rate_limited"], self.budget)

    def test_local_acquire_denial_is_free_and_other_source_can_run(self):
        self.add()
        for _ in range(3):
            self.add(source=WM)
        with patch.object(self.limiters[SA], "acquire", side_effect=RateLimitExceeded("fixture local refusal")):
            result = self.run_refresh()
        self.assert_spend(result, self.budget)
        self.assertEqual(result["refreshed"], self.budget)
        self.assertEqual(result["skipped_rate_limited"], 1)
        self.assertTrue(all(source == WM for _, source, _ in self.calls))

    def test_all_local_acquires_denied_leave_budget_untouched(self):
        self.add()
        with patch.object(self.limiters[SA], "acquire", side_effect=RateLimitExceeded("fixture local refusal")):
            result = self.run_refresh()
        self.assert_spend(result, 0)
        self.assertEqual(result["skipped_rate_limited"], 1)

    def test_mixed_outcomes_stop_at_attempt_limit(self):
        self.add(UpstreamTitleNotFound("fixture miss"))
        self.add(TimeoutError("fixture timeout"))
        self.add()
        result = self.run_refresh()
        self.assert_spend(result, self.budget)
        self.assertEqual(result["skipped_upstream_missing"], 1)
        self.assertEqual(result["errors"], self.budget - 1)
        self.assertEqual(result["refreshed"], 0)

    def test_free_skips_leave_capacity_for_actual_attempts(self):
        fresh = self.add()
        gated = self.add()
        negative = self.add()
        RefreshRequest.objects.create(movie=negative, source="", country="NL", status=Status.DONE, outcome=Outcome.TITLE_NOT_FOUND)
        for _ in range(3):
            self.add()
        result = self.run_refresh(fresh=[fresh.pk], gated=[gated.pk])
        self.assert_spend(result, self.budget)
        self.assertEqual(result["skipped_cached"], 1)
        self.assertEqual(result["skipped_policy_gated"], 1)
        self.assertEqual(result["skipped_upstream_missing"], 1)
        self.assertEqual(result["refreshed"], self.budget)
        run = RefreshRun.objects.latest("pk")
        self.assertEqual(run.rows_refreshed, result["refreshed"])
        self.assertEqual(run.rows_skipped, sum(result[key] for key in (
            "skipped_cached", "skipped_policy_gated", "skipped_upstream_missing",
            "skipped_rate_limited", "skipped_budget_exhausted",
        )))


class QueueBudgetOneTests(RunBudgetCases, TestCase):
    queue = True
    budget = 1


class QueueBudgetTwoTests(RunBudgetCases, TestCase):
    queue = True
    budget = 2


class SweepBudgetOneTests(RunBudgetCases, TestCase):
    queue = False
    budget = 1


class SweepBudgetTwoTests(RunBudgetCases, TestCase):
    queue = False
    budget = 2


class SweepNegativeEvidenceTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.movie = Movie.objects.create(tmdb_id=987001, title="Sweep missing movie")
        self.series = Movie.objects.create(tmdb_id=987002, title="Sweep missing series", content_type="series")
        WatchlistItem.objects.create(user_id=1, movie=self.movie)
        WatchlistItem.objects.create(user_id=2, movie=self.series)
        self.service = MagicMock()
        self.limiter = RateLimiter(SA, daily_limit=None)

    def sweep(self, country="NL", *, user_id=1, scopes=(SA,)):
        with patch.object(bulk, "resolve_source_country", return_value=country), \
             patch.object(bulk, "stale_refresh_scopes", return_value=list(scopes)), \
             patch.object(rr, "_metered_source_is_unnecessary", return_value=False):
            return bulk.process_watchlist_refresh(budget=2, user_id=user_id, refresh_service=self.service, limiter=self.limiter)

    def record_miss(self):
        self.service.refresh_content.side_effect = UpstreamTitleNotFound("fixture missing")
        result = self.sweep()
        self.assertEqual(result["skipped_upstream_missing"], 1)
        row = RefreshRequest.objects.get(movie=self.movie)
        self.assertEqual((row.source, row.country, row.status, row.outcome, row.priority, row.attempts),
                         (SA, "NL", Status.DONE, Outcome.TITLE_NOT_FOUND, RefreshPriority.BACKGROUND, 1))
        self.assertIsNotNone(row.completed_at)
        self.assertEqual(row.last_error, "")
        self.service.refresh_content.reset_mock()
        return row

    def test_second_sweep_reuses_negative_without_call_or_duplicate_evidence(self):
        self.record_miss()
        result = self.sweep()
        self.service.refresh_content.assert_not_called()
        self.assertEqual(result["remaining_budget"], 2)
        self.assertEqual(result["skipped_upstream_missing"], 1)
        self.assertEqual(result["skipped_cached"], 0)
        self.assertEqual(RefreshRequest.objects.count(), 1)
        self.assertEqual(RefreshRun.objects.latest("pk").rows_skipped, 1)

    def test_different_country_and_series_are_not_suppressed(self):
        self.record_miss()
        self.service.refresh_content.side_effect = None
        self.assertEqual(self.sweep("IT")["refreshed"], 1)
        self.service.refresh_content.assert_called_once_with(self.movie, country="IT")
        self.service.refresh_content.reset_mock()
        self.assertEqual(self.sweep(user_id=2)["refreshed"], 1)
        self.service.refresh_content.assert_called_once_with(self.series, country="NL")
        self.assertNotIn(self.series.pk, report._permanent_miss_sources([self.movie.pk, self.series.pk], country="NL"))

    def test_other_source_is_not_suppressed(self):
        self.record_miss()
        other = MagicMock()
        with patch.object(bulk, "AvailabilityRefreshService", return_value=other), \
             patch.object(bulk, "rate_limiter_for", return_value=RateLimiter(WM, daily_limit=None)):
            self.assertEqual(self.sweep(scopes=(WM,))["refreshed"], 1)
        other.refresh_content.assert_called_once_with(self.movie, country="NL")

    def test_newer_definitive_answers_supersede_sweep_negative_and_aliases(self):
        row = self.record_miss()
        # A legacy empty-source alias has the same history precedence.
        row.source = ""
        row.save(update_fields=["source"])
        self.assertEqual(self.sweep()["refreshed"], 0)
        for outcome in (Outcome.NO_OFFER, Outcome.OFFERS_FOUND):
            with self.subTest(outcome=outcome):
                newer = RefreshRequest.objects.create(movie=self.movie, source=SA, country="NL", status=Status.DONE, outcome=outcome)
                self.service.refresh_content.side_effect = None
                self.assertEqual(self.sweep()["refreshed"], 1)
                newer.delete()

    def test_explicit_request_can_recheck_sweep_miss(self):
        self.record_miss()
        request = RefreshRequest.objects.create(movie=self.movie, source="", country="NL", priority=RefreshPriority.EXPLICIT)
        self.service.refresh_content.side_effect = None
        with patch.object(rr, "_service_for_scope", return_value=self.service):
            result = rr.process_refresh_queue(budget=1, service=self.service, limiter=self.limiter)
        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(result["remaining_budget"], 0)
        self.service.refresh_content.assert_called_once_with(self.movie, country="NL")
        request.refresh_from_db()
        self.assertEqual(request.status, Status.DONE)

    def test_transient_sweep_error_remains_retryable(self):
        self.service.refresh_content.side_effect = TimeoutError("fixture timeout")
        self.assertEqual(self.sweep()["errors"], 1)
        self.assertFalse(RefreshRequest.objects.exists())
        self.service.refresh_content.side_effect = None
        self.assertEqual(self.sweep()["refreshed"], 1)

    def test_series_miss_records_only_that_movie_identity(self):
        self.service.refresh_content.side_effect = UpstreamTitleNotFound("fixture series missing")
        self.assertEqual(self.sweep(user_id=2)["skipped_upstream_missing"], 1)
        row = RefreshRequest.objects.get()
        self.assertEqual(row.movie_id, self.series.pk)
        self.assertEqual(row.movie.content_type, "series")
        self.assertNotIn(self.movie.pk, report._permanent_miss_sources([self.movie.pk, self.series.pk], country="NL"))
        self.service.refresh_content.side_effect = None
        self.assertEqual(self.sweep()["refreshed"], 1)

    def test_run_audit_matches_policy_and_new_upstream_miss_summary(self):
        WatchlistItem.objects.filter(movie=self.series).update(user_id=1)
        self.service.refresh_content.side_effect = UpstreamTitleNotFound("fixture missing")
        with patch.object(rr, "_metered_source_is_unnecessary", side_effect=lambda movie, *a, **kw: movie.pk == self.series.pk), \
             patch.object(bulk, "resolve_source_country", return_value="NL"), \
             patch.object(bulk, "stale_refresh_scopes", return_value=[SA]):
            result = bulk.process_watchlist_refresh(budget=2, refresh_service=self.service, limiter=self.limiter)
        self.assertEqual(result["skipped_policy_gated"], 1)
        self.assertEqual(result["skipped_upstream_missing"], 1)
        self.assertEqual(result["skipped_cached"], 0)
        self.assertEqual(result["remaining_budget"], 1)
        run = RefreshRun.objects.latest("pk")
        self.assertEqual(run.rows_refreshed, result["refreshed"])
        self.assertEqual(run.rows_skipped, 2)


class SharedBudgetSchedulingTests(TestCase):
    def check_queue_then_sweep(self, budget):
        queued, first, second = [Movie.objects.create(tmdb_id=988000 + i, title=f"Shared {i}") for i in range(3)]
        WatchlistItem.objects.bulk_create([WatchlistItem(user_id=1, movie=m) for m in (queued, first, second)])
        RefreshRequest.objects.create(movie=queued, source=SA, country="NL", priority=RefreshPriority.EXPLICIT)
        service = MagicMock()
        service.refresh_content.side_effect = [TimeoutError("fixture queued timeout"), None]
        with patch.object(bulk, "resolve_source_country", return_value="NL"), \
             patch.object(bulk, "stale_refresh_scopes", return_value=[SA]), \
             patch.object(rr, "_service_for_scope", return_value=service), \
             patch.object(rr, "_metered_source_is_unnecessary", return_value=False):
            result = bulk.process_watchlist_refresh(budget=budget, refresh_service=service, limiter=RateLimiter(SA, daily_limit=None))
        self.assertEqual(service.refresh_content.call_count, budget)
        self.assertEqual(result["remaining_budget"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["refreshed"], budget - 1)
        self.assertEqual(service.refresh_content.call_args_list[0].args, (queued,))
        self.assertEqual(sum(call.args == (queued,) for call in service.refresh_content.call_args_list), 1)

    def test_queue_failure_exhausts_shared_budget_one(self):
        self.check_queue_then_sweep(1)

    def test_queue_failure_leaves_only_one_sweep_attempt_at_budget_two(self):
        self.check_queue_then_sweep(2)

    def check_fairness(self, budget):
        oldest, explicit, deferred = [Movie.objects.create(tmdb_id=989000 + i, title=f"Fairness {i}") for i in range(3)]
        RefreshRequest.objects.create(movie=oldest, source=SA, country="NL", priority=RefreshPriority.BACKGROUND)
        for movie in (explicit, deferred):
            RefreshRequest.objects.create(movie=movie, source=SA, country="NL", priority=RefreshPriority.EXPLICIT)
        service = MagicMock()
        def fetch(movie, **kwargs):
            if movie == explicit:
                raise TimeoutError("fixture priority timeout")
        service.refresh_content.side_effect = fetch
        with patch.object(rr, "is_stale", return_value=True), \
             patch.object(rr, "_service_for_scope", return_value=service), \
             patch.object(rr, "_metered_source_is_unnecessary", return_value=False):
            result = rr.process_refresh_queue(budget=budget, service=service, limiter=RateLimiter(SA, daily_limit=None))
        self.assertEqual([call.args[0] for call in service.refresh_content.call_args_list], [explicit] + ([oldest] if budget == 2 else []))
        self.assertEqual(result["remaining_budget"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["refreshed"], budget - 1)
        self.assertEqual(RefreshRequest.objects.get(movie=deferred).status, Status.PENDING)

    def test_failed_strict_request_respects_single_unit_budget(self):
        self.check_fairness(1)

    def test_failed_strict_request_preserves_reserved_fairness_unit(self):
        self.check_fairness(2)
