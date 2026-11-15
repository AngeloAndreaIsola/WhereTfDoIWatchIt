"""Tests for the legacy ``_write_result`` helper + rate limiter."""
import datetime as dt
import threading

from django.db import connections
from django.test import TestCase, TransactionTestCase

from tracker.models import (
    ApiCallCounter,
    ApiCallLedger,
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Region,
)
from tracker.services.cache import AvailabilityCache
from tracker.services.rate_limit import (
    DAILY_LIMIT,
    MONTHLY_LIMIT,
    MonthlyRateLimitExceeded,
    RateLimitExceeded,
    RateLimiter,
    source_budget,
)
from tracker.services.streaming_api import StreamingOffer, StreamingResult


def _offer(country="US", service="Netflix", offer_type="subscription", quality="4k", audio=("en",), subs=("en",)):
    return StreamingOffer(
        country=country,
        service_id=service.lower(),
        service_name=service,
        offer_type=offer_type,
        quality=quality,
        audio_languages=list(audio),
        subtitle_languages=list(subs),
    )


class CacheTests(TestCase):
    """The legacy write helper ``AvailabilityCache._write_result``.

    Reads retired with the Phase C read-path migration: freshness and
    rate-limit behaviour now live in the event-driven refresh
    (``test_event_driven_refresh``) and the canonical query services.
    """

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=1, title="Cache Test", year=2020)
        self.cache = AvailabilityCache()

    def test_write_result_persists_offers_and_languages(self):
        result = StreamingResult(
            tmdb_id=1,
            offers=[_offer(), _offer(country="IT", service="Prime")],
        )
        written = self.cache._write_result(self.movie, result)

        self.assertEqual(written, 2)
        self.assertEqual(
            {a.region.country for a in self.movie.availabilities.all()},
            {"US", "IT"},
        )
        # Language entries created per movie+region.
        infos = LanguageInfo.objects.filter(movie=self.movie)
        self.assertEqual(infos.count(), 2)
        self.assertEqual(
            LanguageEntry.objects.filter(language_info__in=infos).count(), 4
        )  # 2 audio + 2 subs

    def test_write_result_is_idempotent(self):
        result = StreamingResult(
            tmdb_id=1,
            offers=[_offer(), _offer(country="IT", service="Prime")],
        )
        self.cache._write_result(self.movie, result)
        self.cache._write_result(self.movie, result)

        self.assertEqual(Availability.objects.filter(movie=self.movie).count(), 2)
        infos = LanguageInfo.objects.filter(movie=self.movie)
        self.assertEqual(infos.count(), 2)
        self.assertEqual(
            LanguageEntry.objects.filter(language_info__in=infos).count(), 4
        )


class RateLimiterTests(TestCase):
    def setUp(self):
        ApiCallCounter.objects.filter(service="rl_test").delete()

    def test_acquire_increments(self):
        limiter = RateLimiter("rl_test")
        limiter.acquire()
        limiter.acquire()
        counter = ApiCallCounter.objects.get(service="rl_test")
        self.assertEqual(counter.count, 2)

    def test_remaining_decreases(self):
        limiter = RateLimiter("rl_test")
        self.assertEqual(limiter.remaining(), DAILY_LIMIT)
        limiter.acquire()
        self.assertEqual(limiter.remaining(), DAILY_LIMIT - 1)

    def test_exceeds_raises(self):
        limiter = RateLimiter("rl_test")
        ApiCallCounter.objects.create(
            service="rl_test",
            call_date=dt.datetime.utcnow().date(),
            count=DAILY_LIMIT,
        )
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire()


class MonthlyRateLimiterTests(TestCase):
    """The rolling monthly guard beside the existing daily counter."""

    service = "rl_monthly_test"

    def setUp(self):
        ApiCallCounter.objects.filter(service=self.service).delete()
        ApiCallLedger.objects.filter(service=self.service).delete()

    def _limiter(self, *, daily=100, monthly=3, window=31):
        return RateLimiter(
            self.service,
            daily_limit=daily,
            monthly_limit=monthly,
            monthly_window_days=window,
        )

    def test_monthly_limit_exhausts_and_daily_still_reports_separately(self):
        limiter = self._limiter(monthly=3)

        for _ in range(3):
            limiter.acquire()

        with self.assertRaises(MonthlyRateLimitExceeded):
            limiter.acquire()

        self.assertEqual(limiter.remaining(), 97)
        self.assertEqual(limiter.monthly_used(), 3)
        self.assertEqual(limiter.monthly_remaining(), 0)
        self.assertTrue(limiter.is_exhausted())
        self.assertEqual(
            ApiCallLedger.objects.get(service=self.service).count, 3
        )

    def test_daily_limit_is_still_enforced_independently(self):
        limiter = self._limiter(daily=2, monthly=100)

        limiter.acquire()
        limiter.acquire()
        with self.assertRaises(RateLimitExceeded) as caught:
            limiter.acquire()

        self.assertNotIsInstance(caught.exception, MonthlyRateLimitExceeded)
        self.assertEqual(caught.exception.kind, "daily")
        self.assertEqual(limiter.monthly_used(), 2)

    def test_rolling_window_drops_a_reservation_older_than_the_window(self):
        today = dt.datetime.utcnow().date()
        ApiCallLedger.objects.create(
            service=self.service,
            day=today - dt.timedelta(days=31),
            count=1000,
        )
        limiter = self._limiter(monthly=1000)

        limiter.acquire()

        self.assertEqual(limiter.monthly_used(), 1)

    def test_rolling_window_includes_the_oldest_day_at_the_boundary(self):
        today = dt.datetime.utcnow().date()
        ApiCallLedger.objects.create(
            service=self.service,
            day=today - dt.timedelta(days=30),
            count=1000,
        )
        limiter = self._limiter(monthly=1000)

        with self.assertRaises(MonthlyRateLimitExceeded):
            limiter.acquire()

    def test_missing_ledger_seeds_from_today_counter_without_losing_calls(self):
        ApiCallCounter.objects.create(service=self.service, count=5)
        limiter = self._limiter(monthly=100)

        limiter.acquire()

        counter = ApiCallCounter.objects.get(service=self.service)
        ledger = ApiCallLedger.objects.get(service=self.service)
        self.assertEqual(counter.count, 6)
        self.assertEqual(ledger.count, 6)
        self.assertEqual(limiter.monthly_used(), 6)

    def test_inconsistent_counters_choose_the_larger_value(self):
        today = dt.datetime.utcnow().date()
        ApiCallCounter.objects.create(service=self.service, count=3)
        ApiCallLedger.objects.create(service=self.service, day=today, count=7)
        limiter = self._limiter(monthly=100)

        limiter.acquire()

        counter = ApiCallCounter.objects.get(service=self.service)
        ledger = ApiCallLedger.objects.get(service=self.service)
        self.assertEqual(counter.count, 8)
        self.assertEqual(ledger.count, 8)
        self.assertEqual(limiter.monthly_used(), 8)

    def test_missing_ledger_history_is_not_fabricated_as_zero(self):
        limiter = self._limiter(monthly=100)
        self.assertIsNone(limiter.monthly_accounting_since())

        limiter.acquire()

        self.assertIsNotNone(limiter.monthly_accounting_since())

    def test_first_window_is_unknown_prior_usage_not_a_fresh_allowance(self):
        """An empty ledger is a lower bound, not a provider quota reading.

        This is the initial-rollout case: calls made before the ledger existed
        cannot be reconstructed. The local count can legitimately read zero
        while the provider has already spent part of its period. Operators must
        treat ``monthly_accounting_since is None`` (or a partial history) as
        unknown prior usage, not as proof of a fresh 1,000-request allowance.
        """
        limiter = self._limiter(monthly=MONTHLY_LIMIT)

        self.assertEqual(limiter.monthly_used(), 0)
        self.assertEqual(limiter.monthly_remaining(), MONTHLY_LIMIT)
        self.assertIsNone(limiter.monthly_accounting_since())

    def test_ledger_ahead_of_daily_counter_denies_instead_of_granting_room(self):
        """Disagreement reconciles upward and cannot authorise a call."""
        today = dt.datetime.utcnow().date()
        ApiCallCounter.objects.create(service=self.service, count=0)
        ApiCallLedger.objects.create(
            service=self.service, day=today, count=3
        )
        limiter = self._limiter(monthly=3)

        with self.assertRaises(MonthlyRateLimitExceeded):
            limiter.acquire()

        self.assertEqual(limiter.monthly_used(), 3)
        self.assertEqual(
            ApiCallLedger.objects.get(service=self.service).count, 3
        )

    def test_daily_counter_ahead_of_ledger_denies_instead_of_granting_room(self):
        """The daily row can be ahead after a direct write or a failed replay."""
        ApiCallCounter.objects.create(service=self.service, count=3)
        limiter = self._limiter(monthly=3)

        with self.assertRaises(MonthlyRateLimitExceeded):
            limiter.acquire()

        self.assertEqual(limiter.monthly_used(), 3)
        self.assertEqual(
            ApiCallCounter.objects.get(service=self.service).count, 3
        )

    def test_reservation_committed_before_a_crash_stays_charged(self):
        """A crash after reservation but before HTTP must not refund the call.

        ``acquire`` commits the reservation and only then does the caller issue
        the request. An exception between those two points therefore cannot
        roll the count back; that is the conservative direction for a metered
        upstream (over-counting is safe, under-counting is not).
        """
        limiter = self._limiter(monthly=10)

        with self.assertRaises(RuntimeError):
            with source_budget(limiter, source=self.service):
                raise RuntimeError("worker died before the HTTP request")

        self.assertEqual(limiter.monthly_used(), 1)
        self.assertEqual(limiter.monthly_remaining(), 9)

    def test_negative_ledger_is_treated_as_exhausted_not_zero(self):
        today = dt.datetime.utcnow().date()
        ApiCallLedger.objects.create(
            service=self.service, day=today, count=-1
        )
        limiter = self._limiter(monthly=3)

        with self.assertRaises(MonthlyRateLimitExceeded):
            limiter.acquire()

        self.assertEqual(limiter.monthly_remaining(), 0)
        self.assertTrue(limiter.is_exhausted())

    def test_source_budget_reserves_once_for_the_context(self):
        limiter = self._limiter(monthly=10)

        with source_budget(limiter, source=self.service):
            # The client-side seam consults this context; a nested reservation
            # for the same source is intentionally a no-op.
            self.assertEqual(limiter.monthly_used(), 1)

        limiter.acquire()
        self.assertEqual(limiter.monthly_used(), 2)


class MonthlyBudgetConcurrencyTests(TransactionTestCase):
    """Concurrent workers cannot both spend the last monthly reservation."""

    def test_three_concurrent_acquires_allow_exactly_the_monthly_budget(self):
        if connections["default"].vendor != "postgresql":
            self.skipTest("row locking is only meaningful on PostgreSQL")

        service = "rl_monthly_concurrency"
        limiter = RateLimiter(
            service, daily_limit=100, monthly_limit=2, monthly_window_days=31
        )
        barrier = threading.Barrier(3)
        outcomes = []
        lock = threading.Lock()

        def worker():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                try:
                    limiter.acquire()
                    outcome = "ok"
                except RateLimitExceeded:
                    outcome = "denied"
                with lock:
                    outcomes.append(outcome)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(sorted(outcomes), ["denied", "ok", "ok"])
        self.assertEqual(
            ApiCallLedger.objects.get(service=service).count, 2
        )

    def test_reservation_survives_connection_loss_before_the_http_call(self):
        """A committed reservation must survive the worker dying before HTTP.

        ``TransactionTestCase`` is deliberate: unlike ``TestCase`` it does not
        wrap the test in an outer transaction, so this exercises the real
        commit in ``acquire``. Closing the connection afterwards stands in for
        a worker process that never reaches the upstream request.
        """
        if connections["default"].vendor != "postgresql":
            self.skipTest("row locking is only meaningful on PostgreSQL")

        service = "rl_monthly_crash"
        limiter = RateLimiter(
            service, daily_limit=100, monthly_limit=10
        )

        with self.assertRaises(RuntimeError):
            with source_budget(limiter, source=service):
                raise RuntimeError("worker died before the HTTP request")

        connections.close_all()
        self.assertEqual(
            RateLimiter(
                service, daily_limit=100, monthly_limit=10
            ).monthly_used(),
            1,
        )

    def test_high_contention_on_missing_rows_allows_exactly_the_budget(self):
        """The first-ever reservation race must not over-spend.

        The daily ``ApiCallCounter`` row is the mutex. When it does not exist,
        several workers can race through ``get_or_create``; the unique
        constraint and the ``SELECT ... FOR UPDATE`` retry must still leave
        exactly one winner per reservation. Repeating the round also catches a
        test that only passes because the row happened to exist already.
        """
        if connections["default"].vendor != "postgresql":
            self.skipTest("row locking is only meaningful on PostgreSQL")

        service = "rl_monthly_concurrency_high"
        workers_count = 8
        budget = 5
        rounds = 3

        for _round in range(rounds):
            ApiCallCounter.objects.filter(service=service).delete()
            ApiCallLedger.objects.filter(service=service).delete()
            limiter = RateLimiter(
                service,
                daily_limit=100,
                monthly_limit=budget,
                monthly_window_days=31,
            )
            barrier = threading.Barrier(workers_count)
            outcomes = []
            errors = []
            lock = threading.Lock()

            def worker():
                # Each thread gets its own Django connection; never reuse the
                # main thread's connection across a barrier.
                connections.close_all()
                try:
                    barrier.wait(timeout=10)
                    try:
                        limiter.acquire()
                        outcome = "ok"
                    except RateLimitExceeded:
                        outcome = "denied"
                    with lock:
                        outcomes.append(outcome)
                except Exception as exc:  # noqa: BLE001 - report, don't hang
                    with lock:
                        errors.append(repr(exc))
                finally:
                    connections.close_all()

            threads = [
                threading.Thread(target=worker) for _ in range(workers_count)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
                self.assertFalse(
                    thread.is_alive(), "a reservation worker did not finish"
                )

            self.assertEqual(errors, [])
            self.assertEqual(
                sorted(outcomes),
                ["denied"] * (workers_count - budget) + ["ok"] * budget,
            )
            self.assertEqual(
                ApiCallLedger.objects.get(service=service).count, budget
            )
            self.assertEqual(
                ApiCallCounter.objects.get(service=service).count, budget
            )
