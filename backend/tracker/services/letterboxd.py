"""Letterboxd CSV and ZIP import.

Supports:
- Watchlist CSV (watchlist.csv)
- Watched CSV (watched.csv)
- Diary CSV (diary.csv)
- Ratings CSV (ratings.csv)
- Reviews CSV (reviews.csv)
- ZIP archives containing standard Letterboxd CSVs

Matching goes through TMDb search; ambiguous results (multiple matches,
or no title/year match) flagged for manual confirmation.
"""
from __future__ import annotations

import csv
import datetime
import io
import logging
import zipfile
from dataclasses import dataclass, field
from typing import Iterable, Optional

from django.conf import settings

from ..models import Movie, MovieWatchEvent, UserMovieData, WatchlistItem
from .tmdb import TMDbClient, TMDbMovie

logger = logging.getLogger(__name__)


@dataclass
class LetterboxdRow:
    title: str
    year: Optional[int]
    uri: Optional[str] = None
    file_type: str = "watchlist"  # "watchlist" | "watched" | "diary" | "ratings" | "reviews"
    rating: Optional[float] = None
    review: Optional[str] = None
    watched_date: Optional[datetime.date] = None
    rewatch: bool = False


@dataclass
class ImportResult:
    resolved: list[WatchlistItem] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    watched_count: int = 0
    ratings_count: int = 0
    reviews_count: int = 0
    diary_events_count: int = 0


def parse_file_or_zip(file_bytes: bytes, filename: str = "") -> list[LetterboxdRow]:
    """Parse an uploaded file (CSV or ZIP) into LetterboxdRow objects."""
    if file_bytes.startswith(b"PK\x03\x04"):
        rows: list[LetterboxdRow] = []
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                for entry_name in sorted(z.namelist()):
                    if entry_name.lower().endswith(".csv") and not entry_name.startswith("__MACOSX"):
                        content = z.read(entry_name)
                        rows.extend(parse_csv(content, filename=entry_name))
            return rows
        except Exception as exc:
            logger.exception("Failed to parse ZIP file: %s", exc)
            return []
    return parse_csv(file_bytes, filename=filename)


def parse_csv(file_bytes: bytes, filename: str = "") -> list[LetterboxdRow]:
    """Parse a Letterboxd CSV into normalized rows.

    Header lookup is case-insensitive; common Letterboxd columns: title/year/uri.
    """
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[LetterboxdRow] = []
    if not reader.fieldnames:
        return rows

    field_map = {name.lower().strip(): name for name in reader.fieldnames}

    title_key = field_map.get("title") or field_map.get("name")
    year_key = field_map.get("year") or field_map.get("release year")
    uri_key = field_map.get("uri") or field_map.get("letterboxd uri")
    rating_key = field_map.get("rating")
    review_key = field_map.get("review")
    date_key = field_map.get("watched date") or field_map.get("date")
    rewatch_key = field_map.get("rewatch")

    fn_lower = filename.lower()
    if "review" in fn_lower or review_key in field_map:
        default_file_type = "reviews"
    elif "diary" in fn_lower or "watched date" in field_map:
        default_file_type = "diary"
    elif "rating" in fn_lower or (rating_key in field_map and review_key not in field_map):
        default_file_type = "ratings"
    elif "watched" in fn_lower:
        default_file_type = "watched"
    else:
        default_file_type = "watchlist"

    if title_key is None:
        return rows

    for raw in reader:
        title = (raw.get(title_key) or "").strip()
        if not title:
            continue

        year_raw = (raw.get(year_key) or "").strip() if year_key else ""
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None

        uri = (raw.get(uri_key) or "").strip() if uri_key else None

        rating: Optional[float] = None
        if rating_key and raw.get(rating_key):
            try:
                rating = float(raw[rating_key].strip())
            except ValueError:
                rating = None

        review: Optional[str] = None
        if review_key and raw.get(review_key):
            rev_str = raw[review_key].strip()
            if rev_str:
                review = rev_str

        watched_date: Optional[datetime.date] = None
        if date_key and raw.get(date_key):
            d_str = raw[date_key].strip()
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
                try:
                    watched_date = datetime.datetime.strptime(d_str, fmt).date()
                    break
                except ValueError:
                    pass

        rewatch = False
        if rewatch_key and raw.get(rewatch_key):
            rewatch_str = raw[rewatch_key].strip().lower()
            rewatch = rewatch_str in ("yes", "true", "1")

        rows.append(
            LetterboxdRow(
                title=title,
                year=year,
                uri=uri or None,
                file_type=default_file_type,
                rating=rating,
                review=review,
                watched_date=watched_date,
                rewatch=rewatch,
            )
        )
    return rows


def import_rows(
    rows: Iterable[LetterboxdRow],
    client: Optional[TMDbClient] = None,
    user_id: int = 1,
    source: str = "letterboxd",
) -> ImportResult:
    client = client or TMDbClient(settings.TMDB_API_KEY, timeout=3)
    result = ImportResult()

    # Fast in-memory cache of local movies to prevent repeated TMDb API calls
    local_movies_by_title_year: dict[tuple[str, Optional[int]], Movie] = {}
    local_movies_by_uri: dict[str, Movie] = {}

    for m in Movie.objects.all():
        if m.title:
            local_movies_by_title_year[(m.title.lower().strip(), m.year)] = m
            if m.year is not None:
                local_movies_by_title_year[(m.title.lower().strip(), None)] = m

    for umd in UserMovieData.objects.select_related("movie").filter(user_id=user_id):
        if umd.letterboxd_uri:
            local_movies_by_uri[umd.letterboxd_uri] = umd.movie

    for row in rows:
        title_key = (row.title.lower().strip(), row.year)
        movie: Optional[Movie] = None

        if row.uri and row.uri in local_movies_by_uri:
            movie = local_movies_by_uri[row.uri]
        elif title_key in local_movies_by_title_year:
            movie = local_movies_by_title_year[title_key]
        elif (row.title.lower().strip(), None) in local_movies_by_title_year and row.year is None:
            movie = local_movies_by_title_year[(row.title.lower().strip(), None)]

        if movie is None:
            try:
                matches = client.search(row.title, year=row.year)
            except Exception as exc:
                logger.warning("TMDb search failed for %s: %s", row.title, exc)
                result.skipped.append(
                    {"title": row.title, "year": row.year, "uri": row.uri, "reason": "tmdb_search_failed"}
                )
                continue
            if not matches:
                result.skipped.append(
                    {"title": row.title, "year": row.year, "uri": row.uri, "reason": "no_match"}
                )
                continue

            pick = _select_match(matches, row.year)
            if pick is None:
                result.ambiguous.append(
                    {
                        "title": row.title,
                        "year": row.year,
                        "uri": row.uri,
                        "candidates": [_candidate_dict(m) for m in matches[:5]],
                        "reason": "year_mismatch",
                    }
                )
                continue

            if len(matches) > 1 and _is_ambiguous(matches, row.year):
                result.ambiguous.append(
                    {
                        "title": row.title,
                        "year": row.year,
                        "uri": row.uri,
                        "candidates": [_candidate_dict(m) for m in matches[:5]],
                        "reason": "multiple_matches",
                    }
                )
                continue

            movie, created = Movie.objects.get_or_create(
                tmdb_id=pick.tmdb_id,
                defaults={"title": pick.title, "year": pick.year, "poster_path": pick.poster_path or ""},
            )
            if not created and pick.poster_path and not movie.poster_path:
                movie.poster_path = pick.poster_path
                movie.save()

            local_movies_by_title_year[(movie.title.lower().strip(), movie.year)] = movie
            if row.uri:
                local_movies_by_uri[row.uri] = movie

        if row.file_type == "watchlist":
            item, _ = WatchlistItem.objects.get_or_create(
                movie=movie, source=source, defaults={"user_id": user_id}
            )
            result.resolved.append(item)

        # Update UserMovieData
        user_data, _ = UserMovieData.objects.get_or_create(user_id=user_id, movie=movie)

        if row.file_type in ("watched", "ratings", "reviews", "diary"):
            user_data.is_watched = True
            result.watched_count += 1

        if row.rating is not None:
            user_data.rating = row.rating
            result.ratings_count += 1

        if row.review:
            user_data.review = row.review
            result.reviews_count += 1

        if row.uri:
            user_data.letterboxd_uri = row.uri

        user_data.save()

        # Update MovieWatchEvent for diary entries
        if row.file_type == "diary" or row.watched_date:
            event, created_event = MovieWatchEvent.objects.get_or_create(
                user_id=user_id,
                movie=movie,
                watched_date=row.watched_date,
                letterboxd_uri=row.uri or "",
                defaults={
                    "rating": row.rating,
                    "review": row.review or "",
                    "rewatch": row.rewatch,
                },
            )
            if not created_event:
                if row.rating is not None:
                    event.rating = row.rating
                if row.review:
                    event.review = row.review
                event.rewatch = row.rewatch
                event.save()
            result.diary_events_count += 1

    return result


def _select_match(matches: list[TMDbMovie], year: Optional[int]) -> Optional[TMDbMovie]:
    if year is None:
        return matches[0]
    same_year = [m for m in matches if m.year == year]
    if len(same_year) == 1:
        return same_year[0]
    if len(same_year) > 1:
        return None  # truly ambiguous
    return None


def _is_ambiguous(matches: list[TMDbMovie], year: Optional[int]) -> bool:
    if year is not None:
        same_year = [m for m in matches if m.year == year]
        return len(same_year) != 1
    return len(matches) > 1


def _candidate_dict(movie: TMDbMovie) -> dict:
    return {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "overview": movie.overview,
        "poster_path": movie.poster_path,
    }
