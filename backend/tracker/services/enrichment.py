"""TMDb enrichment for Movie rows.

Centralises the diff-and-save logic that previously lived in four call sites
(_enrich_movie_from_tmdb in views.py, the inline block in movie_detail, the
inline block in link_tmdb, and the local _enrich in the backfill command).
The field list below is the union of every call site — new fields added here
in one place keep them all in sync.
"""
from __future__ import annotations

import logging

from django.conf import settings

from .tmdb import TMDbClient, TMDbItem
from ..models import Movie

logger = logging.getLogger(__name__)


def sync_movie_fields(movie: Movie, details: TMDbItem) -> bool:
    """Diff *details* against *movie* and persist any changes.

    Returns True if at least one field was updated.
    """
    updated_fields = []
    # Capture canonical evidence while preserving existing display titles.
    # Legacy titles have unknown provenance, and correcting them requires review.
    if details.title and movie.canonical_title != details.title:
        movie.canonical_title = details.title
        updated_fields.append("canonical_title")
    if details.overview and movie.overview != details.overview:
        movie.overview = details.overview
        updated_fields.append("overview")
    if details.poster_path and movie.poster_path != details.poster_path:
        movie.poster_path = details.poster_path
        updated_fields.append("poster_path")
    if details.genres and movie.genres != details.genres:
        movie.genres = details.genres
        updated_fields.append("genres")
    if details.genre_ids and movie.genre_ids != details.genre_ids:
        movie.genre_ids = details.genre_ids
        updated_fields.append("genre_ids")
    if details.cast and movie.cast != details.cast:
        movie.cast = details.cast
        updated_fields.append("cast")
    if details.director is not None and movie.director != details.director:
        movie.director = details.director
        updated_fields.append("director")
    if details.rating is not None and movie.tmdb_rating != details.rating:
        movie.tmdb_rating = details.rating
        updated_fields.append("tmdb_rating")
    if details.year and movie.year != details.year:
        movie.year = details.year
        updated_fields.append("year")
    if details.seasons_data and movie.seasons_data != details.seasons_data:
        movie.seasons_data = details.seasons_data
        updated_fields.append("seasons_data")
    if updated_fields:
        movie.save(update_fields=updated_fields)
    return bool(updated_fields)


def enrich_movie_from_tmdb(movie: Movie) -> bool:
    """Fetch TMDb details for *movie* and sync changed fields.

    No-op (returns False) if TMDb is unconfigured or the fetch fails.
    Callers that already fetched details should use sync_movie_fields instead.
    """
    if not TMDbClient.is_configured():
        return False
    client = TMDbClient(settings.TMDB_API_KEY)
    try:
        details = client.details(movie.tmdb_id, content_type=movie.content_type)
    except Exception:
        logger.exception("TMDb details fetch failed for movie %s", movie.pk)
        return False
    if not isinstance(details, TMDbItem):
        return False
    return sync_movie_fields(movie, details)


def resolve_or_create_movie(tmdb_id: int, content_type: str = "movie") -> Movie:
    """Fetch TMDb details for tmdb_id, create Movie if needed, and sync fields.

    ``content_type`` selects the TMDb details endpoint: ``movie`` (default) or
    ``series``. The fetched ``details.content_type`` lands on the row, so a
    series is stored with ``content_type="series"``. Callers that do not know
    the type keep the movie default.

    Returns the Movie instance. Raises exceptions on TMDb fetch failure.
    """
    client = TMDbClient(settings.TMDB_API_KEY)
    details = client.details(tmdb_id, content_type=content_type)
    movie, _ = Movie.objects.get_or_create(
        tmdb_id=details.tmdb_id,
        content_type=details.content_type or content_type,
        defaults={
            "title": details.title,
            "year": details.year,
            "content_type": details.content_type,
        },
    )
    sync_movie_fields(movie, details)
    return movie
