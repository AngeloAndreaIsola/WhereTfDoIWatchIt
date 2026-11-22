"""WhereTfDoIWatchIt round-trip CSV export/import (application backup format).

Canonical format — one CSV carries both states:

    title,year,letterboxd_uri,status

``status`` is exactly ``watchlist`` or ``watched``. This format is designed
for Export → Download → Import → Restore within this application; it is not
necessarily compatible with Letterboxd's own CSV import.

Both import paths funnel into :func:`tracker.services.letterboxd.import_rows`
(the Letterboxd CSV parser and this canonical parser both produce
:class:`LetterboxdRow` objects), so TMDb matching, ambiguity handling, and
idempotent writes are shared rather than duplicated.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from django.http import HttpResponse

from tracker.ownership import resolve_personal_owner_id
from ..models import UserMovieData, WatchlistItem
from .letterboxd import LetterboxdRow

CANONICAL_FIELDS = ("title", "year", "letterboxd_uri", "status")
STATUS_WATCHLIST = "watchlist"
STATUS_WATCHED = "watched"
VALID_STATUSES = (STATUS_WATCHLIST, STATUS_WATCHED)

FILENAME_WATCHLIST = "wheretfdoiwatchit-watchlist.csv"
FILENAME_WATCHED = "wheretfdoiwatchit-watched.csv"
FILENAME_BOTH = "wheretfdoiwatchit-export.csv"


class CanonicalImportError(ValueError):
    """Uploaded CSV has the canonical header but invalid content."""


def watchlist_export_rows(user_id: int | None = None) -> list[dict]:
    """Rows for everything currently on the user's watchlist."""

    user_id = resolve_personal_owner_id() if user_id is None else user_id
    items = (
        WatchlistItem.objects.select_related("movie")
        .filter(user_id=user_id)
        .order_by("movie__title", "movie__year", "movie_id")
    )
    uris = _letterboxd_uris(user_id)
    rows = []
    for item in items:
        movie = item.movie
        rows.append(
            {
                "title": movie.title,
                "year": movie.year if movie.year is not None else "",
                "letterboxd_uri": uris.get(movie.id, ""),
                "status": STATUS_WATCHLIST,
            }
        )
    return rows


def watched_export_rows(user_id: int | None = None) -> list[dict]:
    """Rows for movies explicitly marked watched (``is_watched=True``)."""

    user_id = resolve_personal_owner_id() if user_id is None else user_id
    watched = (
        UserMovieData.objects.select_related("movie")
        .filter(user_id=user_id, is_watched=True)
        .order_by("movie__title", "movie__year", "movie_id")
    )
    rows = []
    for data in watched:
        movie = data.movie
        rows.append(
            {
                "title": movie.title,
                "year": movie.year if movie.year is not None else "",
                "letterboxd_uri": data.letterboxd_uri or "",
                "status": STATUS_WATCHED,
            }
        )
    return rows


def combined_export_rows(user_id: int | None = None) -> list[dict]:
    """Watchlist and watched rows in one file (a movie in both appears twice)."""

    user_id = resolve_personal_owner_id() if user_id is None else user_id
    return watchlist_export_rows(user_id) + watched_export_rows(user_id)


def render_csv(rows: list[dict]) -> str:
    """Serialize rows with the canonical header using Python's csv module."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CANONICAL_FIELDS)
    for row in rows:
        writer.writerow([row["title"], row["year"], row["letterboxd_uri"], row["status"]])
    return buffer.getvalue()


def csv_response(filename: str, rows: list[dict]) -> HttpResponse:
    """Downloadable CSV response (UTF-8, attachment disposition)."""
    response = HttpResponse(render_csv(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def is_canonical_csv(file_bytes: bytes) -> bool:
    """Detect the canonical header regardless of filename or file magic."""
    if file_bytes.startswith(b"PK\x03\x04") or file_bytes.startswith(b"\x1f\x8b"):
        return False
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    header = next(csv.reader(io.StringIO(text)), None)
    if not header:
        return False
    fields = {name.strip().lower() for name in header}
    return set(CANONICAL_FIELDS) <= fields


def parse_canonical_csv(file_bytes: bytes) -> list[LetterboxdRow]:
    """Parse a canonical export into LetterboxdRow objects for import_rows().

    ``status`` drives ``file_type``: ``watchlist`` rows become WatchlistItems,
    ``watched`` rows set ``UserMovieData.is_watched`` (and never create a
    WatchlistItem) — the same semantics as a Letterboxd watched CSV.
    Raises :class:`CanonicalImportError` on an invalid status.
    """
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CanonicalImportError("CSV has no header row.")
    field_map = {name.strip().lower(): name for name in reader.fieldnames}
    missing = [name for name in CANONICAL_FIELDS if name not in field_map]
    if missing:
        raise CanonicalImportError(
            f"CSV is missing required column(s): {', '.join(missing)}."
        )

    rows: list[LetterboxdRow] = []
    for raw in reader:
        line_no = reader.line_num
        status_raw = (raw.get(field_map["status"]) or "").strip().lower()
        if status_raw not in VALID_STATUSES:
            raise CanonicalImportError(
                f"Line {line_no}: invalid status "
                f"{(raw.get(field_map['status']) or '').strip()!r} — "
                f"must be 'watchlist' or 'watched'."
            )
        title = (raw.get(field_map["title"]) or "").strip()
        if not title:
            continue
        year: Optional[int] = None
        year_raw = (raw.get(field_map["year"]) or "").strip()
        if year_raw:
            try:
                year = int(year_raw)
            except ValueError:
                year = None
        uri = (raw.get(field_map["letterboxd_uri"]) or "").strip() or None
        rows.append(LetterboxdRow(title=title, year=year, uri=uri, file_type=status_raw))
    return rows


def _letterboxd_uris(user_id: int) -> dict[int, str]:
    return {
        data.movie_id: data.letterboxd_uri
        for data in UserMovieData.objects.filter(user_id=user_id).exclude(
            letterboxd_uri=""
        )
    }
