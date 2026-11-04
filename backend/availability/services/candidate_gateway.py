"""TMDb candidate discovery gateway with budget enforcement, caching, and bounded retries."""
from __future__ import annotations

import email.utils
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from django.conf import settings

from tracker.services.tmdb import TMDB_BASE, DEFAULT_TIMEOUT, TMDbClient
from ..domain.enums import ContentType
from ..models import TmdbSearchCache

logger = logging.getLogger(__name__)


class CandidateDiscoveryBudgetExceeded(Exception):
    """Raised when the candidate discovery HTTP request budget is exhausted."""

    def __init__(self, limit: int, attempted: int):
        super().__init__(
            f"Candidate discovery HTTP budget exhausted: limit={limit}, attempted={attempted}"
        )
        self.limit = limit
        self.attempted = attempted


@dataclass
class CandidateDiscoveryBudget:
    """Enforces a finite HTTP request budget and records per-attempt accounting."""

    limit: int
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    cache_hits: int = 0

    def __post_init__(self):
        if not isinstance(self.limit, int) or self.limit <= 0:
            raise ValueError(
                f"CandidateDiscoveryBudget limit must be a positive integer, got {self.limit!r}"
            )

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.attempted)

    def reserve(self, label: str = "") -> None:
        """Reserve a single HTTP attempt before dispatch.

        Raises CandidateDiscoveryBudgetExceeded if limit reached.
        """
        if self.attempted >= self.limit:
            logger.warning(
                "candidate_discovery_budget_exhausted limit=%d attempted=%d label=%s",
                self.limit,
                self.attempted,
                label,
            )
            raise CandidateDiscoveryBudgetExceeded(limit=self.limit, attempted=self.attempted)
        self.attempted += 1

    def record_success(self) -> None:
        self.succeeded += 1

    def record_failure(self) -> None:
        self.failed += 1

    def record_retry(self) -> None:
        self.retried += 1

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "limit": self.limit,
            "attempted": self.attempted,
            "remaining": self.remaining,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "retried": self.retried,
            "cache_hits": self.cache_hits,
        }


class TMDbCandidateGateway:
    """Gateway for executing budgeted, cached, bounded-retry TMDb searches."""

    max_retries: int = 2
    max_backoff_seconds: float = 5.0

    def __init__(
        self,
        client: Optional[TMDbClient] = None,
        budget: Optional[CandidateDiscoveryBudget] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
        allow_cache_writes: bool = True,
    ):
        self.client = client or TMDbClient()
        self.budget = budget
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn
        self.allow_cache_writes = allow_cache_writes

    def search(
        self,
        query: str,
        year: Optional[int] = None,
        content_type: str = ContentType.MOVIE,
        authorize_network: bool = False,
    ) -> list[dict[str, Any]]:
        """Search TMDb using cached results or budgeted HTTP dispatch.

        content_type must map to either movie (/search/movie) or series (/search/tv).
        When authorize_network is False, only cache hits return results; cache misses
        return [] without executing network calls.
        """
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        if content_type in (ContentType.SERIES, "tv"):
            target_type = ContentType.SERIES
            endpoint = "search/tv"
        else:
            target_type = ContentType.MOVIE
            endpoint = "search/movie"

        # 1. Check positive/negative cache
        cache_entry = TmdbSearchCache.objects.filter(
            query__iexact=normalized_query,
            year=year,
            content_type=target_type,
        ).first()

        if cache_entry:
            if self.budget:
                self.budget.record_cache_hit()
            return [] if cache_entry.is_negative else (cache_entry.results or [])

        # If network calls are not authorized, return empty without making network requests
        if not authorize_network:
            return []

        # 2. Execute network request under budget
        results = self._fetch_search(
            endpoint=endpoint,
            query=normalized_query,
            year=year,
            content_type=target_type,
        )

        # 3. Store in positive/negative cache only if permitted
        if self.allow_cache_writes:
            is_negative = len(results) == 0
            TmdbSearchCache.objects.update_or_create(
                query=normalized_query,
                year=year,
                content_type=target_type,
                defaults={
                    "results": results,
                    "is_negative": is_negative,
                },
            )

        return results

    def _fetch_search(
        self,
        endpoint: str,
        query: str,
        year: Optional[int],
        content_type: str,
    ) -> list[dict[str, Any]]:
        params, headers = self.client._auth()
        params.update({"query": query, "include_adult": "false"})
        if year is not None:
            if content_type == ContentType.SERIES:
                params["first_air_date_year"] = year
            else:
                params["year"] = year

        url = f"{TMDB_BASE}/{endpoint}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            if self.budget:
                self.budget.reserve(label=f"{endpoint}:{query}")

            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.client.timeout or DEFAULT_TIMEOUT,
                    allow_redirects=False,
                )

                # Explicitly handle 3xx redirects as an error or count
                if 300 <= resp.status_code < 400:
                    logger.warning(
                        "tmdb_unexpected_redirect status=%d attempt=%d query=%s",
                        resp.status_code,
                        attempt + 1,
                        query,
                    )
                    resp.raise_for_status()

                # Permanent 4xx (except 429) must not retry
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    logger.warning(
                        "tmdb_client_error status=%d query=%s",
                        resp.status_code,
                        query,
                    )
                    if self.budget:
                        self.budget.record_failure()
                    resp.raise_for_status()

                if resp.status_code == 429:
                    retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
                    logger.warning(
                        "tmdb_rate_limited status=429 attempt=%d retry_after=%s query=%s",
                        attempt + 1,
                        retry_after,
                        query,
                    )
                    if attempt < self.max_retries:
                        if self.budget:
                            self.budget.record_retry()
                        backoff = min(
                            retry_after if retry_after is not None else (0.5 * (2 ** attempt)),
                            self.max_backoff_seconds,
                        )
                        self.sleep_fn(backoff)
                        continue
                    if self.budget:
                        self.budget.record_failure()
                    resp.raise_for_status()

                if resp.status_code in (500, 502, 503, 504):
                    logger.warning(
                        "tmdb_server_error status=%d attempt=%d query=%s",
                        resp.status_code,
                        attempt + 1,
                        query,
                    )
                    if attempt < self.max_retries:
                        if self.budget:
                            self.budget.record_retry()
                        backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                        self.sleep_fn(backoff)
                        continue
                    if self.budget:
                        self.budget.record_failure()
                    resp.raise_for_status()

                resp.raise_for_status()
                data = resp.json()
                raw_results = data.get("results", [])
                if self.budget:
                    self.budget.record_success()
                return [self._normalize_result(r, content_type) for r in raw_results]

            except requests.HTTPError as http_err:
                status = getattr(http_err.response, "status_code", None)
                if status and 400 <= status < 500 and status != 429:
                    raise http_err
                last_error = http_err
                if attempt < self.max_retries:
                    if self.budget:
                        self.budget.record_retry()
                    backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                    self.sleep_fn(backoff)
                    continue
                if self.budget:
                    self.budget.record_failure()
                raise http_err

            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "tmdb_request_exception attempt=%d query=%s error=%s",
                    attempt + 1,
                    query,
                    exc,
                )
                if attempt < self.max_retries:
                    if self.budget:
                        self.budget.record_retry()
                    backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                    self.sleep_fn(backoff)
                    continue
                if self.budget:
                    self.budget.record_failure()
                raise exc

        if self.budget:
            self.budget.record_failure()
        if last_error:
            raise last_error
        return []

    @staticmethod
    def _parse_retry_after(header_val: Optional[str]) -> Optional[float]:
        """Parse Retry-After header supporting both integer seconds and RFC 2822 date formats."""
        if not header_val:
            return None
        header_str = str(header_val).strip()
        try:
            return max(0.0, float(header_str))
        except (ValueError, TypeError):
            pass

        # Try parsing HTTP date
        try:
            parsed_date = email.utils.parsedate_to_datetime(header_str)
            now = datetime.now(timezone.utc)
            delta = (parsed_date - now).total_seconds()
            return max(0.0, delta)
        except Exception:
            return None

    @staticmethod
    def _normalize_result(raw: dict[str, Any], content_type: str) -> dict[str, Any]:
        title = (
            raw.get("title")
            or raw.get("name")
            or raw.get("original_title")
            or raw.get("original_name")
            or ""
        )
        release_date = raw.get("release_date") or raw.get("first_air_date")
        year = None
        if release_date and len(str(release_date)) >= 4:
            try:
                year = int(str(release_date)[:4])
            except ValueError:
                year = None

        return {
            "tmdb_id": raw.get("id"),
            "title": title,
            "original_title": raw.get("original_title") or raw.get("original_name") or "",
            "year": year,
            "content_type": content_type,
            "overview": raw.get("overview") or "",
            "poster_path": raw.get("poster_path") or "",
            "genre_ids": raw.get("genre_ids") or [],
            "popularity": raw.get("popularity"),
            "vote_average": raw.get("vote_average"),
            "vote_count": raw.get("vote_count"),
        }
