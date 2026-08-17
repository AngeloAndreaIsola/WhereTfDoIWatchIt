"""DRF views for Phase 2: search, ranked availability, watchlist, preferences."""
from __future__ import annotations

import logging

from django.db import connection
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from .models import Movie, Preference, WatchlistItem
from .serializers import (
    PreferenceSerializer,
    RankedOfferSerializer,
    WatchlistItemSerializer,
)
from .services.cache import AvailabilityCache
from .services.letterboxd import import_rows, parse_csv
from .services.ranking import rank_offers
from .services.rate_limit import RateLimitExceeded
from .services.tmdb import TMDbClient

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


@api_view(["GET", "PUT"])
def preferences(request):
    """Read or update user preferences (single-user, user_id=1)."""
    preference, _ = Preference.objects.get_or_create(user_id=1)
    if request.method == "GET":
        return Response(PreferenceSerializer(preference).data)
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


@api_view(["GET"])
def health_full(request):
    """Alias kept for legacy; identical to health."""
    return health(request._request)