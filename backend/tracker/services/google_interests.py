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
import time
from dataclasses import dataclass, field
from html import unescape
from typing import Iterable, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlparse

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
# URL classification + errors
# ---------------------------------------------------------------------------

INTERESTS_PATH = "/interests/saved/list"
COLLECTIONS_PATH = "/collections/s/list"

# Google paginates long saved lists ("1-200 of 256") through a ``pageNumber``
# query parameter.  Follow at most this many pages per list URL.
MAX_LIST_PAGES = 10

_PAGE_COUNTER_RE = re.compile(r"\b(\d+)-(\d+) of (\d+)\b")


class GoogleAuthRequiredError(ValueError):
    """Google served a sign-in / consent wall instead of the list page."""


def url_kind(url: str) -> str:
    """Classify a Google list URL as ``"interests"``, ``"collections"`` or ``"unknown"``.

    ``urlparse().path`` never carries the host, so only the path fragment is
    compared.  (An earlier version matched the *full* base URL — e.g.
    ``https://www.google.com/interests/saved/list`` — against ``path``, which
    never matched, so every URL fell through to the generic branch: Interests
    pages were fetched without cookies and parsed with the wrong parser.)
    """
    path = urlparse(url or "").path or ""
    if INTERESTS_PATH in path:
        return "interests"
    if COLLECTIONS_PATH in path:
        return "collections"
    return "unknown"


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r'\b((?:19|20)\d{2})\b')
_TRAILING_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\)?\s*$")

_TAG_RE = re.compile(r'<[^>]+>')

# Card markup shared by Interests and Collections lists (verified against live
# Google HTML): each titled card renders one anchor per item.
_ANCHOR_RE = re.compile(r"<a\b([^>]*?)>", re.DOTALL | re.IGNORECASE)
_HREF_ATTR_RE = re.compile(r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_LABEL_ATTR_RE = re.compile(r"""\baria-label\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_HIDDEN_ATTR_RE = re.compile(r"""\baria-hidden\s*=\s*(?:"true"|'true')""", re.IGNORECASE)


def _valid_year(value: Optional[int]) -> Optional[int]:
    """Reject implausible years (e.g. ``2049`` scraped out of a title)."""
    if value is None:
        return None
    if 1880 <= value <= time.gmtime().tm_year + 1:
        return value
    return None


def _attr(pattern: re.Pattern, attrs: str) -> Optional[str]:
    m = pattern.search(attrs)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).strip()


def _extract_year(text: str) -> Optional[int]:
    m = _YEAR_RE.search(text or "")
    if m:
        try:
            return _valid_year(int(m.group(1)))
        except ValueError:
            return None
    return None


def _extract_trailing_year(text: str) -> Optional[int]:
    """Take a year only when it terminates the title (``Some Movie 1996``).

    Trailing-only keeps titles such as ``2001: A Space Odyssey`` or
    ``Blade Runner 2049`` from being read as a release year and pushed into
    TMDb with a filter that can never match.
    """
    m = _TRAILING_YEAR_RE.search(text or "")
    if m:
        try:
            return _valid_year(int(m.group(1)))
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


def _is_placeholder(label: str) -> bool:
    """True for untitled cards that Google renders with a URL as their label."""
    lowered = (label or "").strip().lower()
    if not lowered or lowered in ("q", "search"):
        return True
    return lowered.startswith("http") and "/search" in lowered


def _parse_card_rows(html: str, page_url: str, source: str) -> list[GoogleInterestRow]:
    """Extract titled cards from an Interests / Collections list page.

    Each card renders as ``<a href="…/search?q=…" class="ir4l5" aria-label="Title">``.
    An anchor is skipped when it is an image link (``aria-hidden``), carries no
    ``aria-label``, or when Google failed to resolve the entry and used the raw
    search URL as its label (such cards have no title anywhere).
    """
    rows: list[GoogleInterestRow] = []
    seen: set[tuple[str, Optional[int]]] = set()

    for m in _ANCHOR_RE.finditer(html):
        attrs = m.group(1)
        if _HIDDEN_ATTR_RE.search(attrs):
            continue
        href_raw = _attr(_HREF_ATTR_RE, attrs)
        if not href_raw or "/search?" not in href_raw:
            continue
        label_raw = _attr(_LABEL_ATTR_RE, attrs)
        if label_raw is None:
            continue

        href = unescape(href_raw)
        label = unescape(label_raw).strip()
        if _is_placeholder(label):
            continue

        q_value = ""
        try:
            qs = parse_qs(urlparse(href).query, keep_blank_values=True)
            q_value = unquote(qs.get("q", [""])[0])
        except Exception:
            q_value = ""

        title = label or q_value.splitlines()[0].strip()
        if not title or _is_placeholder(title):
            continue

        _, year_from_q = _parse_interests_query(q_value)
        year = year_from_q if year_from_q is not None else _extract_trailing_year(title)

        key = (title.lower(), year)
        if key in seen:
            continue
        seen.add(key)

        rows.append(
            GoogleInterestRow(
                title=title,
                year=year,
                source_url=page_url,
                source=source,
            )
        )

    return rows


def parse_interests_page(html: str, page_url: str) -> list[GoogleInterestRow]:
    """Extract title/year pairs from a Google Interests saved-list HTML page."""
    rows = _parse_card_rows(html, page_url, source="google_interests")
    if rows:
        return rows

    # Fallback: if the card markup did not match (class names change), try a
    # broader scan for <h3> title anchors inside the body.
    body = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
    if body:
        for m in re.finditer(
            r'<h3[^>]*>.*?<a[^>]+href="[^"]+"[^>]*>(.*?)</a>',
            body.group(1),
            re.DOTALL,
        ):
            title = _strip_tags(m.group(1))
            if title:
                rows.append(
                    GoogleInterestRow(
                        title=title,
                        year=_extract_trailing_year(title),
                        source_url=page_url,
                        source="google_interests",
                    )
                )
    return rows


# ---------------------------------------------------------------------------
# Collections parser (public Google TV / "Save" pages, no auth)
# ---------------------------------------------------------------------------

# Collections, Google TV watchlists and Interests saved lists all render the
# same card markup (see ``_parse_card_rows``): one anchor per titled item, with
# the readable title in ``aria-label`` and the raw title (sometimes with the
# year on its own line) URL-encoded in the ``q`` query parameter.
def parse_collections_page(html: str, page_url: str) -> list[GoogleInterestRow]:
    """Extract title/year pairs from a Google Collections / Google TV list page."""
    return _parse_card_rows(html, page_url, source="google_collections")


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

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


def _looks_like_auth_wall(resp: requests.Response, requested_url: str | None = None) -> bool:
    """True when Google answered with a sign-in / help wall, not the list.

    Expired or wrong-domain cookies do not always land on a sign-in page:
    Google bounces the request through ``accounts.google.com/ServiceLogin`` and
    ends on a support article (``support.google.com/accounts/answer/32050``),
    which would otherwise be parsed as a (nearly empty) list page.
    """
    final = urlparse(resp.url or "")
    host = final.netloc.lower()
    if host.startswith(("accounts.google.", "consent.google.", "support.google.")):
        return True
    if "signin" in final.path or "/answer/" in final.path:
        return True
    if requested_url:
        wanted = urlparse(requested_url).netloc.lower()
        if (
            host
            and wanted
            and host != wanted
            and not (host.startswith("www.google.") and wanted.startswith("www.google."))
        ):
            return True
    head = resp.text[:20000]
    return '<base href="https://accounts.google.com/v3/signin/' in head


def fetch_interests_page(
    url: str,
    cookies: dict[str, str],
    timeout: int = 20,
) -> str:
    """Fetch a Google Interests list page using the supplied Google cookies."""
    if not cookies:
        raise GoogleAuthRequiredError(
            f"URL {url} is a Google Interests page and needs Google cookies, "
            "but none were supplied."
        )
    session = _get_session()
    # Per-request cookies: the shared session must never keep one import's
    # credentials around for the next one.
    resp = session.get(url, timeout=timeout, cookies=cookies)
    resp.raise_for_status()
    if _looks_like_auth_wall(resp, requested_url=url):
        raise GoogleAuthRequiredError(
            "Google answered with a sign-in or help page — the cookies are "
            "missing, expired, or belong to another Google domain. Copy them "
            "from DevTools → Application → Cookies → www.google.com and paste "
            "them into the cookies field."
        )
    return resp.text


def fetch_collections_page(url: str, timeout: int = 20) -> str:
    """Fetch a public Google Collections list page (no auth required)."""
    session = _get_session()
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    if _looks_like_auth_wall(resp, requested_url=url):
        raise GoogleAuthRequiredError(
            "Google answered with a sign-in page instead of the list — this "
            "URL may be private. Supply Google cookies for it."
        )
    return resp.text


def fetch_page(url: str, cookies: dict[str, str] | None = None, timeout: int = 20) -> str:
    """Fetch a Google list page, choosing the right fetching strategy.

    Public Collections URLs (``/collections/s/list/...``) need no cookies.
    Interests URLs (``/interests/saved/list/...``) require a cookie dict.
    """
    kind = url_kind(url)
    if kind == "collections":
        return fetch_collections_page(url, timeout=timeout)
    if kind == "interests":
        if not cookies:
            raise GoogleAuthRequiredError(
                f"URL {url} appears to be a Google Interests page and requires cookies, "
                "but none were supplied."
            )
        return fetch_interests_page(url, cookies=cookies, timeout=timeout)
    # Unknown Google-ish URL — try with cookies if provided, else plain.
    if cookies:
        return fetch_interests_page(url, cookies=cookies, timeout=timeout)
    return fetch_collections_page(url, timeout=timeout)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def _page_counter(html: str) -> Optional[tuple[int, int, int]]:
    """Read Google's ``1-200 of 256`` range counter, if the page has one."""
    m = _PAGE_COUNTER_RE.search(html or "")
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except ValueError:
        return None


def _with_page_number(url: str, page_number: int) -> str:
    """Return ``url`` with its ``pageNumber`` query parameter replaced."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["pageNumber"] = [str(page_number)]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def fetch_list_pages(
    url: str,
    cookies: dict[str, str] | None = None,
    timeout: int = 20,
    max_pages: int = MAX_LIST_PAGES,
) -> list[str]:
    """Fetch one list URL, following Google's ``pageNumber`` pagination.

    Long saved lists render only the first chunk ("1-200 of 256"); the rest
    lives behind ``&pageNumber=2``.  Stops when the counter says the list is
    complete, when Google repeats a page (it clamps out-of-range numbers), or
    after ``max_pages`` fetches.
    """
    pages: list[str] = []
    seen_ranges: set[tuple[int, int]] = set()

    html = fetch_page(url, cookies=cookies, timeout=timeout)
    pages.append(html)

    while len(pages) < max_pages:
        counter = _page_counter(pages[-1])
        if not counter:
            break
        _start, end, total = counter
        if end >= total:
            break
        if (_start, end) in seen_ranges:
            break
        seen_ranges.add((_start, end))

        next_html = fetch_page(
            _with_page_number(url, len(pages) + 1),
            cookies=cookies,
            timeout=timeout,
        )
        if next_html == pages[-1]:
            break
        pages.append(next_html)

    return pages


# ---------------------------------------------------------------------------
# Import orchestration (mirrors ``letterboxd.import_rows`` so the watchlist
# view can call both importers with the same result contract).
# ---------------------------------------------------------------------------

def _search_all(client, title: str, year: Optional[int], attempts: int = 2) -> list:
    """Search TMDb for movies *and* series, retrying once on a transient error.

    ``content_type="all"`` matters here: Google TV watchlists mix films and
    series, and the client's default (``movie`` only) silently dropped every
    series title.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return client.search(title, year=year, content_type="all")
        except Exception as exc:  # noqa: BLE001 - TMDb raises requests errors
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(1.0)
    raise last_exc  # type: ignore[misc]


def import_interests(
    rows: Iterable[GoogleInterestRow],
    client=None,
    user_id: int = 1,
) -> GoogleInterestsImportResult:
    """Resolve a list of GoogleInterestRow entries into WatchlistItem rows.

    Uses the same TMDb search + ambiguity logic as the Letterboxd importer.
    """
    from tracker.models import Movie, RefreshPriority, WatchlistItem
    from availability.freshness import source_configuration_snapshot
    from availability.sources import DEFAULT_SOURCE_SLUG
    from tracker.services.refresh_requests import request_import_refresh
    from tracker.services.tmdb import TMDbClient

    client = client or TMDbClient(settings.TMDB_API_KEY, timeout=3)
    result = GoogleInterestsImportResult()
    import_freshness_configuration = source_configuration_snapshot().get(
        DEFAULT_SOURCE_SLUG, {}
    )

    # Fast in-memory cache of local movies to prevent repeated TMDb calls.
    local_movies_by_title_year: dict[tuple[str, Optional[int]], Movie] = {}
    for m in Movie.objects.all():
        if m.title:
            key = (m.title.lower().strip(), m.year)
            local_movies_by_title_year[key] = m
            if m.year is not None:
                local_movies_by_title_year[(m.title.lower().strip(), None)] = m

    seen_rows: set[tuple[str, Optional[int]]] = set()
    for row in rows:
        if not (row.title or "").strip():
            continue
        title_key = (row.title.lower().strip(), row.year)
        if title_key in seen_rows:
            continue
        seen_rows.add(title_key)

        movie: Optional[Movie] = local_movies_by_title_year.get(title_key)
        movie_created = False

        if movie is None and row.year is None:
            movie = local_movies_by_title_year.get((row.title.lower().strip(), None))

        if movie is None:
            try:
                matches = _search_all(client, row.title, row.year)
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
            content_type=pick.content_type or "movie",
                defaults={
                    "title": pick.title,
                    "year": pick.year,
                    "poster_path": pick.poster_path or "",
                },
            )
            movie_created = created
            if not created and pick.poster_path and not movie.poster_path:
                movie.poster_path = pick.poster_path
                movie.save(update_fields=["poster_path"])

            local_movies_by_title_year[(movie.title.lower().strip(), movie.year)] = movie

        item_source = getattr(row, "source", None) or "google_interests"
        watchlist_item, item_created = WatchlistItem.objects.get_or_create(
            movie=movie,
            source=item_source,
            defaults={"user_id": user_id},
        )
        result.resolved.append(watchlist_item)
        result.watchlist_count += 1

        # Event-driven refresh (Phase B): new content / new watchlist items
        # (or titles with no canonical availability yet) become eligible for
        # an asynchronous availability refresh — never a synchronous fetch.
        request_import_refresh(
            movie,
            priority=RefreshPriority.WATCHLIST,
            entering=movie_created or item_created,
            source_configuration=import_freshness_configuration,
        )

    return result


# ---------------------------------------------------------------------------
# Public helpers used by the view
# ---------------------------------------------------------------------------

def parse_page(html: str, page_url: str) -> list[GoogleInterestRow]:
    """Parse one page of Google list HTML, dispatching on the URL kind."""
    if url_kind(page_url) == "collections":
        return parse_collections_page(html, page_url)
    return parse_interests_page(html, page_url)


def parse_and_import(
    html: str,
    page_url: str,
    client=None,
    user_id: int = 1,
) -> GoogleInterestsImportResult:
    """Parse one page of Google list HTML and import every title found.

    Dispatches to the right parser based on the URL path.
    """
    rows = parse_page(html, page_url)
    if not rows:
        logger.warning("No interests parsed from %s", page_url)
    return import_interests(rows, client=client, user_id=user_id)
