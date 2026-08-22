"""DRF views for Phase 2: search, ranked availability, watchlist, preferences."""
from __future__ import annotations

import logging
from typing import Any

from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response


from .models import LanguageEntry, LanguageInfo,Movie, Preference, Region, Subscription, WatchlistItem
from .serializers import (
    LanguageInfoSerializer,
    MovieSerializer,
    PreferenceSerializer,
    RankedOfferSerializer,
    SubscriptionSerializer,
    WatchlistItemSerializer,
    WatchlistReportRowSerializer,
)
from .services.bulk_refresh import process_watchlist_refresh
from .services.cache import AvailabilityCache
from .services.letterboxd import import_rows, parse_csv
from .services.ranking import rank_offers
from .services.rate_limit import RateLimitExceeded
from .services.tmdb import TMDbClient, TMDbMovie

logger = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    """Liveness + DB connectivity check."""
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    return Response({"status": "ok", "db": "ok" if db_ok else "unreachable"})


@api_view(["GET"])
def search(request):
    """Search TMDb by title (and optional year)."""
    query = request.GET.get("q") or request.GET.get("query") or ""
    year = request.GET.get("year")
    year_int = int(year) if year and year.isdigit() else None
    if not query:
        return Response({"error": "query parameter required"}, status=400)
    try:
        client = TMDbClient()
        results = client.search(query, year=year_int)
    except Exception as exc:  # network or config error
        logger.exception("TMDb search failed")
        return Response({"error": str(exc)}, status=502)
    return Response(
        {
            "results": [
                {"tmdb_id": m.tmdb_id, "title": m.title, "year": m.year}
                for m in results[:10]
            ]
        }
    )


@api_view(["GET"])
def movie_availability(request, tmdb_id: int):
    """Return ranked, cached availability for a movie."""
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return Response({"error": "movie not tracked yet"}, status=404)

    preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)

    try:
        cache = AvailabilityCache()
        status_obj = cache.get_or_refresh(movie)
    except RateLimitExceeded as exc:
        return Response({"error": str(exc), "rate_limited": True}, status=429)

    offers = cache._read_offers(movie)
    ranked = rank_offers(offers, preference)
    return Response(
        {
            "movie": {"tmdb_id": movie.tmdb_id, "title": movie.title, "year": movie.year},
            "cache": {
                "refreshed": status_obj.refreshed,
                "reason": status_obj.reason,
                "offers": status_obj.offers_written,
            },
            "offers": RankedOfferSerializer([offer.to_dict() for offer in ranked], many=True).data,
        }
    )


@api_view(["GET", "POST"])
def subscriptions_list_create(request):
    """List or create user subscriptions (single-user, user_id=1)."""
    if request.method == "GET":
        subs = Subscription.objects.filter(user_id=1).order_by("id")
        return Response(SubscriptionSerializer(subs, many=True).data)

    serializer = SubscriptionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(user_id=1)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
def subscription_detail(request, pk: int):
    """Delete a subscription by primary key (single-user, user_id=1)."""
    sub = get_object_or_404(Subscription, pk=pk, user_id=1)
    sub.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET", "PUT"])
def preferences(request):
    """Read or update user preferences (single-user, user_id=1).

    In v0 the source of truth is server-side (settings.DEFAULT_PREFERENCE).
    On GET, if no Preference row exists, returns the hardcoded defaults
    via resolve_preferences() instead of creating an empty row.
    """
    from .services.preferences import resolve_preferences
    if request.method == "GET":
        pref_dict = resolve_preferences(user_id=1)
        return Response(pref_dict)
    # PUT — update an existing or new preference row
    pref_dict = resolve_preferences(user_id=1)
    preference, _ = Preference.objects.get_or_create(
        user_id=1,
        defaults=pref_dict,
    )
    if pref_dict["audio_languages"] != list(preference.audio_languages) or \
       pref_dict["subtitle_languages"] != list(preference.subtitle_languages) or \
       pref_dict["candidate_countries"] != list(preference.candidate_countries):
        # No existing row (we used defaults) — sync DB to defaults
        preference.audio_languages = pref_dict["audio_languages"]
        preference.subtitle_languages = pref_dict["subtitle_languages"]
        preference.candidate_countries = pref_dict["candidate_countries"]
        preference.save()
    serializer = PreferenceSerializer(preference, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(["GET", "POST"])
@parser_classes([MultiPartParser])
def watchlist(request):
    """List or import watchlist items."""
    if request.method == "GET":
        items = (
            WatchlistItem.objects.select_related("movie")
            .filter(user_id=1)
            .order_by("-added_at")
        )
        return Response(WatchlistItemSerializer(items, many=True).data)

    upload = request.FILES.get("file")
    if upload is None:
        return Response({"error": "file field required"}, status=400)
    rows = parse_csv(upload.read())
    result = import_rows(rows)
    return Response(
        {
            "resolved": WatchlistItemSerializer(result.resolved, many=True).data,
            "ambiguous": result.ambiguous,
            "skipped": result.skipped,
        }
    )


@api_view(["POST"])
def watchlist_resolve(request):
    """Resolve selected movies into Movie and WatchlistItem database records."""
    selections = request.data.get("selections") if isinstance(request.data, dict) else None
    if not selections or not isinstance(selections, list):
        return Response(
            {"error": "selections parameter is required and must not be empty"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate each item has a valid integer tmdb_id
    for item in selections:
        if not isinstance(item, dict) or "tmdb_id" not in item:
            return Response(
                {"error": "selections parameter is required and must not be empty"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tmdb_id = item.get("tmdb_id")
        if tmdb_id is None or isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int):
            return Response(
                {"error": "selections parameter is required and must not be empty"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Deduplicate selections by tmdb_id while maintaining order
    unique_selections = []
    seen_ids = set()
    for item in selections:
        tmdb_id = item["tmdb_id"]
        if tmdb_id not in seen_ids:
            seen_ids.add(tmdb_id)
            unique_selections.append(item)

    watchlist_items = []
    for sel in unique_selections:
        movie, _ = Movie.objects.get_or_create(
            tmdb_id=sel["tmdb_id"],
            defaults={
                "title": sel.get("original_title") or "",
                "year": sel.get("year"),
            },
        )
        item, _ = WatchlistItem.objects.get_or_create(
            user_id=1,
            movie=movie,
            source="letterboxd_resolved",
        )
        watchlist_items.append(item)

    return Response(
        {
            "resolved_count": len(watchlist_items),
            "items": WatchlistItemSerializer(watchlist_items, many=True).data,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def watchlist_report(request):
    """Return aggregated report for watchlist items (user_id=1)."""
    items = (
        WatchlistItem.objects.select_related("movie")
        .filter(user_id=1)
        .order_by("-added_at")
    )
    preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
    cache = AvailabilityCache()

    report = []
    country_movie_sets: dict[str, set[int]] = {}
    service_movie_sets: dict[str, set[int]] = {}
    available_movies_count = 0

    for item in items:
        movie = item.movie
        # Use cached data only to avoid blocking on external API calls.
        # Stale/missing data will be refreshed by background process.
        try:
            status = cache.get_cached_only(movie)
            if status.reason == "missing":
                logger.info("Movie tmdb_id=%s has no cached availability, skipping in report", movie.tmdb_id)
        except Exception:
            logger.exception("Failed to read cached availability for movie tmdb_id=%s", movie.tmdb_id)

        raw_offers = cache._read_offers(movie)
        qualifying_offers = [
            o for o in raw_offers
            if (o.get("offer_type") or "").lower() in ("subscription", "free")
        ]

        ranked = rank_offers(qualifying_offers, preference)

        countries = sorted(list({r.country.upper() for r in ranked if r.country}))
        best = ranked[0] if ranked else None
        best_country = best.country.upper() if best else None
        best_service = best.service if best else None

        if countries:
            available_movies_count += 1

        for c in countries:
            country_movie_sets.setdefault(c, set()).add(movie.id)

        movie_services = {r.service for r in ranked if r.service}
        for s in movie_services:
            service_movie_sets.setdefault(s, set()).add(movie.id)

        report.append(
            {
                "movie": movie,
                "available_countries": countries,
                "best_country": best_country,
                "best_service": best_service,
            }
        )

    total_movies = len(report)
    unavailable_movies_count = total_movies - available_movies_count

    country_list = [
        {"country": c, "count": len(m_set)}
        for c, m_set in country_movie_sets.items()
    ]
    country_list.sort(key=lambda x: (-x["count"], x["country"]))

    service_list = [
        {"service": s, "count": len(m_set)}
        for s, m_set in service_movie_sets.items()
    ]
    service_list.sort(key=lambda x: (-x["count"], x["service"]))

    summary = {
        "total_movies": total_movies,
        "available_movies": available_movies_count,
        "unavailable_movies": unavailable_movies_count,
        "countries": country_list,
        "services": service_list,
    }

    return Response(
        {
            "report": WatchlistReportRowSerializer(report, many=True).data,
            "summary": summary,
        }
    )


@api_view(["GET", "PUT"])
def movie_language_info(request, tmdb_id: int):
    """Read or update LanguageInfo for a movie/region pair."""
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return Response({"error": "movie not tracked yet"}, status=404)

    if request.method == "GET":
        provider = request.GET.get("provider") or ""
        country = request.GET.get("country") or ""
        if not provider.strip() or not country.strip():
            return Response(
                {"error": "provider and country parameters required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider_clean = provider.strip()
        country_clean = country.strip().upper()

        info = (
            LanguageInfo.objects.filter(
                movie=movie,
                region__provider__iexact=provider_clean,
                region__country__iexact=country_clean,
            )
            .prefetch_related("entries")
            .first()
        )

        audio = []
        subs = []
        if info:
            for entry in info.entries.all():
                if entry.kind == "audio":
                    audio.append(entry.language_code)
                elif entry.kind == "subtitle":
                    subs.append(entry.language_code)

        return Response(
            {
                "movie_tmdb_id": tmdb_id,
                "provider": provider_clean,
                "country": country_clean,
                "audio_languages": audio,
                "subtitle_languages": subs,
            }
        )

    # PUT request
    serializer = LanguageInfoSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data

    provider = validated["provider"]
    country = validated["country"]
    audio_languages = validated["audio_languages"]
    subtitle_languages = validated["subtitle_languages"]

    with transaction.atomic():
        region, _ = Region.objects.get_or_create(
            provider=provider,
            country=country,
        )
        info, _ = LanguageInfo.objects.get_or_create(
            movie=movie,
            region=region,
        )
        info.entries.all().delete()
        for code in audio_languages:
            LanguageEntry.objects.create(
                language_info=info,
                kind="audio",
                language_code=code,
            )
        for code in subtitle_languages:
            LanguageEntry.objects.create(
                language_info=info,
                kind="subtitle",
                language_code=code,
            )

    return Response(
        {
            "movie_tmdb_id": tmdb_id,
            "provider": provider,
            "country": country,
            "audio_languages": audio_languages,
            "subtitle_languages": subtitle_languages,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
def watchlist_refresh(request):
    """Trigger budgeted watchlist availability refresh (user_id=1)."""
    data = request.data if isinstance(request.data, dict) else {}
    budget_raw = data.get("budget", 10)

    if isinstance(budget_raw, bool) or not isinstance(budget_raw, int):
        return Response(
            {"error": "budget must be a positive integer"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if budget_raw <= 0:
        return Response(
            {"error": "budget must be greater than 0"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if budget_raw > 90:
        return Response(
            {"error": "budget cannot exceed 90"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    summary = process_watchlist_refresh(budget=budget_raw, user_id=1)

    if summary.get("rate_limited"):
        return Response(summary, status=status.HTTP_429_TOO_MANY_REQUESTS)

    return Response(summary, status=status.HTTP_200_OK)


@api_view(["GET"])
def health_full(request):
    """Alias kept for legacy; identical to health."""
    return health(request._request)


# --- Prime Video MVP endpoints (v0) ---

# TMDb genre IDs → readable category labels (subset for homepage chips).
GENRE_LOOKUP: dict[int, str] = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 18: "Drama", 99: "Documentary", 10751: "Family",
    14: "Fantasy", 27: "Horror", 10749: "Romance", 878: "Sci-Fi",
    53: "Thriller", 10752: "War", 37: "Western",
}

# Simple in-memory cache for the popular endpoint (10-minute TTL).
_POPULAR_CACHE: dict[str, Any] = {}
_POPULAR_TTL = 600  # seconds


@api_view(["GET"])
def prime_popular(request):
    """GET /api/prime/popular/ — trending movies for the homepage grid.

    Returns a small set of {tmdb_id, title, year, poster_path, overview, categories}.
    Cached in-memory for 10 minutes to avoid hitting TMDb on every page load.
    """
    import time as _time
    limit = request.GET.get("limit", "24")
    try:
        limit_int = max(1, min(int(limit), 30))
    except (TypeError, ValueError):
        limit_int = 24

    cached = _POPULAR_CACHE.get("data")
    ts = _POPULAR_CACHE.get("ts")
    now = _time.time()
    if cached is not None and ts is not None and (now - ts) < _POPULAR_TTL:
        data = cached
    else:
        client = TMDbClient()
        try:
            movies = client.popular(limit=limit_int)
            data = {
                "results": [_popular_movie_dict(m) for m in movies],
                "count": len(movies),
                "cached": False,
                "source": "tmdb_trending",
            }
            _POPULAR_CACHE["data"] = data
            _POPULAR_CACHE["ts"] = now
        except Exception as exc:
            logger.exception("TMDb popular fetch failed")
            if cached is not None:
                # Serve stale cache if available
                data = cached
            else:
                return Response(
                    {"error": f"TMDb fetch failed: {exc}", "results": [], "count": 0, "cached": False, "source": "error"},
                    status=502,
                )

    # Return a shallow copy so callers can't mutate the cache.
    return Response({
        "results": data["results"],
        "count": data["count"],
        "cached": cached is not None,
        "source": data["source"],
    })


def _popular_movie_dict(movie: TMDbMovie) -> dict:
    """Convert a TMDbMovie to the homepage dict shape."""
    categories = [
        GENRE_LOOKUP[gid] for gid in (movie.genre_ids or []) if gid in GENRE_LOOKUP
    ]
    return {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "poster_path": movie.poster_path,
        "overview": movie.overview,
        "categories": categories,
    }


@api_view(["GET"])
def prime_movie(request, tmdb_id: int):
    """GET /api/prime/movie/<tmdb_id>/ — ranked Prime Video offers for one movie.

    Filters to subscription/free offers only, provider=DEFAULT_PREFERENCE_PROVIDER
    ("prime"), across all countries. Uses the hardcoded DEFAULT_PREFERENCE (JA/EN)
    via resolve_preferences(). On cache miss:
      - If PRIME_LIVE_FETCH is True (default): hits Streaming Availability API.
      - If PRIME_LIVE_FETCH is False: returns only cached rows.
    """
    from django.conf import settings
    from .services.preferences import resolve_preferences
    from .services.streaming_api import service_display_name

    # Resolve or auto-create the Movie row via TMDb.
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        # Auto-create from TMDb details so the runtime join is non-empty.
        client = TMDbClient()
        try:
            details = client.details(tmdb_id)
            movie, _ = Movie.objects.get_or_create(
                tmdb_id=details.tmdb_id,
                defaults={"title": details.title, "year": details.year},
            )
        except Exception:
            # TMDb lookup failed and movie not in DB.
            return Response(
                {
                    "movie": {"tmdb_id": tmdb_id, "title": None, "year": None},
                    "offers": [],
                    "message": "Movie not tracked and could not be resolved via TMDb.",
                },
                status=404,
            )

    # Resolve preferences (hardcoded defaults if no DB row).
    pref_dict = resolve_preferences(user_id=1)

    # Build a Preference-like object for rank_offers (which expects a Preference
    # instance with audio_languages, subtitle_languages, candidate_countries).
    preference = Preference(
        user_id=1,
        audio_languages=pref_dict["audio_languages"],
        subtitle_languages=pref_dict["subtitle_languages"],
        candidate_countries=pref_dict["candidate_countries"],
    )

    # Get cached/fresh availability, gated by PRIME_LIVE_FETCH.
    cache = AvailabilityCache()
    if not getattr(settings, "PRIME_LIVE_FETCH", True):
        # Offline mode: only return cached rows, never fetch live.
        offers = cache._read_offers(movie)
    else:
        try:
            cache.get_or_refresh(movie)
        except RateLimitExceeded as exc:
            return Response(
                {"error": str(exc), "rate_limited": True}, status=429
            )
        except Exception:
            # A live availability fetch failure (provider outage, an unsubscribed
            # RapidAPI key, a network error) must not 500 the detail page — fall
            # back to whatever is already cached (possibly nothing).
            logger.exception("Live availability fetch failed for tmdb %s", tmdb_id)
        offers = cache._read_offers(movie)

    # Filter to the Prime provider + subscription/free offers only. The provider
    # is matched against the stable service id ("prime"), which is what
    # cache._write_result stores in Region.provider.
    provider = getattr(settings, "DEFAULT_PREFERENCE_PROVIDER", "prime")
    qualifying = [
        o for o in offers
        if o.get("service", "").lower() == provider.lower()
        and o.get("offer_type", "").lower() in ("subscription", "free")
    ]

    # Rank with the (hardcoded) preference. No country filter — Prime Video
    # covers all countries, so candidate_countries is empty/unused here.
    ranked = rank_offers(qualifying, preference)

    # Present the stable service id as its display name ("prime" -> "Prime Video").
    offers_out = []
    for offer in ranked:
        offer_dict = offer.to_dict()
        offer_dict["service"] = service_display_name(offer_dict.get("service", ""))
        offers_out.append(offer_dict)

    return Response(
        {
            "movie": {
                "tmdb_id": movie.tmdb_id,
                "title": movie.title,
                "year": movie.year,
            },
            "offers": offers_out,
        }
    )