"""API v2 views for scraper sources integration (Phase C).

Provides endpoints for:
- Searching scraper sources with filtering by user preferences (language, country)
- Caching with staleness tracking
- Unified ranking: audio match → subtitle match → quality
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from tracker.services.availability_report import (
    TitleAvailabilityState,
    title_source_report,
)

from tracker.models import (
    Movie,
    Preference,
    Provider,
    Subscription,
)
from tracker.ownership import resolve_personal_owner_id
from tracker.scrapers.base import ScrapedItem, ContentMatcher
from tracker.services.preferences import resolve_preferences
from tracker.services.ranking import rank_offers
from tracker.services.scraped_offers import BVOD_CATEGORIES, get_scraped_offers
from tracker.services.streaming_api import service_display_name
from tracker.serializers import (
    MovieSerializer,
    ProviderSerializer,
)

from availability.domain.enums import MatchMethod
from availability.services.catalogue_query import ProviderCatalogueQueryService
from tracker.services.scraper_selection import (
    ScraperExecutionIneligible,
    select_provider_scraper,
)

logger = logging.getLogger(__name__)

# Staleness thresholds for scraper sources (they change more frequently than
# official streaming availability data)
UNOFFICIAL_STALE_AFTER = dt.timedelta(hours=12)

# Provider category scope of the v2 unofficial endpoints: the scraper
# category, extended with the scraped BVOD set when ``include_bvod=true``.
_SCRAPER_CATEGORIES = ("scraper",)

# Same shape the legacy ScrapedContentMappingSerializer produced, assembled
# from the canonical catalogue entry so the response contract is unchanged.
_DT = serializers.DateTimeField()


def _dt_repr(value) -> Optional[str]:
    """DRF datetime rendering (identical to the legacy model serializer)."""
    if value is None:
        return None
    return _DT.to_representation(value)


def _is_stale_scrape(provider: Provider) -> bool:
    """Check if a provider's scraped content is stale."""
    last = provider.last_scraped_at
    if last is None:
        return True
    return (timezone.now() - last) > UNOFFICIAL_STALE_AFTER


@api_view(["GET"])
def unofficial_catalog(request):
    """GET /api/v2/unofficial/

    List scraped content with filtering by user preferences.

    Query params:
      - provider: filter by provider slug (e.g. 'kisskh', 'hydrahd')
      - country: filter by country code (via provider domain/country config)
      - audio_lang: ISO 639-1 code to filter by available audio language
      - subtitle_lang: ISO 639-1 code to filter by available subtitle language
      - q: search query (title filter)
      - available: 'true' (default) to filter only available titles
      - include_bvod: 'true' to also include BVOD/free providers
    """
    provider_slug = request.GET.get("provider", "")
    country_code = (request.GET.get("country") or "").strip().upper()
    audio_lang = (request.GET.get("audio_lang") or "").strip().upper()
    subtitle_lang = (request.GET.get("subtitle_lang") or "").strip().upper()
    query = (request.GET.get("q") or "").strip()
    available_only = request.GET.get("available", "true").lower() != "false"
    include_bvod = request.GET.get("include_bvod", "").lower() == "true"

    categories = set(_SCRAPER_CATEGORIES)
    if include_bvod:
        categories.update(BVOD_CATEGORIES)

    # Canonical read: the same rows the legacy ScrapedContentMapping query
    # returned (scraper/BVOD provider categories, enabled, optional slug /
    # country / title filters, availability tri-state), served from the
    # provider catalogue instead of the ingestion-era table.
    entries = ProviderCatalogueQueryService().list_unofficial(
        providers={provider_slug.strip().lower()} if provider_slug else None,
        search=query or None,
        country=country_code or None,
        provider_categories=categories,
        currently_available=True if available_only else None,
    )

    results = _serialize_unofficial_entries(entries)

    # Apply language filtering after serialization (languages are in raw_payload)
    if audio_lang:
        results = [
            r for r in results
            if _item_has_audio(r, audio_lang)
        ]
    if subtitle_lang:
        results = [
            r for r in results
            if _item_has_subtitle(r, subtitle_lang)
        ]

    return Response({
        "count": len(results),
        "filters": {
            "provider": provider_slug or None,
            "country": country_code or None,
            "audio_lang": audio_lang or None,
            "subtitle_lang": subtitle_lang or None,
            "available_only": available_only,
            "include_bvod": include_bvod,
        },
        "results": results,
    })


def _serialize_unofficial_entries(entries) -> list[dict]:
    """Shape canonical catalogue entries into the legacy v2 catalog contract.

    Field-for-field identical to ``ScrapedContentMappingSerializer`` plus the
    two post-serialization annotations: ``id`` is the canonical row id,
    ``created_at``/``updated_at``/``last_scraped_at`` come from the row's
    observation timestamps, ``is_confirmed`` is the manual-match outcome, and
    ``raw_payload`` is reconstructed with the fields consumers filter and
    render on (languages, country when recorded) — the adapter-specific
    scrape payload is not carried into the canonical metadata. Nested
    ``provider``/``movie`` are the same serializers as before, batched.
    """
    providers = {
        provider.slug: provider
        for provider in Provider.objects.filter(
            slug__in={entry.provider for entry in entries}
        )
    }
    movies = {
        (movie.tmdb_id, movie.content_type): movie
        for movie in Movie.objects.filter(
            tmdb_id__in={entry.tmdb_id for entry in entries if entry.tmdb_id}
        )
    }
    provider_data = {
        slug: ProviderSerializer(provider).data
        for slug, provider in providers.items()
    }

    results = []
    for entry in entries:
        pdata = provider_data.get(entry.provider) or {}
        movie = movies.get((entry.tmdb_id, entry.content_type)) if entry.tmdb_id else None
        if movie is not None and movie.is_quarantined:
            continue
        raw_payload: dict[str, Any] = {
            "audio_languages": entry.audio_languages,
            "subtitle_languages": entry.subtitle_languages,
        }
        if entry.country:
            raw_payload["country"] = entry.country
        results.append({
            "id": entry.content_id,
            "provider": pdata,
            "scraped_title": entry.title,
            "scraped_year": entry.year,
            "scraped_url": entry.provider_url,
            "country_code": entry.country,
            "movie": MovieSerializer(movie).data if movie is not None else None,
            "is_confirmed": entry.match_method == MatchMethod.MANUAL,
            "confidence_score": entry.confidence if entry.confidence is not None else 0.0,
            "raw_payload": raw_payload,
            "created_at": _dt_repr(entry.first_seen),
            "updated_at": _dt_repr(entry.updated_at),
            "last_scraped_at": _dt_repr(entry.last_seen),
            "is_available": entry.currently_available,
            "source_indicator": pdata.get("source_indicator") or "Scraper",
            "provider_category": pdata.get("category") or "scraper",
        })
    return results


def _item_has_audio(item: dict, lang_code: str) -> bool:
    """Check if an item has the given audio language.

    Returns True when the payload has no audio info (don't filter those out),
    otherwise True only if the requested language is present.
    """
    payload = item.get("raw_payload", {}) or {}
    if not isinstance(payload, dict):
        return True
    audio = payload.get("audio_languages", []) or payload.get("audio", [])
    if not audio:
        # No audio metadata — leave the item in the result set.
        return True
    wanted = {lang_code} | _iso_aliases(lang_code)
    return any(str(a).strip().upper() in wanted for a in audio)


def _item_has_subtitle(item: dict, lang_code: str) -> bool:
    """Check if an item has the given subtitle language.

    Returns True when the payload has no subtitle info (don't filter those
    out), otherwise True only if the requested language is present.
    """
    payload = item.get("raw_payload", {}) or {}
    if not isinstance(payload, dict):
        return True
    subs = payload.get("subtitle_languages", []) or payload.get("subtitles", [])
    if not subs:
        return True
    wanted = {lang_code} | _iso_aliases(lang_code)
    return any(str(s).strip().upper() in wanted for s in subs)


def _iso_aliases(code: str) -> set[str]:
    """Return known aliases for a language code."""
    from tracker.languages import normalize_language_code
    result = set()
    canonical = normalize_language_code(code)
    if canonical:
        result.add(canonical)
        result.add(code)
    return result


@api_view(["POST"])
def unofficial_scrape(request):
    """POST /api/v2/unofficial/scrape/

    Trigger a scrape of an unofficial source, optionally filtered by query.
    Returns matched results with TMDb IDs where available.

    Body:
      - provider_slug: required (e.g. 'raiplay')
      - query: optional search term
      - country: optional ISO country code

    Each result carries the ``content_type`` the scraper classified it with
    (``movie``/``series``) next to the matched TMDb row.
    """
    data = request.data if isinstance(request.data, dict) else {}
    provider_slug = (data.get("provider_slug") or "").strip()
    query = (data.get("query") or "").strip()
    country_code = (data.get("country") or "").strip().upper()

    if not provider_slug:
        return Response({"error": "provider_slug is required"}, status=400)

    provider = Provider.objects.filter(slug__iexact=provider_slug).first()
    if not provider:
        return Response(
            {"error": f"Provider '{provider_slug}' not found"}, status=404
        )

    try:
        scraper = select_provider_scraper(provider).scraper
    except ScraperExecutionIneligible as exc:
        return Response(exc.payload(), status=status.HTTP_409_CONFLICT)

    items = scraper.run(query=query)

    # Update last_scraped_at
    provider.last_scraped_at = timezone.now()
    provider.save()

    # Match items against TMDb
    matcher = ContentMatcher()
    results = []
    for item in items:
        if not item.source_url:
            item.source_url = f"https://{provider.domain}/{query}" if query else f"https://{provider.domain}"
        movie, score, candidates, is_confirmed = matcher.match_item(item)
        result = {
            "scraped_title": item.title,
            "scraped_year": item.year,
            "content_type": item.content_type,
            "provider_slug": item.provider_slug,
            "source_url": item.source_url,
            "audio_languages": item.audio_languages,
            "subtitle_languages": item.subtitle_languages,
            "quality": item.quality,
            "matched_movie": {
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "year": movie.year,
                "poster_path": movie.poster_path,
            } if movie else None,
            "confidence_score": score,
            "is_confirmed": is_confirmed,
            "candidates": candidates,
        }
        results.append(result)

    return Response({
        "provider": provider.slug,
        "provider_name": provider.name,
        "provider_category": provider.category,
        "source_indicator": provider.source_indicator,
        "query": query,
        "country": country_code,
        "count": len(results),
        "results": results,
    }, status=status.HTTP_201_CREATED)


def _title_availability(movie_id, country=None):
    """The classifier's verdict for one title, as plain JSON.

    Both detail pages render availability from this endpoint, and an empty
    ``offers`` list is ambiguous on its own: it looks the same whether the title
    was checked and is genuinely unavailable, or whether nothing ever checked it.

    Measured over 400 tracked titles with no scraper offers, every one was
    ``unknown`` (not_checked / not_applicable / title_not_found) and none were
    ``unavailable`` — so a UI that renders that empty list as "not currently
    available" states something false.

    The verdict is *presented*, not recomputed: it comes from
    ``availability_report.title_source_report``, the same classifier the report
    and metrics endpoints use, so this endpoint cannot disagree with them.
    Absent data is UNKNOWN, never UNAVAILABLE.
    """
    if country is None:
        country = ""
    verdict = title_source_report([movie_id], country=str(country).upper() if country else None).get(movie_id, {})
    state = verdict.get("state") or TitleAvailabilityState.UNKNOWN
    return {
        "state": state,
        # Evidence class (``offers`` / ``title_signal`` / ``absence`` /
        # ``none``): lets the page tell "here is an offer" apart from "a
        # source says this is there, but we have no offer to show" — the
        # latter must never read as a playable option.
        "evidence": verdict.get("evidence") or "none",
        "offer_count": verdict.get("offer_count") or 0,
        "title_signals": verdict.get("title_signals", []),
        "available_on": verdict.get("available_on", []),
        "reason": _reason_value(verdict.get("reason")),
        "resolved_sources": verdict.get("resolved_sources", []),
        "unresolved_sources": verdict.get("unresolved_sources", []),
        "sources": [
            {
                "source": row.get("source"),
                "label": row.get("label"),
                "status": row.get("status"),
                "reason": _reason_value(row.get("reason")),
                "blocked_reason": row.get("blocked_reason", ""),
                "available": row.get("available"),
                "checked_at": row.get("checked_at"),
            }
            for row in verdict.get("sources", [])
        ],
    }


def _reason_value(reason):
    """Reasons are enum members in the classifier; the API sends plain strings.

    A string (not the enum name) keeps the frontend contract stable if the enum
    is renamed, and lets an unregistered reason degrade to the slug rather than
    disappearing.
    """
    if reason is None:
        return None
    return getattr(reason, "value", None) or str(reason)


@api_view(["GET"])
def unofficial_movie_offers(request, tmdb_id: int):
    """GET /api/v2/unofficial/movie/<tmdb_id>/offers/

    Return scraper source offers for a movie, filtered by user preferences
    and ranked by: audio match → subtitle match → quality. For an ID
    shared by movie and series records, pass ``?content_type=movie`` or
    ``?content_type=series`` to select the intended identity.

    ``?season=N`` (series rows only) suppresses those show-level scraper
    offers: scraper rows carry no season signal, so a season-filtered request
    answers ``offers: []`` with the season echoed in ``selected_season``.
    Non-integer values are ignored, and movie rows ignore the parameter.
    """
    user_id = resolve_personal_owner_id()
    movies = Movie.objects.visible().filter(tmdb_id=tmdb_id)
    content_type = request.query_params.get("content_type")
    if content_type:
        if content_type not in ("movie", "series"):
            return Response({"error": "content_type must be 'movie' or 'series'"}, status=400)
        movies = movies.filter(content_type=content_type)
    try:
        movie = movies.get()
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
        return Response({"error": "movie not tracked yet"}, status=404)

    # Scraper rows carry no season signal (same Phase 3 rule as the prime
    # pipeline): for a series narrowed to one season the show-level scraper
    # offers are never returned — the answer degrades to an empty list that
    # echoes the selection, exactly like an unknown season upstream.
    season: Optional[int] = None
    if movie.content_type == "series":
        raw_season = request.GET.get("season")
        if raw_season is not None and raw_season.strip() != "":
            try:
                season = int(raw_season)
            except (TypeError, ValueError):
                season = None

    country = (
        request.GET.get("current_country")
        or request.GET.get("country")
        or resolve_preferences(user_id=user_id).get("current_country")
        or ""
    )

    movie_payload = {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "poster_path": movie.poster_path,
        "content_type": movie.content_type,
    }
    if season is not None:
        return Response({
            "movie": movie_payload,
            "offers": [],
            "selected_season": season,
            "availability": _title_availability(movie.id, country=country),
        })

    from tracker.services.access import build_reach, build_user_access, classify_offer
    from tracker.services.preferences import build_preference

    preference = build_preference(user_id)
    preference.current_country = (country or preference.current_country).upper().strip()

    include_bvod = request.GET.get("include_bvod", "").lower() == "true"
    categories = set(_SCRAPER_CATEGORIES)
    if include_bvod:
        categories.update(BVOD_CATEGORIES)

    # Scraper offers for this movie, read through the canonical catalogue
    # (ProviderContent + ContentMapping, newest observation first) instead of
    # the legacy ScrapedContentMapping table; ranking and presentation are
    # unchanged.
    offers, provider_map = get_scraped_offers(movie, provider_categories=categories)

    ranked = rank_offers(
        offers, preference, current_country=preference.current_country
    )
    access = build_user_access(user_id)
    reach = build_reach(preference, preference.current_country)

    # Format for frontend
    from tracker.services.presentation import present_offer
    offers_out = []
    for offer in ranked:
        presented = present_offer(offer, provider_map)
        d = offer.to_dict()
        d["service_display"] = presented.service_display
        d["source_indicator"] = presented.source_indicator
        d["country"] = presented.country
        classified = classify_offer(
            offer,
            access=access,
            preference=preference,
            reach=reach,
            provider_map=provider_map,
        )
        d["locality"] = classified.locality
        d["is_accessible"] = classified.is_accessible
        d["is_entitled"] = classified.is_entitled
        d["rejection_reasons"] = classified.rejection_reasons
        d["territorial_scope"] = classified.territorial_scope
        offers_out.append(d)

    return Response({
        "movie": movie_payload,
        "offers": offers_out,
        "selected_season": None,
        "availability": _title_availability(movie.id, country=country),
    })


@api_view(["GET"])
def unofficial_providers(request):
    """GET /api/v2/unofficial/providers/

    List scraper providers with their source indicators and staleness status.
    """
    categories = ["scraper"]
    if request.GET.get("include_bvod", "") == "true":
        categories.extend(["bvod", "avod", "tvod", "fast"])

    providers = Provider.objects.filter(
        category__in=categories, is_enabled=True
    ).order_by("name")

    results = []
    for p in providers:
        results.append({
            "id": p.id,
            "slug": p.slug,
            "name": p.name,
            "category": p.category,
            "source_indicator": p.source_indicator,
            "is_enabled": p.is_enabled,
            "is_major": p.is_major,
            "icon_name": p.icon_name,
            "domain": p.domain,
            "last_scraped_at": p.last_scraped_at.isoformat() if p.last_scraped_at else None,
            "is_stale": _is_stale_scrape(p),
        })

    return Response({"count": len(results), "results": results})
