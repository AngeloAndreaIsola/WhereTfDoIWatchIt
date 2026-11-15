"""Recorded Prime Video storefront + detail fixtures and an in-process router.

Fixtures under ``fixtures/prime_video/`` are trimmed recordings of the public
pages captured during Phase 11 research (the research egress served the NL
catalogue, so every fixture is a capture — the source performs no network I/O
in tests):

- ``categories.html`` — the genre link farm, trimmed to the single genre whose
  index page is also recorded (``/genre/comedy``);
- ``store.html`` / ``store_page2.html`` — ``/store`` page 1 (with its real
  ``pagination.url``) and the page that URL answers with; ``movie.html``,
  ``tv.html``, ``comedy.html`` — the other index seeds;
- ``detail_<compactGTI>.html`` — one trimmed detail page per recorded title:
  a Prime-included movie, a buy-only movie, a rent-or-buy movie, a pre-order
  movie (the live storefront's *"Available to pre-order"* vocabulary), a
  channel season, a single-season show, a multi-season show, and the seasons
  Prime lists for them.

Trims applied to every capture (documented, never silent):

- only the hydration keys the source reads survive — index pages keep
  ``init.path``/``init.route``, ``head.pageMetadata`` and
  ``body.{containers,pagination}``; detail pages keep ``body.atf.state``'s
  ``pageTitleId``/``self``/``action``/``seasons`` and ``body.btf.state``'s
  ``detail``/``episodeList``/``self``/``pageContext``; unrelated rails,
  strings, sitewide markup and every other script are dropped;
- an index page keeps only the cards whose detail pages are also recorded
  (a card that appears in several recorded carousels appears in several
  fixture carousels, so de-duplication is exercised);
- volatile per-request values in ``dv-web-global-store-data``
  (egress IP, session/request ids, geo token) are blanked.

The router performs zero network I/O: test code mutates ``pages`` to build edge
cases, ``failures`` injects exceptions, ``requests`` records every requested
URL, and anything the fixtures do not cover fails loudly with
:class:`SourceFetchError` — mimicking an upstream 404 instead of silently
looking like an empty page.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from ..sources import SourceFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "prime_video"
BASE_URL = "https://www.primevideo.com"

CATEGORIES_URL = f"{BASE_URL}/categories"
STORE_URL = f"{BASE_URL}/store"
MOVIE_URL = f"{BASE_URL}/movie"
TV_URL = f"{BASE_URL}/tv"
COMEDY_URL = f"{BASE_URL}/genre/comedy"

# Titles recorded as detail pages, by the compactGTI their cards link.
MINIONS = "0Q3VAOUAIPMBYOV70KCJTSNZ6O"          # movie, buy-only, 4K/HDR
LOVE_HYPOTHESIS = "0IH8VKXSSIAXGDCWKPS2UU3HZT"  # movie, Prime-included
IN_THE_GREY = "0PY32WKABX8Z7J927C5H52QG1D"      # movie, Prime-included
THE_HANGOVER = "0FSC5A5MCN5DES4OW71YRJT6CB"     # movie, rent or buy
STERLING_POINT = "0K94N2UBORZCLPIC46VTJQ093E"   # season 1 of a one-season show
HANDMAIDS_TALE = "0RA1WS421BKB5G3N9XIWE44DET"   # season 6 of a six-season show
MAXTON_HALL = "0G88E4BE21TZA35FIJ8U6ZBKJW"      # season 2 of a two-season show
GOOD_OMENS = "0H2D58AC8YVVZ44OTTZ1LGUFHW"       # season 3, Prime-included
EUPHORIA = "0SBS828HS99AGQUG5I7D667WMU"         # season 3, channel-backed
SPIDER_MAN = "0MH84EJKE2M4I598UISANJRS1X"       # movie, pre-order only

DETAIL_GTIS = (
    MINIONS, LOVE_HYPOTHESIS, IN_THE_GREY, THE_HANGOVER, STERLING_POINT,
    HANDMAIDS_TALE, MAXTON_HALL, GOOD_OMENS, EUPHORIA, SPIDER_MAN,
)


def detail_url(gti: str) -> str:
    return f"{BASE_URL}/detail/{gti}"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_for(url: str) -> str:
    """Fixture file serving ``url``, or :class:`SourceFetchError` if unrecorded."""
    parsed = urlsplit(url)
    path, query = parsed.path, parsed.query
    if not path.startswith("/"):
        raise SourceFetchError(f"Prime Video fixture missing for {url}")
    if path == "/categories":
        return "categories.html"
    if path.startswith("/detail/"):
        gti = path.split("/detail/", 1)[1]
        if gti in DETAIL_GTIS:
            return f"detail_{gti}.html"
        raise SourceFetchError(f"Prime Video fixture missing for {url}")
    if path == "/store":
        # Page 1 is the unparameterised storefront; its ``pagination.url``
        # is the recorded ``?startIndex=`` follow-up (page 2).
        return "store_page2.html" if "startIndex=" in query else "store.html"
    if path == "/movie" and not query:
        return "movie.html"
    if path == "/tv" and not query:
        return "tv.html"
    if path == "/genre/comedy" and not query:
        return "comedy.html"
    raise SourceFetchError(f"Prime Video fixture missing for {url}")


class FakePrimeApi:
    """Routes Prime Video URLs to recorded fixtures.

    - Unknown URLs raise :class:`SourceFetchError` (the real site answers a
      page we have not recorded), so an over-reaching walk fails the test
      instead of seeing "empty".
    - ``failures`` maps a URL substring to an exception to inject mid-run.
    - ``pages`` maps a URL substring to HTML served instead of the fixture.
    - Every requested URL is recorded in ``requests`` for assertions.
    """

    def __init__(self, pages: dict | None = None):
        self.pages = dict(pages or {})
        self.failures: dict = {}
        self.requests: list[str] = []

    def __call__(self, url: str) -> str:
        self.requests.append(url)
        for fragment, exc in self.failures.items():
            if fragment in url:
                raise exc
        for fragment, html in self.pages.items():
            if fragment in url:
                return html
        return load(fixture_for(url))

    @property
    def index_requests(self) -> list[str]:
        """Only the index-page requests (no detail pages)."""
        return [url for url in self.requests if "/detail/" not in url]

    @property
    def detail_requests(self) -> list[str]:
        """Only the detail-page requests."""
        return [url for url in self.requests if "/detail/" in url]
