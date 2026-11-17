"""Per-source daily and monthly rate-limit guards.

``ApiCallCounter`` remains the one-row-per-source **daily** counter. A second
table, ``ApiCallLedger``, records one count per source per UTC day so a monthly
budget can be summed over a rolling window without assuming that the upstream
plan resets on the first of the calendar month.

Why per source, not one global counter
-------------------------------------
Refresh All now fans out one ``RefreshRequest`` per (title, source), and a
provider's quota is *its own*: exhausting Streaming Availability's 100
calls/day says nothing about whether Prime Video, Tubi or BBC iPlayer can be
checked. A single shared limiter made one exhausted source look like a
blocked queue for everyone — which is exactly what the worker must never
report.

Only sources that actually declare a quota are metered. ``SOURCE_DAILY_LIMITS``
and ``SOURCE_MONTHLY_LIMITS`` are the one place that knowledge lives; a slug
absent from both (all catalogue driven sources, plus the untargeted web
sources) is *unmetered* and never refuses a call. Streaming Availability and
Watchmode are the two metered sources: the former has a daily and monthly cap,
the latter a monthly plan with a lower local hard stop.

Watchmode accounting counts **outbound attempts**, not logical lookups: each
retry and each endpoint call reserves before the HTTP request. The local ledger
is still a lower bound on provider usage until the dashboard confirms the
current period, so it protects against overrun but never authorises a recheck
on its own.

Monthly-period semantics
-----------------------
Streaming Availability documents a **1,000-request monthly hard cap** but the
provider's exact quota period has not been confirmed (calendar month vs the
RapidAPI billing-cycle anniversary). Assuming a calendar-month reset would
under-count across a billing boundary and could exceed the upstream cap. The
local guard is therefore a **rolling 31-day window**: at most 1,000 reservations
in any 31 consecutive UTC days. That is conservative for any calendar month or
billing cycle of 28–31 days, and it never increases request volume. Once the
provider's period is confirmed, ``monthly_window_days`` can be narrowed without
changing the accounting table.

The first deployment of the ledger cannot reconstruct calls made before it
existed. Those historical days are unknown, not zero; until the ledger has a
full 31-day history, the local count is a **lower bound** on provider usage.
The guard still refuses once the recorded count reaches 1,000 and never permits
a request it knows would cross the cap, but it cannot detect pre-ledger spend.
``monthly_accounting_since`` exposes the earliest ledger day so the limitation
is visible rather than implied. An operator who knows the current provider
usage should treat the local count as partial until 31 days have accumulated.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction

from ..models import ApiCallCounter, ApiCallLedger

logger = logging.getLogger(__name__)

DAILY_LIMIT = 100
MONTHLY_LIMIT = 1000

#: Conservative, provider-period-agnostic monthly window. See module docstring.
MONTHLY_WINDOW_DAYS = 31

#: Declared daily quota per source slug. A slug missing from this mapping has
#: **no** declared daily quota: it is unmetered, ``acquire()`` never refuses on
#: the daily dimension, and ``remaining()`` answers ``None`` (unbounded) rather
#: than a fake number.
#:
#: ``streaming_availability`` is the only source with a declared *daily* API
#: budget (100 req/day free tier). Watchmode has a monthly plan instead, so it
#: appears only in ``SOURCE_MONTHLY_LIMITS``. Every other registered source is
#: either a catalogue-driven scraper or an untargeted web lookup with no
#: published budget, so metering them would invent limits the providers do not
#: impose.
SOURCE_DAILY_LIMITS: dict[str, int] = {
    "streaming_availability": DAILY_LIMIT,
}

#: Declared monthly quota per source slug.
#:
#: ``streaming_availability``: 1,000 requests/month, measured/documented by the
#: provider.
#:
#: ``watchmode``: the plan allows 2,500 requests/month. The local hard stop is
#: deliberately lower (2,200 by default) so a retry burst or an unplanned
#: lookup cannot consume the last of the plan. ``WATCHMODE_MONTHLY_BUDGET``
#: overrides the local cap but is refused above the plan's 2,500.
#:
#: The ledger is not proof of the provider's own usage: it cannot see calls
#: made before the ledger existed, from another deployment, or from the
#: provider's dashboard. It is a local hard stop and a lower-bound counter.
WATCHMODE_PLAN_MONTHLY_LIMIT = 2500
WATCHMODE_MONTHLY_BUDGET_DEFAULT = 2200
WATCHMODE_MONTHLY_HEADROOM = (
    WATCHMODE_PLAN_MONTHLY_LIMIT - WATCHMODE_MONTHLY_BUDGET_DEFAULT
)


def _watchmode_monthly_budget() -> int:
    """Local hard stop for Watchmode, never above the provider's plan.

    An invalid value fails loudly at import rather than silently disabling the
    guard. Failing closed is the safe outcome for a metered API.
    """
    raw = os.environ.get("WATCHMODE_MONTHLY_BUDGET")
    if raw is None or str(raw).strip() == "":
        return WATCHMODE_MONTHLY_BUDGET_DEFAULT
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "WATCHMODE_MONTHLY_BUDGET must be an integer number of requests"
        ) from exc
    if not 1 <= value <= WATCHMODE_PLAN_MONTHLY_LIMIT:
        raise RuntimeError(
            "WATCHMODE_MONTHLY_BUDGET must be between 1 and "
            f"{WATCHMODE_PLAN_MONTHLY_LIMIT}"
        )
    return value


SOURCE_MONTHLY_LIMITS: dict[str, int] = {
    "streaming_availability": MONTHLY_LIMIT,
    "watchmode": _watchmode_monthly_budget(),
}

#: The metered slugs, named once so callers and diagnostics cannot drift.
STREAMING_AVAILABILITY_SLUG = "streaming_availability"
WATCHMODE_SLUG = "watchmode"


class RateLimitExceeded(Exception):
    """Raised when a source's local daily API budget is exhausted."""

    kind = "daily"

    def __init__(
        self,
        message,
        *,
        service: str | None = None,
        limit: int | None = None,
        used: int | None = None,
        remaining: int | None = 0,
    ):
        super().__init__(message)
        self.service = service
        self.limit = limit
        self.used = used
        self.remaining = remaining

    def diagnostic(self) -> str:
        """Stable, credential-free summary safe for logs and reports."""
        bits = [f"kind={self.kind}"]
        if self.service:
            bits.append(f"service={self.service}")
        if self.used is not None and self.limit is not None:
            bits.append(f"used={self.used}/{self.limit}")
        if self.remaining is not None:
            bits.append(f"remaining={self.remaining}")
        return f"{type(self).__name__}({', '.join(bits)}): {self}"


class MonthlyRateLimitExceeded(RateLimitExceeded):
    """Raised when the local rolling monthly budget is exhausted."""

    kind = "monthly"

    def __init__(
        self,
        message,
        *,
        service: str,
        limit: int,
        used: int,
        window_days: int,
        resets_at=None,
    ):
        super().__init__(
            message,
            service=service,
            limit=limit,
            used=used,
            remaining=0,
        )
        self.window_days = window_days
        self.resets_at = resets_at

    def diagnostic(self) -> str:
        bits = [
            f"kind={self.kind}",
            f"service={self.service}",
            f"used={self.used}/{self.limit}",
            f"window_days={self.window_days}",
        ]
        if self.resets_at is not None:
            bits.append(f"resets_at={self.resets_at.isoformat()}")
        return f"{type(self).__name__}({', '.join(bits)}): {self}"


class SourceQuotaCircuit:
    """Tick-scoped suppression for a source that proved a terminal quota block.

    Created fresh for every refresh tick and never persisted. A plan-quota
    refusal cannot succeed again before the plan resets, so once one source
    reports it, the rest of the tick must not keep asking that source. The next
    tick deliberately gets **one** new probe: a short-lived database flag would
    risk disabling a source after its quota reset, and would need expiry and
    recovery semantics that this architecture does not have. One refusal per
    tick is also cheap: it is a refusal, not a successful metered call.

    Only ``SourceQuotaExceeded.terminal`` (a spent plan or a local budget stop)
    may trip this circuit. A retryable rate-limit or unclassified 429 must not
    be recorded as proof that the monthly plan is spent.

    Scope is deliberately **one process-local tick**, not one global lock. Two
    concurrent ticks (for example two API processes, or the title and catalogue
    worker lanes) each get their own first probe; a terminal refusal suppressed
    in one process is not shared with another. That is the deliberate trade-off
    that keeps a provider reset detectable without persistence. All title-level
    Watchmode callers are covered because every production call to
    ``AvailabilityRefreshService.refresh_content`` is inside a coordinator that
    owns a circuit, and Watchmode cannot enter the catalogue path
    (``supports_catalogue_sync=False``).
    """

    def __init__(self):
        self._terminal: dict[str, str] = {}

    def trip(self, source: str, diagnostic: str) -> None:
        """Remember the first terminal diagnostic for *source* this tick."""
        if source and source not in self._terminal:
            self._terminal[source] = diagnostic or ""

    def is_tripped(self, source: str) -> bool:
        return bool(source) and source in self._terminal

    def diagnostic(self, source: str) -> str:
        return self._terminal.get(source, "")

    def tripped_sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._terminal))


class _BudgetCredit:
    """Mutable one-shot credit shared with parent contexts when nested."""

    __slots__ = ("source", "consumed")

    def __init__(self, source: str):
        self.source = source
        self.consumed = False


#: One-shot coordinator reservation credits in the current execution context.
#: The first outbound attempt consumes a credit; retries reserve separately.
#: Context-local means concurrent threads/workers do not share the credits.
_reserved_sources: ContextVar[tuple[_BudgetCredit, ...]] = ContextVar(
    "reserved_source_budgets", default=()
)


def budget_is_reserved(source: str) -> bool:
    """True when *source*'s budget was already reserved in this context."""
    return bool(source) and any(
        credit.source == source and not credit.consumed
        for credit in _reserved_sources.get()
    )


def consume_reserved_budget(source: str | None) -> bool:
    """Consume one coordinator credit for one outbound attempt, if available.

    This makes the coordinator reservation one-shot: the initial attempt reuses
    it and each retry reserves another unit. A boolean context flag would make
    every retry free.
    """
    if not source:
        return False
    for credit in reversed(_reserved_sources.get()):
        if credit.source == source and not credit.consumed:
            credit.consumed = True
            return True
    return False


@contextmanager
def source_budget(limiter: "RateLimiter", *, source: str | None = None):
    """Reserve one call from *limiter* and mark *source* as already reserved.

    The coordinator uses this around ``refresh_content``. A source client that
    supports central enforcement consumes the reservation for its first
    outbound attempt, then accounts for retries itself.
    """
    limiter.acquire()
    reserve_for = source or getattr(limiter, "service", "") or ""
    credit = _BudgetCredit(reserve_for)
    token = _reserved_sources.set(_reserved_sources.get() + (credit,))
    try:
        yield limiter
    finally:
        _reserved_sources.reset(token)


class RateLimiter:
    """Daily and optional rolling-monthly budget guard for one source.

    ``daily_limit=None`` and ``monthly_limit=None`` means the source declares
    no quota: every :meth:`acquire` succeeds and :meth:`remaining` answers
    ``None``. That is the honest reading of "no published budget" — never
    ``DAILY_LIMIT``, which would report headroom that does not exist.

    The daily row in ``ApiCallCounter`` is also the mutex for the monthly
    ledger. Every reservation locks it first, so two workers can never both
    observe "below the cap" and then each add a call. The ledger is only written
    while that row lock is held.
    """

    def __init__(
        self,
        service: str,
        daily_limit: int | None = DAILY_LIMIT,
        monthly_limit: int | None = None,
        *,
        monthly_window_days: int = MONTHLY_WINDOW_DAYS,
    ):
        self.service = service
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self.monthly_window_days = max(int(monthly_window_days), 1)

    @property
    def metered(self) -> bool:
        """True when this source has a declared daily or monthly budget."""
        return self.daily_limit is not None or self.monthly_limit is not None

    @staticmethod
    def _today() -> dt.date:
        return dt.datetime.utcnow().date()

    def _window_start(self, today: dt.date) -> dt.date:
        return today - dt.timedelta(days=self.monthly_window_days - 1)

    def _window_rows(self, today: dt.date):
        return ApiCallLedger.objects.filter(
            service=self.service,
            day__gte=self._window_start(today),
            day__lte=today,
        ).values_list("day", "count")

    def _lock_today_ledger(
        self, counter: ApiCallCounter, today: dt.date
    ) -> ApiCallLedger:
        """Lock/create today's ledger row and reconcile it with the daily row.

        The daily row was locked by the caller, so this reconciliation is
        serialised with every other reservation for this service. When the two
        counters disagree we keep the **larger** value: under-counting would
        permit a request past the upstream cap, while over-counting only makes
        the local guard stricter.
        """
        ledger, created = ApiCallLedger.objects.select_for_update().get_or_create(
            service=self.service,
            day=today,
            defaults={"count": max(counter.count, 0)},
        )
        if created:
            return ledger
        if ledger.count < 0 or counter.count < 0:
            # Negative accounting is corrupt, not a licence to spend. Leave the
            # values untouched so ``_monthly_used`` can fail closed below.
            return ledger
        if ledger.count < counter.count:
            ledger.count = counter.count
            ledger.save(update_fields=["count"])
        elif counter.count < ledger.count:
            counter.count = ledger.count
            counter.save(update_fields=["count"])
        return ledger

    def _monthly_used(
        self,
        today: dt.date,
        counter: ApiCallCounter | None = None,
    ) -> int:
        """Conservatively sum the rolling window.

        Ledger rows are authoritative. If today's ledger row is missing (for
        example immediately after deploying the ledger, or a direct write that
        predates it), today's daily counter is added instead so those calls are
        not silently forgotten. Historical days before the ledger existed
        remain unknown; they are never fabricated as zero.
        """
        rows = list(self._window_rows(today))
        if counter is not None and counter.count < 0:
            # Corrupt accounting is treated as exhausted, never as zero.
            return self.monthly_limit
        if any(int(count or 0) < 0 for _day, count in rows):
            return self.monthly_limit
        total = sum(max(int(count or 0), 0) for _day, count in rows)
        if counter is not None and counter.call_date == today:
            today_count = dict(rows).get(today)
            if today_count is None:
                total += max(counter.count, 0)
            elif counter.count > today_count:
                total += counter.count - today_count
        return total

    @transaction.atomic
    def acquire(self) -> None:
        """Reserve one call. Raises if the daily or monthly budget is spent."""
        if self.daily_limit is None and self.monthly_limit is None:
            return

        today = self._today()
        counter, _ = ApiCallCounter.objects.select_for_update().get_or_create(
            service=self.service,
        )
        if counter.call_date != today:
            counter.call_date = today
            counter.count = 0

        if counter.count < 0:
            logger.error(
                "api_call_counter_negative service=%s count=%s",
                self.service,
                counter.count,
            )
            if self.daily_limit is not None:
                raise RateLimitExceeded(
                    f"{self.service}: daily counter is negative; refusing to "
                    "spend from unreliable accounting",
                    service=self.service,
                    limit=self.daily_limit,
                    used=counter.count,
                    remaining=0,
                )

        if self.daily_limit is not None and counter.count >= self.daily_limit:
            raise RateLimitExceeded(
                f"{self.service}: daily limit of {self.daily_limit} calls reached",
                service=self.service,
                limit=self.daily_limit,
                used=counter.count,
                remaining=0,
            )

        ledger = None
        if self.monthly_limit is not None:
            ledger = self._lock_today_ledger(counter, today)
            used = self._monthly_used(today, counter)
            if used >= self.monthly_limit:
                raise MonthlyRateLimitExceeded(
                    f"{self.service}: monthly limit of {self.monthly_limit} calls "
                    f"reached over a {self.monthly_window_days}-day window",
                    service=self.service,
                    limit=self.monthly_limit,
                    used=used,
                    window_days=self.monthly_window_days,
                    resets_at=self.monthly_resets_at(today),
                )

        counter.count += 1
        counter.save(update_fields=["call_date", "count"])
        if ledger is not None:
            ledger.count += 1
            ledger.save(update_fields=["count"])

    def remaining(self) -> int | None:
        """Calls left today, or ``None`` when no daily budget is declared."""
        if self.daily_limit is None:
            return None
        counter = ApiCallCounter.objects.filter(service=self.service).first()
        if not counter or counter.call_date != self._today():
            return self.daily_limit
        return max(self.daily_limit - counter.count, 0)

    def monthly_used(self) -> int | None:
        """Reservations in the rolling monthly window, or ``None`` if unset."""
        if self.monthly_limit is None:
            return None
        today = self._today()
        counter = ApiCallCounter.objects.filter(service=self.service).first()
        return self._monthly_used(today, counter)

    def monthly_remaining(self) -> int | None:
        """Calls left in the rolling monthly window, or ``None`` if unset."""
        used = self.monthly_used()
        if used is None:
            return None
        return max(self.monthly_limit - used, 0)

    def monthly_accounting_since(self) -> dt.date | None:
        """Earliest ledger day, exposing how much history the guard can see."""
        if self.monthly_limit is None:
            return None
        return (
            ApiCallLedger.objects.filter(service=self.service)
            .order_by("day")
            .values_list("day", flat=True)
            .first()
        )

    def monthly_resets_at(self, today: dt.date | None = None):
        """When the oldest reservation leaves the rolling window, if exhausted.

        Returns a timezone-aware UTC datetime, or ``None`` when nothing is
        exhausted/recorded. This is a local-window reset, not a claim about the
        provider's unconfirmed billing period.
        """
        if self.monthly_limit is None:
            return None
        today = today or self._today()
        used = self._monthly_used(today, ApiCallCounter.objects.filter(
            service=self.service
        ).first())
        if used < self.monthly_limit:
            return None
        oldest = (
            ApiCallLedger.objects.filter(
                service=self.service,
                day__gte=self._window_start(today),
                day__lte=today,
                count__gt=0,
            )
            .order_by("day")
            .values_list("day", flat=True)
            .first()
        )
        if oldest is None:
            return None
        reset_day = oldest + dt.timedelta(days=self.monthly_window_days)
        return dt.datetime(
            reset_day.year, reset_day.month, reset_day.day,
            tzinfo=dt.timezone.utc,
        )

    def daily_exhausted(self) -> bool:
        remaining = self.remaining()
        return remaining is not None and remaining <= 0

    def monthly_exhausted(self) -> bool:
        remaining = self.monthly_remaining()
        return remaining is not None and remaining <= 0

    def is_exhausted(self) -> bool:
        """True only when a declared daily or monthly budget is spent.

        An unmetered source is never exhausted, so a queue whose requests
        all point at unmetered sources is never reported as blocked.
        """
        if self.daily_exhausted():
            return True
        return self.monthly_exhausted()

    def diagnostic(self) -> dict:
        """Safe, non-secret quota state for diagnostics and reports."""
        return {
            "service": self.service,
            "daily": {
                "used": self.daily_limit - self.remaining()
                if self.daily_limit is not None and self.remaining() is not None
                else None,
                "limit": self.daily_limit,
                "remaining": self.remaining(),
            },
            "monthly": {
                "used": self.monthly_used(),
                "limit": self.monthly_limit,
                "remaining": self.monthly_remaining(),
                "window_days": self.monthly_window_days,
                "accounting_since": self.monthly_accounting_since(),
                "resets_at": self.monthly_resets_at(),
            },
            "exhausted": self.is_exhausted(),
        }


def rate_limiter_for(source: str) -> RateLimiter:
    """The limiter that governs *source*, metered only if it declares a quota.

    The single place a source's budget is resolved, so the worker, the status
    endpoint and the drain can never disagree about which sources are limited.
    """
    return RateLimiter(
        source,
        SOURCE_DAILY_LIMITS.get(source),
        SOURCE_MONTHLY_LIMITS.get(source),
    )


def reserve_streaming_availability_call() -> None:
    """Central SA budget seam used by the HTTP client.

    Direct callers of ``StreamingAvailabilityClient.get_availability`` cannot
    bypass the monthly or daily cap: the client calls this immediately before
    each upstream request. When the coordinator already reserved the same call,
    the context flag makes this a no-op instead of a second charge.
    """
    if consume_reserved_budget(STREAMING_AVAILABILITY_SLUG):
        return
    rate_limiter_for(STREAMING_AVAILABILITY_SLUG).acquire()


def reserve_watchmode_call() -> None:
    """Reserve one **outbound attempt** immediately before a Watchmode request.

    The Watchmode client calls this inside its retry loop, not once per logical
    lookup: a 5xx retry or a second page costs another upstream request and
    must consume another unit. A spent local budget raises
    :class:`RateLimitExceeded` before the HTTP call is made, so the guard is a
    true stop rather than an after-the-fact counter.
    """
    if consume_reserved_budget(WATCHMODE_SLUG):
        return
    rate_limiter_for(WATCHMODE_SLUG).acquire()
