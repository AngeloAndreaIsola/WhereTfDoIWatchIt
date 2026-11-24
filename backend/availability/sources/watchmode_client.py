"""Watchmode client: thin, bounded, quota-aware transport for the Watchmode API.

Transport only, like :mod:`.justwatch_client`: request construction, the HTTP
round trip, retry classification and quota accounting. It never interprets a
provider or a monetization type — that belongs to
:mod:`availability.sources.watchmode`.

Measured API shape (probed against the live account, September 2026)
--------------------------------------------------------------------
- Auth is the ``X-Api-Key`` header (no query parameter).
- ``GET /v1/search/?search_field=tmdb_movie_id&search_value=550`` returns a
  **dict** ``{"success": true, "title_results": [...], "people_results": [...]}``
  — *not* a list, and the field names are ``search_field`` /
  ``search_value``. A ``?query=`` search is rejected with HTTP 400.
- ``GET /v1/title/{id}/sources/?regions=NL,CA,JP`` returns a list of source
  rows: ``{source_id, name, type, region, web_url, format, price, ...}``. There
  is **no** ``offer_type`` and **no** ``streaming_type`` field — monetization
  lives in ``type`` (``sub``/``rent``/``buy``/``free``).
- Regions are **plan-scoped**: an unenabled region answers HTTP 400 with
  ``"XX is not enabled for your current plan."``. Measured enabled set on the
  current account: ``NL``, ``CA``, ``JP``. This must surface as an explicit
  unsupported-region error, never as an empty availability result.
- Quota is advertised in headers: ``x-account-quota``, ``x-account-quota-used``,
  ``x-ratelimit-limit``, ``x-ratelimit-remaining``. Measured 2026-10-21 on the
  exhausted account: ``x-ratelimit-limit: 120``, ``x-ratelimit-remaining: 0``,
  ``retry-after: 60`` and body
  ``{"success": false, "errorMessage": "Over plan quota on this API Key."}``.
  The account headers were absent. A retry 70 seconds later returned the same
  answer, so ``retry-after`` is **not** a reliable indicator that a 429 is a
  short rate window: the monthly plan quota can produce the same header.

Failure taxonomy, keeping "failed" distinguishable from "empty":

- :class:`WatchmodeTransportError` — network/5xx/408/425. Retryable within a
  bounded budget.
- :class:`WatchmodeQuotaExceeded` — HTTP 429 or an explicit quota message. It
  carries ``kind`` so the caller can tell a spent **plan** (terminal until the
  plan resets) from a short **rate limit** (retryable) without guessing from
  the status code alone.
- :class:`WatchmodeAuthError` — 401/403. Not retried: the key is wrong or the
  plan lacks access.
- :class:`WatchmodeRegionUnsupported` — a region the plan does not include.
- :class:`WatchmodeResponseError` — a 2xx body that is not the JSON envelope
  we require.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.watchmode.com/v1"

DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.8

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
AUTH_STATUS = frozenset({401, 403})

#: Header names carrying the account quota / rate-limit state.
QUOTA_HEADERS = ("x-account-quota", "x-account-quota-used")
RATELIMIT_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining")

#: Body markers that identify a *spent plan* rather than a short window.
#: ``"over plan quota"`` is the measured live wording (2026-10-21).
PLAN_QUOTA_MARKERS = (
    "over plan quota",
    "plan quota",
    "monthly quota",
    "current month",
)
#: Body markers that identify a short rate window.
RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "throttl",
)


class WatchmodeError(Exception):
    """Base for every Watchmode failure."""


class WatchmodeTransportError(WatchmodeError):
    """Network or retryable HTTP failure after the retry budget was spent."""


class WatchmodeQuotaExceeded(WatchmodeError):
    """Account quota or rate limit refused the request.

    ``kind`` is the part that matters to the scheduler:

    - ``"plan_quota"`` — the plan's request allowance is spent (measured body:
      ``"Over plan quota on this API Key."``). Terminal until the plan resets;
      retrying cannot succeed.
    - ``"local_budget"`` — the local pre-request guard refused before any HTTP
      call. Terminal for the same reason: retrying cannot spend anything until
      the local window moves.
    - ``"rate_limit"`` — a short window (``Retry-After``/rate headers) without
      evidence that the plan is exhausted. Retryable on a later tick.
    - ``"unknown"`` — a 429 we cannot classify. Callers treat it as retryable.

    ``quota`` is the response's quota/rate header snapshot, so the recorded
    error can distinguish the two conditions later. It never contains the API
    key.
    """

    def __init__(
        self,
        message,
        *,
        kind: str = "unknown",
        retry_after: int | None = None,
        status_code: int | None = None,
        quota: dict | None = None,
        detail: str = "",
    ):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.status_code = status_code
        self.quota = dict(quota or {})
        self.detail = detail or str(message)

    @property
    def terminal(self) -> bool:
        """True for a spent plan or a local budget stop, never a rate window."""
        return self.kind in ("plan_quota", "local_budget")


class WatchmodeAuthError(WatchmodeError):
    """401/403: the API key or the plan's access is insufficient."""


class WatchmodeRegionUnsupported(WatchmodeError):
    """The requested region is not part of the current plan.

    Explicit, and deliberately *not* an empty result: "we are not allowed to
    look" is a different fact from "there is nothing there".
    """

    def __init__(self, regions):
        self.regions = sorted(regions)
        super().__init__(
            f"Watchmode region(s) not enabled for this plan: {', '.join(self.regions)}"
        )


class WatchmodeResponseError(WatchmodeError):
    """A 2xx response whose body is not the JSON envelope we require."""


class WatchmodeSourceRow:
    """One provider/region row from ``/title/{id}/sources/``."""

    __slots__ = ("source_id", "name", "type", "region", "web_url", "format",
                 "price", "seasons", "episodes")

    def __init__(self, source_id=None, name="", type="", region="", web_url="",
                 format="", price=None, seasons=0, episodes=0):
        self.source_id = source_id
        self.name = name or ""
        self.type = type or ""
        self.region = region or ""
        self.web_url = web_url or ""
        self.format = format or ""
        self.price = price
        self.seasons = seasons
        self.episodes = episodes

    def __repr__(self):
        return (f"WatchmodeSourceRow(name={self.name!r}, type={self.type!r}, "
                f"region={self.region!r})")


class WatchmodeTitleResult:
    """One entry from ``/search/``."""

    __slots__ = ("id", "name", "type", "year", "tmdb_id", "imdb_id", "tmdb_type")

    def __init__(self, id=None, name="", type="", year=None, tmdb_id=None,
                 imdb_id=None, tmdb_type=None):
        self.id = id
        self.name = name or ""
        self.type = type or ""
        self.year = year
        self.tmdb_id = tmdb_id
        self.imdb_id = imdb_id
        self.tmdb_type = tmdb_type

    def __repr__(self):
        return (f"WatchmodeTitleResult(id={self.id!r}, name={self.name!r}, "
                f"tmdb_id={self.tmdb_id!r})")


def _requests_get(url, *, headers, timeout):
    return requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)


class WatchmodeClient:
    """Transport-only Watchmode client with bounded retries and quota checks."""

    def __init__(self, http_get=None, *, base_url=API_BASE, api_key=None,
                 timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES,
                 backoff=DEFAULT_BACKOFF, sleeper=None, budget=None):
        # ``http_get`` is the seam every test injects: callable
        # ``(url, headers, timeout)`` returning a requests-like response.
        self._http_get = http_get or _requests_get
        self.base_url = base_url.rstrip("/")
        # Read from the environment, never hard-coded; falls back to the
        # Django setting when one is configured.
        self.api_key = (
            api_key
            or os.environ.get("WATCHMODE_API_KEY", "")
            or _django_setting("WATCHMODE_API_KEY")
        )
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleeper or time.sleep
        #: Pre-request budget seam, called once per outbound attempt. ``None``
        #: uses the persistent local Watchmode guard; tests inject a no-op or
        #: a fake so ``SimpleTestCase`` never touches the database.
        self._budget = budget
        self.last_quota: dict[str, Optional[str]] = {}
        #: Every response header, lower-cased, for the *last* response. Kept
        #: separately from ``last_quota`` so diagnostics can include
        #: ``Retry-After`` without changing the documented quota snapshot.
        self.last_headers: dict[str, str] = {}

    # -- operations -------------------------------------------------------
    def sources(self) -> list[dict]:
        """Every provider Watchmode knows about (used to map ids)."""
        data = self._get("/sources/")
        return data if isinstance(data, list) else []

    def find_title(self, *, tmdb_id=None, tmdb_type="movie", imdb_id=None,
                   name=None):
        """Resolve a Watchmode title id from an external id.

        The search response is a dict, so the ``title_results`` list is read
        explicitly — treating it as a list is the easy way to get a
        ``TypeError`` in production.
        """
        if tmdb_id:
            field = "tmdb_movie_id" if tmdb_type == "movie" else "tmdb_tv_id"
            data = self._get(f"/search/?search_field={field}&search_value={tmdb_id}")
        elif imdb_id:
            data = self._get(f"/search/?search_field=imdb_id&search_value={imdb_id}")
        elif name:
            data = self._get(f"/search/?search_field=name&search_value={name}")
        else:
            raise ValueError("find_title requires tmdb_id, imdb_id or name")

        if isinstance(data, list):
            # Tolerated shape (not what the live API returns) so a future API
            # change degrades instead of crashing.
            results = data
        elif isinstance(data, dict):
            results = data.get("title_results") or []
        else:
            results = []
        return [_parse_title(row) for row in results if isinstance(row, dict)]

    def title_sources(self, title_id, regions) -> list[WatchmodeSourceRow]:
        """Source rows for *title_id* across *regions*, in one request."""
        wanted = sorted({str(r).strip().upper() for r in regions if str(r).strip()})
        if not wanted:
            return []
        data = self._get(
            f"/title/{title_id}/sources/?regions={','.join(wanted)}"
        )
        rows = data if isinstance(data, list) else []
        return [_parse_source_row(row) for row in rows if isinstance(row, dict)]

    # -- transport --------------------------------------------------------
    def _get(self, path):
        url = f"{self.base_url}{path}"
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            if not self.api_key:
                # No credential is a configuration failure, not an empty result.
                raise WatchmodeAuthError(
                    "WATCHMODE_API_KEY is not configured; refusing to call Watchmode"
                )
            self._reserve_call()
            try:
                response = self._http_get(url, headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as exc:
                from .base import SourceFetchStopped
                raise SourceFetchStopped("Watchmode request interrupted") from exc
            try:
                status = int(getattr(response, "status_code", 0))
            except (TypeError, ValueError):
                status = 0

            self._record_quota(getattr(response, "headers", {}) or {})

            if status in AUTH_STATUS or 300 <= status < 400:
                raise WatchmodeAuthError(
                    f"Watchmode HTTP {status}: API key rejected or plan lacks access"
                )

            message = self._status_message(response)

            if status == 429:
                # A 429 is a budget answer, not a transient fault: raise it
                # immediately with its headers and body intact. Retrying inside
                # this client cannot help (a plan quota lasts until the plan
                # resets, and a rate window is longer than our bounded retry
                # budget), and it would hide the distinction the caller needs.
                raise self._quota_error(status, message)

            if status in RETRYABLE_STATUS:
                # Some upstreams signal quota in a non-429 retryable status.
                # Preserve that signal rather than retrying a spent budget.
                if "quota" in message.lower() or "rate limit" in message.lower():
                    raise self._quota_error(status, message)
                last_error = WatchmodeTransportError(
                    f"Watchmode HTTP {status} (attempt {attempt}/{self.retries}): {message}"
                )
                logger.warning("watchmode_retry status=%s attempt=%s", status, attempt)
                self._sleep_backoff(attempt)
                continue

            if status >= 400:
                # "XX is not enabled for your current plan" is a plan fact, not
                # a transient error, and must not read as an empty result.
                if "not enabled" in message.lower():
                    raise WatchmodeRegionUnsupported(
                        [c.strip() for c in message.split() if c.strip().isalpha() and
                         len(c.strip()) == 2]
                    )
                raise WatchmodeTransportError(
                    f"Watchmode HTTP {status}: {message}"
                )

            return self._parse_envelope(response)

        raise WatchmodeTransportError(
            f"Watchmode transport failed after {self.retries} attempts: {last_error}"
        )

    def _reserve_call(self):
        """Reserve one outbound attempt, failing closed when the budget is spent.

        The reservation happens **before** the HTTP call and inside the retry
        loop, so every attempted request — including retries — is accounted
        for. A local refusal is translated into a Watchmode quota error with
        ``kind="local_budget"``; the source then exposes it as a terminal
        :class:`~.base.SourceQuotaExceeded`.
        """
        from tracker.services.rate_limit import (
            RateLimitExceeded,
            reserve_watchmode_call,
        )

        reserve = self._budget or reserve_watchmode_call
        try:
            reserve()
        except RateLimitExceeded as exc:
            detail = exc.diagnostic() if hasattr(exc, "diagnostic") else str(exc)
            raise WatchmodeQuotaExceeded(
                f"Watchmode local budget refused the request: {detail}",
                kind="local_budget",
                detail=detail,
            ) from exc

    def _headers(self):
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    def _sleep_backoff(self, attempt):
        self._sleep(self.backoff * (2 ** (attempt - 1)) + random.uniform(0, self.backoff))

    def _record_quota(self, headers):
        lowered = {}
        for key, value in dict(headers or {}).items():
            lowered[str(key).lower()] = value
        self.last_headers = lowered
        self.last_quota = {
            key: lowered.get(key) for key in QUOTA_HEADERS + RATELIMIT_HEADERS
        }

    def _quota_error(self, status, message):
        """Classify a quota/rate refusal from the body *and* the headers.

        The status code alone is not enough: the live account answers a spent
        monthly plan with the same 429 and a misleading ``Retry-After: 60``.
        The body (``"Over plan quota on this API Key."``) is the decisive
        evidence, and the account headers are checked first when present.
        """
        headers = dict(getattr(self, "last_headers", {}) or {})
        kind = self._classify_quota(message, headers)
        retry_after = _retry_after(headers)
        quota = {
            key: value for key, value in (self.last_quota or {}).items()
            if value is not None
        }
        if retry_after is not None:
            quota["retry-after"] = str(retry_after)
        detail = message or f"HTTP {status}"
        label = {
            "plan_quota": "plan quota exhausted",
            "rate_limit": "rate limit",
        }.get(kind, "quota refusal")
        return WatchmodeQuotaExceeded(
            f"Watchmode {label}: {detail}",
            kind=kind,
            retry_after=retry_after,
            status_code=status,
            quota=quota,
            detail=detail,
        )

    @staticmethod
    def _classify_quota(message, headers):
        text = str(message or "").lower()

        # Account quota headers are authoritative when the plan exposes them.
        limit = _as_int(headers.get("x-account-quota"))
        used = _as_int(headers.get("x-account-quota-used"))
        if limit is not None and used is not None and used >= limit:
            return "plan_quota"

        # The live 429 body is the only signal the exhausted account gives.
        if any(marker in text for marker in PLAN_QUOTA_MARKERS):
            return "plan_quota"
        if any(marker in text for marker in RATE_LIMIT_MARKERS):
            return "rate_limit"
        return "unknown"

    @staticmethod
    def _status_message(response):
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - any decode failure is the same class
            return str(getattr(response, "text", "") or "")[:200]
        if isinstance(body, dict):
            # ``errorMessage`` is the field the live API uses for quota
            # refusals; ``statusMessage``/``error`` are kept for the other
            # measured shapes. Dropping ``errorMessage`` is why the persisted
            # error used to read only "429".
            return str(
                body.get("errorMessage")
                or body.get("statusMessage")
                or body.get("error")
                or ""
            )[:200]
        return str(body)[:200]

    @staticmethod
    def _parse_envelope(response):
        headers = dict(getattr(response, "headers", {}) or {})
        content_type = str(headers.get("content-type", "")
                           or headers.get("Content-Type", ""))
        if "json" not in content_type.lower():
            raise WatchmodeResponseError(
                f"Watchmode returned non-JSON: content-type={content_type!r}"
            )
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise WatchmodeResponseError(
                f"Watchmode returned undecodable JSON: {exc}"
            ) from exc


def _retry_after(headers):
    """``Retry-After`` in seconds, or ``None`` when absent/malformed.

    Only the integer-seconds form is used; an HTTP-date is deliberately not
    guessed at. The value is diagnostic, not a sleep instruction: the caller
    decides whether the refusal is terminal or retryable.
    """
    value = str((headers or {}).get("retry-after") or "").strip()
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _django_setting(name):
    try:
        from django.conf import settings
    except Exception:  # noqa: BLE001 - importable only under Django
        return ""
    return getattr(settings, name, "") or ""


def _parse_source_row(row):
    return WatchmodeSourceRow(
        source_id=row.get("source_id"),
        name=row.get("name") or "",
        type=row.get("type") or "",
        region=row.get("region") or "",
        web_url=row.get("web_url") or "",
        format=row.get("format") or "",
        price=row.get("price"),
        seasons=row.get("seasons") or 0,
        episodes=row.get("episodes") or 0,
    )


def _parse_title(row):
    return WatchmodeTitleResult(
        id=row.get("id"),
        name=row.get("name") or "",
        type=row.get("type") or "",
        year=row.get("year"),
        tmdb_id=row.get("tmdb_id"),
        imdb_id=row.get("imdb_id"),
        tmdb_type=row.get("tmdb_type"),
    )