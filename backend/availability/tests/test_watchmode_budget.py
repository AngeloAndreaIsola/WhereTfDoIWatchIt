"""Watchmode local pre-request budget guard.

The provider's plan is metered, and the dashboard is the only authority on
remaining quota. These tests pin the *local* safety net:

- every outbound attempt is reserved, including retries;
- reservations persist across client instances;
- the guard refuses **before** HTTP, so a spent local budget cannot overrun;
- a provider 429 is classified and never retried automatically.
"""
from __future__ import annotations

import datetime as dt
import os
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from availability.sources.base import SourceQuotaExceeded
from availability.sources.watchmode_client import (
    WatchmodeClient,
    WatchmodeQuotaExceeded,
)
from availability.tests import aggregator_fixtures as fx
from tracker.models import ApiCallCounter, ApiCallLedger
from tracker.services import rate_limit


def _today():
    return dt.datetime.utcnow().date()


class WatchmodeBudgetConfigTests(SimpleTestCase):
    def test_default_keeps_headroom_below_the_plan(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WATCHMODE_MONTHLY_BUDGET", None)
            self.assertEqual(
                rate_limit._watchmode_monthly_budget(),
                rate_limit.WATCHMODE_MONTHLY_BUDGET_DEFAULT,
            )
        self.assertLess(
            rate_limit.WATCHMODE_MONTHLY_BUDGET_DEFAULT,
            rate_limit.WATCHMODE_PLAN_MONTHLY_LIMIT,
        )
        self.assertGreater(rate_limit.WATCHMODE_MONTHLY_HEADROOM, 0)

    def test_env_override_is_honoured(self):
        with patch.dict(os.environ, {"WATCHMODE_MONTHLY_BUDGET": "2500"}):
            self.assertEqual(rate_limit._watchmode_monthly_budget(), 2500)

    def test_an_override_above_the_plan_fails_closed(self):
        with patch.dict(os.environ, {"WATCHMODE_MONTHLY_BUDGET": "2501"}):
            with self.assertRaises(RuntimeError):
                rate_limit._watchmode_monthly_budget()

    def test_a_non_numeric_override_fails_closed(self):
        with patch.dict(os.environ, {"WATCHMODE_MONTHLY_BUDGET": "lots"}):
            with self.assertRaises(RuntimeError):
                rate_limit._watchmode_monthly_budget()

    def test_local_budget_is_terminal_for_the_scheduler(self):
        self.assertTrue(SourceQuotaExceeded("stop", kind="local_budget").terminal)
        self.assertTrue(SourceQuotaExceeded("plan", kind="plan_quota").terminal)
        self.assertFalse(SourceQuotaExceeded("window", kind="rate_limit").terminal)


class WatchmodeBudgetAccountingTests(TestCase):
    def setUp(self):
        self.calls = []
        self.addCleanup(self._restore_limits)
        self._saved_limits = dict(rate_limit.SOURCE_MONTHLY_LIMITS)

    def _restore_limits(self):
        rate_limit.SOURCE_MONTHLY_LIMITS.clear()
        rate_limit.SOURCE_MONTHLY_LIMITS.update(self._saved_limits)

    def _limit(self, value):
        rate_limit.SOURCE_MONTHLY_LIMITS["watchmode"] = value

    def _client(self, *responses):
        queue = list(responses)

        def http_get(url, headers=None, timeout=None):
            self.calls.append(url)
            item = queue.pop(0) if queue else fx.FakeResponse(payload=[])
            if isinstance(item, Exception):
                raise item
            return item

        return WatchmodeClient(
            http_get=http_get, api_key="k", sleeper=lambda s: None, retries=3,
        )

    def _counts(self):
        counter = ApiCallCounter.objects.filter(service="watchmode").first()
        ledger = ApiCallLedger.objects.filter(
            service="watchmode", day=_today()
        ).first()
        return (
            counter.count if counter else 0,
            ledger.count if ledger else 0,
        )

    def test_every_retry_consumes_a_reservation(self):
        self._limit(10)
        client = self._client(
            fx.FakeResponse(status_code=503, payload={"errorMessage": "flap"}),
            fx.FakeResponse(status_code=503, payload={"errorMessage": "flap"}),
            fx.FakeResponse(payload=[]),
        )
        self.assertEqual(client.sources(), [])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self._counts(), (3, 3))

    def test_the_guard_refuses_before_http_and_persists_across_clients(self):
        self._limit(2)
        first = self._client(fx.FakeResponse(payload=[]),
                             fx.FakeResponse(payload=[]))
        first.sources()
        first.sources()
        self.assertEqual(self._counts(), (2, 2))

        before = len(self.calls)
        second = self._client(fx.FakeResponse(payload=[]))
        with self.assertRaises(WatchmodeQuotaExceeded) as ctx:
            second.sources()

        self.assertEqual(ctx.exception.kind, "local_budget")
        self.assertTrue(ctx.exception.terminal)
        self.assertEqual(len(self.calls), before, "no HTTP after the hard stop")
        self.assertEqual(self._counts(), (2, 2))

    def test_a_provider_429_is_never_retried_and_is_counted_once(self):
        self._limit(10)
        client = self._client(fx.FakeResponse(
            status_code=429,
            payload=fx.WM_ERROR_PLAN_QUOTA,
            headers=fx.WM_PLAN_QUOTA_HEADERS,
        ))
        with self.assertRaises(WatchmodeQuotaExceeded) as ctx:
            client.sources()

        self.assertEqual(ctx.exception.kind, "plan_quota")
        self.assertTrue(ctx.exception.terminal)
        self.assertEqual(len(self.calls), 1, "a 429 must not be retried")
        self.assertEqual(self._counts(), (1, 1))
