"""Read-side assembly for ``GET /api/catalog/unified/``.

Two responsibilities, both deliberately outside the view:

- ``resolve_catalogue_scope`` — which halves a request asks for (official
  vs unofficial), derived from the ``providers=`` / ``provider_categories=``
  parameters and the provider taxonomy (major, bvod/free, scraper/unofficial,
  individual slugs);
- ``unofficial_catalogue_items`` — the unofficial half of the response,
  served through the availability catalogue read boundary
  (``ProviderCatalogueQueryService.list_unofficial``) over the canonical
  ``ProviderContent`` / ``ContentMapping`` tables and shaped to the API
  contract.

This module never reads ``tracker.ScrapedContentMapping``. Legacy scraper
rows reach this response only through the availability ingestion bridge
(``availability.services.legacy_ingest``), so the unified catalogue is a
pure read model: local canonical data only, no HTTP, no scraper execution,
no refresh — a stale row is answered as-is until the scheduled refresh
updates it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from availability.services.catalogue_query import ProviderCatalogueQueryService

from ..models import Movie, Provider
from .filtering import cast_matches_actors, parse_actor_filters


@dataclass(frozen=True)
class CatalogueScope:
    """Which halves of the unified response a request wants."""

    allowed_provider_slugs: Optional[set[str]]
    include_official: bool
    include_scraper: bool


def resolve_catalogue_scope(params) -> CatalogueScope:
    """Resolve ``providers=`` / ``provider_categories=`` into query scope.

    Categories map onto the provider taxonomy: ``major`` selects major
    providers, ``bvod``/``free`` the non-major BVOD set, ``scraper``/
    ``unofficial`` the scraper set, and any other value is taken as a raw
    provider category. When an explicit selection matches official providers
    only (or scraper providers only), only that half of the catalogue is
    queried — exactly the historical behavior of the endpoint.
    """
    raw_providers = params.get("providers") or params.get("provider") or ""
    raw_categories = params.get("provider_categories") or ""

    requested_slugs: set[str] = set()
    if raw_providers:
        for p in raw_providers.split(","):
            if p.strip():
                requested_slugs.add(p.strip().lower())

    if raw_categories:
        cats = [c.strip().lower() for c in raw_categories.split(",") if c.strip()]
        cat_providers = Provider.objects.filter(is_enabled=True)
        cat_slugs = set()
        for c in cats:
            if c == "major":
                cat_slugs.update(cat_providers.filter(is_major=True).values_list("slug", flat=True))
            elif c in ("bvod", "free"):
                cat_slugs.update(cat_providers.filter(category="bvod", is_major=False).values_list("slug", flat=True))
            elif c in ("scraper", "unofficial"):
                cat_slugs.update(cat_providers.filter(category="scraper").values_list("slug", flat=True))
            else:
                cat_slugs.update(cat_providers.filter(category=c).values_list("slug", flat=True))
        requested_slugs.update({s.lower() for s in cat_slugs})

    allowed_provider_slugs = requested_slugs if (raw_providers or raw_categories) else None

    # Determine whether official or scraper or both should be queried
    include_official = True
    include_scraper = True
    if allowed_provider_slugs is not None:
        official_providers = set(
            Provider.objects.filter(is_enabled=True)
            .filter(is_major=True)
            .values_list("slug", flat=True)
        ) | set(
            Provider.objects.filter(is_enabled=True, category__in=["bvod", "avod", "tvod", "fast"])
            .values_list("slug", flat=True)
        )
        scraper_providers = set(
            Provider.objects.filter(is_enabled=True, category="scraper")
            .values_list("slug", flat=True)
        )

        # Also include any provider matching allowed_provider_slugs
        matched_official = False
        matched_scraper = False

        for slug in allowed_provider_slugs:
            if slug in official_providers or Provider.objects.filter(slug__iexact=slug, category__in=["official", "bvod", "avod", "tvod", "fast"]).exists():
                matched_official = True
            if slug in scraper_providers or Provider.objects.filter(slug__iexact=slug, category="scraper").exists():
                matched_scraper = True

        # If user explicitly selected provider(s), only query matching source types
        if matched_official or matched_scraper:
            include_official = matched_official
            include_scraper = matched_scraper

    return CatalogueScope(
        allowed_provider_slugs=allowed_provider_slugs,
        include_official=include_official,
        include_scraper=include_scraper,
    )


def unofficial_catalogue_items(
    params,
    *,
    allowed_provider_slugs: Optional[set[str]],
    exclude_tmdb_ids: Optional[set[int]] = None,
) -> list[dict]:
    """The unofficial half of the unified catalogue, canonically sourced.

    Reads through ``ProviderCatalogueQueryService`` (local canonical
    ``ProviderContent`` / ``ContentMapping`` rows) and maps each entry onto
    the existing response shape, so the frontend contract is unchanged.
    ``exclude_tmdb_ids`` carries the ids already presented by the official
    half so one title never appears twice.

    Display follows the historical response: for a resolved title the
    ``Movie`` row wins (canonical title/year/poster/content type), for an
    unresolved one the scraped values do. The batched ``Movie`` lookup is
    the only extra query, and only runs when something resolved.
    """
    search = (params.get("q") or params.get("query") or "").strip()
    country = (params.get("country") or "").strip().upper()

    entries = ProviderCatalogueQueryService().list_unofficial(
        providers=allowed_provider_slugs,
        search=search or None,
        country=country or None,
        year_min=_digit(params.get("year_min")),
        year_max=_digit(params.get("year_max")),
        exclude_tmdb_ids=exclude_tmdb_ids,
    )
    movies = _movies_by_tmdb_id(entry.tmdb_id for entry in entries)
    actor_ids, actor_names = parse_actor_filters(params)
    if actor_ids or actor_names:
        entries = [
            entry
            for entry in entries
            if (movie := movies.get(entry.tmdb_id)) is not None
            and cast_matches_actors(movie.cast, actor_ids, actor_names)
        ]
    return [
        _as_api_item(entry, movies.get(entry.tmdb_id))
        for entry in entries
    ]


def _digit(value) -> Optional[int]:
    if value is None or not str(value).isdigit():
        return None
    return int(value)


def _movies_by_tmdb_id(tmdb_ids) -> dict[int, Movie]:
    """Batch ``Movie`` lookup for resolved entries (one query, at most)."""
    wanted = {tmdb_id for tmdb_id in tmdb_ids if tmdb_id}
    if not wanted:
        return {}
    movies = Movie.objects.filter(tmdb_id__in=wanted).only(
        "tmdb_id", "title", "year", "poster_path", "content_type", "cast"
    )
    return {movie.tmdb_id: movie for movie in movies}


def _as_api_item(entry, movie: Optional[Movie] = None) -> dict:
    """Shape one canonical entry to the unified catalogue response contract.

    ``movie`` is the resolved ``Movie`` (if any); when present it supplies
    the display fields exactly as the legacy response did — a resolved title
    shows its canonical name, year, poster and type, never the scraped
    string.
    """
    if movie is not None:
        title = movie.title
        year = movie.year
        poster_path = movie.poster_path
        content_type = movie.content_type
    else:
        title = entry.title
        year = entry.year
        poster_path = entry.poster_path
        content_type = entry.content_type

    return {
        "id": f"scraper_{entry.content_id}",
        "tmdb_id": entry.tmdb_id,
        "source": "scraper",
        "source_indicator": entry.source_indicator or "Scraper",
        "title": title,
        "year": year,
        "poster_path": poster_path,
        "content_type": content_type,
        "provider": {
            "slug": entry.provider,
            "name": entry.provider_name,
            "category": entry.provider_category,
        },
        "watchability": None,
        "unofficial_details": {
            "scraped_url": entry.provider_url,
            "confidence_score": entry.confidence if entry.confidence is not None else 0.0,
            "is_confirmed": entry.match_method == "manual",
            "audio_languages": list(entry.audio_languages),
            "subtitle_languages": list(entry.subtitle_languages),
        },
        "watch_url": entry.provider_url,
    }
