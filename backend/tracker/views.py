"""DRF views for Phase 2: search, ranked availability, watchlist, preferences."""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from .languages import normalize_language_code
from .models import (
    Availability,
    ExternalProviderMapping,
    LanguageEntry,
    LanguageInfo,
    Movie,
    MovieWatchEvent,
    Preference,
    Provider,
    Region,
    ScrapedContentMapping,
    Subscription,
    UserMovieData,
    WatchlistItem,
)
from .serializers import (
    LanguageInfoSerializer,
    MovieDetailSerializer,
    MovieSerializer,
    MovieWatchEventSerializer,
    PreferenceSerializer,
    ProviderSerializer,
    RankedOfferSerializer,
    ScrapedContentMappingSerializer,
    SubscriptionSerializer,
    UserMovieDataSerializer,
    WatchlistItemSerializer,
    WatchlistReportRowSerializer,
)
from tracker.scrapers import SCRAPER_REGISTRY, ContentMatcher, ScrapedItem, resolve_scraper_class
from tracker.services.filtering import apply_composable_filters
from tracker.services.watchability import evaluate_watchability, get_user_subscriptions
import math


def paginate_list(items: list, page: int, page_size: int) -> tuple[list, dict]:
    """Helper to paginate any in-memory list."""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    total_count = len(items)
    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_items = items[start_idx:end_idx]

    pagination = {
        "count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
    }
    return page_items, pagination
from .services.bulk_refresh import process_watchlist_refresh
from .services.cache import AvailabilityCache
from .services.letterboxd import import_rows, parse_file_or_zip
from .services.ranking import rank_offers
from .services.preferences import resolve_preferences
from .services.rate_limit import RateLimitExceeded
from .services.enrichment import enrich_movie_from_tmdb, resolve_or_create_movie, sync_movie_fields
from .services.tmdb import TMDbClient, TMDbItem, TMDbMovie

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
def geo_ip(request):
    """Detect current apparent country from client IP/headers."""
    country = (
        request.META.get("HTTP_CF_IPCOUNTRY") or
        request.META.get("HTTP_X_COUNTRY_CODE") or
        request.META.get("HTTP_CLOUDFRONT_VIEWER_COUNTRY") or
        "US"
    ).strip().upper()
    return Response({"country": country, "ip": request.META.get("REMOTE_ADDR")})


@api_view(["GET"])
def search_person(request):
    """Autocomplete person/actor search via TMDb."""
    query = request.GET.get("q") or request.GET.get("query") or ""
    if not query:
        return Response({"results": []})
    try:
        client = TMDbClient(settings.TMDB_API_KEY)
        results = client.search_person(query)
        return Response({"results": results})
    except Exception as exc:
        logger.exception("Person search failed")
        return Response({"error": str(exc)}, status=502)


@api_view(["GET"])
def genres_list(request):
    """Return TMDb genres list."""
    genres = [
        {"id": 28, "name": "Action"},
        {"id": 12, "name": "Adventure"},
        {"id": 16, "name": "Animation"},
        {"id": 35, "name": "Comedy"},
        {"id": 80, "name": "Crime"},
        {"id": 99, "name": "Documentary"},
        {"id": 18, "name": "Drama"},
        {"id": 10751, "name": "Family"},
        {"id": 14, "name": "Fantasy"},
        {"id": 36, "name": "History"},
        {"id": 27, "name": "Horror"},
        {"id": 10402, "name": "Music"},
        {"id": 9648, "name": "Mystery"},
        {"id": 10749, "name": "Romance"},
        {"id": 878, "name": "Sci-Fi"},
        {"id": 10770, "name": "TV Movie"},
        {"id": 53, "name": "Thriller"},
        {"id": 10752, "name": "War"},
        {"id": 37, "name": "Western"},
    ]
    return Response({"results": genres})


@api_view(["GET"])
def homepage_feed(request):
    """GET /api/homepage/ — returns only watchable titles (🟢 Watchable here & 🟡 Watchable with VPN) with pagination."""
    curr_country = request.GET.get("country") or "US"
    pref = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
    user_subs = get_user_subscriptions(user_id=1)

    movies = Movie.objects.all().order_by("-id")
    evaluated, _ = apply_composable_filters(
        movies,
        request.GET,
        pref,
        user_subscriptions=user_subs,
        current_country=curr_country,
    )

    watchable_items = [
        item for item in evaluated
        if item["watchability"].status in ("watchable_here", "watchable_vpn")
    ]

    results = []
    for item in watchable_items:
        m_data = MovieSerializer(item["movie"]).data
        m_data["watchability"] = item["watchability"].to_dict()
        results.append(m_data)

    page = int(request.GET.get("page", 1)) if str(request.GET.get("page", "")).isdigit() else 1
    page_size = int(request.GET.get("page_size", 24)) if str(request.GET.get("page_size", "")).isdigit() else 24
    paged_results, pagination = paginate_list(results, page, page_size)

    return Response({**pagination, "results": paged_results})


@api_view(["GET"])
def unified_catalog(request):
    """GET /api/catalog/unified/ — Unified catalog endpoint merging official and unofficial sources."""
    curr_country = request.GET.get("current_country") or request.GET.get("country") or "US"
    pref = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
    user_subs = get_user_subscriptions(user_id=1)

    raw_providers = request.GET.get("providers") or request.GET.get("provider") or ""
    raw_categories = request.GET.get("provider_categories") or ""

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

    unified_items = []
    seen_tmdb_ids = set()

    # 1. Fetch Official Catalog Items
    if include_official:
        movies = Movie.objects.all().order_by("-id")
        evaluated, _ = apply_composable_filters(
            movies,
            request.GET,
            pref,
            user_subscriptions=user_subs,
            current_country=curr_country,
            allowed_provider_slugs=allowed_provider_slugs,
        )

        for item in evaluated:
            m = item["movie"]
            watch_res = item["watchability"]
            seen_tmdb_ids.add(m.tmdb_id)

            best_offer = watch_res.best_offer.to_dict() if watch_res.best_offer else None
            watch_url = best_offer.get("url") if best_offer else None

            # Determine provider details from best offer or watchability
            provider_info = None
            p_obj = None
            if best_offer and best_offer.get("service"):
                svc_slug = best_offer["service"]
                p_obj = Provider.objects.filter(slug__iexact=svc_slug).first()
                provider_info = {
                    "slug": svc_slug,
                    "name": p_obj.name if p_obj else svc_slug.capitalize(),
                    "category": p_obj.category if p_obj else "official",
                }

            source_ind = p_obj.source_indicator if p_obj else "Official"

            unified_items.append({
                "id": f"official_{m.id or m.tmdb_id}",
                "tmdb_id": m.tmdb_id,
                "source": "official",
                "source_indicator": source_ind,
                "title": m.title,
                "year": m.year,
                "poster_path": m.poster_path,
                "content_type": m.content_type or "movie",
                "provider": provider_info,
                "watchability": watch_res.to_dict(),
                "unofficial_details": None,
                "watch_url": watch_url,
            })

    # 2. Fetch Scraper Content Mappings
    if include_scraper:
        qs = ScrapedContentMapping.objects.select_related("provider", "movie").filter(
            provider__is_enabled=True,
            is_available=True,
        )

        # Apply provider filtering
        if allowed_provider_slugs is not None:
            qs = qs.filter(provider__slug__in=list(allowed_provider_slugs))

        # Text search filter
        q = (request.GET.get("q") or request.GET.get("query") or "").strip()
        if q:
            qs = qs.filter(scraped_title__icontains=q)

        # Country filter
        country_code = (request.GET.get("country") or "").strip().upper()
        if country_code:
            qs = qs.filter(provider__country_code=country_code) | qs.filter(country_code=country_code)

        # Year filter
        year_min = request.GET.get("year_min")
        year_max = request.GET.get("year_max")
        if year_min and str(year_min).isdigit():
            qs = qs.filter(scraped_year__gte=int(year_min))
        if year_max and str(year_max).isdigit():
            qs = qs.filter(scraped_year__lte=int(year_max))

        qs = qs.order_by("-updated_at")

        for mapping in qs:
            m = mapping.movie
            # If movie is already in official catalog results, deduplicate / skip or merge
            if m and m.tmdb_id in seen_tmdb_ids:
                continue

            payload = mapping.raw_payload or {}
            audio = [str(a).upper() for a in (payload.get("audio_languages") or payload.get("audio") or [])]
            subs = [str(s).upper() for s in (payload.get("subtitle_languages") or payload.get("subtitles") or [])]

            unified_items.append({
                "id": f"scraper_{mapping.id}",
                "tmdb_id": m.tmdb_id if m else None,
                "source": "scraper",
                "source_indicator": mapping.provider.source_indicator or "Scraper",
                "title": m.title if m else mapping.scraped_title,
                "year": m.year if m else mapping.scraped_year,
                "poster_path": m.poster_path if m else "",
                "content_type": m.content_type if m else (payload.get("content_type") or "movie"),
                "provider": {
                    "slug": mapping.provider.slug,
                    "name": mapping.provider.name,
                    "category": mapping.provider.category,
                },
                "watchability": None,
                "unofficial_details": {
                    "scraped_url": mapping.scraped_url,
                    "confidence_score": mapping.confidence_score,
                    "is_confirmed": mapping.is_confirmed,
                    "audio_languages": audio,
                    "subtitle_languages": subs,
                },
                "watch_url": mapping.scraped_url,
            })

    # Sort combined results if sort_by parameter is provided
    sort_by = (request.GET.get("sort_by") or request.GET.get("sort") or "").lower().strip()
    if sort_by:
        def get_item_rating(item):
            if item.get("source") == "official":
                return getattr(item.get("watchability"), "tmdb_rating", None) or getattr(Movie.objects.filter(tmdb_id=item.get("tmdb_id")).first(), "tmdb_rating", None)
            return None

        def get_item_year(item):
            return item.get("year")

        def get_item_title(item):
            return (item.get("title") or "").lower()

        if sort_by in ("rating_desc", "rating_high"):
            unified_items.sort(key=lambda x: (get_item_rating(x) or -1, get_item_year(x) or -1), reverse=True)
        elif sort_by in ("rating_asc", "rating_low"):
            unified_items.sort(key=lambda x: (get_item_rating(x) if get_item_rating(x) is not None else 999, get_item_year(x) if get_item_year(x) is not None else 9999))
        elif sort_by in ("year_desc", "year_newest"):
            unified_items.sort(key=lambda x: get_item_year(x) or -1, reverse=True)
        elif sort_by in ("year_asc", "year_oldest"):
            unified_items.sort(key=lambda x: get_item_year(x) if get_item_year(x) is not None else 9999)
        elif sort_by in ("title_asc", "title"):
            unified_items.sort(key=get_item_title)
        elif sort_by in ("title_desc", "-title"):
            unified_items.sort(key=get_item_title, reverse=True)

    page = int(request.GET.get("page", 1)) if str(request.GET.get("page", "")).isdigit() else 1
    page_size = int(request.GET.get("page_size", 24)) if str(request.GET.get("page_size", "")).isdigit() else 24
    paged_results, pagination = paginate_list(unified_items, page, page_size)

    return Response({**pagination, "results": paged_results})


@api_view(["GET"])
def search(request):
    """Search TMDb by title (and optional year).

    Optional ``type`` (alias ``content_type``) selects the target:
    ``movie`` (default), ``series``/``tv``, or ``all``. Every result carries
    an explicit ``content_type`` of ``movie`` or ``series``.
    """
    query = request.GET.get("q") or request.GET.get("query") or ""
    year = request.GET.get("year")
    year_int = int(year) if year and year.isdigit() else None
    if not query:
        return Response({"error": "query parameter required"}, status=400)

    raw_type = (request.GET.get("type") or request.GET.get("content_type") or "movie").strip().lower()
    if raw_type not in ("movie", "series", "tv", "all"):
        return Response(
            {"error": "invalid type: expected movie, series, tv or all"},
            status=400,
        )
    search_type = "series" if raw_type == "tv" else raw_type

    try:
        client = TMDbClient(settings.TMDB_API_KEY)
        results = client.search(query, year=year_int, content_type=search_type)
    except Exception as exc:  # network or config error
        logger.exception("TMDb search failed")
        return Response({"error": str(exc)}, status=502)

    # "all" runs two TMDb searches; interleave so neither type is starved
    # by the result cap below.
    if search_type == "all":
        movies = [m for m in results if (m.content_type or "movie") == "movie"]
        series = [m for m in results if (m.content_type or "movie") != "movie"]
        merged = []
        for i in range(max(len(movies), len(series))):
            if i < len(movies):
                merged.append(movies[i])
            if i < len(series):
                merged.append(series[i])
        results = merged

    return Response(
        {
            "results": [
                {
                    "tmdb_id": m.tmdb_id,
                    "title": m.title,
                    "year": m.year,
                    "content_type": m.content_type or "movie",
                }
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

    offers = cache.read_offers(movie)
    ranked = rank_offers(offers, preference)

    user_data = UserMovieData.objects.filter(user_id=1, movie=movie).first()
    watch_events = MovieWatchEvent.objects.filter(user_id=1, movie=movie).order_by("-watched_date", "-created_at")

    return Response(
        {
            "movie": {"tmdb_id": movie.tmdb_id, "title": movie.title, "year": movie.year, "poster_path": movie.poster_path},
            "user_data": UserMovieDataSerializer(user_data).data if user_data else None,
            "watch_events": MovieWatchEventSerializer(watch_events, many=True).data,
            "cache": {
                "refreshed": status_obj.refreshed,
                "reason": status_obj.reason,
                "offers": status_obj.offers_written,
            },
            "offers": RankedOfferSerializer([offer.to_dict() for offer in ranked], many=True).data,
        }
    )


@api_view(["GET"])
def providers_list(request):
    """List canonical providers catalog with subscription state for user_id=1."""
    category = request.GET.get("category")
    is_major = request.GET.get("is_major")

    qs = Provider.objects.filter(is_enabled=True).prefetch_related("external_mappings").order_by("id")
    if category:
        qs = qs.filter(category=category.lower())
    if is_major is not None:
        qs = qs.filter(is_major=is_major.lower() in ("true", "1"))

    user_sub_provider_ids = set(
        Subscription.objects.filter(user_id=1, provider__isnull=False).values_list("provider_id", flat=True)
    )

    serializer = ProviderSerializer(
        qs, many=True, context={"user_subscribed_ids": user_sub_provider_ids}
    )
    return Response(serializer.data)


@api_view(["POST"])
def provider_toggle(request, slug: str):
    """Toggle user subscription state for a provider by slug."""
    provider = get_object_or_404(Provider, slug__iexact=slug, is_enabled=True)
    sub = Subscription.objects.filter(user_id=1, provider=provider).first()

    if sub:
        sub.delete()
        is_subscribed = False
    else:
        Subscription.objects.create(user_id=1, provider=provider, name=provider.name)
        is_subscribed = True

    return Response({
        "provider": provider.slug,
        "is_subscribed": is_subscribed,
        "message": f"{'Subscribed to' if is_subscribed else 'Unsubscribed from'} {provider.name}"
    })


@api_view(["GET", "POST"])
def subscriptions_list_create(request):
    """List or create user subscriptions (single-user, user_id=1)."""
    if request.method == "GET":
        subs = Subscription.objects.filter(user_id=1).order_by("id")
        return Response(SubscriptionSerializer(subs, many=True).data)

    serializer = SubscriptionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data
    resolved_p = validated.get("resolved_provider")

    if resolved_p:
        sub, created = Subscription.objects.get_or_create(
            user_id=1,
            provider=resolved_p,
            defaults={"name": resolved_p.name},
        )
    else:
        name_clean = (validated.get("name") or "").strip()
        sub, created = Subscription.objects.get_or_create(
            user_id=1,
            name=name_clean,
        )

    return Response(SubscriptionSerializer(sub).data, status=status.HTTP_201_CREATED)


from urllib.parse import urlparse
from django.utils import timezone


def detect_provider_from_url(url: str) -> tuple[Provider, str]:
    """Dynamically get or create a scraper provider from any URL."""
    parsed = urlparse(url.strip())
    domain = parsed.netloc.lower() or parsed.path.split("/")[0].lower()
    domain_clean = domain.replace("www.", "")

    if not domain_clean:
        raise ValueError("Invalid URL: domain could not be parsed.")

    # Derive slug and display name
    slug = domain_clean.split(".")[0]
    name = domain_clean.capitalize()

    provider, _ = Provider.objects.get_or_create(
        slug=slug,
        defaults={
            "name": name,
            "category": "scraper",
            "is_major": False,
            "is_enabled": True,
            "icon_name": slug,
            "domain": domain_clean,
        },
    )
    if not provider.domain:
        provider.domain = domain_clean
        provider.save()

    parts = [p for p in parsed.path.split("/") if p]
    query = parts[-1].replace("-", " ").replace("_", " ") if parts else name
    return provider, query


@api_view(["POST"])
def scraper_import_url(request):
    """Import a small provider/source by URL, trigger the scraper, and register source."""
    from tracker.scrapers.adapters import GenericURLAdapter

    data = request.data if isinstance(request.data, dict) else {}
    url = (data.get("url") or "").strip()
    if not url:
        return Response({"error": "URL is required"}, status=400)

    try:
        provider, query = detect_provider_from_url(url)
    except Exception as exc:
        return Response({"error": str(exc)}, status=400)

    scraper_cls = resolve_scraper_class(provider.slug)
    if scraper_cls:
        scraper = scraper_cls()
    else:
        scraper = GenericURLAdapter(provider_slug=provider.slug, domain=provider.domain or "example.com")

    items = scraper.run(query=query)

    provider.last_scraped_at = timezone.now()
    provider.save()

    # Automatically subscribe/register user to this small provider source
    Subscription.objects.get_or_create(
        user_id=1,
        provider=provider,
        defaults={"name": provider.name},
    )

    matcher = ContentMatcher()
    results = []
    for item in items:
        if not item.source_url:
            item.source_url = url
        movie, score, candidates, is_confirmed = matcher.match_item(item)
        results.append({
            "scraped_item": {
                "title": item.title,
                "year": item.year,
                "content_type": item.content_type,
                "provider_slug": item.provider_slug,
                "source_url": item.source_url,
                "audio_languages": item.audio_languages,
                "subtitle_languages": item.subtitle_languages,
                "quality": item.quality,
            },
            "matched_movie": MovieSerializer(movie).data if movie else None,
            "confidence_score": score,
            "is_confirmed": is_confirmed,
            "candidates": candidates,
        })

    return Response({
        "imported_url": url,
        "provider": ProviderSerializer(provider).data,
        "query": query,
        "count": len(results),
        "results": results,
    }, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def scraper_run(request):
    """Trigger a scraper run for a given scraper provider slug."""
    from tracker.scrapers.adapters import GenericURLAdapter

    data = request.data if isinstance(request.data, dict) else {}
    provider_slug = data.get("provider_slug") or data.get("slug") or ""
    query = data.get("query")
    if query is not None and not str(query).strip():
        query = None

    if not provider_slug:
        return Response({"error": "provider_slug is required"}, status=400)

    provider = Provider.objects.filter(slug__iexact=provider_slug).first()
    if not provider:
        return Response({"error": f"Provider '{provider_slug}' not found"}, status=404)

    scraper_cls = resolve_scraper_class(provider.slug)
    if scraper_cls:
        scraper = scraper_cls()
    else:
        scraper = GenericURLAdapter(provider_slug=provider.slug, domain=provider.domain or "example.com")

    items = scraper.run(query=query)

    provider.last_scraped_at = timezone.now()
    provider.save()

    # Ensure all scraped items are saved to database mappings for provider stats
    for item in items:
        payload = dict(item.raw_payload) if isinstance(item.raw_payload, dict) else {}
        payload.setdefault("content_type", item.content_type)
        ScrapedContentMapping.objects.get_or_create(
            provider=provider,
            scraped_title=item.title,
            scraped_year=item.year,
            defaults={
                "scraped_url": item.source_url,
                "raw_payload": payload,
                "is_available": item.availability,
            },
        )

    matcher = ContentMatcher()
    results = []
    # Match first 30 items against TMDb to keep HTTP response fast
    for item in items[:30]:
        movie, score, candidates, is_confirmed = matcher.match_item(item)
        results.append({
            "scraped_item": {
                "title": item.title,
                "year": item.year,
                "content_type": item.content_type,
                "provider_slug": item.provider_slug,
                "source_url": item.source_url,
                "audio_languages": item.audio_languages,
                "subtitle_languages": item.subtitle_languages,
                "quality": item.quality,
            },
            "matched_movie": MovieSerializer(movie).data if movie else None,
            "confidence_score": score,
            "is_confirmed": is_confirmed,
            "candidates": candidates,
        })

    return Response({
        "provider": provider.slug,
        "query": query,
        "count": len(results),
        "results": results,
    })


@api_view(["DELETE"])
def provider_delete(request, slug: str):
    """Delete a custom scraper provider and its scraped mappings."""
    provider = get_object_or_404(Provider, slug__iexact=slug)
    if provider.is_major:
        return Response({"error": "Major canonical providers cannot be deleted."}, status=400)

    provider.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def catalog_list(request):
    """GET /api/catalog/ — List all tracked catalog items with composable filters, watchability & pagination."""
    curr_country = request.GET.get("country") or "US"
    pref = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
    user_subs = get_user_subscriptions(user_id=1)

    movies = Movie.objects.all().order_by("-id")
    evaluated, _ = apply_composable_filters(
        movies,
        request.GET,
        pref,
        user_subscriptions=user_subs,
        current_country=curr_country,
    )

    results = []
    for item in evaluated:
        m_data = MovieSerializer(item["movie"]).data
        m_data["watchability"] = item["watchability"].to_dict()
        results.append(m_data)

    page = int(request.GET.get("page", 1)) if str(request.GET.get("page", "")).isdigit() else 1
    page_size = int(request.GET.get("page_size", 24)) if str(request.GET.get("page_size", "")).isdigit() else 24
    paged_results, pagination = paginate_list(results, page, page_size)

    return Response({**pagination, "results": paged_results})


@api_view(["GET"])
def scraper_unresolved(request):
    """List unconfirmed/ambiguous scraped mappings for review."""
    unresolved = ScrapedContentMapping.objects.filter(
        is_confirmed=False
    ).select_related("provider", "movie").order_by("-updated_at")

    matcher = ContentMatcher()
    out = []
    for mapping in unresolved:
        payload = mapping.raw_payload if isinstance(mapping.raw_payload, dict) else {}
        # The type the scrape was classified with, stored by _upsert_mapping.
        content_type = payload.get("content_type") or "movie"
        item = ScrapedItem(
            title=mapping.scraped_title,
            provider_slug=mapping.provider.slug,
            year=mapping.scraped_year,
            content_type=content_type,
            source_url=mapping.scraped_url,
        )

        candidates = []
        try:
            raw_cands = matcher.tmdb_client.search(
                item.title, year=item.year, content_type=content_type
            )
            candidates = [
                {
                    "tmdb_id": c.tmdb_id,
                    "title": c.title,
                    "year": c.year,
                    "confidence_score": matcher.calculate_confidence(item, c),
                }
                for c in raw_cands[:5]
            ]
        except Exception:
            pass

        data = ScrapedContentMappingSerializer(mapping).data
        data["candidates"] = candidates
        out.append(data)

    return Response(out)


@api_view(["POST"])
def scraper_confirm_match(request):
    """Confirm a mapping between scraped content and a TMDb movie.

    ``content_type`` (``movie`` default, or ``series``) picks the TMDb details
    endpoint and is stored on a newly created row. An already-tracked row is only
    retyped when the caller states the type explicitly — a scraper result never
    silently flips an existing row.
    """
    data = request.data if isinstance(request.data, dict) else {}
    mapping_id = data.get("mapping_id")
    tmdb_id = data.get("tmdb_id")

    if not mapping_id or not tmdb_id:
        return Response({"error": "mapping_id and tmdb_id are required"}, status=400)

    raw_type = str(data.get("content_type") or "").strip().lower()
    if raw_type and raw_type not in ("movie", "series"):
        return Response({"error": "content_type must be 'movie' or 'series'"}, status=400)
    content_type = raw_type or "movie"

    mapping = get_object_or_404(ScrapedContentMapping, id=mapping_id)

    client = TMDbClient(settings.TMDB_API_KEY)
    try:
        details = client.details(tmdb_id, content_type=content_type)
        movie, created = Movie.objects.get_or_create(
            tmdb_id=details.tmdb_id,
            defaults={
                "title": details.title,
                "year": details.year,
                "content_type": details.content_type or content_type,
            },
        )
        if not created and raw_type and movie.content_type != details.content_type:
            movie.content_type = details.content_type
            movie.save(update_fields=["content_type"])
    except Exception as exc:
        return Response({"error": f"Failed to fetch TMDb details: {exc}"}, status=400)

    mapping.movie = movie
    mapping.is_confirmed = True
    mapping.confidence_score = 1.0
    mapping.save()

    return Response(ScrapedContentMappingSerializer(mapping).data)


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
        pref_dict["candidate_countries"] != list(preference.candidate_countries) or \
        pref_dict["current_country"] != preference.current_country:
        # No existing row (we used defaults) — sync DB to defaults
        preference.audio_languages = pref_dict["audio_languages"]
        preference.subtitle_languages = pref_dict["subtitle_languages"]
        preference.candidate_countries = pref_dict["candidate_countries"]
        preference.current_country = pref_dict["current_country"]
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
        try:
            items = (
                WatchlistItem.objects.select_related("movie")
                .filter(user_id=1)
                .order_by("-added_at")
            )
            return Response(WatchlistItemSerializer(items, many=True).data)
        except Exception as exc:
            logger.exception("Failed to fetch watchlist")
            return Response([], status=status.HTTP_200_OK)

    upload = request.FILES.get("file")
    if upload is None:
        return Response({"error": "file field required"}, status=400)
    file_bytes = upload.read()
    filename = upload.name or ""
    rows = parse_file_or_zip(file_bytes, filename=filename)
    result = import_rows(rows)
    return Response(
        {
            "resolved": WatchlistItemSerializer(result.resolved, many=True).data,
            "ambiguous": result.ambiguous,
            "skipped": result.skipped,
            "watched_count": result.watched_count,
            "ratings_count": result.ratings_count,
            "reviews_count": result.reviews_count,
            "diary_events_count": result.diary_events_count,
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
        movie, created = Movie.objects.get_or_create(
            tmdb_id=sel["tmdb_id"],
            defaults={
                "title": sel.get("original_title") or "",
                "year": sel.get("year"),
                "poster_path": sel.get("poster_path") or "",
            },
        )
        if not created and sel.get("poster_path") and not movie.poster_path:
            movie.poster_path = sel.get("poster_path")
            movie.save()
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
    """Return aggregated report for watchlist items (user_id=1) with full watchability evaluation, composable filters & pagination."""
    curr_country = request.GET.get("current_country") or request.query_params.get("current_country") or request.GET.get("country") or "US"
    preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
    user_subs = get_user_subscriptions(user_id=1)

    items = (
        WatchlistItem.objects.select_related("movie")
        .filter(user_id=1)
        .order_by("-added_at")
    )
    watchlist_movies = [item.movie for item in items]

    # Apply composable filters to the watchlist movies
    evaluated_filtered, _ = apply_composable_filters(
        watchlist_movies,
        request.GET,
        preference,
        user_subscriptions=user_subs,
        current_country=curr_country,
    )

    cache = AvailabilityCache()

    report = []
    country_movie_sets: dict[str, set[int]] = {}
    service_movie_sets: dict[str, set[int]] = {}
    available_movies_count = 0

    for eval_item in evaluated_filtered:
        movie = eval_item["movie"]
        watch_res = eval_item["watchability"]

        # Check if movie is marked as watched for watchlist status
        user_data = UserMovieData.objects.filter(user_id=1, movie=movie).first()
        is_watched = bool(user_data and user_data.is_watched)

        # Skip filtering offers - just use ranked offers for country/service breakdown
        raw_offers = cache.read_offers(movie)
        ranked = rank_offers(raw_offers, preference, current_country=curr_country)

        countries = sorted(list({r.country.upper() for r in ranked if r.country}))
        best = ranked[0] if ranked else None
        best_country = best.country.upper() if best else None
        best_service = best.service if best else None

        if countries or watch_res.status in ("watchable_here", "watchable_vpn"):
            available_movies_count += 1

        for c in countries:
            country_movie_sets.setdefault(c, set()).add(movie.id)

        movie_services = {r.service for r in ranked if r.service}
        for s in movie_services:
            service_movie_sets.setdefault(s, set()).add(movie.id)

        report.append(
            {
                "movie": MovieSerializer(movie).data,
                "available_countries": countries,
                "best_country": best_country,
                "best_service": best_service,
                "watchability": watch_res.to_dict(),
                "is_watched": is_watched,
                "user_data": UserMovieDataSerializer(user_data).data if user_data else None,
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

    page = int(request.GET.get("page", 1)) if str(request.GET.get("page", "")).isdigit() else 1
    page_size = int(request.GET.get("page_size", 24)) if str(request.GET.get("page_size", "")).isdigit() else 24
    paged_report, pagination = paginate_list(report, page, page_size)

    return Response(
        {
            "report": paged_report,
            "pagination": pagination,
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
                norm = normalize_language_code(entry.language_code) or entry.language_code.upper()
                if entry.kind == "audio" and norm not in audio:
                    audio.append(norm)
                elif entry.kind == "subtitle" and norm not in subs:
                    subs.append(norm)

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
_POPULAR_SERIES_CACHE: dict[str, Any] = {}
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
        client = TMDbClient(settings.TMDB_API_KEY)
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


@api_view(["GET"])
def prime_popular_series(request):
    """GET /api/prime/popular-series/ — series across all active providers (up to limit).

    Combines tracked series from active providers in the database with TMDb series
    up to limit (default 48, max 100).
    """
    import time as _time
    limit = request.GET.get("limit", "48")
    try:
        limit_int = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit_int = 48

    cache_key = f"data_{limit_int}"
    cached = _POPULAR_SERIES_CACHE.get(cache_key)
    ts = _POPULAR_SERIES_CACHE.get(f"ts_{limit_int}")
    now = _time.time()
    if cached is not None and ts is not None and (now - ts) < _POPULAR_TTL:
        data = cached
    else:
        # Fetch series in DB across active providers
        db_series_qs = Movie.objects.filter(content_type="series").order_by("-id")[:limit_int]
        results = [
            _popular_movie_dict(
                TMDbItem(
                    tmdb_id=m.tmdb_id,
                    title=m.title,
                    year=m.year,
                    content_type="series",
                    poster_path=m.poster_path,
                    overview=m.overview,
                    genre_ids=m.genre_ids,
                )
            )
            for m in db_series_qs
        ]
        seen_tmdb_ids = {m.tmdb_id for m in db_series_qs}

        # Fill remaining slots with trending TV series from TMDb if below limit
        if len(results) < limit_int:
            client = TMDbClient(settings.TMDB_API_KEY)
            try:
                tmdb_series = client.popular_series(limit=limit_int)
                for s in tmdb_series:
                    if s.tmdb_id not in seen_tmdb_ids:
                        results.append(_popular_movie_dict(s))
                        seen_tmdb_ids.add(s.tmdb_id)
                        if len(results) >= limit_int:
                            break
            except Exception as exc:
                logger.warning("TMDb popular_series fallback failed: %s", exc)

        data = {
            "results": results[:limit_int],
            "count": len(results[:limit_int]),
            "cached": False,
            "source": "active_providers_and_tmdb",
        }
        _POPULAR_SERIES_CACHE[cache_key] = data
        _POPULAR_SERIES_CACHE[f"ts_{limit_int}"] = now

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
        "content_type": movie.content_type,
        "categories": categories,
    }


def _current_country(request) -> str:
    """Resolve the active country: explicit query param, then saved preference."""
    return (
        request.query_params.get("current_country")
        or resolve_preferences(user_id=1).get("current_country")
        or ""
    )


def _load_content_item(tmdb_id: int, content_type: str) -> Movie | None:
    """Load the tracked row for tmdb_id, resolving it through TMDb when missing.

    Movies keep the historical behavior: any existing row is served as-is and
    an unknown id is resolved with the movie details endpoint. A series only
    reuses a row already typed as a series; a missing or mistyped row is
    (re)resolved through the TMDb TV details endpoint. Returns None when
    nothing could be resolved (the failure is logged).
    """
    try:
        item = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        item = None

    if item is not None and (content_type == "movie" or item.content_type == content_type):
        return item

    try:
        return resolve_or_create_movie(tmdb_id, content_type=content_type)
    except Exception:
        logger.exception("TMDb resolve failed for %s %s", content_type, tmdb_id)
        return None


def _requested_season(request, item: Movie) -> int | None:
    """Season filter from ``?season=N`` for series only.

    A non-integer value is treated as absent (ignored), matching how other
    optional params behave here. A well-formed but unknown season number is
    kept: the detail payload echoes it back as ``selected_season`` and the
    offers list comes out empty, so a stale URL degrades instead of erroring.
    """
    if item.content_type != "series":
        return None
    raw = request.query_params.get("season")
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _season_summaries(item: Movie) -> list[dict]:
    """Series season list for the UI, straight from TMDb's ``seasons_data``.

    The selector is metadata-driven (not cache-driven) so a season the streaming
    catalog has no option for still shows up; selecting it simply yields no
    offers.
    """
    out: list[dict] = []
    for entry in item.seasons_data or []:
        if not isinstance(entry, dict):
            continue
        number = entry.get("season_number")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        out.append(
            {
                "season": number,
                "name": entry.get("name") or "",
                "episode_count": entry.get("episode_count") or 0,
                "poster_path": entry.get("poster_path") or "",
            }
        )
    out.sort(key=lambda row: row["season"])
    return out


def _build_prime_offers(request, movie: Movie, season: int | None = None) -> list[dict]:
    """Ranked official + BVOD offers for one tracked movie or series.

    ``season`` (series only) restricts the official result set to rows that
    recorded that season; the scraped BVOD block is skipped because scraper
    rows are show-level and carry no season signal.
    """
    from tracker.models import ProviderCategory
    from tracker.services.offers import Offer
    from tracker.services.presentation import present_offer
    from tracker.services.ranking import rank_offers

    from .services.offer_pipeline import OfficialOffersPipeline

    pipeline = OfficialOffersPipeline(
        user_id=1,
        default_provider_slug=getattr(settings, "DEFAULT_PREFERENCE_PROVIDER", "prime"),
        provider_category_set={ProviderCategory.OFFICIAL, ProviderCategory.BVOD, ProviderCategory.AVOD, ProviderCategory.TVOD, ProviderCategory.FAST},
    )
    current_country = _current_country(request)
    offers_out = pipeline.run(
        movie, current_country=current_country, season=season
    ).offers_out

    if season is not None:
        return offers_out

    # Include BVOD scraped offers in the main offers list
    bvod_categories = ["bvod", "avod", "tvod", "fast"]
    scraped = ScrapedContentMapping.objects.filter(
        movie=movie,
        is_available=True,
        provider__category__in=bvod_categories,
        provider__is_enabled=True,
    ).select_related("provider")

    if scraped:
        preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
        provider_map = {p.provider.slug: p.provider for p in scraped}
        bvod_offers = []
        for mapping in scraped:
            payload = mapping.raw_payload or {}
            audio = payload.get("audio_languages", []) if isinstance(payload, dict) else []
            subs = payload.get("subtitle_languages", []) if isinstance(payload, dict) else []
            bvod_offers.append(Offer.from_dict({
                "country": payload.get("country", "") if isinstance(payload, dict) else "",
                "service": mapping.provider.slug,
                "offer_type": "free",
                "quality": mapping.provider.config.get("quality") if mapping.provider.config else "",
                "url": mapping.scraped_url,
                "audio": [str(a).upper() for a in audio],
                "subs": [str(s).upper() for s in subs],
                "is_scraped_source": True,
            }))

        ranked = rank_offers(bvod_offers, preference, current_country=current_country)
        for offer in ranked:
            presented = present_offer(offer, provider_map)
            offer_dict = offer.to_dict()
            offer_dict["service"] = presented.service_display
            offer_dict["source_indicator"] = presented.source_indicator
            offer_dict["country"] = presented.country
            offers_out.append(offer_dict)

    return offers_out


def _prime_detail_response(request, item: Movie, key: str) -> Response:
    """Shared detail payload for prime_movie / prime_series, keyed by ``key``."""
    user_data = UserMovieData.objects.filter(user_id=1, movie=item).first()
    is_in_watchlist = WatchlistItem.objects.filter(user_id=1, movie=item).exists()
    watch_events = MovieWatchEvent.objects.filter(user_id=1, movie=item).order_by("-watched_date", "-created_at")
    season = _requested_season(request, item)
    payload = {
        key: MovieDetailSerializer(item).data,
        "user_data": UserMovieDataSerializer(user_data).data if user_data else None,
        "is_in_watchlist": is_in_watchlist,
        "watch_events": MovieWatchEventSerializer(watch_events, many=True).data,
        "offers": _build_prime_offers(request, item, season=season),
    }
    if key == "series":
        payload["seasons"] = _season_summaries(item)
        payload["selected_season"] = season
    return Response(payload)


@api_view(["GET"])
def prime_movie(request, tmdb_id: int):
    """GET /api/prime/movie/<tmdb_id>/ — ranked Prime Video offers for one movie.
    Filters to subscription/free offers only.
    """
    movie = _load_content_item(tmdb_id, "movie")
    if movie is None:
        return Response(
            {
                "movie": {"tmdb_id": tmdb_id, "title": None, "year": None},
                "offers": [],
                "message": "Movie not tracked and could not be resolved via TMDb.",
            },
            status=404,
        )
    return _prime_detail_response(request, movie, "movie")


@api_view(["GET"])
def prime_series(request, tmdb_id: int):
    """GET /api/prime/series/<tmdb_id>/ — ranked Prime Video offers for one series.

    Mirrors prime_movie but resolves the row through the TMDb TV endpoint and
    is keyed on ``series`` so callers can tell movies and series apart.

    Series responses also carry ``seasons`` (TMDb season metadata for the
    selector) and ``selected_season``. The optional ``?season=N`` query param
    narrows ``offers`` to the rows recorded for that season; with no param the
    unfiltered, show-level result is returned unchanged.
    """
    series = _load_content_item(tmdb_id, "series")
    if series is None:
        return Response(
            {
                "series": {"tmdb_id": tmdb_id, "title": None, "year": None},
                "offers": [],
                "message": "Series not tracked and could not be resolved via TMDb.",
            },
            status=404,
        )
    return _prime_detail_response(request, series, "series")


def _tmdb_missing_id(exc: BaseException) -> bool:
    """True when a TMDb details fetch failed because the id has no entry there."""
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 404:
        return True
    text = str(exc).lower()
    return "404" in text or "not found" in text


def _resolve_watch_item(tmdb_id: int) -> Movie | None:
    """Load the tracked row behind /watch/<tmdb_id>/, resolving via TMDb if missing.

    An existing row is always used as-is — it carries the only signal of
    whether the id is a movie or a series. When no row exists yet the movie
    details endpoint is tried first (historical behavior), then, if TMDb
    reports that the id does not exist as a movie, the TV endpoint. Returns
    None when nothing could be resolved (each failure is logged).
    """
    try:
        return Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        pass

    try:
        return resolve_or_create_movie(tmdb_id)
    except Exception as movie_exc:
        logger.exception("TMDb movie resolve failed for watch redirect %s", tmdb_id)
        if not _tmdb_missing_id(movie_exc):
            return None

    try:
        return resolve_or_create_movie(tmdb_id, content_type="series")
    except Exception:
        logger.exception("TMDb series resolve failed for watch redirect %s", tmdb_id)
        return None


@api_view(["GET"])
def watch_redirect(request, tmdb_id: int):
    """Resolve top ranked watch destination for a movie or series."""
    movie = _resolve_watch_item(tmdb_id)
    if movie is None:
        return Response(
            {
                "found": False,
                "movie": {"tmdb_id": tmdb_id, "title": None, "year": None},
                "message": "Movie not found",
            },
            status=404,
        )

    preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)

    user_subs = list(
        Subscription.objects.filter(user_id=1).values_list("provider__slug", "name")
    )
    subscribed_slugs = set()
    for slug, name in user_subs:
        if slug:
            subscribed_slugs.add(slug.lower())
        if name:
            subscribed_slugs.add(name.lower())

    cache = AvailabilityCache()
    try:
        cache.get_or_refresh(movie)
    except RateLimitExceeded:
        pass
    except Exception:
        logger.exception("Live availability refresh failed for watch redirect tmdb %s", tmdb_id)

    offers = cache.read_offers(movie)
    qualifying = [
        o for o in offers
        if (o.offer_type or "").lower() in ("subscription", "free")
    ]

    if subscribed_slugs:
        matched = [
            o for o in qualifying
            if (o.service or "").lower() in subscribed_slugs
        ]
        if matched:
            qualifying = matched

    current_country = _current_country(request)
    ranked = rank_offers(qualifying, preference, current_country=current_country)

    top_offer = None
    for offer in ranked:
        if offer.url and offer.url.strip():
            top_offer = offer
            break

    movie_payload = {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "poster_path": movie.poster_path,
        "content_type": movie.content_type,
    }

    if top_offer:
        return Response(
            {
                "found": True,
                "watch_url": top_offer.url,
                "movie": movie_payload,
                "offer": top_offer.to_dict(),
            }
        )

    return Response(
        {
            "found": False,
            "movie": movie_payload,
            "message": "Not currently available to watch on your subscribed services.",
        }
    )


@api_view(["GET"])
def movie_detail(request, pk: int):
    """GET /api/movie/<int:pk>/ — full movie detail with TMDB metadata, availability, and user data."""
    try:
        movie = Movie.objects.get(pk=pk)
    except Movie.DoesNotExist:
        return Response({"error": "movie not found"}, status=404)

    # Fetch full TMDb metadata if missing or stale
    try:
        enrich_movie_from_tmdb(movie)
    except Exception:
        logger.exception("TMDb details fetch failed for movie %s", pk)

    user_data = UserMovieData.objects.filter(user_id=1, movie=movie).first()
    watch_events = MovieWatchEvent.objects.filter(user_id=1, movie=movie).order_by("-watched_date", "-created_at")

    return Response(
        {
            "movie": MovieDetailSerializer(movie).data,
            "user_data": UserMovieDataSerializer(user_data).data if user_data else None,
            "watch_events": MovieWatchEventSerializer(watch_events, many=True).data,
        }
    )


@api_view(["POST"])
def link_tmdb(request, pk: int):
    """POST /api/movie/<int:pk>/link-tmdb/ — manually link a TMDB movie to an existing DB movie.

    Request body:
        tmdb_id (int, required): the TMDb ID to link
        title (str, optional): override title (if omitted, uses TMDb title)
    """
    try:
        movie = Movie.objects.get(pk=pk)
    except Movie.DoesNotExist:
        return Response({"error": "movie not found"}, status=404)

    data = request.data if isinstance(request.data, dict) else {}
    tmdb_id = data.get("tmdb_id")
    if tmdb_id is None or not isinstance(tmdb_id, int):
        return Response({"error": "tmdb_id is required and must be an integer"}, status=400)

    client = TMDbClient(settings.TMDB_API_KEY)
    try:
        details = client.details(tmdb_id, content_type=data.get("content_type", "movie"))
    except Exception as exc:
        logger.exception("TMDb details fetch failed for tmdb_id=%s", tmdb_id)
        return Response({"error": f"TMDb fetch failed: {exc}"}, status=502)

    override_title = (data.get("title") or "").strip()
    new_title = override_title or details.title

    with transaction.atomic():
        old_tmdb_id = movie.tmdb_id
        movie.tmdb_id = details.tmdb_id
        movie.title = new_title
        movie.year = details.year
        movie.content_type = details.content_type
        sync_movie_fields(movie, details)
        # tmdb_id / title / year / content_type are set above but may not
        # trigger a save inside sync_movie_fields (those fields aren't tracked
        # there). Force a save so the full set of changes lands atomically.
        movie.save()

        # Invalidate cached availability for the old tmdb_id if it differs
        if old_tmdb_id != details.tmdb_id:
            Availability.objects.filter(movie=movie).delete()

    return Response({
        "message": f"Linked to TMDB {details.tmdb_id}",
        "movie": MovieDetailSerializer(movie).data,
    }, status=status.HTTP_200_OK)


@api_view(["PATCH"])
def update_movie(request, tmdb_id: int):
    """PATCH /api/movie/<tmdb_id>/ — update title and/or poster_path."""
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return Response({"error": "movie not found"}, status=404)

    data = request.data if isinstance(request.data, dict) else {}
    title = data.get("title")
    poster_path = data.get("poster_path")
    tmdb_rating = data.get("tmdb_rating")

    if title is not None:
        movie.title = title.strip()
    if poster_path is not None:
        movie.poster_path = poster_path.strip()
    if tmdb_rating is not None:
        movie.tmdb_rating = float(tmdb_rating)

    movie.save()
    return Response({
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "poster_path": movie.poster_path,
        "tmdb_rating": movie.tmdb_rating,
    })


# ---------------------------------------------------------------------------
# Manual watchlist management endpoints (new functionality)
# ---------------------------------------------------------------------------

@api_view(["POST"])
def add_to_watchlist(request):
    """POST /api/watchlist/add/ — manually add a movie or series to the watchlist.

    Request body:
        tmdb_id (int, required): the TMDb ID of the movie/series to add
        content_type (str, optional): "movie" (default) or "series"
    """
    from .services.enrichment import resolve_or_create_movie

    data = request.data if isinstance(request.data, dict) else {}
    tmdb_id = data.get("tmdb_id")
    if not isinstance(tmdb_id, int) or tmdb_id <= 0:
        return Response({"error": "tmdb_id is required and must be a positive integer"}, status=400)

    content_type = data.get("content_type", "movie")
    if not isinstance(content_type, str) or content_type not in ("movie", "series"):
        return Response({"error": "content_type must be 'movie' or 'series'"}, status=400)

    try:
        movie = resolve_or_create_movie(tmdb_id, content_type=content_type)
    except Exception as exc:
        logger.exception("Failed to resolve/create movie for tmdb_id=%s", tmdb_id)
        return Response({"error": f"Movie lookup failed: {exc}"}, status=502)

    # Check if already in watchlist
    if WatchlistItem.objects.filter(user_id=1, movie=movie, source="manual").exists():
        return Response({
            "message": "Movie already in watchlist",
            "watchlist_item": WatchlistItemSerializer(WatchlistItem.objects.filter(user_id=1, movie=movie, source="manual").first()).data,
        })

    try:
        with transaction.atomic():
            watchlist_item = WatchlistItem.objects.create(
                user_id=1,
                movie=movie,
                source="manual",
            )

        # Create UserMovieData entry if not exists
        user_data, _ = UserMovieData.objects.get_or_create(
            user_id=1,
            movie=movie,
            defaults={"is_watched": False},
        )

        return Response({
            "message": "Movie added to watchlist",
            "watchlist_item": WatchlistItemSerializer(watchlist_item).data,
            "user_data": UserMovieDataSerializer(user_data).data,
        }, status=status.HTTP_201_CREATED)
    except Exception as exc:
        logger.exception("Failed to add movie to watchlist for tmdb_id=%s", tmdb_id)
        return Response({"error": f"Failed to add to watchlist: {exc}"}, status=502)

@api_view(["POST"])
def mark_movie_watched(request, tmdb_id: int):
    """POST /api/watched/<int:tmdb_id>/ — mark a movie as watched.

    Request body (optional):
        watched_date (str, optional): ISO date string (e.g., "2024-01-15")
        rating (float, optional): rating 1-10
        review (str, optional): review text
        rewatch (bool, optional): whether this is a rewatch (default false)
    """
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return Response({"error": "Movie not found"}, status=404)

    data = request.data if isinstance(request.data, dict) else {}
    watched_date = data.get("watched_date")
    rating = data.get("rating")
    review = data.get("review")
    rewatch = data.get("rewatch", False)
    if isinstance(watched_date, str):
        from datetime import date
        watched_date_value = date.fromisoformat(watched_date)
    elif watched_date:
        watched_date_value = watched_date
    else:
        watched_date_value = timezone.now().date()

    try:
        with transaction.atomic():
            # Get or create UserMovieData
            user_data, created = UserMovieData.objects.get_or_create(
                user_id=1,
                movie=movie,
                defaults={
                    "is_watched": True,
                    "rating": rating,
                    "review": review or "",
                    "watched_date": watched_date_value,
                    "updated_at": timezone.now(),
                },
            )

            if not created:
                # Update existing UserMovieData
                if watched_date:
                    user_data.watched_date = watched_date_value
                if rating is not None:
                    user_data.rating = float(rating)
                if review is not None:
                    user_data.review = review
                user_data.is_watched = True

                user_data.updated_at = timezone.now()
                user_data.save()

            # Create watch event
            watch_event_data = {
                "user_id": 1,
                "movie": movie,
                "rating": rating,
                "review": review or "",
                "rewatch": rewatch,
                "watched_date": watched_date_value,
            }

            # Add letterboxd_uri from UserMovieData if available
            if hasattr(user_data, 'letterboxd_uri') and user_data.letterboxd_uri:
                watch_event_data["letterboxd_uri"] = user_data.letterboxd_uri

            MovieWatchEvent.objects.update_or_create(
                user_id=1,
                movie=movie,
                watched_date=watch_event_data["watched_date"],
                letterboxd_uri=watch_event_data.get("letterboxd_uri", ""),
                defaults={
                    "rating": rating,
                    "review": review or "",
                    "rewatch": rewatch,
                },
            )

        return Response({
            "message": "Movie marked as watched",
            "user_data": UserMovieDataSerializer(user_data).data,
        })
    except Exception as exc:
        logger.exception("Failed to mark movie as watched for tmdb_id=%s", tmdb_id)
        return Response({"error": f"Failed to mark as watched: {exc}"}, status=502)


@api_view(["POST"])
def unmark_movie_watched(request, tmdb_id: int):
    """POST /api/unwatched/<int:tmdb_id>/ — unmark a movie as watched (remove from watched list).

    This removes the movie from the watched status but keeps it in watchlist.
    """
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return Response({"error": "Movie not found"}, status=404)

    try:
        with transaction.atomic():
            # Get UserMovieData for this movie
            user_data = UserMovieData.objects.get(user_id=1, movie=movie)

            # Update the watched status
            user_data.is_watched = False
            user_data.updated_at = timezone.now()
            user_data.save()

        return Response({
            "message": "Movie unmarked as watched",
            "user_data": UserMovieDataSerializer(user_data).data,
        })
    except UserMovieData.DoesNotExist:
        return Response({"error": "Movie not found in watchlist or not marked as watched"}, status=404)
    except Exception as exc:
        logger.exception("Failed to unmark movie as watched for tmdb_id=%s", tmdb_id)
        return Response({"error": f"Failed to unmark as watched: {exc}"}, status=502)


@api_view(["DELETE"])
def remove_from_watchlist(request, tmdb_id: int):
    """DELETE /api/watchlist/remove/<int:tmdb_id>/ — remove a movie from the watchlist.

    This only removes wishlist/watchlist membership. Watched state, ratings,
    reviews, and watch history are intentionally preserved.
    """
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return Response({"error": "Movie not found"}, status=404)

    try:
        with transaction.atomic():
            WatchlistItem.objects.filter(user_id=1, movie=movie).delete()

        return Response({
            "message": "Movie removed from watchlist",
        })
    except Exception as exc:
        logger.exception("Failed to remove movie from watchlist for tmdb_id=%s", tmdb_id)
        return Response({"error": f"Failed to remove from watchlist: {exc}"}, status=502)


# ---------------------------------------------------------------------------
# Google Interests importer endpoint
# ---------------------------------------------------------------------------


@api_view(["GET"])
def search_movie_matches(request, tmdb_id: int):
    """GET /api/movie/<tmdb_id>/search-matches/?q= — search TMDb for poster/title matches."""
    query = request.GET.get("q", "")
    if not query:
        return Response({"error": "q parameter is required"}, status=400)

    client = TMDbClient(settings.TMDB_API_KEY)
    try:
        results = client.search(query)
    except Exception as exc:
        return Response({"error": f"TMDb search failed: {exc}"}, status=502)

    return Response({
        "query": query,
        "results": [
            {
                "tmdb_id": r.tmdb_id,
                "title": r.title,
                "year": r.year,
                "poster_path": r.poster_path,
                "overview": r.overview,
                "rating": r.rating,
            }
            for r in results[:10]
        ],
    })


# ---------------------------------------------------------------------------
# Google Interests importer endpoint
# ---------------------------------------------------------------------------

@api_view(["POST"])
def google_interests_import(request):
    """POST /api/importers/google-interests/ — import Google Interests saved lists.

    Request body (JSON):
        cookies (dict, required): Google cookies keyed by cookie name, e.g.
            {"CONSENT": "...", "NID": "...", "SOCS": "..."}.
        urls (list[str], required): one or more Interest list page URLs.
        user_id (int, optional): defaults to 1.

    Returns the same import shape as the Letterboxd importer so the frontend
    watchlist UI can reuse it with a different source label.
    """
    from tracker.services.tmdb import TMDbClient

    data = request.data if isinstance(request.data, dict) else {}
    raw_cookies = data.get("cookies") or {}
    urls = data.get("urls") or []
    user_id = data.get("user_id", 1)

    if not isinstance(raw_cookies, dict):
        return Response({"error": "cookies must be a JSON object (or omitted for public pages)"}, status=400)
    if not isinstance(urls, list) or not urls:
        return Response({"error": "urls list is required"}, status=400)

    cookies: dict[str, str] = {str(k): str(v) for k, v in raw_cookies.items() if v is not None}

    from tracker.services.google_interests import fetch_page, parse_and_import

    client = TMDbClient(settings.TMDB_API_KEY, timeout=3)
    all_resolved: list = []
    all_ambiguous: list = []
    all_skipped: list = []
    total_watchlist_count = 0

    for url in urls:
        url_stripped = (url or "").strip()
        if not url_stripped:
            continue
        try:
            html = fetch_page(url_stripped, cookies=cookies)
        except Exception as exc:
            logger.exception("Google Interests fetch failed for %s", url_stripped)
            all_skipped.append({"url": url_stripped, "reason": f"fetch_failed: {exc}"})
            continue

        result = parse_and_import(html, page_url=url_stripped, client=client, user_id=user_id)
        all_resolved.extend(result.resolved)
        all_ambiguous.extend(result.ambiguous)
        all_skipped.extend(result.skipped)
        total_watchlist_count += result.watchlist_count

    return Response(
        {
            "resolved": WatchlistItemSerializer(all_resolved, many=True).data,
            "ambiguous": all_ambiguous,
            "skipped": all_skipped,
            "watchlist_count": total_watchlist_count,
        }
    )
