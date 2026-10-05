"""Per-source daily rate-limit guards.

Counter row lives in tracker.models.ApiCallCounter, one row **per source
slug**, so each provider's daily budget is tracked independently. Resets on
calendar day (UTC).

Why per source, not one global counter
-------------------------------------
Refresh All now fans out one ``RefreshRequest`` per (title, source), and a
provider's quota is *its own*: exhausting Streaming Availability's 100
calls/day says nothing about whether Prime Video, Tubi or BBC iPlayer can be
checked. A single shared limiter made one exhausted source look like a
blocked queue for everyone — which is exactly what the worker must never
report.

Only sources that actually declare a quota are metered. ``SOURCE_DAILY_LIMITS``
is the one place that knowledge lives; a slug absent from it (all catalogue
driven sources, plus the untargeted web sources) is *unmetered* and never
refuses a call. That keeps the guarantee the real provider gives us — "only
Streaming Availability has a hard daily budget" — instead of inventing a
100/day quota for scrapers that have none.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction

from ..models import ApiCallCounter

logger = logging.getLogger(__name__)

DAILY_LIMIT = 100

#: Declared daily quota per source slug. A slug missing from this mapping has
#: **no** declared quota: it is unmetered, ``acquire()`` never refuses, and
#: ``remaining()`` answers ``None`` (unbounded) rather than a fake number.
#:
#: ``streaming_availability`` is the only source on a metered upstream API
#: (100 req/day free tier). Every other registered source is either a
#: catalogue-driven scraper or an untargeted web lookup with no published
#: budget, so metering them would invent limits the providers do not impose.
SOURCE_DAILY_LIMITS: dict[str, int] = {
    "streaming_availability": DAILY_LIMIT,
}


class RateLimitExceeded(Exception):
    """Raised when a source's daily API budget is exhausted."""


class RateLimiter:
    """Daily budget guard for one source.

    ``daily_limit=None`` means the source declares no quota: every
    :meth:`acquire` succeeds and :meth:`remaining` answers ``None``. That is
    the honest reading of "no published budget" — never ``DAILY_LIMIT``,
    which would report headroom that does not exist.
    """

    def __init__(self, service: str, daily_limit: int | None = DAILY_LIMIT):
        self.service = service
        self.daily_limit = daily_limit

    @property
    def metered(self) -> bool:
        """True when this source has a declared daily budget."""
        return self.daily_limit is not None

    @transaction.atomic
    def acquire(self) -> None:
        """Reserve one call. Raises RateLimitExceeded if the budget is spent."""
        if self.daily_limit is None:
            return
        today = dt.datetime.utcnow().date()
        counter, _ = ApiCallCounter.objects.select_for_update().get_or_create(
            service=self.service,
        )
        if counter.call_date != today:
            counter.call_date = today
            counter.count = 0
        if counter.count >= self.daily_limit:
            raise RateLimitExceeded(
                f"{self.service}: daily limit of {self.daily_limit} calls reached"
            )
        counter.count += 1
        counter.save(update_fields=["call_date", "count"])

    def remaining(self) -> int | None:
        """Calls left today, or ``None`` when the source is unmetered."""
        if self.daily_limit is None:
            return None
        counter = ApiCallCounter.objects.filter(service=self.service).first()
        if not counter or counter.call_date != dt.datetime.utcnow().date():
            return self.daily_limit
        return max(self.daily_limit - counter.count, 0)

    def is_exhausted(self) -> bool:
        """True only for a metered source that has spent today's budget.

        An unmetered source is never exhausted, so a queue whose requests
        all point at unmetered sources is never reported as blocked.
        """
        remaining = self.remaining()
        return remaining is not None and remaining <= 0


def rate_limiter_for(source: str) -> RateLimiter:
    """The limiter that governs *source*, metered only if it declares a quota.

    The single place a source's budget is resolved, so the worker, the status
    endpoint and the drain can never disagree about which sources are limited.
    """
    return RateLimiter(source, SOURCE_DAILY_LIMITS.get(source))
