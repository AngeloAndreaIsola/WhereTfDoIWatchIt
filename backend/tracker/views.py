"""DRF views for Phase 2: search, ranked availability, watchlist, preferences."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta, timezone as dt_timezone
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, Lower
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from .languages import normalize_language_code
from .models import (
    Availability,
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    AvailabilityResolutionReason,
    ExternalProviderMapping,
    LanguageEntry,
    LanguageInfo,
    Movie,
    MovieWatchEvent,
    Preference,
    Provider,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    Region,
    ScrapedContentMapping,
    Subscription,
    UserMovieData,
    WatchlistItem,
    provider_domain_root,
    provider_domain_root_expr,
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
from tracker.scrapers import ContentMatcher, ScrapedItem
from tracker.services.filtering import apply_composable_filters, parse_actor_filters
from tracker.services.unified_catalogue import resolve_catalogue_scope, unofficial_catalogue_items
from tracker.services.watchability import (
    QUALIFYING_OFFER_TYPES,
    WatchabilityStatus,
    evaluate_watchability,
)
from tracker.services.access import SubscriptionAccessMode
from availability.services.legacy_ingest import ingest_scraped_mapping
from availability.services.write_barrier import MaintenanceInProgress, writer_barrier
from availability.sources import DEFAULT_SOURCE_SLUG
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
from .services.bulk_refresh import (
    process_watchlist_refresh,
    stale_catalogue_sources,
    title_level_source_slugs,
)
from .services.availability_reads import get_local_offers, get_offer_evidence
from .services.availability_metrics import (
    WINDOW_DAYS,
    availability_metrics as collect_availability_metrics,
)
from .services.availability_report import (
    active_refresh,
    build_run_summary,
    record_run,
    source_label,
    sync_run_progress,
    title_source_report,
)
from .services import source_policy
from .services.refresh_lifecycle import TERMINAL_STATUSES
from .services.provider_status import (
    CATALOGUE,
    SCRAPER,
    provider_card_status,
    refresh_target,
)
from .services.rate_limit import rate_limiter_for
from .services.refresh_requests import (
    refresh_states_for,
    request_availability_refresh,
    request_catalogue_refresh,
    request_title_refresh,
    request_watchlist_refresh,
)
from .services.scraper_runner import run_provider_scraper
from .services.scraper_policy import require_legacy_execution
from .services.scraper_selection import (
    ScraperExecutionIneligible,
    select_provider_scraper,
)
from .services.letterboxd import (
    LetterboxdFetchError,
    LetterboxdInvalidURLError,
    LetterboxdProfileNotFound,
    fetch_profile_rows,
    import_rows,
    normalize_profile_url,
    parse_file_or_zip,
)
from .services.ranking import rank_offers
from .services.preferences import resolve_preferences
from .services.enrichment import enrich_movie_from_tmdb, resolve_or_create_movie, sync_movie_fields
from .services.tmdb import TMDbClient, TMDbItem, TMDbMovie
from .services.watchlist_export import (
    FILENAME_BOTH,
    FILENAME_WATCHED,
    FILENAME_WATCHLIST,
    CanonicalImportError,
    combined_export_rows,
    csv_response,
    is_canonical_csv,
    parse_canonical_csv,
    watched_export_rows,
    watchlist_export_rows,
)

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
    curr_country = _active_country(request)
    pref = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)

    movies = Movie.objects.all().order_by("-id")
    # The resolver builds the access map from ``Subscription`` rows, exactly as
    # the detail page does. Passing the legacy flat slug set here would route
    # list surfaces through ``build_explicit_access``, which ignores
    # ``access_mode`` and AVOD/FAST auto-entitlement — the detail↔list
    # disagreement this redesign exists to remove.
    evaluated, _ = apply_composable_filters(
        movies,
        request.GET,
        pref,
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
    curr_country = _active_country(request)
    pref = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)

    scope = resolve_catalogue_scope(request.GET)
    allowed_provider_slugs = scope.allowed_provider_slugs
    include_official = scope.include_official
    include_scraper = scope.include_scraper

    unified_items = []
    seen_tmdb_ids = set()

    # Watched flag for the official half, read once. Official rows are Movie
    # rows, so this is the same UserMovieData source the mark/unmark endpoints
    # write — never the watchlist.
    watched_filter = (
        request.GET.get("watched") or request.GET.get("watched_status") or ""
    ).lower().strip()
    watched_movie_ids: set[int] = set()
    if watched_filter in ("watched", "unwatched", "all"):
        watched_movie_ids = set(
            UserMovieData.objects.filter(
                user_id=1, is_watched=True
            ).values_list("movie_id", flat=True)
        )

    # 1. Fetch Official Catalog Items
    if include_official:
        movies = Movie.objects.all().order_by("-id")
        evaluated, _ = apply_composable_filters(
            movies,
            request.GET,
            pref,
            current_country=curr_country,
            allowed_provider_slugs=allowed_provider_slugs,
        )

        for item in evaluated:
            m = item["movie"]
            watch_res = item["watchability"]
            seen_tmdb_ids.add(m.tmdb_id)

            # Membership already reflects the watched filter (applied inside
            # apply_composable_filters); this only labels the row for display.
            is_watched = (
                m.id in watched_movie_ids
                if watched_filter in ("watched", "unwatched", "all")
                else None
            )

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
                "is_watched": is_watched,
            })

    # 2. Fetch Unofficial Catalog Items (canonical, via the scraper bridge)
    if include_scraper:
        scraped_items = unofficial_catalogue_items(
            request.GET,
            allowed_provider_slugs=allowed_provider_slugs,
            exclude_tmdb_ids=seen_tmdb_ids,
        )
        raw_content_type = (
            request.GET.get("content_type") or request.GET.get("type") or ""
        ).strip().lower()
        if raw_content_type in ("movie", "series", "tv"):
            selected_content_type = (
                "series" if raw_content_type in ("series", "tv") else "movie"
            )
            scraped_items = [
                item
                for item in scraped_items
                if (item.get("content_type") or "movie") == selected_content_type
            ]
        if watched_filter in ("watched", "unwatched", "all"):
            # An unofficial entry has watch state only once it resolves to a
            # local Movie; an unresolved entry has never been tracked, so it
            # can only ever be unwatched. One batched read over the resolved
            # tmdb ids, then keep/drop each row.
            resolved_ids = {
                item["tmdb_id"] for item in scraped_items if item.get("tmdb_id")
            }
            watched_tmdb_ids: set[int] = set()
            if resolved_ids:
                watched_tmdb_ids = set(
                    UserMovieData.objects.filter(
                        user_id=1, movie__tmdb_id__in=resolved_ids, is_watched=True
                    ).values_list("movie__tmdb_id", flat=True)
                )
            keep = []
            for item in scraped_items:
                is_watched = item.get("tmdb_id") in watched_tmdb_ids
                if watched_filter != "all" and is_watched != (watched_filter == "watched"):
                    continue
                item["is_watched"] = is_watched
                keep.append(item)
            scraped_items = keep
        unified_items.extend(scraped_items)

    # Sort combined results if sort_by parameter is provided
    sort_by = (request.GET.get("sort_by") or request.GET.get("sort") or "").lower().strip()
    if sort_by:
        def get_item_rating(item):
            if item.get("source") == "official":
                return getattr(item.get("watchability"), "tmdb_rating", None) or getattr(_unique_movie_for_tmdb(item.get("tmdb_id")), "tmdb_rating", None)
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

    # Language filters use the same canonical ISO codes as offer ingestion.
    # Unknown or absent language data cannot satisfy an active filter.
    def requested_languages(param_name: str) -> set[str] | None:
        raw = (request.GET.get(param_name) or "").strip()
        if not raw:
            return None
        return {
            code
            for part in raw.split(",")
            if (code := normalize_language_code(part.strip()))
        }

    audio_filter = requested_languages("audio_languages")
    subtitle_filter = requested_languages("subtitle_languages")
    if audio_filter is not None or subtitle_filter is not None:
        raw_access = request.GET.get("access") or request.GET.get("access_type") or ""
        access_filter = {
            value.strip().lower()
            for value in raw_access.split(",")
            if value.strip()
        }
        if "all" in access_filter:
            access_filter.clear()
        status_filter = (
            request.GET.get("availability_status") or request.GET.get("status") or ""
        ).strip().lower()
        watchable_only = request.GET.get("watchable") in ("true", "1") or request.GET.get("watchable_only") in ("true", "1")
        filtered_items = []
        for item in unified_items:
            matching_offer = False
            if item.get("source") == "official":
                watchability = item.get("watchability") or {}
                # The resolver partitions offers by current entitlement and
                # reachability. Excluded offers cannot make a language match;
                # purchase offers remain eligible only when the resolver has
                # already confirmed entitlement and territory reach.
                offers = list(watchability.get("watchable_offers") or [])
                offers.extend(watchability.get("purchase_offers") or [])
                if allowed_provider_slugs is not None:
                    offers = [
                        offer for offer in offers
                        if (offer.get("service") or "").lower() in allowed_provider_slugs
                    ]
                if access_filter:
                    offers = [
                        offer for offer in offers
                        if (offer.get("access_type") or "").lower() in access_filter
                    ]
                if watchable_only or status_filter in ("watchable", "watchable_any"):
                    offers = [offer for offer in offers if offer.get("is_accessible")]
                elif status_filter == "watchable_here":
                    offers = [
                        offer for offer in offers
                        if offer.get("is_accessible") and offer.get("is_current_country")
                    ]
                elif status_filter == "watchable_vpn":
                    offers = [
                        offer for offer in offers
                        if offer.get("is_accessible") and offer.get("is_candidate_country")
                    ]
                elif status_filter == "purchase_only":
                    offers = [offer for offer in offers if offer.get("requires_payment")]

                for offer in offers:
                    available_audio = {
                        code
                        for value in offer.get("audio_languages") or []
                        if (code := normalize_language_code(str(value)))
                    }
                    available_subtitles = {
                        code
                        for value in offer.get("subtitle_languages") or []
                        if (code := normalize_language_code(str(value)))
                    }
                    if (
                        (audio_filter is None or audio_filter.intersection(available_audio))
                        and (
                            subtitle_filter is None
                            or subtitle_filter.intersection(available_subtitles)
                        )
                    ):
                        matching_offer = True
                        break
            else:
                # Each unofficial result is one canonical ProviderContent
                # entry with its own provider identity. Keep both language
                # checks on that same entry; never union across entries.
                details = item.get("unofficial_details") or {}
                available_audio = {
                    code
                    for value in details.get("audio_languages") or []
                    if (code := normalize_language_code(str(value)))
                }
                available_subtitles = {
                    code
                    for value in details.get("subtitle_languages") or []
                    if (code := normalize_language_code(str(value)))
                }
                matching_offer = bool(
                    (item.get("provider") or {}).get("slug")
                    and item.get("id")
                    and (
                        audio_filter is None
                        or audio_filter.intersection(available_audio)
                    )
                    and (
                        subtitle_filter is None
                        or subtitle_filter.intersection(available_subtitles)
                    )
                )

            if not matching_offer:
                continue
            filtered_items.append(item)
        unified_items = filtered_items

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
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
        return Response({"error": "movie not tracked yet"}, status=404)

    preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)

    offers, last_known = get_offer_evidence(movie)
    ranked = rank_offers(offers, preference)

    user_data = UserMovieData.objects.filter(user_id=1, movie=movie).first()
    watch_events = MovieWatchEvent.objects.filter(user_id=1, movie=movie).order_by("-watched_date", "-created_at")

    return Response(
        {
            "movie": {"tmdb_id": movie.tmdb_id, "title": movie.title, "year": movie.year, "poster_path": movie.poster_path},
            "user_data": UserMovieDataSerializer(user_data).data if user_data else None,
            "watch_events": MovieWatchEventSerializer(watch_events, many=True).data,
            "cache": {
                "refreshed": False,
                "reason": "local",
                "offers": len(offers),
            },
            "offers": RankedOfferSerializer([offer.to_dict() for offer in ranked], many=True).data,
            # Stale/unverified positives: history ("last seen"), never current.
            "last_known_offers": RankedOfferSerializer(
                [offer.to_dict() for offer in rank_offers(last_known, preference)], many=True
            ).data,
        }
    )


@api_view(["POST"])
def refresh_availability(request, tmdb_id: int):
    """POST /api/movie/<tmdb_id>/refresh/ — queue an explicit availability refresh.

    The backend for the manual "Refresh availability" button on the
    movie/series detail pages. One endpoint serves both pages: movies and
    series are the same tracked title row and share the canonical
    availability pipeline, so the refresh mechanism is identical.

    Enqueue-only by contract — the HTTP request never contacts a provider.
    It creates (or upgrades, preserving deduplication) at most one pending
    ``RefreshRequest`` at ``RefreshPriority.EXPLICIT`` and answers
    immediately; the existing ``refresh_worker`` drains the queue and the
    ``AvailabilityRefreshService`` writes the canonical offers, which the
    frontend then picks up by refetching the detail payload. Enqueueing with
    ``if_stale=False`` forces the refresh even when the current offers still
    read as fresh — the whole point of a manual refresh — while the partial
    unique constraint keeps repeated clicks on the same pending row.
    """
    movie = _unique_movie_for_tmdb(tmdb_id)
    if movie is None:
        return Response({"error": "movie identity absent or ambiguous"}, status=404)

    refresh_request = request_availability_refresh(
        movie,
        priority=RefreshPriority.EXPLICIT,
        if_stale=False,
        country=_active_country(request),
    )

    pending = RefreshRequest.objects.filter(
        movie=movie, status=RefreshRequestStatus.PENDING
    ).exists()
    if not pending and refresh_request is None:
        # request_availability_refresh swallows enqueue failures so it can
        # never break a read; for an explicit user action the failure must
        # surface instead of masquerading as a queued refresh.
        return Response(
            {"error": "failed to queue availability refresh"}, status=500
        )

    return Response(
        {
            "message": "Availability refresh queued",
            "tmdb_id": movie.tmdb_id,
            "content_type": movie.content_type,
            "refresh_pending": True,
            "refresh_status": RefreshRequestStatus.PENDING,
            "priority": int(RefreshPriority.EXPLICIT),
            "request_id": refresh_request.pk if refresh_request else None,
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
def providers_list(request):
    """List canonical providers catalog with subscription state for user_id=1.

    Also carries every operational field the square source cards render (last
    run, available items, refresh target, refreshing state) so the page can
    paint N cards from one response — see ``tracker.services.provider_status``
    for the batched computation and the refresh-target rules.
    """
    category = request.GET.get("category")
    is_major = request.GET.get("is_major")

    qs = (
        # Every service our sources can actually cover: either already active
        # (``is_enabled``) or discovered in the JustWatch catalogue (has a
        # ``justwatch`` external mapping). Including the discovered-but-disabled
        # ones is what makes the service-access manager *catalogue-driven*: a
        # newly found service is visible and selectable, while still defaulting
        # to inactive until the user declares access to it.
        Provider.objects.filter(
            Q(is_enabled=True) | Q(external_mappings__integration_name="justwatch")
        )
        .distinct()
        .prefetch_related("external_mappings")
        # Batched scraped-item count: the serializer prefers this annotation
        # over its per-object fallback count.
        .annotate(scraped_total=Count("scraped_mappings", distinct=True))
        .order_by("id")
    )
    if category:
        qs = qs.filter(category=category.lower())
    if is_major is not None:
        qs = qs.filter(is_major=is_major.lower() in ("true", "1"))

    providers = list(qs)

    user_sub_provider_ids = set(
        Subscription.objects.filter(user_id=1, provider__isnull=False).values_list("provider_id", flat=True)
    )
    user_access_modes = dict(
        Subscription.objects.filter(user_id=1, provider__isnull=False).values_list("provider_id", "access_mode")
    )

    serializer = ProviderSerializer(
        providers,
        many=True,
        context={
            "user_subscribed_ids": user_sub_provider_ids,
            "user_access_modes": user_access_modes,
            "provider_status": provider_card_status(providers),
        },
    )
    return Response(serializer.data)


@api_view(["POST"])
def providers_sync(request):
    """Reconcile the local provider catalogue from JustWatch's provider list.

    Idempotent and additive: creates *disabled* ``Provider`` rows for newly
    discovered services and upserts the per-country ``justwatch`` external
    mappings the refresh service resolves offers through. Never touches
    ``Subscription`` rows. ``country`` (body, repeated or comma-separated)
    limits the markets walked; default is every enabled country.
    """
    from .services.provider_catalogue import run_sync_provider_catalogue

    raw = (request.data or {}).get("country") if isinstance(request.data, dict) else None
    if raw is None:
        countries = None
    elif isinstance(raw, (list, tuple)):
        countries = [str(c) for c in raw]
    else:
        countries = [part for part in str(raw).split(",")]

    report = run_sync_provider_catalogue(countries)
    return Response(report)


@api_view(["GET"])
def provider_packages(request):
    """Inspect the persisted JustWatch package catalogue — resolved or not.

    Requirement 4.4: an unresolved package must be *inspectable* with a
    structured reason, not merely absent from a count. Filters: ``country``
    (comma-separated), ``status`` (``mapped``/``alias``/``addon``/``unresolved``),
    ``reason`` (a ``PackageReasonCode``), and ``resolved=true|false``. Read-only;
    it never triggers a sync and never touches availability.
    """
    from tracker.models import (
        PackageResolutionStatus,
        ProviderCataloguePackage,
    )

    qs = ProviderCataloguePackage.objects.all().order_by("country", "technical_name")
    raw_country = request.GET.get("country")
    if raw_country:
        codes = [c.strip().upper() for c in raw_country.split(",") if c.strip()]
        qs = qs.filter(country__in=codes)
    status = (request.GET.get("status") or "").strip().lower()
    if status:
        qs = qs.filter(resolution_status=status)
    reason = (request.GET.get("reason") or "").strip().lower()
    if reason:
        qs = qs.filter(reason_code=reason)
    resolved = (request.GET.get("resolved") or "").strip().lower()
    if resolved in ("true", "1"):
        qs = qs.exclude(resolution_status=PackageResolutionStatus.UNRESOLVED)
    elif resolved in ("false", "0"):
        qs = qs.filter(resolution_status=PackageResolutionStatus.UNRESOLVED)

    rows = [
        {
            "country": row.country,
            "package_id": row.package_id,
            "technical_name": row.technical_name,
            "clear_name": row.clear_name,
            "short_name": row.short_name,
            "monetization_types": row.monetization_types,
            "resolution_status": row.resolution_status,
            "reason_code": row.reason_code,
            "reason": row.reason,
            "resolved_provider_slug": row.resolved_provider_slug,
            "first_seen": row.first_seen,
            "last_seen": row.last_seen,
        }
        for row in qs[:1000]
    ]
    return Response({"count": len(rows), "results": rows})


@api_view(["POST"])
def provider_toggle(request, slug: str):
    """Own/unown a provider, or set the user's *access mode* for it.

    Two contracts share this endpoint so nothing existing breaks:

    * **Bodyless POST** — legacy flip: subscribed → unsubscribe, else
      subscribe as ``included``. What the source cards have always done.
    * **POST with ``access_mode``** — set-mode (idempotent): ``included``,
      ``addon``, ``free``, ``rent_buy`` record *how* the user holds the
      service, while ``none`` removes it. This is the write path behind the
      service-access manager UI, where "subscribe/unsubscribe" is not a toggle
      but a declared state.

    A mapped-but-disabled provider (discovered by catalogue sync, not yet
    active) is addressable here too: declaring access to it flips it to active,
    because owning a service is exactly the signal that its catalogue should be
    collected. Un-declaring leaves ``is_enabled`` alone — the service stays
    browsable, only the user's claim to it is removed.
    """
    provider = get_object_or_404(
        Provider.objects.filter(
            Q(is_enabled=True) | Q(external_mappings__integration_name="justwatch")
        ).distinct(),
        slug__iexact=slug,
    )
    data = request.data if isinstance(request.data, dict) else {}

    def _activate():
        # Idempotent: only touches the row when it is still inactive.
        if not provider.is_enabled:
            provider.is_enabled = True
            provider.save(update_fields=["is_enabled"])

    sub = Subscription.objects.filter(user_id=1, provider=provider).first()

    if "access_mode" not in data:
        if sub:
            sub.delete()
            return Response({
                "provider": provider.slug,
                "is_subscribed": False,
                "access_mode": SubscriptionAccessMode.NONE,
                "message": f"Unsubscribed from {provider.name}",
            })
        Subscription.objects.create(user_id=1, provider=provider, name=provider.name)
        _activate()
        return Response({
            "provider": provider.slug,
            "is_subscribed": True,
            "access_mode": SubscriptionAccessMode.INCLUDED,
            "message": f"Subscribed to {provider.name}",
        })

    requested = str(data.get("access_mode") or "included").lower().strip()
    valid_modes = {choice for choice, _ in SubscriptionAccessMode.CHOICES}
    if requested not in valid_modes:
        return Response(
            {"error": f"Invalid access_mode '{requested}'."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if requested == SubscriptionAccessMode.NONE:
        if sub:
            sub.delete()
        return Response({
            "provider": provider.slug,
            "is_subscribed": False,
            "access_mode": SubscriptionAccessMode.NONE,
            "message": f"Marked {provider.name} as not owned",
        })

    if sub:
        sub.access_mode = requested
        sub.save(update_fields=["access_mode"])
        created = False
    else:
        sub = Subscription.objects.create(
            user_id=1, provider=provider, name=provider.name, access_mode=requested
        )
        created = True

    _activate()
    return Response({
        "provider": provider.slug,
        "is_subscribed": True,
        "access_mode": requested,
        "created": created,
        "message": f"{'Subscribed to' if created else 'Updated'} {provider.name}",
    })


@api_view(["POST"])
def provider_refresh(request, slug: str):
    """POST /api/providers/<slug>/refresh/ — run this one source's collector.

    Source-specific by construction: the target comes from the same registry
    mapping the cards use (``provider_status.refresh_target``) and is never a
    request parameter, so one press can only ever run one collector:

    - catalogue sources enqueue the existing ``CatalogueRefreshRequest``
      marker; the running ``refresh_worker`` drains it through
      ``process_catalogue_refresh(source_slug=...)`` under the per-source
      advisory lock. Enqueue-only by contract (202 ``queued: true``), so the
      HTTP request never contacts a provider;
    - scraper providers run their own scraper synchronously through
      ``run_provider_scraper`` — the same execution path
      ``/api/scrapers/run/`` uses (200 with the scraped count).

    Subscription state is deliberately untouched: refreshing and the
    active/inactive toggle are independent concepts, and a failed refresh
    records its error on the source's ``RefreshRun`` instead of pretending
    it succeeded.
    """
    provider = get_object_or_404(Provider, slug__iexact=slug, is_enabled=True)
    refresh_kind, source_slug = refresh_target(provider)

    if refresh_kind == CATALOGUE:
        marker = request_catalogue_refresh([source_slug])
        return Response(
            {
                "provider": provider.slug,
                "kind": CATALOGUE,
                "source": source_slug,
                "queued": True,
                "refreshing": True,
                "marker_id": marker.pk,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    if refresh_kind == SCRAPER:
        try:
            items = run_provider_scraper(provider)
        except ScraperExecutionIneligible as exc:
            return Response(exc.payload(), status=status.HTTP_409_CONFLICT)
        except Exception as exc:  # noqa: BLE001 - surface the collector error to the card
            logger.exception(
                "provider_refresh_scraper_failed provider=%s error=%s",
                provider.slug, exc,
            )
            return Response(
                {
                    "provider": provider.slug,
                    "kind": SCRAPER,
                    "queued": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        # run_provider_scraper stamps provider.last_scraped_at on this same
        # instance as part of its persistence pass.
        return Response(
            {
                "provider": provider.slug,
                "kind": SCRAPER,
                "queued": False,
                "count": len(items),
                "last_run": provider.last_scraped_at,
            }
        )

    # A registry entry can disappear between the card read and this request.
    # Ask the shared selector for the same fail-closed contract instead of
    # letting a retired named scraper fall through to GenericURLAdapter.
    try:
        select_provider_scraper(provider)
    except ScraperExecutionIneligible as exc:
        return Response(exc.payload(), status=status.HTTP_409_CONFLICT)
    return Response(
        {
            "code": "refresh_target_unavailable",
            "error": f"No refresh target is registered for '{provider.slug}'.",
            "provider": provider.slug,
            "alternative": None,
        },
        status=status.HTTP_409_CONFLICT,
    )


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
    access_mode = validated.get("access_mode") or "included"

    if resolved_p:
        sub, created = Subscription.objects.get_or_create(
            user_id=1,
            provider=resolved_p,
            defaults={"name": resolved_p.name, "access_mode": access_mode},
        )
        if not created and sub.access_mode != access_mode:
            sub.access_mode = access_mode
            sub.save(update_fields=["access_mode"])
    else:
        name_clean = (validated.get("name") or "").strip()
        sub, created = Subscription.objects.get_or_create(
            user_id=1,
            name=name_clean,
            defaults={"access_mode": access_mode},
        )
        if not created and sub.access_mode != access_mode:
            sub.access_mode = access_mode
            sub.save(update_fields=["access_mode"])

    return Response(SubscriptionSerializer(sub).data, status=status.HTTP_201_CREATED)


from urllib.parse import urlparse
from django.utils import timezone


def detect_provider_from_url(url: str) -> tuple[Provider, str]:
    """Dynamically get or create a scraper provider from any URL.

    The lookup goes through the provider's *domain root* first, so a URL on a
    site that already has a provider row (e.g. bbc.co.uk/iplayer) reuses it
    instead of minting a second row from the domain's first label — the
    historical source of duplicate provider cards.
    """
    parsed = urlparse(url.strip())
    domain = parsed.netloc.lower() or parsed.path.split("/")[0].lower()
    domain_clean = domain.replace("www.", "")

    if not domain_clean:
        raise ValueError("Invalid URL: domain could not be parsed.")

    # Derive slug and display name
    slug = domain_clean.split(".")[0]
    name = domain_clean.capitalize()

    existing = (
        Provider.objects.annotate(_domain_root=provider_domain_root_expr())
        .filter(_domain_root=provider_domain_root(domain_clean))
        .order_by("id")
        .first()
    )
    if existing is not None:
        if not existing.domain:
            existing.domain = domain_clean
            existing.save()
        provider = existing
    else:
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
    data = request.data if isinstance(request.data, dict) else {}
    url = (data.get("url") or "").strip()
    if not url:
        return Response({"error": "URL is required"}, status=400)

    try:
        provider, query = detect_provider_from_url(url)
    except Exception as exc:
        return Response({"error": str(exc)}, status=400)

    try:
        scraper = select_provider_scraper(provider).scraper
    except ScraperExecutionIneligible as exc:
        return Response(exc.payload(), status=status.HTTP_409_CONFLICT)

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

    # Shared with POST /api/providers/<slug>/refresh/: same scraper
    # execution, same persistence, same bridge onto the canonical catalogue.
    try:
        items = run_provider_scraper(provider, query=query)
    except ScraperExecutionIneligible as exc:
        return Response(exc.payload(), status=status.HTTP_409_CONFLICT)

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
    from availability.models import (
        ContentMapping,
        Offer as AvailabilityOffer,
        ProviderContent,
        SyncCursor,
        SyncRun,
    )

    provider = get_object_or_404(Provider, slug__iexact=slug)
    if provider.is_major:
        return Response({"error": "Major canonical providers cannot be deleted."}, status=400)

    # The availability tables PROTECT their provider, so the canonical rows
    # this provider owns are removed first (the legacy mappings cascade away
    # with the provider itself).  The shared writer barrier is acquired before
    # the transaction opens and held through its commit, so this deletion can
    # never race an availability refresh or the remediation.  The nested
    # projection deletion fired by the ``ScrapedContentMapping`` cascade reuses
    # this acquisition (``writer_barrier`` is re-entrant); it never takes a
    # second session lock.
    try:
        with writer_barrier(f"provider deletion for {provider.slug}"):
            with transaction.atomic():
                ContentMapping.objects.filter(provider=provider).delete()
                ProviderContent.objects.filter(provider=provider).delete()
                AvailabilityOffer.objects.filter(provider=provider).delete()
                SyncRun.objects.filter(provider=provider).delete()
                SyncCursor.objects.filter(provider=provider).delete()
                provider.delete()
    except MaintenanceInProgress as exc:
        logger.warning(
            "provider_delete_refused provider=%s reason=%s", provider.slug, exc
        )
        return Response(
            {"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def catalog_list(request):
    """GET /api/catalog/ — List all tracked catalog items with composable filters, watchability & pagination."""
    curr_country = _active_country(request)
    pref = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)

    movies = Movie.objects.all().order_by("-id")
    evaluated, _ = apply_composable_filters(
        movies,
        request.GET,
        pref,
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

    # Review is a read-only projection of saved observations. Opening this
    # list must never perform an implicit TMDb search (or change a mapping).
    out = []
    for mapping in unresolved:
        candidates = []
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

    try:
        require_legacy_execution(mapping.provider)
    except ScraperExecutionIneligible as exc:
        return Response(exc.payload(), status=status.HTTP_409_CONFLICT)

    client = TMDbClient(settings.TMDB_API_KEY)
    try:
        details = client.details(tmdb_id, content_type=content_type)
        movie, created = Movie.objects.get_or_create(
            tmdb_id=details.tmdb_id,
            content_type=details.content_type or content_type,
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
    # Re-project the confirmed mapping so the canonical row carries the
    # manual match (MatchMethod.MANUAL) the catalogue reads back.
    ingest_scraped_mapping(mapping)
    # Manual match = explicit user intent: if the canonical path has no
    # availability for this TMDb id yet, request an asynchronous refresh.
    request_availability_refresh(movie, priority=RefreshPriority.EXPLICIT, country=_active_country(request))

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
    if is_canonical_csv(file_bytes):
        try:
            rows = parse_canonical_csv(file_bytes)
        except CanonicalImportError as exc:
            return Response({"error": str(exc)}, status=400)
    else:
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


@require_GET
def export_watchlist(request):
    """Download the watchlist as a canonical WhereTfDoIWatchIt CSV backup."""
    return csv_response(FILENAME_WATCHLIST, watchlist_export_rows(user_id=1))


@require_GET
def export_watched(request):
    """Download explicitly watched movies as a canonical CSV backup."""
    return csv_response(FILENAME_WATCHED, watched_export_rows(user_id=1))


@require_GET
def export_combined(request):
    """Download watchlist and watched movies in one canonical CSV backup."""
    return csv_response(FILENAME_BOTH, combined_export_rows(user_id=1))


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

        if (item.get("content_type") or "movie") not in {"movie", "series"}:
            return Response({"error": "content_type must be movie or series"}, status=400)

    # Deduplicate selections by TMDb namespace while maintaining order
    unique_selections = []
    seen_ids = set()
    for item in selections:
        identity = (item["tmdb_id"], item.get("content_type") or "movie")
        if identity not in seen_ids:
            seen_ids.add(identity)
            unique_selections.append(item)

    watchlist_items = []
    for sel in unique_selections:
        movie, created = Movie.objects.get_or_create(
            tmdb_id=sel["tmdb_id"],
            content_type=sel.get("content_type") or "movie",
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
        # Event-driven refresh: resolved selections become eligible for
        # canonical availability refresh (idempotent, local-only).
        request_availability_refresh(movie, priority=RefreshPriority.WATCHLIST, country=_active_country(request))
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
    curr_country = _active_country(request)
    preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)

    items = (
        WatchlistItem.objects.select_related("movie")
        .filter(user_id=1)
        .order_by("-added_at")
    )
    watchlist_movies = [item.movie for item in items]

    # What the refresh queue knows about each title (pending/checked/failed/
    # never_checked): threaded into the watchability evaluation so a title
    # with no offers reads 🔵 Checking… while its refresh is queued, ⚫ Not
    # available once checked, and ⚪ Unknown only when never fetched.
    refresh_states = refresh_states_for({m.id for m in watchlist_movies})

    # Apply composable filters to the watchlist movies
    evaluated_filtered, _ = apply_composable_filters(
        watchlist_movies,
        request.GET,
        preference,
        current_country=curr_country,
        refresh_states=refresh_states,
    )

    report = []
    country_movie_sets: dict[str, set[int]] = {}
    service_movie_sets: dict[str, set[int]] = {}
    available_movies_count = 0
    needs_recheck_movies_count = 0
    user_data_by_movie = {
        row.movie_id: row
        for row in UserMovieData.objects.filter(
            user_id=1,
            movie_id__in=[item["movie"].id for item in evaluated_filtered],
        )
    }

    for eval_item in evaluated_filtered:
        movie = eval_item["movie"]
        watch_res = eval_item["watchability"]

        # Check if movie is marked as watched for watchlist status
        user_data = user_data_by_movie.get(movie.id)
        is_watched = bool(user_data and user_data.is_watched)

        # ``apply_composable_filters`` already performed one bulk canonical
        # read for this report.  Reusing that same list keeps report summaries
        # in lockstep with the badge and avoids a second N+1 reconciliation.
        raw_offers = eval_item["offers"]
        ranked = rank_offers(raw_offers, preference, current_country=curr_country)

        countries = sorted(list({r.country.upper() for r in ranked if r.country}))
        best = ranked[0] if ranked else None
        best_country = best.country.upper() if best else None
        best_service = best.service if best else None

        if countries or watch_res.status in ("watchable_here", "watchable_vpn"):
            available_movies_count += 1
        elif watch_res.status == WatchabilityStatus.NEEDS_RECHECK:
            needs_recheck_movies_count += 1

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
    unavailable_movies_count = (
        total_movies - available_movies_count - needs_recheck_movies_count
    )

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
        # Only stale/unverified evidence: neither available nor unavailable.
        "needs_recheck_movies": needs_recheck_movies_count,
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


@api_view(["GET"])
def watched_report(request):
    """GET /api/watched/report/ — the Watchlist report scoped to watched titles.

    Same evaluation, filters (including actor multi-AND and duration min/max)
    and pagination as /api/watchlist/report/; the only difference is the source
    of titles: ``UserMovieData.is_watched`` rows (the same watch state the
    mark/unmark endpoints write), so there is no second watched-list model.
    Ordered most-recently-watched first, undated rows last.
    """
    curr_country = _active_country(request)
    preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
    def positive_int(name, default, maximum=None):
        raw = request.GET.get(name, str(default))
        try:
            value = max(1, int(raw)) if raw.isdecimal() else default
        except (ValueError, TypeError):
            value = default
        return min(value, maximum) if maximum else value

    page = positive_int("page", 1)
    page_size = positive_int("page_size", 24, 100)
    events = (
        MovieWatchEvent.objects.filter(user_id=1, movie_id=OuterRef("movie_id"))
        .order_by().values("movie_id")
        .annotate(watch_count=Count("pk"), last_watched_at=Max("watched_date"))
    )
    watched_qs = UserMovieData.objects.filter(user_id=1, is_watched=True).select_related("movie")
    if (request.GET.get("watched") or request.GET.get("watched_status") or "").strip().lower() == "unwatched":
        watched_qs = watched_qs.none()
    watched_qs = watched_qs.annotate(
        watch_count=Coalesce(Subquery(events.values("watch_count")), 1),
        last_watched_at=Coalesce(Subquery(events.values("last_watched_at")), F("watched_date")),
    )

    # Apply the common predicates before counting/slicing. The report only
    # hydrates and evaluates the requested page for ordinary browsing.
    search = (request.GET.get("q") or request.GET.get("query") or "").strip()
    if search:
        watched_qs = watched_qs.filter(Q(movie__title__icontains=search) | Q(movie__overview__icontains=search))
    content_type = (request.GET.get("content_type") or request.GET.get("type") or "").lower()
    if content_type in ("movie", "series", "tv"):
        watched_qs = watched_qs.filter(movie__content_type="series" if content_type == "tv" else content_type)
    year_exact = request.GET.get("year")
    if year_exact and year_exact.isdigit():
        watched_qs = watched_qs.filter(movie__year=int(year_exact))
    else:
        year_min = request.GET.get("year_min") or request.GET.get("year_start")
        year_max = request.GET.get("year_max") or request.GET.get("year_end")
        if year_min and year_min.isdigit(): watched_qs = watched_qs.filter(movie__year__gte=int(year_min))
        if year_max and year_max.isdigit(): watched_qs = watched_qs.filter(movie__year__lte=int(year_max))
    score = request.GET.get("score_min") or request.GET.get("min_score") or request.GET.get("rating_min")
    if score:
        try: watched_qs = watched_qs.filter(movie__tmdb_rating__gte=float(score))
        except (ValueError, TypeError): pass
    dur_min, dur_max = request.GET.get("duration_min"), request.GET.get("duration_max")
    try: dur_min = int(dur_min) if dur_min not in (None, "") and int(dur_min) >= 0 else None
    except (ValueError, TypeError): dur_min = None
    try: dur_max = int(dur_max) if dur_max not in (None, "") and int(dur_max) >= 0 else None
    except (ValueError, TypeError): dur_max = None
    if dur_min is not None and dur_max is not None and dur_min > dur_max:
        # Preserve the shared filter contract: an invalid interval is ignored.
        dur_min = dur_max = None
    if dur_min is not None or dur_max is not None:
        watched_qs = watched_qs.filter(movie__content_type="movie", movie__runtime_minutes__gt=0)
        if dur_min is not None: watched_qs = watched_qs.filter(movie__runtime_minutes__gte=dur_min)
        if dur_max is not None: watched_qs = watched_qs.filter(movie__runtime_minutes__lte=dur_max)

    actor_ids, actor_names = parse_actor_filters(request.GET)
    # PostgreSQL JSONB containment keeps repeated numeric actor selections
    # entirely in SQL, with AND semantics and no duplicate candidate rows.
    for actor_id in set(actor_ids):
        watched_qs = watched_qs.filter(movie__cast__contains=[{"id": actor_id}])

    availability_dependent = any(request.GET.get(key) for key in (
        "country", "availability_status", "status", "watchable", "watchable_only",
        "providers", "provider", "service", "quality", "access", "access_type",
    ))
    # Remaining metadata filters (actor names/genre) and availability filters
    # need Python evaluation. Bound this exceptional path explicitly; never
    # silently paginate before filtering or return a misleading total.
    requires_candidate_scan = availability_dependent or bool(actor_names) or bool(request.GET.get("genre") or request.GET.get("genre_id"))
    candidate_limit = 5000
    total_candidates = watched_qs.count()
    if requires_candidate_scan and total_candidates > candidate_limit:
        return Response({"error": "This filter combination matches too many watched titles; narrow the filters and try again."}, status=400)

    sort = (request.GET.get("sort_by") or request.GET.get("sort") or "").lower().strip()
    title_tiebreakers = (Lower("movie__title"), "movie__title", "movie__tmdb_id")
    if sort in ("watched_rating_desc", "personal_rating_desc", "rating"):
        ordering = (F("rating").desc(nulls_last=True), *title_tiebreakers)
    elif sort in ("rating_desc", "rating_high"):
        ordering = (F("movie__tmdb_rating").desc(nulls_last=True), *title_tiebreakers)
    elif sort in ("year_desc", "year_newest"):
        ordering = (F("movie__year").desc(nulls_last=True), *title_tiebreakers)
    elif sort in ("year_asc", "year_oldest"):
        ordering = (F("movie__year").asc(nulls_last=True), *title_tiebreakers)
    elif sort in ("title_desc", "-title"):
        ordering = (Lower("movie__title").desc(), "movie__title", "movie__tmdb_id")
    elif sort in ("title_asc", "title"):
        ordering = (Lower("movie__title").asc(), "movie__title", "movie__tmdb_id")
    else:
        # Empty/default and legacy last-watched keys share the event-based order.
        ordering = (F("last_watched_at").desc(nulls_last=True), *title_tiebreakers)
    watched_qs = watched_qs.order_by(*ordering)

    # Sorting is owned by the queryset/candidate stage, so the shared filter
    # evaluator must not re-sort an already paginated ordinary page.
    filter_params = request.GET.copy()
    filter_params.pop("sort_by", None)
    filter_params.pop("sort", None)

    if requires_candidate_scan:
        watched = list(watched_qs)
        watched_movies = [row.movie for row in watched]
        filtered, _ = apply_composable_filters(
            watched_movies, filter_params, preference, current_country=curr_country,
            refresh_states=refresh_states_for({m.id for m in watched_movies}),
        )
        allowed = {entry["movie"].id for entry in filtered}
        filtered_rows = [row for row in watched if row.movie_id in allowed]
        # SQL ordering on the unfiltered candidates is not sufficient here:
        # filters can remove arbitrary rows, so apply the same global key to
        # the complete (bounded) matching set before page slicing.
        def sort_candidate_rows(rows, value_for, *, descending=False):
            rows.sort(key=lambda row: (row.movie.title.lower(), row.movie.title, row.movie.tmdb_id))
            dated = [row for row in rows if value_for(row) is not None]
            undated = [row for row in rows if value_for(row) is None]
            dated.sort(key=value_for, reverse=descending)
            return dated + undated

        if sort in ("watched_rating_desc", "personal_rating_desc", "rating"):
            filtered_rows = sort_candidate_rows(filtered_rows, lambda row: row.rating, descending=True)
        elif sort in ("rating_desc", "rating_high"):
            filtered_rows = sort_candidate_rows(filtered_rows, lambda row: row.movie.tmdb_rating, descending=True)
        elif sort in ("year_desc", "year_newest"):
            filtered_rows = sort_candidate_rows(filtered_rows, lambda row: row.movie.year, descending=True)
        elif sort in ("year_asc", "year_oldest"):
            filtered_rows = sort_candidate_rows(filtered_rows, lambda row: row.movie.year)
        elif sort in ("title_desc", "-title"):
            filtered_rows = sort_candidate_rows(filtered_rows, lambda row: row.movie.title.lower(), descending=True)
        elif sort in ("title_asc", "title"):
            filtered_rows = sort_candidate_rows(filtered_rows, lambda row: row.movie.title.lower())
        else:
            filtered_rows = sort_candidate_rows(filtered_rows, lambda row: row.last_watched_at, descending=True)
        total_movies = len(filtered_rows)
        start = (page - 1) * page_size
        watched = filtered_rows[start:start + page_size]
        watched_movies = [row.movie for row in watched]
        evaluated_filtered = [entry for entry in filtered if entry["movie"].id in {m.id for m in watched_movies}]
    else:
        total_movies = total_candidates
        start = (page - 1) * page_size
        watched = list(watched_qs[start:start + page_size])
        watched_movies = [row.movie for row in watched]

    watched_by_id = {row.movie_id: row for row in watched}
    watch_counts = {row.movie_id: row.watch_count for row in watched}
    refresh_states = refresh_states_for({m.id for m in watched_movies})

    # Apply composable filters (actor multi-AND, duration min/max, content
    # type, year, genre, score, country, provider, watchable, status, quality,
    # watched) to the watched titles.
    if not requires_candidate_scan:
        evaluated_filtered, _ = apply_composable_filters(
            watched_movies,
            filter_params,
            preference,
            current_country=curr_country,
            refresh_states=refresh_states,
        )

    report = []
    country_movie_sets: dict[str, set[int]] = {}
    service_movie_sets: dict[str, set[int]] = {}
    available_movies_count = 0
    needs_recheck_movies_count = 0

    for eval_item in evaluated_filtered:
        movie = eval_item["movie"]
        watch_res = eval_item["watchability"]

        raw_offers = eval_item["offers"]
        ranked = rank_offers(raw_offers, preference, current_country=curr_country)

        countries = sorted(list({r.country.upper() for r in ranked if r.country}))
        best = ranked[0] if ranked else None
        best_country = best.country.upper() if best else None
        best_service = best.service if best else None

        if countries or watch_res.status in ("watchable_here", "watchable_vpn"):
            available_movies_count += 1
        elif watch_res.status == WatchabilityStatus.NEEDS_RECHECK:
            needs_recheck_movies_count += 1

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
                "is_watched": True,
                "user_data": {
                    "is_watched": True,
                    "rating": watched_by_id[movie.id].rating,
                    "review": watched_by_id[movie.id].review,
                    "watched_date": (
                        watched_by_id[movie.id].watched_date.isoformat()
                        if watched_by_id[movie.id].watched_date else None
                    ),
                },
                "watch_count": watch_counts.get(movie.id, 1),
                "last_watched_at": (
                    watched_by_id[movie.id].last_watched_at.isoformat()
                    if watched_by_id[movie.id].last_watched_at else None
                ),
            }
        )

    # The legacy watched page sorted by the user's own rating. Keep that
    # ordering distinct from TMDb title-score sorting on the shared FilterBar.
    unavailable_movies_count = (
        len(report) - available_movies_count - needs_recheck_movies_count
    )

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
        "needs_recheck_movies": needs_recheck_movies_count,
        "countries": country_list,
        "services": service_list,
    }

    total_pages = max(1, (total_movies + page_size - 1) // page_size)
    pagination = {
        "count": total_movies, "page": page, "page_size": page_size,
        "total_pages": total_pages, "has_next": page < total_pages,
        "has_previous": page > 1,
    }

    return Response(
        {
            "report": report,
            "pagination": pagination,
            "summary": summary,
        }
    )


def _canonical_provider(provider: str) -> str:
    """Resolve a provider string to the slug Regions are keyed by.

    Offer cards show display names ("Prime Video"), and the manual-entry modal
    sends that display name back, while the cache writes Regions keyed by
    service id ("prime"). Resolve through the Provider table so a manual
    language entry lands on the same row the offers pipeline reads; unknown
    strings pass through unchanged so the endpoint keeps working for
    providers with no Provider row.
    """
    prov = Provider.objects.filter(slug__iexact=provider).first()
    if prov is None:
        prov = Provider.objects.filter(name__iexact=provider).first()
    return prov.slug if prov else provider


@api_view(["GET", "PUT"])
def movie_language_info(request, tmdb_id: int):
    """Read or update LanguageInfo for a movie/region pair (show-level)."""
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
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
                region__provider__iexact=_canonical_provider(provider_clean),
                region__country__iexact=country_clean,
                season__isnull=True,
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
            provider=_canonical_provider(provider),
            country=country,
        )
        info, _ = LanguageInfo.objects.get_or_create(
            movie=movie,
            region=region,
            season=None,
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


@api_view(["POST"])
def watchlist_refresh_explicit(request):
    """POST /api/watchlist/refresh-explicit/ — queue a bulk explicit refresh.

    Backend for the watchlist page's "Refresh all availability" action. The
    single-title refresh endpoint does one title; this one enqueues every
    watchlisted title at ``RefreshPriority.EXPLICIT`` in one local pass and
    answers immediately — enqueue-only by contract, so the HTTP request
    never contacts a provider and never spends API budget. The existing
    ``refresh_worker`` drains the queue under the shared rate limit and
    daily budget; the frontend polls ``refresh-status`` for progress.

    The run owns a ``batch`` uuid stamped onto every request it touches, so
    the progress endpoint counts *this run's* backlog — repeated clicks and
    rows opened earlier by other events collapse into the same batch.

    Catalogue-capable sources (the scheduled whole-catalogue syncers) are
    not per-title work: when any of them reads stale, a
    ``CatalogueRefreshRequest`` marker is queued too and the worker syncs
    those whole catalogues after the title queue — so "Refresh all" genuinely
    covers every source, not only the default title-targeted one.
    ``catalogue_queued`` reports which.

    ``now`` is the server epoch captured *before* the enqueue pass, so every
    completion this run produces (including ones racing the response) counts
    toward the status endpoint's progress, while earlier refreshes never do.
    """
    started_at = timezone.now()
    result = request_watchlist_refresh(user_id=1, country=_active_country(request))

    if result["total_watchlist"] and not result["queued"]:
        # request_watchlist_refresh swallows enqueue failures (same contract
        # as the per-title path); for an explicit user action a total
        # failure must surface instead of reporting a phantom success.
        return Response(
            {"error": "failed to queue availability refresh"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Record the run before the catalogue marker so the marker can be
    # attributed to it. ``/api/availability/refreshes/`` already does this; the
    # explicit watchlist path did not, which is why an explicit "Refresh all"
    # left 601 RefreshRequests whose batch had no AvailabilityRefresh row.
    movie_ids = _watchlist_movie_ids()
    refresh = record_run(
        AvailabilityRefreshType.WATCHLIST,
        batch=result.get("batch"),
        movie_ids=movie_ids,
        source_policy=title_level_source_slugs(),
        country=_active_country(request),
    )

    stale_catalogue = stale_catalogue_sources()
    if stale_catalogue:
        request_catalogue_refresh(stale_catalogue, availability_refresh=refresh)

    return Response(
        {
            **result,
            "refresh_id": refresh.pk,
            "now": started_at.timestamp(),
            "catalogue_queued": stale_catalogue,
        },
        status=status.HTTP_202_ACCEPTED,
    )


def _next_utc_midnight_ts() -> float:
    """Epoch seconds of the next UTC midnight — where the daily quota resets."""
    now = datetime.now(dt_timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, time.min, tzinfo=dt_timezone.utc)
    return midnight.timestamp()


@api_view(["GET"])
def watchlist_refresh_status(request):
    """GET /api/watchlist/refresh-status/?since=<epoch>[&batch=<uuid>] — bulk progress.

    Counts the watchlist titles' refresh requests, split into ``pending``
    (still queued for the worker) plus ``refreshed`` (done) and ``failed``.

    **Source fan-out.** Refresh All now queues one request per (title, source)
    across every registered title-level source, so a run's unit of work is a
    *source check*, not a title — ``total`` and the three counts are all in
    checks, and ``titles``/``checks_per_title`` let the UI say so honestly
    instead of implying one check per title. With ``batch`` (the enqueue
    response's ``batch`` uuid) every count is scoped to that run's stamped
    rows across **all** sources: ``pending`` then means "this button's
    backlog", never unrelated requests queued elsewhere — repeated clicks
    re-stamp rows, so the newest batch always sees the full queue it owns.
    Without ``batch`` the legacy unscoped behavior stands.

    Legacy ``source=""`` batches keep working: those rows are counted like any
    other source-scoped row, so an in-flight batch queued before source
    fan-out still reports real progress rather than zero.

    **Rate limiting is per source**, so the response names the blocked sources
    (``rate_limited_sources``) instead of claiming the whole watchlist is
    blocked. ``rate_limited`` stays for backwards compatibility and means
    "at least one source is waiting for its quota" — it is never used to claim
    nothing else is progressing. ``source_quotas`` reports the remaining
    budget per source and omits unmetered sources entirely (there is no quota
    to report for them, so none is invented).
    """
    since_raw = request.GET.get("since")
    try:
        since_ts = float(since_raw)
    except (TypeError, ValueError):
        return Response(
            {"error": "since must be an epoch timestamp"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    since = datetime.fromtimestamp(since_ts, tz=dt_timezone.utc)

    batch_raw = request.GET.get("batch")
    batch_id = None
    if batch_raw:
        try:
            batch_id = uuid.UUID(batch_raw)
        except ValueError:
            return Response(
                {"error": "batch must be a UUID"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    watchlist_movie_ids = WatchlistItem.objects.filter(user_id=1).values("movie_id")
    # No ``source`` filter: a run owns one row per (title, source), and this
    # endpoint must aggregate all of them — the legacy ``source=""`` rows and
    # every explicit slug alike.
    scope = RefreshRequest.objects.filter(
        movie_id__in=Subquery(watchlist_movie_ids),
    )
    if batch_id is not None:
        batch_scope = scope.filter(batch=batch_id)
        pending = batch_scope.filter(status=RefreshRequestStatus.PENDING).count()
        refreshed = batch_scope.filter(status=RefreshRequestStatus.DONE).count()
        failed = batch_scope.filter(status=RefreshRequestStatus.FAILED).count()
        total = batch_scope.count()
    else:
        # No run boundary was supplied, so this endpoint is reporting global
        # progress. "Outstanding" must therefore mean *outstanding work some
        # live run still owns* — not "any pending row that ever existed".
        #
        # These are different, and conflating them is a live defect. Two runs
        # (5 and 6) were recorded `partial` under the pre-P6a contract while
        # still owning 476 pending rows. Those rows are historical and runnable:
        # no source is out of budget, so they were never legitimately settled,
        # and inventing an outcome for them would falsify the record. They are
        # left untouched — but they must not be counted as work in flight,
        # because there is no active run and the frontend would render 382
        # pending requests as a refresh currently progressing.
        #
        # A row belongs to live work if either (a) a non-terminal refresh owns
        # it, or (b) it has no batch at all.
        #
        # Case (b) matters and was missed in the first pass of this fix. A
        # single-title refresh — the detail page, an import, cron — is queued
        # with `batch=NULL` and is genuinely outstanding work that no run
        # "owns". Excluding it made the endpoint report zero pending for a
        # watchlist with a real queued title, which is the mirror image of the
        # bug being fixed. So: exclude rows sitting behind a *terminal* run, and
        # keep everything else.
        #
        # `since` still bounds *completion* counts; ownership bounds pending.
        terminal_batches = AvailabilityRefresh.objects.filter(
            status__in=TERMINAL_STATUSES
        ).exclude(batch=None).values_list("batch", flat=True)

        pending = scope.filter(status=RefreshRequestStatus.PENDING).exclude(
            batch__in=terminal_batches
        ).count()
        refreshed = scope.filter(
            status=RefreshRequestStatus.DONE, completed_at__gte=since
        ).count()
        failed = scope.filter(
            status=RefreshRequestStatus.FAILED, completed_at__gte=since
        ).count()
        # Without a batch there is no run boundary, so the comparable total is
        # everything still outstanding plus what completed since ``since``.
        total = pending + refreshed + failed

    # Which sources are out of budget, and what is left for each. Computed
    # from the limiters themselves so this endpoint can never disagree with
    # the worker about what is blocked.
    source_quotas = {}
    for slug in title_level_source_slugs():
        limiter = rate_limiter_for(slug)
        remaining = limiter.remaining()
        monthly_remaining = limiter.monthly_remaining()
        if remaining is None and monthly_remaining is None:
            continue  # unmetered: no quota to report, none invented
        monthly_resets_at = limiter.monthly_resets_at()
        accounting_since = limiter.monthly_accounting_since()
        source_quotas[slug] = {
            # Kept for existing clients: this is the daily headroom.
            "remaining": remaining,
            "limit": limiter.daily_limit,
            "exhausted": limiter.is_exhausted(),
            "monthly_used": limiter.monthly_used(),
            "monthly_remaining": monthly_remaining,
            "monthly_limit": limiter.monthly_limit,
            "monthly_window_days": limiter.monthly_window_days,
            "monthly_accounting_since": (
                accounting_since.isoformat() if accounting_since else None
            ),
            "monthly_resets_at": (
                monthly_resets_at.timestamp() if monthly_resets_at else None
            ),
        }
    rate_limited_sources = sorted(
        slug for slug, quota in source_quotas.items() if quota["exhausted"]
    )

    # How many titles this run covers, so the UI can distinguish "3 of 29
    # titles" from "60 of 87 checks". Only meaningful for a batched run.
    #
    # ``checks_per_title`` counts the distinct sources actually queued per
    # title rather than dividing total by titles: rows settle over time, so a
    # mean would drift mid-run (571/191 = 2.98) even though nothing about the
    # fan-out changed. The distinct-source count is the actual fan-out width
    # and stays exact.
    titles = 0
    checks_per_title = 0
    if batch_id is not None:
        batched = scope.filter(batch=batch_id)
        titles = batched.values("movie_id").distinct().count()
        per_title_sources = (
            batched.values("movie_id")
            .annotate(n=Count("source", distinct=True))
            .aggregate(m=Max("n"))["m"]
        )
        checks_per_title = per_title_sources or 0

    return Response(
        {
            "total": total,
            "pending": pending,
            "refreshed": refreshed,
            "failed": failed,
            "titles": titles,
            "checks_per_title": checks_per_title,
            "since": since_ts,
            "batch": str(batch_id) if batch_id else None,
            # "at least one source is waiting for its quota" — never "the whole
            # refresh is blocked".
            "rate_limited": bool(rate_limited_sources),
            "rate_limited_sources": rate_limited_sources,
            "source_quotas": source_quotas,
            "resets_at": _next_utc_midnight_ts(),
        },
        status=status.HTTP_200_OK,
    )


# --- source-first availability observability ------------------------------


def _watchlist_movie_ids() -> list[int]:
    return list(
        WatchlistItem.objects.filter(user_id=1).values_list("movie_id", flat=True)
    )


def _serialize_refresh(refresh, movie_ids) -> dict:
    """One refresh run as the UI consumes it, with live per-source progress.

    **Progress counters are recomputed on read; the run *status* is not.**
    That split is deliberate and is the fix for the design this replaced.

    Recomputing the per-source and title counters is what makes a poll after
    a browser reload show exactly what the button showed before it, and it is
    derived purely from persisted state, so it cannot be wrong. But
    ``status`` is a *lifecycle* fact written by the worker (see
    :mod:`tracker.services.refresh_lifecycle`) and is read straight off the
    row. A reader that inferred "no pending requests, therefore completed"
    would be making the transition itself — which is how a run gets marked
    COMPLETED by a GET request, and how a crashed run gets quietly reported
    as a success.
    """
    if refresh.status not in TERMINAL_STATUSES:
        # Not terminal yet: refresh the progress rows so the UI can show what
        # is moving, but leave the status to the worker.
        sync_run_progress(refresh, movie_ids, batch=refresh.batch)
    return {
        "id": refresh.pk,
        "type": refresh.run_type,
        "status": refresh.status,
        "batch": str(refresh.batch) if refresh.batch else None,
        "created_at": refresh.created_at,
        "started_at": refresh.started_at,
        "finished_at": refresh.finished_at,
        "is_active": refresh.is_active,
        "total_titles": refresh.total_titles,
        "completed_titles": refresh.completed_titles,
        "available_titles": refresh.available_titles,
        "unavailable_titles": refresh.unavailable_titles,
        "unknown_titles": refresh.unknown_titles,
        "failed_titles": refresh.failed_titles,
        "unknown_reasons": refresh.unknown_reasons,
        "policy": source_policy.policy_description(),
        "sources": [
            {
                "source": row.source,
                "label": source_label(row.source),
                "tier": row.tier,
                "kind": row.kind,
                "status": row.status,
                "reason": row.reason,
                "total": row.total,
                "completed": row.completed,
                "successful": row.successful,
                "failed": row.failed,
                "unknown": row.unknown,
                "queued": row.queued,
                "error": row.error,
            }
            for row in refresh.sources.all()
        ],
    }


@api_view(["GET"])
def availability_report(request):
    """GET /api/availability/report/ — the source-first availability picture.

    Answers, in one read, the two questions the old button-state could not:

    - "what is running, what is queued, what succeeded, and which source
      produced the result" — ``sources`` (per-title checks) and ``collectors``
      (whole-catalogue syncs, reported separately because a catalogue source
      cannot answer a question about one film);
    - "why are these titles still unknown" — ``unknown_reasons``, a
      ``{reason: count}`` map over the same evidence the per-title breakdown
      uses.

    Also returns ``policy`` and ``counterfactual`` so the source hierarchy
    is never implicit: the user can see which ladder is active and what
    switching it would cost before switching it.
    """
    movie_ids = _watchlist_movie_ids()
    batch_raw = request.GET.get("batch")
    batch = batch_raw or None
    country = (
        request.GET.get("current_country")
        or request.GET.get("country")
        or resolve_preferences(user_id=1).get("current_country")
        or None
    )
    summary = build_run_summary(movie_ids, batch=batch, country=country)
    counterfactual = source_policy.counterfactual(
        summary.pop("_states"), summary.pop("_resolved_sources")
    )
    return Response(
        {
            **summary,
            "policy": source_policy.policy_description(),
            "counterfactual": counterfactual,
            "resets_at": _next_utc_midnight_ts(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def availability_metrics(request):
    """GET /api/availability/metrics/ — seven-day operational metrics.

    Separate from ``/api/availability/report/`` on purpose. The report answers
    "what is true about availability right now, per title"; this answers "how
    has the refresh machinery itself been behaving lately". Mixing them would
    make the report heavier for every consumer just to serve a dashboard.

    Entirely observational: ``availability_metrics`` reads persisted state and
    makes no upstream call, so this endpoint is safe to poll and costs no quota.

    Three presentation rules, applied here rather than left to the dashboard,
    because a client that cannot see the difference will render it wrongly:

    * ``null`` means *no data*, ``0`` means *measured zero*. A window with no
      runs reports ``completion_rate: null``, not ``0.0``.
    * Sources that never executed are returned with ``never_executed: true`` and
      null counters, so "never ran" is never confused with "ran and found
      nothing".
    * Refresh failure is read from ``status='failed'`` alone, never inferred
      from unknown-title counts.
    """
    days_param = request.GET.get("days")
    days = WINDOW_DAYS
    if days_param:
        try:
            days = max(1, min(int(days_param), 90))
        except (TypeError, ValueError):
            return Response(
                {"error": f"days must be an integer; got {days_param!r}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
    return Response(
        collect_availability_metrics(days=days), status=status.HTTP_200_OK
    )


@api_view(["GET"])
def availability_title_report(request, tmdb_id: int):
    """GET /api/availability/report/<tmdb_id>/ — one title's per-source verdicts.

    The per-title half of the plan's "Availability sources checked" panel:

        ✓ JustWatch          checked 14:32
        ✓ YouTube            checked 14:31
        — BBC iPlayer        not applicable (catalogue source)
        ⏳ Streaming API      queued — quota exhausted

        Result: Available on BBC iPlayer

    The headline is the *title's* state, which is ``available`` if **any**
    source produced an offer regardless of how many others are silent — the
    one behaviour the old single ``unknown`` could not express, and the one
    that stops a blocked source from being reported as absence of
    availability.
    """
    movie = _unique_movie_for_tmdb(tmdb_id)
    if movie is None:
        return Response(
            {"error": "movie not found"}, status=status.HTTP_404_NOT_FOUND
        )
    access_country = (
        request.query_params.get("current_country")
        or request.query_params.get("country")
        or resolve_preferences(user_id=1).get("current_country")
        or ""
    )
    report = title_source_report([movie.pk], country=str(access_country).upper() if access_country else None).get(movie.pk, {})
    from .services.availability_report import catalogue_progress

    return Response(
        {
            "tmdb_id": movie.tmdb_id,
            "title": movie.title,
            **report,
            "collectors": catalogue_progress(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def availability_refresh_list(request):
    """GET /api/availability/refreshes/?active=true — runs a page load can resume.

    This is the endpoint that makes a browser reload harmless. The frontend
    does not own the state of a long-running refresh: it asks for the active
    run, redraws it, and keeps polling.

    ``results`` is always the recent history, newest first, and ``active`` is
    the newest non-terminal run (or ``null``). The two are deliberately
    independent: the frontend's *job* is "attach to whatever is running", and
    a page load that also wants to show the last finished run must not have
    to make a second request. Filtering ``results`` down to active-only would
    mean a reload after the run finished shows an empty list and no outcome
    at all — losing exactly the information the panel exists to surface.

    Reading this endpoint never changes a run's status. The worker owns the
    lifecycle; see ``refresh_lifecycle.AvailabilityRefreshLifecycle``.
    """
    runs = AvailabilityRefresh.objects.all()[:20]
    movie_ids = _watchlist_movie_ids()
    current = active_refresh()
    return Response(
        {
            "results": [_serialize_refresh(run, movie_ids) for run in runs],
            "active": _serialize_refresh(current, movie_ids) if current else None,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def availability_refresh_detail(request, pk: int):
    """GET /api/availability/refreshes/<pk>/ — one run, reconstructable from the DB."""
    refresh = AvailabilityRefresh.objects.filter(pk=pk).first()
    if refresh is None:
        return Response(
            {"error": "refresh not found"}, status=status.HTTP_404_NOT_FOUND
        )
    return Response(
        _serialize_refresh(refresh, _watchlist_movie_ids()),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
def availability_refresh_start(request):
    """POST /api/availability/refresh/ — queue a run and return its id.

    Enqueue-only by contract, exactly like the existing
    ``refresh-explicit`` endpoint: the HTTP request never contacts a
    provider. It returns ``{refresh_id, batch, ...}`` and the client polls
    ``GET /api/availability/refreshes/<refresh_id>/`` — so a reload resumes
    from the id rather than losing the run.
    """
    started_at = timezone.now()
    result = request_watchlist_refresh(user_id=1, country=_active_country(request))

    if result["total_watchlist"] and not result["queued"]:
        return Response(
            {"error": "failed to queue availability refresh"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    stale_catalogue = stale_catalogue_sources()

    movie_ids = _watchlist_movie_ids()
    # The run's frozen policy is the union of sources it may consult across
    # its titles — the same set the enqueue just decided from. Note this is
    # `title_level_source_slugs()`, not the role set: a catalogue-driven
    # source has no per-title request row, so recording it as run scope would
    # be a claim about scheduling the queue cannot honour.
    refresh = record_run(
        AvailabilityRefreshType.WATCHLIST,
        batch=result.get("batch"),
        movie_ids=movie_ids,
        source_policy=title_level_source_slugs(),
    )
    # The marker is queued first here (the run is recorded just above), so it is
    # associated afterwards. A marker already pending from an earlier refresh
    # keeps its existing links and simply gains this one: coalescing means one
    # marker can legitimately represent several refreshes.
    if stale_catalogue:
        request_catalogue_refresh(
            stale_catalogue, availability_refresh=refresh
        )

    sync_run_progress(refresh, movie_ids, batch=refresh.batch)
    return Response(
        {
            "refresh_id": refresh.pk,
            **result,
            "now": started_at.timestamp(),
            "catalogue_queued": stale_catalogue,
            "refresh": _serialize_refresh(refresh, movie_ids),
        },
        status=status.HTTP_202_ACCEPTED,
    )


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


def _active_country(request) -> str:
    """Active country for the list surfaces (homepage, catalog, watchlist).

    Mirrors :func:`_current_country` (the detail pages) but also honours the
    legacy ``country`` parameter. All four surfaces must evaluate the same
    country, or the same title reads 🟢 "Watchable here" on its detail page and
    🟡 "Watchable with VPN" in the watchlist — the movie 615457 report. They
    used to default to a hard-coded "US" while the detail page used the user's
    saved country (NL), so a local offer looked foreign.
    """
    return (
        request.GET.get("current_country")
        or request.GET.get("country")
        or resolve_preferences(user_id=1).get("current_country")
        or "US"
    )


def _load_content_item(tmdb_id: int, content_type: str) -> Movie | None:
    """Load the tracked row for tmdb_id, resolving it through TMDb when missing.

    Typed detail routes always select their own TMDb namespace. Missing rows
    are resolved through the matching TMDb endpoint.
    """
    try:
        item = Movie.objects.get(tmdb_id=tmdb_id, content_type=content_type)
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
        item = None

    if item is not None:
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
    from tracker.services.presentation import present_offer
    from tracker.services.ranking import rank_offers
    from tracker.services.scraped_offers import BVOD_CATEGORIES, get_scraped_offers
    from tracker.services.access import build_reach, build_user_access, classify_offer
    from tracker.services.preferences import build_preference

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

    # Include BVOD scraped offers in the main offers list — read through the
    # canonical catalogue (provider observations + ContentMapping resolution)
    # instead of the legacy ScrapedContentMapping table.
    bvod_offers, provider_map = get_scraped_offers(
        movie, provider_categories=BVOD_CATEGORIES
    )
    if bvod_offers:
        preference = Preference.objects.filter(user_id=1).first() or Preference(user_id=1)
        classify_pref = build_preference(1)
        access = build_user_access(1)
        reach = build_reach(classify_pref, current_country)
        ranked = rank_offers(bvod_offers, preference, current_country=current_country)
        for offer in ranked:
            presented = present_offer(offer, provider_map)
            offer_dict = offer.to_dict()
            offer_dict["service"] = presented.service_display
            offer_dict["source_indicator"] = presented.source_indicator
            offer_dict["country"] = presented.country
            classified = classify_offer(
                offer, access=access, preference=classify_pref,
                reach=reach, provider_map=provider_map,
            )
            offer_dict["access_type"] = classified.access_type
            offer_dict["access_label"] = classified.access_label
            offer_dict["locality"] = classified.locality
            offer_dict["is_accessible"] = classified.is_accessible
            offer_dict["is_entitled"] = classified.is_entitled
            offer_dict["requires_payment"] = classified.requires_payment
            offer_dict["is_addon"] = classified.is_addon
            offer_dict["required_base"] = classified.required_base
            offer_dict["required_base_display"] = classified.required_base_display
            offer_dict["rejection_reasons"] = classified.rejection_reasons
            offers_out.append(offer_dict)

    return offers_out


def _prime_detail_response(request, item: Movie, key: str) -> Response:
    """Shared detail payload for prime_movie / prime_series, keyed by ``key``."""
    # Refresh-on-view (Phase B): this is the title detail page the frontend
    # opens. Local offers are returned immediately; a stale/missing title
    # gets an asynchronous refresh request, never a synchronous fetch.
    #
    # Fan out to every eligible title-level source, exactly as ``movie_detail``
    # does, so both detail entry points schedule the same targeted lookups
    # (capability-driven — catalogue-only collectors are structurally excluded
    # and no catalogue crawl can happen). Reusing ``request_title_refresh``
    # keeps the dedup, cooldown and metered-source policy identical here.
    request_title_refresh(
        item,
        priority=RefreshPriority.RECENTLY_VIEWED,
        country=_current_country(request),
    )

    user_data = UserMovieData.objects.filter(user_id=1, movie=item).first()
    is_in_watchlist = WatchlistItem.objects.filter(user_id=1, movie=item).exists()
    watch_events = MovieWatchEvent.objects.filter(user_id=1, movie=item).order_by("-watched_date", "-created_at")
    season = _requested_season(request, item)
    pending = RefreshRequest.objects.filter(
        movie=item, status=RefreshRequestStatus.PENDING
    ).exists()
    latest = (
        RefreshRequest.objects.filter(movie=item)
        .order_by("-requested_at", "-pk")
        .first()
    )
    from tracker.services.access import resolve_title as access_resolve_title
    from tracker.services.preferences import build_preference

    from tracker.services.watchability import describe_last_known, qualify_watchability_dict

    access_pref = build_preference(1)
    access_country = _current_country(request) or access_pref.current_country
    current_offers, last_known_offers = get_offer_evidence(item, season=season)
    access_verdict = access_resolve_title(
        item,
        current_offers,
        access_pref,
        current_country=access_country,
        refresh_state=(
            None if last_known_offers
            else refresh_states_for({item.pk}).get(item.pk)
        ),
    )
    last_known = describe_last_known(
        item, last_known_offers, access_pref, current_country=access_country
    )

    payload = {
        key: MovieDetailSerializer(item).data,
        "user_data": UserMovieDataSerializer(user_data).data if user_data else None,
        "is_in_watchlist": is_in_watchlist,
        "watch_events": MovieWatchEventSerializer(watch_events, many=True).data,
        "offers": _build_prime_offers(request, item, season=season),
        # The single access verdict for this title, computed by the same
        # resolver the watchlist badge uses (tracker.services.access). The
        # detail page and the watchlist can therefore never disagree about
        # whether a title is watchable, reachable via VPN, purchase-only or
        # genuinely unavailable.
        "watchability": qualify_watchability_dict(
            access_verdict.to_watchability_dict(), last_known
        ),
        # Stale ("last seen") or uncorroborated positive offers, classified by
        # the same resolver. History only: never part of ``offers``/counts.
        "last_known_offers": last_known.offers if last_known else [],
        # Rent/buy offers are surfaced separately (never in ``offers``, which
        # stays subscription/free by design) so "available only for an
        # additional payment" is distinguishable from "included".
        "purchase_offers": [c.to_dict() for c in access_verdict.purchase_offers],
        # The canonical availability verdict for this title, exactly as the
        # availability report computes it. Present so an empty ``offers`` list
        # is never ambiguous: the detail page can tell "checked and genuinely
        # unavailable" apart from "the evidence never resolved" without
        # inventing a second classifier. Absence of this field is read by the
        # frontend as unknown, never as unavailable.
        "availability": title_source_report([item.pk], country=str(access_country) if access_country else None).get(item.pk),
        # True while this title still waits in the refresh queue — the
        # frontend polls on it ("Checking availability…") until the worker
        # drains the request and canonical offers land.
        "refresh_pending": pending,
        # Status of the newest request for this title ("pending" / "done" /
        # "failed", null when never requested) straight off the same
        # RefreshRequest rows the queue maintains — so the frontend can tell
        # a completed refresh from one the worker gave up on, without a
        # second status system.
        "refresh_status": latest.status if latest else None,
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
    except Movie.MultipleObjectsReturned:
        return None
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

    offers = get_local_offers(movie)
    qualifying = [
        o for o in offers
        if (o.offer_type or "").lower() in QUALIFYING_OFFER_TYPES
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

    # Refresh-on-view (Phase B): opening a title is meaningful user intent.
    # The local availability is served above; a stale/missing title only
    # *requests* an asynchronous refresh here — the response never waits.
    #
    # Fan out to every eligible title-level source (capability-driven, so
    # catalogue-only collectors are structurally excluded and no catalogue
    # crawl is triggered). One default-scope check cannot answer a market only
    # JustWatch covers, which is why an opened title used to stay unknown.
    request_title_refresh(
        movie,
        priority=RefreshPriority.RECENTLY_VIEWED,
        country=_current_country(request),
    )

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
            # The canonical offers now key on a TMDb id that has likely never
            # been fetched — request a refresh (local insert, async drain).
            request_availability_refresh(movie, priority=RefreshPriority.EXPLICIT, country=_active_country(request))

    return Response({
        "message": f"Linked to TMDB {details.tmdb_id}",
        "movie": MovieDetailSerializer(movie).data,
    }, status=status.HTTP_200_OK)


@api_view(["PATCH"])
def update_movie(request, tmdb_id: int):
    """PATCH /api/movie/<tmdb_id>/ — update title and/or poster_path."""
    try:
        movie = Movie.objects.get(tmdb_id=tmdb_id)
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
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
            # Event-driven refresh (Phase B): local insert in the same
            # transaction — the response never waits for a provider; the
            # budgeted refresh run drains it later.
            request_availability_refresh(
                watchlist_item.movie,
                priority=RefreshPriority.WATCHLIST,
                country=_active_country(request),
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

@api_view(["GET"])
def watched_list(request):
    """GET /api/watched/ — Paginated list of titles the user has watched."""
    user_id = 1

    def positive_int(name, default):
        raw = request.GET.get(name, str(default))
        try:
            return max(1, int(raw)) if raw.isdecimal() else default
        except ValueError:
            # Python also rejects decimal strings beyond its integer digit limit.
            return default

    page = positive_int("page", 1)
    page_size = min(positive_int("page_size", 24), 100)

    qs = UserMovieData.objects.filter(user_id=user_id, is_watched=True).select_related("movie")

    # This endpoint is already scoped to watched rows. Keep the shared
    # watched selector semantics: "watched" and "all" are equivalent here,
    # while "unwatched" intersects this scope to an empty result.
    watched_filter = (request.GET.get("watched") or "all").strip().lower()
    if watched_filter == "unwatched":
        qs = qs.none()

    content_type = request.GET.get("content_type")
    if content_type in ("movie", "series"):
        qs = qs.filter(movie__content_type=content_type)

    search = (request.GET.get("search") or "").strip()
    if search:
        qs = qs.filter(movie__title__icontains=search)

    total_count = qs.count()
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    pagination = {
        "count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
    }
    start = (page - 1) * page_size
    # Avoid sending an arbitrarily large OFFSET to the database.
    if start >= total_count:
        return Response({"results": [], "pagination": pagination})

    # Isolate event aggregation from watchlist sources to avoid join fan-out.
    events = (
        MovieWatchEvent.objects.filter(user_id=user_id, movie_id=OuterRef("movie_id"))
        .order_by()
        .values("movie_id")
        .annotate(watch_count=Count("pk"), last_watched_at=Max("watched_date"))
    )
    qs = qs.annotate(
        watch_count=Coalesce(Subquery(events.values("watch_count")), 1),
        last_watched_at=Coalesce(Subquery(events.values("last_watched_at")), F("watched_date")),
        is_in_watchlist=Exists(
            WatchlistItem.objects.filter(user_id=user_id, movie_id=OuterRef("movie_id"))
        ),
    )
    sort = request.GET.get("sort", "last_watched")
    secondary_order = ("movie__title", "movie__tmdb_id")
    if sort == "title":
        qs = qs.order_by(*secondary_order)
    elif sort == "rating":
        qs = qs.order_by(F("rating").desc(nulls_last=True), *secondary_order)
    else:
        qs = qs.order_by(F("last_watched_at").desc(nulls_last=True), *secondary_order)

    results = []
    for r in qs[start:start + page_size]:
        m = r.movie
        results.append({
            "movie": {
                "tmdb_id": m.tmdb_id,
                "title": m.title,
                "year": m.year,
                "content_type": m.content_type,
                "poster_path": getattr(m, "poster_path", None),
            },
            "user_data": {
                "is_watched": r.is_watched,
                "rating": r.rating,
                "review": r.review,
                "watched_date": r.watched_date.isoformat() if r.watched_date else None,
            },
            "watch_count": r.watch_count,
            "last_watched_at": r.last_watched_at.isoformat() if r.last_watched_at else None,
            "is_in_watchlist": r.is_in_watchlist,
        })

    return Response({"results": results, "pagination": pagination})


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
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
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

        # Refresh-on-watch: the title just became history worth keeping
        # accurate. Freshness-guarded (a title checked within the stale
        # window enqueues nothing) and never-fetched titles still bump to
        # NEW — a local insert only, the response never waits for a fetch.
        request_availability_refresh(
            movie,
            priority=RefreshPriority.WATCHED,
            country=_active_country(request),
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
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
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
    except (Movie.DoesNotExist, Movie.MultipleObjectsReturned):
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

    from tracker.services import google_interests as gi

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
            # Follows Google's own pagination ("1-200 of 256") so a long list
            # imports completely instead of only its first chunk.
            html_pages = gi.fetch_list_pages(url_stripped, cookies=cookies)
        except Exception as exc:
            logger.warning("Google Interests fetch failed for %s: %s", url_stripped, exc)
            all_skipped.append({"url": url_stripped, "reason": f"fetch_failed: {exc}"})
            continue

        rows = []
        for html in html_pages:
            rows.extend(gi.parse_page(html, url_stripped))

        result = gi.import_interests(rows, client=client, user_id=user_id)
        if not rows:
            # Never report a silent 0/0/0: an empty parse means the page was
            # not a list we understand (consent page, empty list, untitled
            # cards) and the caller should see why.
            logger.warning("No titles parsed from %s", url_stripped)
            all_skipped.append(
                {
                    "url": url_stripped,
                    "reason": (
                        "no_items_found: page contained no list entries "
                        "(empty list, consent page, or cards without titles)"
                    ),
                }
            )
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


# ---------------------------------------------------------------------------
# Letterboxd public-profile URL importer endpoint
# ---------------------------------------------------------------------------

@api_view(["POST"])
def letterboxd_url_import(request):
    """POST /api/importers/letterboxd-url/ — import a public Letterboxd URL.

    Request body (JSON):
        url (str, required): profile / watchlist / films URL, e.g.
            https://letterboxd.com/USERNAME/ or .../USERNAME/watchlist/
        type (str, optional): "watchlist" or "watched". Required when the URL
            is a bare profile URL; optional otherwise (the URL path wins only
            when ``type`` is omitted).
        user_id (int, optional): defaults to 1.

    Fetches public paginated HTML (no Letterboxd login/cookies), normalizes it
    into LetterboxdRow objects and reuses import_rows() — the exact same TMDb
    matching / ambiguity / idempotency behaviour as the CSV importer.

    Errors are application-level only: 400 for bad input, 404 for a missing
    profile/list, 502 for upstream Letterboxd failures (timeout, blocked,
    non-2xx). Raw exception traces are never returned.
    """
    data = request.data if isinstance(request.data, dict) else {}
    url = str(data.get("url") or "").strip()
    requested_type = str(data.get("type") or "").strip().lower()
    try:
        user_id = int(data.get("user_id", 1) or 1)
    except (TypeError, ValueError):
        user_id = 1

    if not url:
        return Response({"error": "url is required"}, status=400)
    if requested_type and requested_type not in ("watchlist", "watched"):
        return Response(
            {"error": "type must be 'watchlist' or 'watched'"}, status=400
        )

    try:
        username, url_type = normalize_profile_url(url)
    except LetterboxdInvalidURLError as exc:
        return Response({"error": str(exc)}, status=400)

    file_type = requested_type or url_type
    if not file_type:
        return Response(
            {
                "error": "type is required for a bare profile URL — "
                "pass \"watchlist\" or \"watched\"."
            },
            status=400,
        )

    try:
        rows, pages_fetched = fetch_profile_rows(username, file_type)
    except LetterboxdProfileNotFound as exc:
        return Response({"error": str(exc)}, status=404)
    except LetterboxdFetchError as exc:
        return Response({"error": str(exc)}, status=502)
    except Exception:
        logger.exception("Letterboxd URL import failed for %s (%s)", url, file_type)
        return Response(
            {"error": "Letterboxd request failed. Try again later."}, status=502
        )

    client = TMDbClient(settings.TMDB_API_KEY, timeout=3)
    result = import_rows(rows, client=client, user_id=user_id)

    return Response(
        {
            "source": "letterboxd_url",
            "username": username,
            "import_type": file_type,
            "pages_fetched": pages_fetched,
            "rows_found": len(rows),
            "resolved": WatchlistItemSerializer(result.resolved, many=True).data,
            "ambiguous": result.ambiguous,
            "skipped": result.skipped,
            "watched_count": result.watched_count,
            "ratings_count": result.ratings_count,
            "reviews_count": result.reviews_count,
            "diary_events_count": result.diary_events_count,
        }
    )


def _unique_movie_for_tmdb(tmdb_id):
    """Numeric-only legacy reads fail closed when media namespaces overlap."""
    candidates = list(Movie.objects.filter(tmdb_id=tmdb_id)[:2])
    return candidates[0] if len(candidates) == 1 else None
