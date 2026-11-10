"""Rights-reviewed public-domain archive collector.

Ingests verified public-domain films and serials from trusted metadata catalogues
(Internet Archive and Library of Congress) and links to their original landing pages.

Contract and invariants:
- Provider slug: `internet_archive` (or `public_domain_archive` as supported).
- Validates per-item rights: never asserts public-domain status based solely on playlist
  or collection membership.
- Preserves rights provenance (license URL, original landing page URL, territorial scope).
- Ingests URLs and metadata only, no streaming content or media file downloads.
- Idempotent catalog sync with cursor checkpointing and batch limits.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

import requests

from ..domain.enums import ContentType, OfferType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
    normalize_content_type,
    normalize_country,
    normalize_languages,
    normalize_offer_type,
)
from ..services.public_domain_catalogue import (
    RightsStatus,
    SourceBudgetTracker,
    TerritorialScope,
    build_provenance_record,
    evaluate_item_rights,
)
from . import checkpoints
from .base import (
    AvailabilitySource,
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
)

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "internet_archive"
DEFAULT_COUNTRY = "US"
SOURCE_SLUG = "public_domain_archive"
SOURCE_VERSION = "pd-archive-v1"
CURSOR_VERSION = 1
PART_ITEMS = "items"
DEFAULT_BATCH_ITEMS = 50
DEFAULT_TIMEOUT = 30


class PublicDomainArchiveClient:
    """HTTP client for public domain metadata catalogues.

    Accepts an injectable ``fetcher`` callable for offline deterministic test fixtures.
    """

    def __init__(self, *, fetcher: Optional[Callable[[str, Optional[dict]], dict]] = None, timeout: int = DEFAULT_TIMEOUT):
        self.fetcher = fetcher
        self.timeout = timeout

    def fetch_catalogue(self, endpoint: str, cursor: Optional[str] = None, params: Optional[dict] = None) -> dict:
        if self.fetcher is not None:
            try:
                request_params = dict(params or {})
                if cursor:
                    request_params["cursor"] = cursor
                return self.fetcher(endpoint, request_params)
            except SourceFetchError:
                raise
            except Exception as exc:
                raise SourceFetchError(f"PublicDomainArchive fetch failed: {exc}") from exc

        # Default live HTTP fetcher
        try:
            req_params = dict(params or {})
            if cursor:
                req_params["cursor"] = cursor
            resp = requests.get(
                endpoint,
                params=req_params,
                headers={"User-Agent": "WhereTfDoIWatchIt-PublicDomainCollector/1.0"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SourceFetchError(f"PublicDomainArchive HTTP error: {exc}") from exc

        if resp.status_code != 200:
            raise SourceFetchError(f"PublicDomainArchive returned HTTP {resp.status_code}")

        try:
            return resp.json()
        except ValueError as exc:
            raise SourceFetchError("PublicDomainArchive returned non-JSON payload") from exc


class PublicDomainArchiveSource(AvailabilitySource):
    """Catalogue-driven collector for rights-reviewed public domain films and media."""

    slug = SOURCE_SLUG
    name = "Public Domain Archive"
    source_type = SourceType.API
    capabilities = SourceCapabilities(
        supported_providers=frozenset({DEFAULT_PROVIDER}),
        supported_countries=frozenset({DEFAULT_COUNTRY}),
        supported_content_types=frozenset({ContentType.MOVIE, ContentType.SERIES, ContentType.EPISODE}),
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        supports_resumable_catalogue_sync=True,
        supports_authoritative_snapshots=False,
        supports_tmdb_ids=False,
        supports_seasons=True,
        supports_episodes=True,
        supports_languages=True,
    )

    def __init__(
        self,
        client: Optional[PublicDomainArchiveClient] = None,
        batch_size: int = DEFAULT_BATCH_ITEMS,
        provider_slug: str = DEFAULT_PROVIDER,
        catalogue_endpoint: str = "https://archive.org/advancedsearch.php",
    ):
        self.client = client or PublicDomainArchiveClient()
        self.batch_size = max(1, int(batch_size))
        self.provider_slug = provider_slug
        self.catalogue_endpoint = catalogue_endpoint
        self.budget_tracker = SourceBudgetTracker(max_requests=100)

    def fetch(
        self,
        content: Optional[ContentRequest] = None,
        *,
        provider: Optional[str] = None,
        country: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                f"{self.slug} is catalogue-driven: fetch the catalogue with content=None"
            )

        resume = checkpoints.decode(
            cursor,
            version=CURSOR_VERSION,
            parts=(PART_ITEMS,),
            log_prefix=self.slug,
        )
        anchor = resume.anchor(PART_ITEMS)

        self.budget_tracker.record_request(success=True)
        payload = self.client.fetch_catalogue(self.catalogue_endpoint, cursor=anchor)
        if not isinstance(payload, dict):
            raise SourceFetchError(f"Unexpected payload type from {self.slug}")

        raw_items = payload.get("items")
        if raw_items is None and "response" in payload:
            raw_items = payload.get("response", {}).get("docs", [])
        if not isinstance(raw_items, list):
            raise SourceFetchError(f"Malformed catalogue payload in {self.slug}: missing items list")

        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        walked = 0
        last_id = anchor

        for raw in raw_items:
            if not isinstance(raw, dict):
                continue

            self.budget_tracker.items_evaluated += 1
            evaluation = evaluate_item_rights(raw)
            identifier = str(raw.get("identifier") or "").strip()
            title = str(raw.get("title") or "").strip()

            # Skip items with no title or identifier
            if not identifier or not title:
                logger.warning(
                    "%s_item_skipped reason=missing_identifier_or_title item=%r",
                    self.slug,
                    raw,
                )
                self.budget_tracker.rejected_items += 1
                continue

            # Check rights verification
            if not evaluation.is_watchable_public_domain:
                if evaluation.status == RightsStatus.AMBIGUOUS_UNKNOWN:
                    self.budget_tracker.ambiguous_items_flagged += 1
                    logger.info(
                        "%s_rights_ambiguous identifier=%s title=%r",
                        self.slug,
                        identifier,
                        title,
                    )
                else:
                    self.budget_tracker.rejected_items += 1
                    logger.info(
                        "%s_rights_rejected identifier=%s status=%s",
                        self.slug,
                        identifier,
                        evaluation.status.value,
                    )
                continue

            # Content has passed rights verification
            provenance = build_provenance_record(raw, evaluation)
            walked += 1
            last_id = identifier
            self.budget_tracker.pd_items_accepted += 1

            # Determine content type and ordinals
            raw_type = str(raw.get("content_type") or raw.get("mediatype") or "movie").strip().lower()
            if raw_type == "episode" or raw.get("episode_number") is not None:
                content_type = ContentType.EPISODE
            elif raw_type in ("series", "show"):
                content_type = ContentType.SERIES
            else:
                content_type = ContentType.MOVIE

            season_number = _as_int(raw.get("season_number"))
            episode_number = _as_int(raw.get("episode_number"))
            release_date = _parse_date(raw.get("year") or raw.get("publicdate"))

            # Determine applicable country
            target_country = (
                "US"
                if evaluation.territorial_scope == TerritorialScope.US_ONLY
                else (country or DEFAULT_COUNTRY)
            )

            discovered = DiscoveredContent(
                provider_id=self.provider_slug,
                provider_content_id=identifier,
                content_type=content_type,
                title=title,
                release_date=release_date,
                season_number=season_number,
                episode_number=episode_number,
                url=provenance.landing_page_url,
                poster=str(raw.get("poster") or raw.get("image_url") or ""),
                description=str(raw.get("description") or "").strip(),
                external_ids={},
                source_id=self.slug,
            )
            contents.append(discovered)

            offer = ProviderOffer(
                provider_id=self.provider_slug,
                provider_content_id=identifier,
                country=normalize_country(target_country),
                content_type=content_type,
                tmdb_id=None,
                season_number=season_number,
                episode_number=episode_number,
                offer_type=normalize_offer_type("free"),
                provider_url=provenance.landing_page_url,
                quality="",
                audio_languages=normalize_languages(["en"]),
                subtitle_languages=None,
                available_from=None,
                available_until=None,
                source_record_id=identifier,
                source_version=SOURCE_VERSION,
            )
            offers.append(offer)

            if walked >= self.batch_size:
                break

        # Checkpointing
        next_raw_cursor = payload.get("cursor")
        if not next_raw_cursor and walked >= self.batch_size and last_id:
            next_raw_cursor = last_id

        next_cursor = (
            checkpoints.encode(CURSOR_VERSION, {PART_ITEMS: next_raw_cursor})
            if next_raw_cursor
            else None
        )

        return SourceFetchResult(
            contents=contents,
            offers=offers,
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.FULL,
            cursor=next_cursor,
        )


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    s = str(value).strip()
    if len(s) == 4 and s.isdigit():
        return date(int(s), 1, 1)
    try:
        dt = datetime.fromisoformat(s[:10])
        return dt.date()
    except (TypeError, ValueError):
        return None
