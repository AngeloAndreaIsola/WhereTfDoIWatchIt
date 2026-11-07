"""Local-only query service for the provider catalogue (Phase 8).

Read boundary — the only path the application uses to read ``ProviderContent``:

    HTTP / application code
        -> ProviderCatalogueQueryService   (this module: validation + views)
        -> ProviderContentRepository       (filters, ordering, bulk joins)
        -> local Postgres                  (ProviderContent/ContentMapping/Offer)

The write path stays exactly where Phase 3–7 left it and is never reachable
from here:

    background refresh -> AvailabilitySource -> ProviderContent persistence
                       -> Offer persistence

Nothing in this module imports a source, an HTTP client, or the refresh
service, and no method triggers a refresh: a catalogue query answers from the
database even when the local data is stale (staleness is a write-side concern
owned by the scheduled refresh).

Concepts kept separate in every result:

- ``ProviderContent`` — *the provider catalogue entry exists / was observed*;
- ``ContentMapping`` — *optional* provider-native → TMDb resolution; mapped
  and unresolved rows are both first-class results (``tmdb_id=None`` is data,
  not an error);
- ``Offer`` — *a currently active access/commercial offer exists*; exposed
  only as presence (``has_offers``/``offer_count``), never inferred from —
  and never written back to — ``ProviderContent.currently_available``.
"""
from __future__ import annotations

from typing import Optional

from tracker.models import Provider

from ..domain.enums import ContentType
from ..domain.models import ProviderContentView, UnofficialCatalogueEntry
from ..persistence.repositories import (
    MappingRepository,
    ProviderContentRepository,
)


def _upper_languages(values) -> list[str]:
    """Upper-cased, de-duplicated language codes (legacy read semantics)."""
    if not isinstance(values, (list, tuple)):
        return []
    normalized = []
    for value in values:
        code = str(value).strip().upper()
        if code and code not in normalized:
            normalized.append(code)
    return normalized


class ProviderCatalogueQueryService:
    """Read-only access to the local provider catalogue.

    Conventions follow ``AvailabilityQueryService``: callers identify a
    provider by its stable ``tracker.Provider`` slug (never a database primary
    key), an unknown provider slug yields empty results (like an unknown
    country code), and an invalid content type raises ``ValueError``.

    Filters map onto columns ``ProviderContent`` already has — provider,
    content type, provider-native id, season/episode slots, current
    observation — plus a case-insensitive partial title search. Results are
    deterministic lists (identity order; title then identity for search) and
    are assembled in a fixed number of queries regardless of result size.
    """

    def __init__(self, repository=None, mappings=None):
        self.repo = repository or ProviderContentRepository()
        self.mappings = mappings or MappingRepository()

    # ------------------------------------------------------------------ read

    def get(
        self,
        provider: str,
        provider_content_id: str,
        content_type: str,
        *,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
    ) -> Optional[ProviderContentView]:
        """One catalogue entry by its full provider-native identity, or None.

        Season/episode slots matter exactly as they do for identity: the
        series row and its season rows are different entries of the same
        provider-native id.
        """
        if content_type is None:
            raise ValueError("content_type is required to read one catalogue entry")
        self._validate_content_type(content_type)
        provider_row = self._provider(provider)
        if provider_row is None:
            return None
        row = self.repo.get(
            provider=provider_row,
            provider_content_id=provider_content_id,
            content_type=content_type,
            season_number=season_number,
            episode_number=episode_number,
        )
        if row is None:
            return None
        return self._views([row])[0]

    def list(
        self,
        *,
        provider: Optional[str] = None,
        content_type: Optional[str] = None,
        provider_content_id: Optional[str] = None,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        currently_available: Optional[bool] = None,
    ) -> list[ProviderContentView]:
        """Catalogue entries matching the given dimensions, in identity order."""
        self._validate_content_type(content_type)
        filters = {}
        if provider is not None:
            provider_row = self._provider(provider)
            if provider_row is None:
                return []
            filters["provider"] = provider_row
        if content_type is not None:
            filters["content_type"] = content_type
        if provider_content_id is not None:
            filters["provider_content_id"] = provider_content_id
        if season_number is not None:
            filters["season_number"] = season_number
        if episode_number is not None:
            filters["episode_number"] = episode_number
        if currently_available is not None:
            filters["currently_available"] = currently_available
        return self._views(list(self.repo.list(**filters)))

    def search(
        self,
        title: str,
        *,
        provider: Optional[str] = None,
        content_type: Optional[str] = None,
        currently_available: Optional[bool] = None,
    ) -> list[ProviderContentView]:
        """Case-insensitive partial title match over persisted catalogue rows.

        Deterministic and database-backed; blank terms raise ``ValueError``
        instead of quietly matching the whole catalogue.
        """
        self._validate_content_type(content_type)
        term = (title or "").strip()
        if not term:
            raise ValueError("Search title must not be empty")
        filters = {}
        if provider is not None:
            provider_row = self._provider(provider)
            if provider_row is None:
                return []
            filters["provider"] = provider_row
        if content_type is not None:
            filters["content_type"] = content_type
        if currently_available is not None:
            filters["currently_available"] = currently_available
        return self._views(list(self.repo.search_by_title(term, **filters)))

    def list_unofficial(
        self,
        *,
        providers: Optional[set[str]] = None,
        search: Optional[str] = None,
        country: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        exclude_tmdb_ids: Optional[set[int]] = None,
        provider_categories: Optional[set[str]] = None,
        currently_available: Optional[bool] = True,
    ) -> list[UnofficialCatalogueEntry]:
        """Unofficial catalogue entries for the unified catalogue read path.

        The canonical replacement for reading ``tracker.ScrapedContentMapping``
        directly: rows are ``ProviderContent`` observations owned by
        scraper-category providers or ingested by the legacy scraper bridge,
        enriched with their ``ContentMapping`` resolution in one bulk query.

        ``exclude_tmdb_ids`` drops entries whose TMDb id already appears
        through the official half of the unified response — deduplication by
        resolved outcome, so a legacy row and a canonical row for the same
        title never surface twice. ``provider_categories`` narrows to raw
        ``Provider`` categories and ``currently_available`` is tri-state
        (``None`` skips the availability filter — the legacy
        ``available=false`` answer); both default to the unified-catalogue
        behaviour.

        Local-only like every other method in this service: no source, HTTP
        client, or refresh module is reachable, and a stale row is answered
        as-is rather than refreshed.
        """
        rows = self.repo.list_unofficial(
            provider_slugs=providers,
            search=search,
            country=country,
            year_min=year_min,
            year_max=year_max,
            provider_categories=provider_categories,
            currently_available=currently_available,
        )
        return self._unofficial_entries(rows, exclude_tmdb_ids=exclude_tmdb_ids)

    def list_for_tmdb(
        self,
        tmdb_id: int,
        *,
        provider_categories: Optional[set[str]] = None,
    ) -> list[UnofficialCatalogueEntry]:
        """Unofficial catalogue rows mapped to *tmdb_id*, newest observation first.

        The canonical replacement for reading ``ScrapedContentMapping`` by
        ``movie=`` (prime BVOD block and v2 per-movie offers): same unofficial
        row predicate as ``list_unofficial`` — enabled providers, currently
        available, scraper-owned or legacy-bridge-observed — joined through
        ``ContentMapping``, with the optional raw provider category set
        (bvod/avod/tvod/fast) applied. Local-only like every other method in
        this service: no source, HTTP client, or refresh module is reachable.
        """
        rows = self.repo.list_mapped_to_tmdb(
            tmdb_id=tmdb_id,
            provider_categories=provider_categories,
        )
        return self._unofficial_entries(rows)

    def _unofficial_entries(
        self, rows, *, exclude_tmdb_ids: Optional[set[int]] = None
    ) -> list[UnofficialCatalogueEntry]:
        """Assemble entries for evaluated rows in one bulk mapping query."""
        if not rows:
            return []
        summaries = self.mappings.summaries_for_provider_content(rows)
        entries = []
        for row in rows:
            key = (row.provider_id, row.provider_content_id)
            tmdb_id, confidence, match_method = summaries.get(key, (None, None, ""))
            if tmdb_id is not None and exclude_tmdb_ids and tmdb_id in exclude_tmdb_ids:
                continue
            metadata = row.metadata if isinstance(row.metadata, dict) else {}
            entries.append(
                UnofficialCatalogueEntry(
                    content_id=row.pk,
                    provider=row.provider.slug,
                    provider_name=row.provider.name,
                    provider_category=row.provider.category,
                    source_indicator=row.provider.source_indicator or "Scraper",
                    provider_content_id=row.provider_content_id,
                    content_type=row.content_type,
                    title=row.title,
                    year=row.release_date.year if row.release_date else None,
                    poster_path=str(metadata.get("poster") or ""),
                    provider_url=row.provider_url,
                    country=str(metadata.get("country_code") or ""),
                    tmdb_id=tmdb_id,
                    confidence=confidence,
                    match_method=match_method or "",
                    audio_languages=_upper_languages(metadata.get("audio_languages")),
                    subtitle_languages=_upper_languages(
                        metadata.get("subtitle_languages")
                    ),
                    currently_available=row.currently_available,
                    first_seen=row.first_seen,
                    last_seen=row.last_seen,
                    updated_at=row.updated_at,
                )
            )
        return entries

    # --------------------------------------------------------------- private

    def _views(self, rows) -> list[ProviderContentView]:
        """Assemble views for evaluated rows in two bulk queries (no N+1)."""
        if not rows:
            return []
        tmdb_ids = self.repo.tmdb_ids_for(rows)
        offer_counts = self.repo.active_offer_counts(rows)
        return [self._view(row, tmdb_ids, offer_counts) for row in rows]

    def _view(self, row, tmdb_ids, offer_counts) -> ProviderContentView:
        key = (row.provider_id, row.provider_content_id)
        return ProviderContentView(
            provider=row.provider.slug,
            provider_content_id=row.provider_content_id,
            content_type=row.content_type,
            title=row.title,
            release_date=row.release_date,
            season_number=row.season_number,
            episode_number=row.episode_number,
            provider_url=row.provider_url,
            currently_available=row.currently_available,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            tmdb_id=tmdb_ids.get(key),
            offer_count=offer_counts.get(key, 0),
        )

    @staticmethod
    def _provider(slug):
        """Resolve a provider slug through the existing Provider abstraction."""
        if not slug:
            return None
        return Provider.objects.filter(slug=slug).first()

    @staticmethod
    def _validate_content_type(content_type):
        if content_type is not None and content_type not in ContentType.values:
            raise ValueError(
                f"Invalid content_type: {content_type}. "
                f"Must be one of: {', '.join(ContentType.values)}"
            )
