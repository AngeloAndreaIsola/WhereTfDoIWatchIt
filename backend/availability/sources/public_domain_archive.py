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
- Supports real Internet Archive advancedsearch query parameters (q, fl[], rows, page, output=json)
  and responses (response.docs).
- Accurately tracks request budget, halts on exhaustion, and does not invent unasserted audio languages.
- Separates "item has a PD marker" from "watchable in country X":
  An item with UNKNOWN territorial scope emits DiscoveredContent (keeping provenance for audit/review)
  but emits NO confirmed ProviderOffer for an arbitrary country.
  Only countries with established rights (e.g. US for US_ONLY, or any country for WORLDWIDE)
  receive a ProviderOffer.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

import requests

from ..domain.enums import ContentType, OfferType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer, ProviderOfferWithdrawal
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

# Canonical fields requested from Internet Archive advancedsearch
IA_FIELDS = [
    "identifier",
    "title",
    "year",
    "publicdate",
    "mediatype",
    "licenseurl",
    "rights",
    "jurisdiction",
    "description",
    "language",
]


class PublicDomainArchiveClient:
    """HTTP client for public domain metadata catalogues.

    Accepts an injectable ``fetcher`` callable for offline deterministic test fixtures.
    """

    def __init__(
        self,
        *,
        fetcher: Optional[Callable[[str, Optional[dict]], dict]] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.fetcher = fetcher
        self.timeout = timeout

    def fetch_catalogue(
        self,
        endpoint: str,
        page: int = 1,
        rows: int = DEFAULT_BATCH_ITEMS,
        query: str = "mediatype:movies AND (licenseurl:*publicdomain* OR licenseurl:*creativecommons*)",
        params: Optional[dict] = None,
    ) -> dict:
        request_params: dict[str, Any] = {
            "q": query,
            "fl[]": IA_FIELDS,
            "rows": rows,
            "page": page,
            "output": "json",
        }
        if params:
            request_params.update(params)

        if self.fetcher is not None:
            try:
                return self.fetcher(endpoint, request_params)
            except SourceFetchError:
                raise
            except Exception as exc:
                raise SourceFetchError(f"PublicDomainArchive fetch failed: {exc}") from exc

        # Default live HTTP fetcher
        try:
            resp = requests.get(
                endpoint,
                params=request_params,
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
        supported_content_types=frozenset(
            {ContentType.MOVIE, ContentType.SERIES, ContentType.EPISODE}
        ),
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
        max_requests: int = 100,
    ):
        self.client = client or PublicDomainArchiveClient()
        self.batch_size = max(1, int(batch_size))
        self.provider_slug = provider_slug
        self.catalogue_endpoint = catalogue_endpoint
        self.budget_tracker = SourceBudgetTracker(max_requests=max_requests)

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

        if self.budget_tracker.budget_exhausted:
            logger.warning("%s_budget_exhausted requests=%d", self.slug, self.budget_tracker.requests_made)
            return SourceFetchResult(
                contents=[],
                offers=[],
                authoritative=False,
                complete_snapshot=False,
                sync_mode=SyncMode.FULL,
                cursor=cursor,
            )

        resume = checkpoints.decode(
            cursor,
            version=CURSOR_VERSION,
            parts=(PART_ITEMS,),
            log_prefix=self.slug,
        )
        anchor = resume.anchor(PART_ITEMS)

        # Parse current page number from anchor or default to 1
        page = 1
        if anchor and anchor.startswith("page_"):
            try:
                page = int(anchor.split("_", 1)[1])
            except ValueError:
                page = 1
        elif anchor and anchor.isdigit():
            page = int(anchor)

        try:
            payload = self.client.fetch_catalogue(
                self.catalogue_endpoint,
                page=page,
                rows=self.batch_size,
            )
            self.budget_tracker.record_request(success=True)
        except Exception:
            self.budget_tracker.record_request(success=False)
            raise

        if not isinstance(payload, dict):
            raise SourceFetchError(f"Unexpected payload type from {self.slug}")

        raw_items = payload.get("items")
        num_found = 0
        if raw_items is None and "response" in payload:
            response_obj = payload.get("response") or {}
            raw_items = response_obj.get("docs", [])
            num_found = response_obj.get("numFound", 0)

        if not isinstance(raw_items, list):
            raise SourceFetchError(f"Malformed catalogue payload in {self.slug}: missing items list")

        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        withdrawals: list[ProviderOfferWithdrawal] = []
        walked = 0
        eval_country = country or DEFAULT_COUNTRY

        for raw in raw_items:
            if not isinstance(raw, dict):
                continue

            self.budget_tracker.items_evaluated += 1
            evaluation = evaluate_item_rights(raw)
            identifier = str(raw.get("identifier") or "").strip()
            title = str(raw.get("title") or "").strip()

            # Skip items with missing identity
            if not identifier or not title:
                logger.warning(
                    "%s_item_skipped reason=missing_identifier_or_title item=%r",
                    self.slug,
                    raw,
                )
                self.budget_tracker.rejected_items += 1
                continue

            # Check rights verification
            if not evaluation.has_public_domain_mark:
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

            # Keep unknown/restricted metadata for audit, without an offer.
            provenance = build_provenance_record(raw, evaluation)
            walked += 1
            if evaluation.has_public_domain_mark:
                self.budget_tracker.pd_items_accepted += 1

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

            # Always emit DiscoveredContent (keeping provenance & metadata for review/audit)
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
                rights_provenance={
                    "identifier": provenance.identifier,
                    "landing_page_url": provenance.landing_page_url,
                    "rights_url": provenance.rights_url,
                    "rights_status": provenance.rights_status.value,
                    "territorial_scope": provenance.territorial_scope.value,
                    "raw_rights_text": provenance.raw_rights_text,
                    "jurisdiction": str(raw.get("jurisdiction") or "").strip(),
                    "observed_at": provenance.observed_at.isoformat(),
                    "assessment_notes": evaluation.notes,
                },
            )
            contents.append(discovered)

            # Check territorial authorization before emitting a confirmed ProviderOffer.
            # Do NOT emit positive offers for an arbitrary country when territorial rights are UNKNOWN
            # or do not match the target market.
            if evaluation.is_watchable_in_country(eval_country):
                target_country = (
                    "US"
                    if evaluation.territorial_scope == TerritorialScope.US_ONLY
                    else eval_country
                )
                raw_lang = raw.get("language")
                audio_languages = (
                    normalize_languages([str(raw_lang)])
                    if raw_lang
                    else None
                )

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
                    audio_languages=audio_languages,
                    subtitle_languages=None,
                    available_from=None,
                    available_until=None,
                    source_record_id=identifier,
                    source_version=SOURCE_VERSION,
                )
                offers.append(offer)
            else:
                withdrawals.append(ProviderOfferWithdrawal(
                    provider_id=self.provider_slug, provider_content_id=identifier,
                    country=normalize_country(eval_country), content_type=content_type,
                    season_number=season_number, episode_number=episode_number,
                ))
                logger.info(
                    "%s_offer_omitted_territory_unauthorized identifier=%s scope=%s country=%s",
                    self.slug,
                    identifier,
                    evaluation.territorial_scope.value,
                    eval_country,
                )

            if walked >= self.batch_size:
                break

        # Checkpoint computation
        next_raw_cursor = payload.get("cursor")
        if not next_raw_cursor:
            start = (page - 1) * self.batch_size
            if num_found > (start + len(raw_items)):
                next_raw_cursor = f"page_{page + 1}"
            elif payload.get("has_more") or (len(raw_items) >= self.batch_size and not num_found):
                next_raw_cursor = f"page_{page + 1}"

        next_cursor = (
            checkpoints.encode(CURSOR_VERSION, {PART_ITEMS: str(next_raw_cursor)})
            if next_raw_cursor
            else None
        )

        return SourceFetchResult(
            contents=contents,
            offers=offers,
            offer_withdrawals=withdrawals,
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
