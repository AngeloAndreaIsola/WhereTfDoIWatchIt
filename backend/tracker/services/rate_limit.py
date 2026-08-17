"""Daily rate-limit guard for Streaming Availability API free tier (100 req/day).

Counter row lives in tracker.models.ApiCallCounter. Resets on calendar day (UTC).
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction

from ..models import ApiCallCounter

logger = logging.getLogger(__name__)

DAILY_LIMIT = 100


class RateLimitExceeded(Exception):
    """Raised when the daily API budget is exhausted."""


class RateLimiter:
    def __init__(self, service: str, daily_limit: int = DAILY_LIMIT):
        self.service = service
        self.daily_limit = daily_limit

    @transaction.atomic
    def acquire(self) -> None:
        """Reserve one call. Raises RateLimitExceeded if budget exhausted."""
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

    def remaining(self) -> int:
        counter = ApiCallCounter.objects.filter(service=self.service).first()
        if not counter or counter.call_date != dt.datetime.utcnow().date():
            return self.daily_limit
        return max(self.daily_limit - counter.count, 0)