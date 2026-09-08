"""API v2 views for unofficial sources integration (Phase C).

Provides endpoints for:
- Searching unofficial sources with filtering by user preferences (language, country)
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
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from tracker.models import (
    Movie,
    Preference,
    Provider,
    ScrapedContentMapping,
    Subscription,
)
from tracker.scrapers.base import ScrapedItem, ContentMatcher
from tracker.scrapers import SCRAPER_REGISTRY, resolve_scraper_class
from tracker.services.preferences import resolve_preferences
from tracker.services.ranking import rank_offers
from tracker.services.cache import AvailabilityCache
from tracker.services.streaming_api import service_display_name
from tracker.services.offers import Offer
from tracker.serializers import (
    ProviderSerializer,
    ScrapedContentMappingSerializer,
)

logger = logging.getLogger(__name__)

# Staleness thresholds for unofficial sources (they change more frequently than
# official streaming availability data)
UNOFFICIAL_STALE_AFTER = dt.timedelta(hours=12)


def _is_stale_scrape(provider: Provider) -> bool:
    """Check if a provider's scraped content is stale."""
    last = provider.last_scraped_at
    if last is None:
        return True
    return (timezone.now() - last) > UNOFFICIAL_STALE_AFTER


def _resolve_scraper(provider_slug: str):
    """Dynamically resolve a scraper class from the registry."""
    return resolve_scraper_class(provider_slug)


@api_view(["GET"])
def unofficial_catalog(request):
    """GET /api/v2/unofficial/

    List scraped unofficial content with filtering by user preferences.

    Query params:
      - provider: filter by provider slug (e.g. 'rai-play', 'bbc')
      - country: filter by country code (via provider domain/country config)
      - audio_lang: ISO 639-1 code to filter by available audio language
      - subtitle_lang: ISO 639-1 code to filter by available subtitle language
      - q: search query (title filter)
      - available: 'true' (default) to filter only available titles
      - include_official: 'true' to also include official/free providers
    """
    provider_slug = request.GET.get("provider", "")
    country_code = (request.GET.get("country") or "").strip().upper()
    audio_lang = (request.GET.get("audio_lang") or "").strip().upper()
    subtitle_lang = (request.GET.get("subtitle_lang") or "").strip().upper()
    query = (request.GET.get("q") or "").strip()
    available_only = request.GET.get("available", "true").lower() != "false"
    # The Unofficial Sources tab lists Imported Small Providers & Sources (scrapers).
    # Official Free Streaming Providers (bbc_iplayer, raiplay, nhk, ...) are served
    # by the official /api/catalog/ endpoint, so they are excluded here by default.
    # include_official=true re-adds them for the combined scrape view.
    include_official = request.GET.get("include_official", "").lower() == "true"

    # Default: scrapers only. Official + Free providers live in the official tab.
    categories = ["scraper"]
    if include_official:
        categories = ["official", "free", "scraper"]

    qs = ScrapedContentMapping.objects.select_related("provider", "movie")
    qs = qs.filter(provider__category__in=categories, provider__is_enabled=True)

    if provider_slug:
        qs = qs.filter(provider__slug__iexact=provider_slug.strip())

    if country_code:
        # Match providers configured for the given country, or with country in config
        qs = qs.filter(
            provider__country_code=country_code
        ) | qs.filter(
            country_code=country_code
        )

    if query:
        qs = qs.filter(scraped_title__icontains=query)

    if available_only:
        qs = qs.filter(is_available=True)

    qs = qs.order_by("-updated_at")

    serializer = ScrapedContentMappingSerializer(qs, many=True)

    # Apply language filtering after serialization (languages are in raw_payload)
    results = serializer.data
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

    # Annotate with source indicator
    for result in results:
        provider_data = result.get("provider") or {}
        result["source_indicator"] = provider_data.get("source_indicator") or "Scraper"
        result["provider_category"] = provider_data.get("category") or "scraper"

    return Response({
        "count": len(results),
        "filters": {
            "provider": provider_slug or None,
            "country": country_code or None,
            "audio_lang": audio_lang or None,
            "subtitle_lang": subtitle_lang or None,
            "available_only": available_only,
            "include_official": include_official,
        },
        "results": results,
    })


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
      - provider_slug: required (e.g. 'rai-play')
      - query: optional search term
      - country: optional ISO country code
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

    scraper_cls = _resolve_scraper(provider_slug)
    if not scraper_cls:
        from tracker.scrapers.adapters import GenericURLAdapter
        scraper = GenericURLAdapter(
            provider_slug=provider.slug,
            domain=provider.domain or "example.com",
        )
    else:
        scraper = scraper_cls()

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


@api_view(["GET"])
def unofficial_movie_offers(request, tmdb_id: int):
    """GET /api/v2/unofficial/movie/<tmdb_id>/offers/

    Return unofficial source offers for a movie, filtered by user preferences
    and ranked by: audio match → subtitle match → quality.
    """
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return Response({"error": "movie not tracked yet"}, status=404)

    pref_dict = resolve_preferences(user_id=1)
    preference = Preference(
        user_id=1,
        audio_languages=pref_dict["audio_languages"],
        subtitle_languages=pref_dict["subtitle_languages"],
        candidate_countries=pref_dict["candidate_countries"],
        language_rule=pref_dict.get("language_rule"),
    )

    include_official = request.GET.get("include_official", "").lower() == "true"
    categories = ["scraper", "unofficial"]
    if include_official:
        categories = ["official", "free", "scraper", "unofficial"]

    # Gather unofficial scraped offers for this movie
    scraped = ScrapedContentMapping.objects.filter(
        movie=movie,
        is_available=True,
        provider__category__in=categories,
        provider__is_enabled=True,
    ).select_related("provider").order_by("-updated_at")

    offers = []
    for mapping in scraped:
        payload = mapping.raw_payload or {}
        audio = payload.get("audio_languages", []) if isinstance(payload, dict) else []
        subs = payload.get("subtitle_languages", []) if isinstance(payload, dict) else []
        offers.append(Offer.from_dict({
            "country": payload.get("country", "") if isinstance(payload, dict) else "",
            "service": mapping.provider.slug,
            "offer_type": "free",
            "quality": mapping.provider.config.get("quality") if mapping.provider.config else "",
            "url": mapping.scraped_url,
            "audio": [str(a).upper() for a in audio],
            "subs": [str(s).upper() for s in subs],
            "is_scraped_source": True,
        }))

    ranked = rank_offers(offers, preference)

    # Format for frontend
    from tracker.services.presentation import present_offer
    provider_map = {m.provider.slug: m.provider for m in scraped}
    offers_out = []
    for offer in ranked:
        presented = present_offer(offer, provider_map)
        d = offer.to_dict()
        d["service_display"] = presented.service_display
        d["source_indicator"] = presented.source_indicator
        d["country"] = presented.country
        offers_out.append(d)

    return Response({
        "movie": {
            "tmdb_id": movie.tmdb_id,
            "title": movie.title,
            "year": movie.year,
            "poster_path": movie.poster_path,
        },
        "offers": offers_out,
    })


@api_view(["GET"])
def unofficial_providers(request):
    """GET /api/v2/unofficial/providers/

    List scraper (Imported Small Providers & Sources) providers with their
    source indicators and staleness status. Official Free Streaming Providers
    are not part of the Unofficial Sources tab.
    """
    # Scrapers only by default. include_official=true re-adds Major + Free.
    categories = ["scraper"]
    if request.GET.get("include_official", "") == "true":
        categories = ["official", "free", "scraper"]

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
