"""Letterboxd CSV/ZIP import and public profile/list URL import.

CSV/ZIP support:
- Watchlist CSV (watchlist.csv)
- Watched CSV (watched.csv)
- Diary CSV (diary.csv)
- Ratings CSV (ratings.csv)
- Reviews CSV (reviews.csv)
- ZIP archives containing standard Letterboxd CSVs

URL support (public profiles only, no Letterboxd login or cookies):
- https://letterboxd.com/USERNAME/
- https://letterboxd.com/USERNAME/watchlist/
- https://letterboxd.com/USERNAME/films/  (aka /films/watched/)

The URL path is scraped from Letterboxd's public paginated HTML and normalized
into the same :class:`LetterboxdRow` objects the CSV parser produces, so every
import funnels through :func:`import_rows` (TMDb matching, ambiguity flags,
idempotent writes). Matching goes through TMDb search; ambiguous results
(multiple matches, or no title/year match) are flagged for manual confirmation.
"""
from __future__ import annotations

import csv
import datetime
import gzip
import io
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings

from availability.freshness import source_configuration_snapshot
from availability.sources import DEFAULT_SOURCE_SLUG
from ..models import Movie, MovieWatchEvent, RefreshPriority, UserMovieData, WatchlistItem
from .refresh_requests import request_import_refresh
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
    source: str = "letterboxd"
    mal_id: Optional[int] = None
    # Declared media type of the imported row ("movie" | "series"), when the
    # source format provides one (#107). None = undeclared; the importer then
    # applies the format default (MyAnimeList anime → series, else movie).
    media_type: Optional[str] = None


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
    """Parse an uploaded file (CSV, ZIP, XML, GZ, XML.GZ) into LetterboxdRow objects."""
    # Check for GZIP compression magic bytes (\x1f\x8b)
    if file_bytes.startswith(b"\x1f\x8b"):
        try:
            decompressed = gzip.decompress(file_bytes)
            return parse_file_or_zip(decompressed, filename=filename)
        except Exception as exc:
            logger.exception("Failed to decompress GZIP file: %s", exc)
            return []

    # Check for ZIP archive magic bytes (PK\x03\x04)
    if file_bytes.startswith(b"PK\x03\x04"):
        rows: list[LetterboxdRow] = []
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                for entry_name in sorted(z.namelist()):
                    if entry_name.startswith("__MACOSX"):
                        continue
                    content = z.read(entry_name)
                    if entry_name.lower().endswith(".xml"):
                        rows.extend(parse_mal_xml(content))
                    elif entry_name.lower().endswith(".csv"):
                        rows.extend(parse_csv(content, filename=entry_name))
            return rows
        except Exception as exc:
            logger.exception("Failed to parse ZIP file: %s", exc)
            return []

    # Check for XML content or file extension
    fn_lower = filename.lower()
    if fn_lower.endswith(".xml") or b"<myanimelist>" in file_bytes.lower() or b"<?xml" in file_bytes.lower():
        return parse_mal_xml(file_bytes)

    return parse_csv(file_bytes, filename=filename)


def parse_mal_xml(file_bytes: bytes) -> list[LetterboxdRow]:
    """Parse MyAnimeList XML export into LetterboxdRow objects.

    Includes anime with my_status 'Plan to Watch' or 'Watching'.
    Excludes 'Completed', 'On-Hold', 'Dropped'.
    """
    rows: list[LetterboxdRow] = []
    try:
        root = ET.fromstring(file_bytes)
    except Exception as exc:
        logger.warning("Failed to parse MAL XML data: %s", exc)
        return rows

    for anime in root.findall("anime"):
        status_elem = anime.find("my_status")
        status = status_elem.text.strip() if status_elem is not None and status_elem.text else ""
        if status not in ("Plan to Watch", "Watching"):
            continue

        title_elem = anime.find("series_title")
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
        if not title:
            continue

        mal_id: Optional[int] = None
        mal_id_elem = anime.find("series_animedb_id")
        if mal_id_elem is not None and mal_id_elem.text:
            try:
                mal_id = int(mal_id_elem.text.strip())
            except ValueError:
                mal_id = None

        # MAL exports declare series_type (TV / Movie / OVA / ...). Anime
        # films are movies; everything else is TV-shaped. Undeclared types
        # stay None and fall back to the MAL series default at import time
        # (#107) — a typed search miss goes to review, never a wrong guess.
        media_type: Optional[str] = None
        type_elem = anime.find("series_type")
        series_type = (type_elem.text or "").strip().lower() if type_elem is not None else ""
        if series_type == "movie":
            media_type = "movie"
        elif series_type:
            media_type = "series"

        rows.append(
            LetterboxdRow(
                title=title,
                year=None,
                source="myanimelist",
                mal_id=mal_id,
                media_type=media_type,
            )
        )
    return rows


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


# ---------------------------------------------------------------------------
# Letterboxd public profile/list URL import
#
# Pipeline: URL -> normalize -> fetch paginated HTML -> parse poster tiles ->
# LetterboxdRow[] -> import_rows() (unchanged TMDb matching / ambiguity logic).
# ---------------------------------------------------------------------------

LETTERBOXD_BASE_URL = "https://letterboxd.com"

#: Hard upper bound on pages fetched for a single URL import. Protects against
#: runaway pagination if Letterboxd ever serves endless pages. 72 posters/page
#: * 200 pages is far beyond any real profile.
MAX_PROFILE_PAGES = 200

#: Per-request timeout (seconds) for Letterboxd HTML fetches.
PROFILE_FETCH_TIMEOUT = 20

_LETTERBOXD_HOSTS = frozenset({"letterboxd.com", "www.letterboxd.com"})

# First path segments Letterboxd reserves for site pages; never a username.
_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "film",
        "films",
        "list",
        "lists",
        "watchlist",
        "tv",
        "search",
        "settings",
        "sign-in",
        "signin",
        "register",
        "forgot",
        "help",
        "faq",
        "about",
        "tags",
        "people",
        "person",
        "journal",
        "pro",
        "features",
        "admin",
        "static",
        "assets",
    }
)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$")

# "Natural Born Killers (1994)" -> ("Natural Born Killers", 1994).
# Only a single trailing year counts; ranges like "(2002-2008)" are ignored so
# the year is never guessed from unreliable markup.
_YEAR_SUFFIX_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:18|19|20)\d{2})\)$")

SUPPORTED_IMPORT_TYPES = ("watchlist", "watched")


class LetterboxdImportError(Exception):
    """Base class for user-facing Letterboxd URL import failures."""

    code = "letterboxd_error"


class LetterboxdInvalidURLError(LetterboxdImportError):
    """The supplied URL is not a supported public Letterboxd profile/list URL."""

    code = "invalid_url"


class LetterboxdProfileNotFound(LetterboxdImportError):
    """Letterboxd returned 404 (or a 'page not found' body) for the URL."""

    code = "not_found"


class LetterboxdFetchError(LetterboxdImportError):
    """Network/HTTP failure while talking to Letterboxd (never leaks traces)."""

    code = "fetch_failed"


def normalize_profile_url(url: str) -> tuple[str, str]:
    """Validate + normalize a Letterboxd profile/list URL.

    Returns ``(username, list_type)`` where ``list_type`` is ``"watchlist"``,
    ``"watched"``, or ``""`` when the URL is a bare profile (the caller must
    then require an explicit ``type`` — never guess).

    Accepted examples::

        https://letterboxd.com/foo/                  -> ("foo", "")
        https://letterboxd.com/foo/watchlist/        -> ("foo", "watchlist")
        https://letterboxd.com/foo/films/watched/    -> ("foo", "watched")
        https://letterboxd.com/foo/films/            -> ("foo", "watched")

    Raises :class:`LetterboxdInvalidURLError` for anything else (foreign
    hosts, site pages such as ``/film/inception/``, unknown path prefixes).
    Pure function: no HTTP, no parsing.
    """
    raw = (url or "").strip()
    if not raw:
        raise LetterboxdInvalidURLError("Letterboxd URL is required.")
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise LetterboxdInvalidURLError(
            "Not a Letterboxd URL. Expected something like "
            "https://letterboxd.com/<username>/watchlist/."
        )

    host = (parsed.hostname or "").lower()
    if host not in _LETTERBOXD_HOSTS:
        raise LetterboxdInvalidURLError(
            "Not a Letterboxd URL (expected letterboxd.com). Expected "
            "something like https://letterboxd.com/<username>/watchlist/."
        )

    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        raise LetterboxdInvalidURLError(
            "No username in the Letterboxd URL. Expected "
            "https://letterboxd.com/<username>/."
        )

    username = segments[0]
    if username.lower() in _RESERVED_PATH_SEGMENTS:
        raise LetterboxdInvalidURLError(
            f"'{username}' is a Letterboxd site page, not a profile. Paste your "
            "profile, watchlist or films URL, e.g. "
            "https://letterboxd.com/<username>/watchlist/."
        )
    if not _USERNAME_RE.match(username):
        raise LetterboxdInvalidURLError(
            f"'{username}' does not look like a Letterboxd username."
        )

    rest = [seg.lower() for seg in segments[1:]]
    if not rest:
        return username, ""
    if rest[0] == "watchlist":
        # /watchlist/, /watchlist/page/2/, /watchlist/by/name/, ... all describe
        # the watchlist; imports always restart from page 1.
        return username, "watchlist"
    if rest[0] == "films":
        # Profile "Films" page == watched films (Letterboxd template
        # '/object/person/films-watched'); /films/watched/ is an accepted alias.
        return username, "watched"

    raise LetterboxdInvalidURLError(
        "Unsupported Letterboxd URL path. Expected a profile URL, a "
        "/watchlist/ URL, or a /films/ (watched) URL."
    )


def build_profile_page_url(username: str, file_type: str, page: int = 1) -> str:
    """Canonical (absolute) URL for page ``page`` of a profile listing."""
    if file_type == "watchlist":
        base = f"{LETTERBOXD_BASE_URL}/{username}/watchlist/"
    elif file_type == "watched":
        base = f"{LETTERBOXD_BASE_URL}/{username}/films/"
    else:
        raise ValueError(f"Unsupported Letterboxd import type: {file_type!r}")
    if page > 1:
        return f"{base}page/{page}/"
    return base


def _split_title_year(display_name: str) -> tuple[str, Optional[int]]:
    """Split a Letterboxd display name like ``"Inception (2010)"``."""
    name = (display_name or "").strip()
    if not name:
        return "", None
    match = _YEAR_SUFFIX_RE.match(name)
    if match:
        try:
            return match.group("title").strip(), int(match.group("year"))
        except ValueError:
            return name, None
    return name, None


def _entry_link(element) -> Optional[str]:
    """Absolute Letterboxd film URL for a poster element, if it has one."""
    link = element.get("data-target-link")
    if not link:
        child = element.select_one("[data-target-link]")
        if child:
            link = child.get("data-target-link")
    if not link:
        anchor = element.select_one('a[href*="/film/"]')
        if anchor:
            link = anchor.get("href")
    if not link:
        return None
    link = str(link).strip()
    if not link or "/film/" not in link:
        return None
    return urljoin(LETTERBOXD_BASE_URL + "/", link)


def _entry_title_year(element) -> tuple[str, Optional[int]]:
    """Title + year for a poster element.

    Prefers Letterboxd's own ``data-item-name`` / ``data-item-full-display-name``
    (rendered as "Title (Year)" — reliable, no extra request). Falls back to the
    poster image ``alt`` attribute, which carries the title only; in that case
    the year stays ``None`` and TMDb matching resolves it.
    """
    name = element.get("data-item-name") or element.get("data-item-full-display-name")
    if not name:
        holder = element.select_one("[data-item-name]")
        if holder:
            name = holder.get("data-item-name") or holder.get("data-item-full-display-name")
    title, year = _split_title_year(str(name)) if name else ("", None)
    if title:
        return title, year

    img = element.select_one("img")
    if img is not None:
        alt = str(img.get("alt") or "").strip()
        if alt:
            return alt, None
    return "", None


def parse_profile_page(html: str, page_url: str, file_type: str) -> list[LetterboxdRow]:
    """Parse one Letterboxd profile/list page into :class:`LetterboxdRow` objects.

    Pure function (no HTTP). Handles both the current poster markup
    (``react-component`` tiles carrying ``data-target-link`` /
    ``data-item-name``) and the classic ``.poster-container`` pattern used by
    older Letterboxd pages and third-party scrapers.

    Relative film links (``/film/inception/``) are normalized against
    ``https://letterboxd.com``. Rows carry ``source="letterboxd_url"`` so
    downstream imports are distinguishable from CSV imports.
    """
    if not html or not str(html).strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select("[data-target-link]")
    if not elements:
        elements = soup.select(".poster-container")

    rows: list[LetterboxdRow] = []
    seen: set[str] = set()
    for element in elements:
        uri = _entry_link(element)
        if not uri:
            continue
        title, year = _entry_title_year(element)
        if not title:
            continue
        key = uri or title.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            LetterboxdRow(
                title=title,
                year=year,
                uri=uri,
                file_type=file_type,
                source="letterboxd_url",
            )
        )
    return rows


def parse_watchlist_page(html: str, page_url: str) -> list[LetterboxdRow]:
    """Parse a ``/<username>/watchlist/`` page into watchlist rows."""
    return parse_profile_page(html, page_url, "watchlist")


def parse_watched_page(html: str, page_url: str) -> list[LetterboxdRow]:
    """Parse a ``/<username>/films/`` (watched) page into watched rows."""
    return parse_profile_page(html, page_url, "watched")


_SESSION: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """Browser-like navigation session.

    Letterboxd sits behind Cloudflare bot protection. Requests that look like
    a real top-level browser navigation (Client Hints + ``Sec-Fetch-*``) pass;
    a bare ``User-Agent`` alone gets an interstitial challenge (HTTP 403) on
    some paths, notably ``/<username>/films/page/<n>/``.
    """
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
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            }
        )
    return _SESSION


def _looks_like_challenge(body: str) -> bool:
    """Detect an interstitial (e.g. Cloudflare) instead of real content."""
    head = body[:4000].lower()
    return "just a moment..." in head or "challenges.cloudflare.com" in head


def fetch_profile_page(url: str, timeout: int = PROFILE_FETCH_TIMEOUT) -> str:
    """Fetch one public Letterboxd page (no auth/cookies required).

    Raises application-level errors only — raw exception traces never reach
    the API response:

    - :class:`LetterboxdProfileNotFound` on 404 / "page not found" bodies
    - :class:`LetterboxdFetchError` on timeout, connection errors, bot
      challenges (403), rate limits (429), and any other non-2xx response
    """
    session = _get_session()
    try:
        response = session.get(url, timeout=timeout)
    except requests.Timeout as exc:
        raise LetterboxdFetchError(
            "Letterboxd request timed out. Try again in a moment."
        ) from exc
    except requests.ConnectionError as exc:
        raise LetterboxdFetchError(
            "Could not connect to Letterboxd. Check your connection and try again."
        ) from exc
    except requests.RequestException as exc:
        raise LetterboxdFetchError("Letterboxd request failed. Try again later.") from exc

    body = response.text or ""
    status_code = response.status_code

    if status_code == 404 or "sorry, we can't find that page" in body.lower():
        raise LetterboxdProfileNotFound(
            "Letterboxd profile or list not found. Check the username/URL."
        )
    if status_code in (401, 403) or _looks_like_challenge(body):
        raise LetterboxdFetchError(
            "Letterboxd blocked the request (bot check or private profile). "
            "Try again later, and make sure the profile is public."
        )
    if status_code == 429:
        raise LetterboxdFetchError(
            "Letterboxd rate limited the request. Try again in a few minutes."
        )
    if not 200 <= status_code < 300:
        raise LetterboxdFetchError(f"Letterboxd request failed (HTTP {status_code}).")
    return body


def fetch_profile_rows(
    username: str,
    file_type: str,
    *,
    fetcher: Optional[Callable[[str], str]] = None,
    max_pages: int = MAX_PROFILE_PAGES,
) -> tuple[list[LetterboxdRow], int]:
    """Fetch every page of a profile listing sequentially.

    Stops at the first page with zero poster entries, and never exceeds
    ``max_pages`` (see :data:`MAX_PROFILE_PAGES`) even if every page returns
    rows. Returns ``(rows, pages_fetched)``; ``pages_fetched`` counts requests
    actually made, including the terminating empty page.
    """
    fetch = fetcher or fetch_profile_page
    rows: list[LetterboxdRow] = []
    pages_fetched = 0

    for page in range(1, max(1, max_pages) + 1):
        page_url = build_profile_page_url(username, file_type, page)
        html = fetch(page_url)
        pages_fetched += 1
        page_rows = parse_profile_page(html, page_url, file_type)
        if not page_rows:
            break
        rows.extend(page_rows)
    else:
        logger.warning(
            "Letterboxd import for %s/%s stopped at MAX_PROFILE_PAGES=%s; "
            "listing may be truncated.",
            username,
            file_type,
            max_pages,
        )

    return rows, pages_fetched


def import_rows(
    rows: Iterable[LetterboxdRow],
    client: Optional[TMDbClient] = None,
    user_id: int = 1,
    source: str = "letterboxd",
) -> ImportResult:
    client = client or TMDbClient(settings.TMDB_API_KEY, timeout=3)
    result = ImportResult()
    import_freshness_configuration = source_configuration_snapshot().get(
        DEFAULT_SOURCE_SLUG, {}
    )

    # Index each typed identity under its title and optional release year.
    # A title can have several exact local matches in the same namespace.
    local_titles: dict[tuple[str, Optional[int], str], dict[int, Movie]] = defaultdict(dict)
    local_movies_by_uri: dict[str, Movie] = {}

    def remember_title(movie: Movie):
        title = _normalize_title(movie.title)
        if title:
            content_type = movie.content_type or "movie"
            local_titles[(title, movie.year, content_type)][movie.pk] = movie
            local_titles[(title, None, content_type)][movie.pk] = movie

    for m in Movie.objects.all():
        if m.title:
            remember_title(m)

    for umd in UserMovieData.objects.select_related("movie").filter(user_id=user_id):
        if umd.letterboxd_uri:
            local_movies_by_uri[umd.letterboxd_uri] = umd.movie

    for row in rows:
        movie: Optional[Movie] = None
        # Whether this row introduces new content / a new watchlist item —
        # the gate that decides if the row may request an availability
        # refresh (see request_import_refresh below).
        movie_created = False
        item_created = False

        expected_type = _search_content_type(row)
        if row.uri and row.uri in local_movies_by_uri:
            movie = _typed_cache_hit(local_movies_by_uri[row.uri], expected_type)
        if movie is None:
            title_key = (_normalize_title(row.title), row.year, expected_type)
            candidates = local_titles.get(title_key, {})
            if len(candidates) > 1:
                result.ambiguous.append({
                    "title": row.title,
                    "year": row.year,
                    "uri": row.uri,
                    "candidates": [_candidate_dict(candidates[pk]) for pk in sorted(candidates)[:5]],
                    "reason": "multiple_exact_titles",
                    "decision": "multiple_local_exact_titles",
                })
                continue
            movie = next(iter(candidates.values()), None)

        if movie is None:
            try:
                # Provenance-driven media type (#107): MAL anime searches the
                # TV endpoint; Letterboxd rows keep movie semantics.
                matches = client.search(
                    row.title, year=row.year, content_type=expected_type
                )
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

            # Unique exact-title evidence first (#108); conflicting exact
            # evidence defers to review, never to arbitrary year-only picks.
            pick, decision = _exact_title_decision(matches, row.year, row.title)
            if pick is None and decision in _EXACT_TITLE_CONFLICT_REASONS:
                result.ambiguous.append(
                    {
                        "title": row.title,
                        "year": row.year,
                        "uri": row.uri,
                        "candidates": [_candidate_dict(m) for m in matches[:5]],
                        "reason": decision,
                        "decision": decision,
                    }
                )
                continue
            if pick is None:
                pick = _select_match(matches, row.year)
            if pick is None:
                result.ambiguous.append(
                    {
                        "title": row.title,
                        "year": row.year,
                        "uri": row.uri,
                        "candidates": [_candidate_dict(m) for m in matches[:5]],
                        "reason": "year_mismatch",
                        "decision": decision,
                    }
                )
                continue

            if (
                decision not in ("exact_title_unique", "exact_title_unique_year")
                and len(matches) > 1
                and _is_ambiguous(matches, row.year)
            ):
                result.ambiguous.append(
                    {
                        "title": row.title,
                        "year": row.year,
                        "uri": row.uri,
                        "candidates": [_candidate_dict(m) for m in matches[:5]],
                        "reason": "multiple_matches",
                        "decision": decision,
                    }
                )
                continue

            if pick.content_type and pick.content_type != expected_type:
                review_matches = [pick] + [candidate for candidate in matches if candidate is not pick]
                result.ambiguous.append({
                    "title": row.title,
                    "year": row.year,
                    "uri": row.uri,
                    "candidates": [_candidate_dict(candidate) for candidate in review_matches[:5]],
                    "reason": "candidate_type_conflict",
                    "decision": "candidate_type_conflict",
                    "expected_content_type": expected_type,
                })
                continue

            logger.info(
                "import_match_decision title=%r tmdb_id=%s decision=%s",
                row.title,
                pick.tmdb_id,
                decision
                if decision in ("exact_title_unique", "exact_title_unique_year")
                else "legacy_year_match",
            )

            movie, created = Movie.objects.get_or_create(
                tmdb_id=pick.tmdb_id,
                content_type=pick.content_type or expected_type,
                defaults={"title": pick.title, "year": pick.year, "poster_path": pick.poster_path or ""},
            )
            movie_created = created
            if not created and pick.poster_path and not movie.poster_path:
                movie.poster_path = pick.poster_path
                movie.save()

            remember_title(movie)
            if row.uri:
                local_movies_by_uri[row.uri] = movie

        if row.file_type == "watchlist":
            # Reuse an existing item for this movie regardless of its original
            # source so re-importing an exported backup (whatever source the
            # item was first created with) never duplicates the movie.
            item = WatchlistItem.objects.filter(user_id=user_id, movie=movie).first()
            if item is None:
                item_source = getattr(row, "source", None) or source
                item, item_created = WatchlistItem.objects.get_or_create(
                    movie=movie, source=item_source, defaults={"user_id": user_id}
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

        # Event-driven refresh (Phase B): only titles entering the catalogue
        # (new content row or new watchlist item) or titles with no canonical
        # availability data request a refresh. Fresh titles are skipped
        # inside the helper and already-seen stale titles are left to the
        # scheduled sweep, so a large import never floods the queue.
        if row.file_type == "watchlist":
            request_import_refresh(
                movie,
                priority=RefreshPriority.WATCHLIST,
                entering=movie_created or item_created,
                source_configuration=import_freshness_configuration,
            )
        else:
            request_import_refresh(
                movie,
                priority=RefreshPriority.BACKGROUND,
                entering=movie_created,
                source_configuration=import_freshness_configuration,
            )

    return result


def _search_content_type(row: LetterboxdRow) -> str:
    """Media-type contract for TMDb candidate search (#107).

    Explicit row declaration wins; otherwise MyAnimeList anime rows search
    the TV endpoint (anime exports are TV-shaped; declared anime films carry
    ``media_type="movie"``). Letterboxd CSV/URL and canonical-CSV rows declare
    no media type and keep movie semantics.
    """
    if row.media_type in ("movie", "series"):
        return row.media_type
    if row.source == "myanimelist":
        return "series"
    return "movie"


def _typed_cache_hit(candidate: Optional[Movie], expected_type: str) -> Optional[Movie]:
    """A local-cache hit must match the row's declared media type (#107).

    Movie and series TMDb IDs live in separate namespaces; reusing a cached
    movie for a series row (or vice versa) would link the wrong identity.
    """
    if candidate is None:
        return None
    if (candidate.content_type or "movie") != expected_type:
        return None
    return candidate


def _normalize_title(value: str) -> str:
    """Deterministic exact-title normalization (#108).

    Casefold, strip diacritics, unify apostrophe variants and collapse
    whitespace. Deliberately conservative: no article stripping, no fuzzy
    similarity — this is identity evidence, not a ranking signal.
    """
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[\u2018\u2019\u02bc]", "'", text)
    return re.sub(r"\s+", " ", text).strip()


#: Exact-title decisions that must defer to manual review instead of falling
#: back to year-only selection: exact evidence exists but is contradictory.
_EXACT_TITLE_CONFLICT_REASONS = frozenset(
    {"multiple_exact_titles", "exact_title_year_conflict", "exact_title_type_collision"}
)


def _exact_title_decision(
    matches: list[TMDbMovie], year: Optional[int], row_title: str
) -> tuple[Optional[TMDbMovie], str]:
    """Prefer a unique exact normalized-title candidate (#108).

    Returns ``(pick, reason)``. A single exact-title candidate wins over
    fuzzy/sequel/edition search results when no year or media-type evidence
    contradicts it. Multiple exact-title candidates stay ambiguous unless the
    row year isolates exactly one. Conflicting exact evidence never falls back
    to arbitrary year-only acceptance.
    """
    target = _normalize_title(row_title)
    if not target:
        return None, "no_title"
    exact = [
        candidate for candidate in matches
        if target in (_normalize_title(candidate.title),
                      _normalize_title(getattr(candidate, "original_title", "")))
    ]
    if not exact:
        return None, "no_exact_title"
    if len({m.content_type or "movie" for m in exact}) > 1:
        return None, "exact_title_type_collision"
    if len(exact) > 1:
        if year is not None:
            same_year = [m for m in exact if m.year == year]
            if len(same_year) == 1:
                return same_year[0], "exact_title_unique_year"
        return None, "multiple_exact_titles"
    cand = exact[0]
    if year is not None and cand.year is not None and cand.year != year:
        return None, "exact_title_year_conflict"
    return cand, "exact_title_unique"


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


def _candidate_dict(movie: TMDbMovie | Movie) -> dict:
    return {
        "tmdb_id": movie.tmdb_id,
        "content_type": movie.content_type,
        "title": movie.title,
        "year": movie.year,
        "overview": movie.overview,
        "poster_path": movie.poster_path,
    }
