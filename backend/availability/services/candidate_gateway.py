"""TMDb candidate discovery gateway with budget enforcement, caching, and bounded retries."""
from __future__ import annotations

import email.utils
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any, Optional

import requests
from django.conf import settings
from django.utils import timezone

from tracker.services.tmdb import TMDB_BASE, DEFAULT_TIMEOUT, TMDbClient
from ..domain.enums import ContentType
from ..models import TmdbDetailsCache, TmdbSearchCache

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

    stop_on_rate_limit: bool = False
    max_retries: int = 2
    max_backoff_seconds: float = 5.0
    positive_cache_ttl = timedelta(days=30)
    negative_cache_ttl = timedelta(days=7)
    endpoint_version = "v3"

    def __init__(
        self,
        client: Optional[TMDbClient] = None,
        budget: Optional[CandidateDiscoveryBudget] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
        allow_cache_writes: bool = True,
        locale: str = "en-US",
        min_request_interval_seconds: float = 0.0,
        monotonic_fn=time.monotonic,
    ):
        if min_request_interval_seconds < 0:
            raise ValueError("min_request_interval_seconds must be non-negative")
        self.client = client or TMDbClient()
        self.budget = budget
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn
        self.allow_cache_writes = allow_cache_writes
        self.locale = locale
        self.min_request_interval_seconds = min_request_interval_seconds
        self.monotonic_fn = monotonic_fn
        self._last_request_at: Optional[float] = None

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
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            return []
        if authorize_network and self.budget is None:
            raise ValueError("authorize_network=True requires a finite positive CandidateDiscoveryBudget")

        if content_type in (ContentType.SERIES, "tv"):
            target_type = ContentType.SERIES
            endpoint = "search/tv"
        else:
            target_type = ContentType.MOVIE
            endpoint = "search/movie"
        year_fingerprint = self._year_fingerprint(year)

        # A cache entry is usable only until its positive/negative TTL expires.
        cache_entry = TmdbSearchCache.objects.filter(
            query=normalized_query,
            year_fingerprint=year_fingerprint,
            content_type=target_type,
            locale=self.locale,
            endpoint_version=self.endpoint_version,
            expires_at__gt=timezone.now(),
        ).first()

        if cache_entry:
            if self.budget:
                self.budget.record_cache_hit()
            return [] if cache_entry.is_negative else (cache_entry.results or [])

        # If network calls are not authorized, return empty without making network requests
        if not authorize_network:
            return []

        # _fetch_search raises on transport/HTTP failures; only a successful
        # response, including a successful empty result, is eligible for caching.
        results = self._fetch_search(
            endpoint=endpoint,
            query=normalized_query,
            year=year,
            content_type=target_type,
        )

        if self.allow_cache_writes:
            is_negative = len(results) == 0
            TmdbSearchCache.objects.update_or_create(
                query=normalized_query,
                year_fingerprint=year_fingerprint,
                content_type=target_type,
                locale=self.locale,
                endpoint_version=self.endpoint_version,
                defaults={
                    "year": year,
                    "results": results,
                    "is_negative": is_negative,
                    "expires_at": timezone.now() + (
                        self.negative_cache_ttl if is_negative else self.positive_cache_ttl
                    ),
                },
            )

        return results

    def get_details(
        self,
        tmdb_id: int,
        content_type: str = ContentType.MOVIE,
        authorize_network: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Fetch TMDb item details using cached entries or budgeted HTTP dispatch.

        content_type must map to either movie (/movie/{id}) or series (/tv/{id}).
        When authorize_network is False, only cache hits return results; cache misses
        return None without executing network calls.
        """
        try:
            tmdb_id = int(tmdb_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid tmdb_id: {tmdb_id!r}") from exc

        if content_type in (ContentType.SERIES, "tv"):
            target_type = ContentType.SERIES
            endpoint = f"tv/{tmdb_id}"
        else:
            target_type = ContentType.MOVIE
            endpoint = f"movie/{tmdb_id}"

        if authorize_network and self.budget is None:
            raise ValueError("authorize_network=True requires a finite positive CandidateDiscoveryBudget")

        cache_entry = TmdbDetailsCache.objects.filter(
            tmdb_id=tmdb_id,
            content_type=target_type,
            locale=self.locale,
            endpoint_version=self.endpoint_version,
            expires_at__gt=timezone.now(),
        ).first()

        if cache_entry:
            if self.budget:
                self.budget.record_cache_hit()
            return None if cache_entry.is_negative else (cache_entry.details or None)

        if not authorize_network:
            return None

        details = self._fetch_details(
            endpoint=endpoint,
            tmdb_id=tmdb_id,
            content_type=target_type,
        )

        if self.allow_cache_writes:
            is_negative = details is None
            TmdbDetailsCache.objects.update_or_create(
                tmdb_id=tmdb_id,
                content_type=target_type,
                locale=self.locale,
                endpoint_version=self.endpoint_version,
                defaults={
                    "details": details or {},
                    "is_negative": is_negative,
                    "expires_at": timezone.now() + (
                        self.negative_cache_ttl if is_negative else self.positive_cache_ttl
                    ),
                },
            )

        return details

    def _fetch_details(
        self,
        endpoint: str,
        tmdb_id: int,
        content_type: str,
    ) -> Optional[dict[str, Any]]:
        params, headers = self.client._auth()
        params.update({"language": self.locale, "append_to_response": "credits"})
        url = f"{TMDB_BASE}/{endpoint}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            if self.budget:
                self.budget.reserve(label=f"{endpoint}")
            self._pace_request()

            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.client.timeout or DEFAULT_TIMEOUT,
                    allow_redirects=False,
                )
                if self.budget and resp.status_code >= 300 and resp.status_code != 404:
                    self.budget.record_failure()

                if 300 <= resp.status_code < 400:
                    logger.warning(
                        "tmdb_unexpected_redirect status=%d attempt=%d endpoint=%s",
                        resp.status_code,
                        attempt + 1,
                        endpoint,
                    )
                    error = requests.HTTPError(f"Unexpected TMDb redirect: HTTP {resp.status_code}")
                    error.response = resp
                    raise error

                # 404 indicates resource does not exist on TMDb: treat as negative response
                if resp.status_code == 404:
                    if self.budget:
                        self.budget.record_success()
                    return None

                # Permanent 4xx (except 429) must not retry
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    logger.warning(
                        "tmdb_client_error status=%d endpoint=%s",
                        resp.status_code,
                        endpoint,
                    )
                    resp.raise_for_status()

                if resp.status_code == 429 and self.stop_on_rate_limit:
                    resp.raise_for_status()

                if resp.status_code == 429:
                    retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
                    logger.warning(
                        "tmdb_rate_limited status=429 attempt=%d retry_after=%s endpoint=%s",
                        attempt + 1,
                        retry_after,
                        endpoint,
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
                    resp.raise_for_status()

                if resp.status_code in (500, 502, 503, 504):
                    logger.warning(
                        "tmdb_server_error status=%d attempt=%d endpoint=%s",
                        resp.status_code,
                        attempt + 1,
                        endpoint,
                    )
                    if attempt < self.max_retries:
                        if self.budget:
                            self.budget.record_retry()
                        backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                        self.sleep_fn(backoff)
                        continue
                    resp.raise_for_status()

                resp.raise_for_status()
                data = resp.json()
                if self.budget:
                    self.budget.record_success()
                return self._normalize_details(data, content_type)

            except requests.HTTPError as http_err:
                status = getattr(http_err.response, "status_code", None)
                if status == 429 and self.stop_on_rate_limit:
                    raise http_err
                if status and ((300 <= status < 400) or (400 <= status < 500 and status != 429)):
                    raise http_err
                last_error = http_err
                if attempt < self.max_retries:
                    if self.budget:
                        self.budget.record_retry()
                    backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                    self.sleep_fn(backoff)
                    continue
                raise http_err

            except requests.RequestException as exc:
                if self.budget:
                    self.budget.record_failure()
                last_error = exc
                logger.warning(
                    "tmdb_request_exception attempt=%d endpoint=%s error=%s",
                    attempt + 1,
                    endpoint,
                    type(exc).__name__,
                )
                if attempt < self.max_retries:
                    if self.budget:
                        self.budget.record_retry()
                    backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                    self.sleep_fn(backoff)
                    continue
                raise exc

        if last_error:
            raise last_error
        return None

    @staticmethod
    def _normalize_details(data: dict[str, Any], content_type: str) -> dict[str, Any]:
        title = data.get("title") or data.get("name") or data.get("original_title") or data.get("original_name") or ""
        release_date = data.get("release_date") or data.get("first_air_date")
        year = None
        if release_date and len(str(release_date)) >= 4:
            try:
                year = int(str(release_date)[:4])
            except ValueError:
                year = None

        genres = [{"id": g["id"], "name": g["name"]} for g in data.get("genres", []) if "id" in g]
        genre_ids = [g["id"] for g in genres]

        cast = []
        credits_data = data.get("credits", {})
        for c in (credits_data.get("cast", []) or [])[:15]:
            cast.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "character": c.get("character"),
            })

        director: list[str] = []
        if content_type == ContentType.SERIES:
            for c in data.get("created_by", []) or []:
                name = c.get("name")
                if name:
                    director.append(name)
        else:
            for crew in (credits_data.get("crew", []) or []):
                if crew.get("job") == "Director" and crew.get("name"):
                    director.append(crew["name"])

        seasons_data = []
        if content_type == ContentType.SERIES and "seasons" in data:
            for s in data.get("seasons", []) or []:
                seasons_data.append({
                    "season_number": s.get("season_number"),
                    "episode_count": s.get("episode_count"),
                    "name": s.get("name"),
                    "poster_path": s.get("poster_path"),
                })

        return {
            "tmdb_id": data.get("id"),
            "title": title,
            "original_title": data.get("original_title") or data.get("original_name") or "",
            "year": year,
            "content_type": content_type,
            "overview": data.get("overview") or "",
            "poster_path": data.get("poster_path") or "",
            "genre_ids": genre_ids,
            "genres": genres,
            "cast": cast,
            "director": director,
            "seasons_data": seasons_data,
            "runtime_minutes": data.get("runtime"),
            "tmdb_rating": data.get("vote_average"),
            "vote_count": data.get("vote_count"),
            "status": data.get("status"),
        }

    def _fetch_search(
        self,
        endpoint: str,
        query: str,
        year: Optional[int],
        content_type: str,
    ) -> list[dict[str, Any]]:
        params, headers = self.client._auth()
        params.update({"query": query, "include_adult": "false", "language": self.locale})
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
            self._pace_request()

            try:
                resp = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.client.timeout or DEFAULT_TIMEOUT,
                    allow_redirects=False,
                )
                if self.budget and resp.status_code >= 300:
                    self.budget.record_failure()

                # Reject redirects as a terminal failure: requests does not
                # raise for 3xx, and retrying would conceal an unexpected host.
                if 300 <= resp.status_code < 400:
                    logger.warning(
                        "tmdb_unexpected_redirect status=%d attempt=%d query=%s",
                        resp.status_code,
                        attempt + 1,
                        query,
                    )
                    error = requests.HTTPError(f"Unexpected TMDb redirect: HTTP {resp.status_code}")
                    error.response = resp
                    raise error

                # Permanent 4xx (except 429) must not retry
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    logger.warning(
                        "tmdb_client_error status=%d query=%s",
                        resp.status_code,
                        query,
                    )
                    resp.raise_for_status()

                if resp.status_code == 429 and self.stop_on_rate_limit:
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
                    resp.raise_for_status()

                resp.raise_for_status()
                data = resp.json()
                raw_results = data.get("results", [])
                if self.budget:
                    self.budget.record_success()
                normalized = [self._normalize_result(r, content_type) for r in raw_results]
                if data.get("total_pages", 1) > 1:
                    for result in normalized:
                        result["search_truncated"] = True
                return normalized

            except requests.HTTPError as http_err:
                status = getattr(http_err.response, "status_code", None)
                if status == 429 and self.stop_on_rate_limit:
                    raise http_err
                if status and ((300 <= status < 400) or (400 <= status < 500 and status != 429)):
                    raise http_err
                last_error = http_err
                if attempt < self.max_retries:
                    if self.budget:
                        self.budget.record_retry()
                    backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                    self.sleep_fn(backoff)
                    continue
                raise http_err

            except requests.RequestException as exc:
                if self.budget:
                    self.budget.record_failure()
                last_error = exc
                logger.warning(
                    "tmdb_request_exception attempt=%d query=%s error=%s",
                    attempt + 1,
                    query,
                    type(exc).__name__,
                )
                if attempt < self.max_retries:
                    if self.budget:
                        self.budget.record_retry()
                    backoff = min(0.5 * (2 ** attempt), self.max_backoff_seconds)
                    self.sleep_fn(backoff)
                    continue
                raise exc

        if last_error:
            raise last_error
        return []

    def _pace_request(self) -> None:
        """Apply configured spacing before each actual HTTP attempt."""
        if self._last_request_at is not None and self.min_request_interval_seconds:
            elapsed = self.monotonic_fn() - self._last_request_at
            remaining = self.min_request_interval_seconds - elapsed
            if remaining > 0:
                self.sleep_fn(remaining)
        self._last_request_at = self.monotonic_fn()

    @staticmethod
    def _normalize_query(query: str) -> str:
        # NFC makes canonically equivalent user input share a cache entry; \w
        # retains Unicode letters/digits rather than erasing non-ASCII titles.
        normalized = unicodedata.normalize("NFC", str(query or "")).casefold()
        return re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()

    @staticmethod
    def _year_fingerprint(year: Optional[int]) -> str:
        return f"year:{year}" if year is not None else "year:none"

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
            now = datetime.now(datetime_timezone.utc)
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
