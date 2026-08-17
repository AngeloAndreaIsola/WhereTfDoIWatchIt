"""Letterboxd CSV import.

Expected columns: Title, Year, Letterboxd URI (URI is optional).
Matching goes through TMDb search; ambiguous results (multiple matches,
or no title/year match) flagged for manual confirmation.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..models import Movie, WatchlistItem
from .tmdb import TMDbClient, TMDbMovie

logger = logging.getLogger(__name__)


@dataclass
class LetterboxdRow:
    title: str
    year: Optional[int]
    uri: Optional[str] = None


@dataclass
class ImportResult:
    resolved: list[WatchlistItem] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


REQUIRED_COLUMNS = {"title"}


def parse_csv(file_bytes: bytes) -> list[LetterboxdRow]:
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
        rows.append(LetterboxdRow(title=title, year=year, uri=uri or None))
    return rows


def import_rows(
    rows: Iterable[LetterboxdRow],
    client: Optional[TMDbClient] = None,
    user_id: int = 1,
    source: str = "letterboxd",
) -> ImportResult:
    client = client or TMDbClient()
    result = ImportResult()
    for row in rows:
        matches = client.search(row.title, year=row.year)
        if not matches:
            result.skipped.append(
                {"title": row.title, "year": row.year, "uri": row.uri, "reason": "no_match"}
            )
            continue
        # Strict year match when year supplied; otherwise first match.
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
        movie, _ = Movie.objects.get_or_create(
            tmdb_id=pick.tmdb_id,
            defaults={"title": pick.title, "year": pick.year},
        )
        item, created = WatchlistItem.objects.get_or_create(
            movie=movie, source=source, defaults={"user_id": user_id}
        )
        result.resolved.append(item)
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
    return {"tmdb_id": movie.tmdb_id, "title": movie.title, "year": movie.year}