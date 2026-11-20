"""Hydrate posters using the existing typed TMDb details and quota gates."""

from datetime import timedelta
from math import ceil
import re

from django.utils import timezone
import requests

from availability.models import TmdbDetailsCache
from availability.services.candidate_gateway import CandidateDiscoveryBudget, TMDbCandidateGateway
from tracker.models import Movie
from tracker.services.bulk_refresh import release_source_lock, try_source_lock
from tracker.services.rate_limit import RateLimitExceeded, RateLimiter
from tracker.services.tmdb import TMDbClient


POSTER_PATH = re.compile(r"/[a-zA-Z0-9_-]+\.(?:jpg|jpeg|png|webp)")
POSTER_CACHE = "v3-poster"
POSTER_LOCK = "tmdb-poster-enrichment"
POSTER_DAILY_LIMIT = 100


class _PosterBudget(CandidateDiscoveryBudget):
    def reserve(self, label=""):
        super().reserve(label)
        RateLimiter(POSTER_LOCK, daily_limit=POSTER_DAILY_LIMIT).acquire()


class _PosterDetailsGateway(TMDbCandidateGateway):
    max_retries = 0
    stop_on_rate_limit = True


def _lookup(movie):
    identity = {
        "tmdb_id": movie.tmdb_id,
        "content_type": movie.content_type,
        "endpoint_version": POSTER_CACHE,
    }
    cached = TmdbDetailsCache.objects.filter(**identity, expires_at__gt=timezone.now()).first()
    if cached and cached.details.get("movie_year") == movie.year:
        retry_after = 0
        if cached.details.get("backoff"):
            retry_after = max(0, ceil((cached.expires_at - timezone.now()).total_seconds()))
        return cached.details.get("poster_path") or None, retry_after

    gateway = _PosterDetailsGateway(
        client=TMDbClient(timeout=3), budget=_PosterBudget(1), allow_cache_writes=False,
    )
    poster, retry_after = None, 0
    ttl = timedelta(hours=24)
    try:
        details = gateway.get_details(
            movie.tmdb_id, movie.content_type, authorize_network=TMDbClient.is_configured(),
        )
    except RateLimitExceeded:
        retry_after, ttl = 3600, timedelta(hours=1)
    except requests.RequestException as error:
        ttl = timedelta(minutes=15)
        if error.response is not None and error.response.status_code == 429:
            header = error.response.headers.get("Retry-After", "")
            retry_after = max(60, min(86400, int(header))) if re.fullmatch(r"[0-9]{1,8}", header) else 60
            ttl = timedelta(seconds=retry_after)
    except (KeyError, TypeError, ValueError):
        ttl = timedelta(minutes=15)
    else:
        if (details and details.get("tmdb_id") == movie.tmdb_id
                and details.get("content_type") == movie.content_type
                and (movie.year is None or details.get("year") == movie.year)):
            path = details.get("poster_path")
            if isinstance(path, str) and POSTER_PATH.fullmatch(path):
                poster, ttl = path, timedelta(days=7)
    if TMDbClient.is_configured() or poster:
        TmdbDetailsCache.objects.update_or_create(
            **identity, locale="en-US",
            defaults={
                "details": {"poster_path": poster, "movie_year": movie.year, "backoff": bool(retry_after)},
                "is_negative": not poster,
                "expires_at": timezone.now() + ttl,
            },
        )
    return poster, retry_after


def hydrate_poster(tmdb_id, content_type, observed):
    """Return poster path, HTTP status and client retry delay in seconds."""
    movies = Movie.objects.visible().filter(tmdb_id=tmdb_id, content_type=content_type)
    movie = movies.first()
    if movie is None:
        return None, 404, 0
    if movie.poster_path != observed:
        return movie.poster_path or None, 200, 0
    if not try_source_lock(POSTER_LOCK):
        return None, 503, 1
    try:
        movie = movies.first()
        if movie is None:
            return None, 404, 0
        if movie.poster_path != observed:
            return movie.poster_path or None, 200, 0
        poster, retry_after = _lookup(movie)
        if retry_after:
            return None, 429, retry_after
        if not poster or poster == observed:
            return None, 200, 0
        current = movies.filter(pk=movie.pk, year=movie.year)
        if not observed:
            current.filter(poster_path="").update(poster_path=poster)
        result = current.values_list("poster_path", flat=True).first()
        if result is None:
            return None, 404, 0
        # A valid alternate can be displayed after an image error. A saved
        # manual or pre-existing path remains the authoritative stored value.
        return (poster if result == observed else result or None), 200, 0
    finally:
        release_source_lock(POSTER_LOCK)
