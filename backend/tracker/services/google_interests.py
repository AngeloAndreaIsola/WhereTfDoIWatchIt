"""
Google Interests / Google TV "Saved lists" and Google Collections import.

Supports two kinds of Google list pages:

1. Google Interests (older) — ``/interests/saved/list/<list_id>[?q=...]``
   These live behind a Google Account sign-in wall, so they require an
   already-authenticated Google session (cookies).

2. Google Collections (newer) — ``/collections/s/list/<list_id>/<item_id>``
   These are public pages.  No cookies are needed; a plain HTTP GET with a
   browser-like User-Agent is enough.

Both are parsed into :class:`GoogleInterestRow` entries and resolved against
TMDb via the same matching logic used by the Letterboxd importer, so the
frontend watchlist UI can treat them uniformly.

Usage
~~~~~
POST ``/api/importers/google-interests/`` with JSON:

.. code-block:: http

   POST /api/importers/google-interests/
   Content-Type: application/json

   {
     "cookies": {"CONSENT": "...", "NID": "...", "SOCS": "..."},
     "urls": [
       "https://www.google.com/interests/saved/list/VCKbqG1z408FuigOgZNM0qVzoM5P4g?q=Cerca%20lista%20di%20titoli",
       "https://www.google.com/collections/s/list/VCKbqG1z408FuigOgZNM0qVzoM5P4g/_GXlRSfmBQM"
     ]
   }

The ``cookies`` field is optional — omit it (or pass ``{}``) when every URL
you supply is a public Collections page.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data shapes (parallel the Letterboxd importer's *Row / ImportResult so the
# watchlist import view can treat both sources uniformly).
# ---------------------------------------------------------------------------

@dataclass
class GoogleInterestRow:
    """One entry parsed from a Google list page (Interests or Collections)."""
    title: str
    year: Optional[int] = None
    source_url: str = ""
    source: str = "google_interests"


@dataclass
class GoogleInterestsImportResult:
    resolved: list = field(default_factory=list)   # WatchlistItem rows created
    ambiguous: list = field(default_factory=list)  # TMDb candidates needing review
    skipped: list = field(default_factory=list)    # unparseable / no-match entries
    watchlist_count: int = 0


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(
    r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>.*?<a[^>]*href="[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL,
)

_YEAR_RE = re.compile(r'\b((?:19|20)\d{2})\b')

_TAG_RE = re.compile(r'<[^>]+>')


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).strip()


def _extract_year(text: str) -> Optional[int]:
    m = _YEAR_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _parse_interests_query(query_qs: str) -> tuple[str, Optional[int]]:
    """Decode a Google Interests ``q=`` parameter into (title, year).

    Older Interests URLs encode the title+year as ``q=Title%20Here%0A%0A2024``
    or similar; the year often sits on its own line after a blank line.
    """
    decoded = unquote(query_qs or "").replace("\u000a", "\n")
    lines = [ln.strip() for ln in decoded.splitlines() if ln.strip()]
    if not lines:
        return decoded, None
    title = lines[0]
    year: Optional[int] = None
    for ln in reversed(lines[1:]):
        m = _YEAR_RE.fullmatch(ln)
        if m:
            try:
                year = int(m.group(1))
            except ValueError:
                pass
            break
    return title, year


def parse_interests_page(html: str, page_url: str) -> list[GoogleInterestRow]:
    """Extract title/year pairs from a Google Interests saved-list HTML page."""
    rows: list[GoogleInterestRow] = []
    # Each interest card title usually lives inside an <h3> with a link.
    for m in _TITLE_RE.finditer(html):
        anchor_inner = m.group(1)
        title = _strip_tags(anchor_inner)
        if not title:
            continue
        year = _extract_year(anchor_inner)
        rows.append(
            GoogleInterestRow(
                title=title,
                year=year,
                source_url=page_url,
                source="google_interests",
            )
        )
    # Fallback: if the above didn't match (class names change), try a broader
    # scan for <h3> title anchors inside the body.
    if not rows:
        body = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
        if body:
            for m in re.finditer(
                r'<h3[^>]*>.*?<a[^>]+href="[^"]+"[^>]*>(.*?)</a>',
                body.group(1),
                re.DOTALL,
            ):
                title = _strip_tags(m.group(1))
                if title:
                    year = _extract_year(m.group(0))
                    rows.append(
                        GoogleInterestRow(
                            title=title,
                            year=year,
                            source_url=page_url,
                            source="google_interests",
                        )
                    )
    return rows


# ---------------------------------------------------------------------------
# Collections parser (public Google TV / "Save" pages, no auth)
# ---------------------------------------------------------------------------

# Each collection item renders as:
#   <a href="https://www.google.com/search?q=TITLE_HERE&stick=..." aria-label="TITLE" ...>
# The ``q`` query parameter holds the full title (sometimes with the year
# separated by newlines / blank lines).  The visible title is also in
# ``aria-label``.
_COLLECTIONS_LINK_RE = re.compile(
    r'<a[^>]+href="[^"]*/search\?q=([^&"]+)[^"]*"\s[^>]*aria-label="([^"]*)"',
    re.DOTALL,
)


def parse_collections_page(html: str, page_url: str) -> list[GoogleInterestRow]:
    """Extract title/year pairs from a public Google Collections list page."""
    rows: list[GoogleInterestRow] = []

    for m in _COLLECTIONS_LINK_RE.finditer(html):
        href = m.group(0)
        aria_label = m.group(2).strip()
        if not aria_label:
            continue

        # Extract the ``q`` query parameter from the href URL properly.
        href_match = re.search(r'href="([^"]+)"', m.group(0))
        q_val = ""
        year_from_q: Optional[int] = None
        if href_match:
            href_url = href_match.group(1).replace("&amp;", "&")
            try:
                parsed_href = urlparse(href_url)
                qs = parse_qs(parsed_href.query, keep_blank_values=True)
                raw_q = qs.get("q", [""])[0]
                q_val = unquote(raw_q)
                # Try to extract year from the q value (may contain newlines).
                _, year_from_q = _parse_interests_query(q_val)
            except Exception:
                pass

        # Determine the title: use the aria-label (always present and human-readable),
        # then fall back to the decoded q value only when aria-label is absent.
        title = aria_label or q_val.strip()

        # If we couldn't pull a title out of the aria-label, fall back to the
        # q param (which is always present on Google collection links).
        if not title or title.lower() in ("", "q", "search"):
            title = q_val.strip() or aria_label

        # If the year is still missing, try the aria-label text itself.
        year: Optional[int] = year_from_q
        if year is None:
            year = _extract_year(aria_label)

        # Skip blank / junk entries (e.g. "https://www.google.com/search?q&...")
        if not title or title.lower() in ("", "q", "search"):
            continue

        rows.append(
            GoogleInterestRow(
                title=title.strip(),
                year=year,
                source_url=page_url,
                source="google_collections",
            )
        )

    return rows


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

GOOGLE_INTERESTS_BASE = "https://www.google.com/interests/saved/list"
GOOGLE_COLLECTIONS_BASE = "https://www.google.com/collections/s/list"

_SESSION: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
    return _SESSION


def fetch_interests_page(
    url: str,
    cookies: dict[str, str],
    timeout: int = 20,
) -> str:
    """Fetch a Google Interests list page using the supplied Google cookies."""
    session = _get_session()
    session.cookies.update(cookies)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_collections_page(url: str, timeout: int = 20) -> str:
    """Fetch a public Google Collections list page (no auth required)."""
    session = _get_session()
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_page(url: str, cookies: dict[str, str] | None = None, timeout: int = 20) -> str:
    """Fetch a Google list page, choosing the right fetching strategy.

    Public Collections URLs (``/collections/s/list/...``) need no cookies.
    Interests URLs (``/interests/saved/list/...``) require a cookie dict.
    """
    parsed = urlparse(url)
    if GOOGLE_COLLECTIONS_BASE in parsed.path and "interests" not in parsed.path:
        # Public collections page — no auth
        return fetch_collections_page(url, timeout=timeout)
    if GOOGLE_INTERESTS_BASE in parsed.path:
        if not cookies:
            raise ValueError(
                f"URL {url} appears to be a Google Interests page and requires cookies, "
                "but none were supplied."
            )
        return fetch_interests_page(url, cookies=cookies, timeout=timeout)
    # Unknown Google-ish URL — try with cookies if provided, else plain.
    if cookies:
        return fetch_interests_page(url, cookies=cookies, timeout=timeout)
    return fetch_collections_page(url, timeout=timeout)


# ---------------------------------------------------------------------------
# Import orchestration (mirrors ``letterboxd.import_rows`` so the watchlist
# view can call both importers with the same result contract).
# ---------------------------------------------------------------------------

def import_interests(
    rows: Iterable[GoogleInterestRow],
    client=None,
    user_id: int = 1,
) -> GoogleInterestsImportResult:
    """Resolve a list of GoogleInterestRow entries into WatchlistItem rows.

    Uses the same TMDb search + ambiguity logic as the Letterboxd importer.
    """
    from tracker.models import Movie, WatchlistItem
    from tracker.services.tmdb import TMDbClient

    client = client or TMDbClient(settings.TMDB_API_KEY, timeout=3)
    result = GoogleInterestsImportResult()

    # Fast in-memory cache of local movies to prevent repeated TMDb calls.
    local_movies_by_title_year: dict[tuple[str, Optional[int]], Movie] = {}
    for m in Movie.objects.all():
        if m.title:
            key = (m.title.lower().strip(), m.year)
            local_movies_by_title_year[key] = m
            if m.year is not None:
                local_movies_by_title_year[(m.title.lower().strip(), None)] = m

    for row in rows:
        title_key = (row.title.lower().strip(), row.year)
        movie: Optional[Movie] = local_movies_by_title_year.get(title_key)

        if movie is None and row.year is None:
            movie = local_movies_by_title_year.get((row.title.lower().strip(), None))

        if movie is None:
            try:
                matches = client.search(row.title, year=row.year)
            except Exception as exc:
                logger.warning("TMDb search failed for Google Interest '%s': %s", row.title, exc)
                result.skipped.append(
                    {"title": row.title, "year": row.year, "reason": "tmdb_search_failed"}
                )
                continue

            if not matches:
                result.skipped.append({"title": row.title, "year": row.year, "reason": "no_match"})
                continue

            from .letterboxd import _select_match, _is_ambiguous, _candidate_dict

            pick = _select_match(matches, row.year)
            if pick is None:
                result.ambiguous.append(
                    {
                        "title": row.title,
                        "year": row.year,
                        "source_url": row.source_url,
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
                        "source_url": row.source_url,
                        "candidates": [_candidate_dict(m) for m in matches[:5]],
                        "reason": "multiple_matches",
                    }
                )
                continue

            movie, created = Movie.objects.get_or_create(
                tmdb_id=pick.tmdb_id,
                defaults={
                    "title": pick.title,
                    "year": pick.year,
                    "poster_path": pick.poster_path or "",
                },
            )
            if not created and pick.poster_path and not movie.poster_path:
                movie.poster_path = pick.poster_path
                movie.save(update_fields=["poster_path"])

            local_movies_by_title_year[(movie.title.lower().strip(), movie.year)] = movie

        item_source = getattr(row, "source", None) or "google_interests"
        watchlist_item, _ = WatchlistItem.objects.get_or_create(
            movie=movie,
            source=item_source,
            defaults={"user_id": user_id},
        )
        result.resolved.append(watchlist_item)
        result.watchlist_count += 1

    return result


# ---------------------------------------------------------------------------
# Public helpers used by the view
# ---------------------------------------------------------------------------

def parse_and_import(
    html: str,
    page_url: str,
    client=None,
    user_id: int = 1,
) -> GoogleInterestsImportResult:
    """Parse one page of Google list HTML and import every title found.

    Dispatches to the right parser based on the URL path.
    """
    parsed = urlparse(page_url)
    if GOOGLE_COLLECTIONS_BASE in parsed.path and "interests" not in parsed.path:
        rows = parse_collections_page(html, page_url)
    else:
        rows = parse_interests_page(html, page_url)
    if not rows:
        logger.warning("No interests parsed from %s", page_url)
    return import_interests(rows, client=client, user_id=user_id)
