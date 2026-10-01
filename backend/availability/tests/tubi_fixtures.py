"""Recorded Tubi sitemap + detail-page fixtures and an in-process page router.

Fixtures under ``fixtures/tubi/`` are trimmed recordings of the public pages
captured during Phase 10 research (Tubi geo-blocks the research egress, so
every fixture comes from a capture rather than a live request):

- ``sitemap_index.xml`` — the index as the server returns it (namespaced XML),
  listing the two title sitemaps plus editorial children the source must skip;
- ``sitemaps/*.xml`` — one ``<urlset>`` per title sitemap, including
  ``<video:video>`` extension tags the parser must ignore;
- ``pages/*.html`` — one trimmed SSR page per title: the ``window.__data``
  literal is kept **verbatim**, including the JS-only values Tubi ships
  (``undefined``, ``new Date("...")``), and the page's own season/episode
  links are the recorded markup. Everything else (styles, rails, unrelated
  scripts) is dropped;
- ``pages/shell.html`` — the ``200`` SPA shell a stale/removed title answers
  with: ``window.__data`` present, ``"byId": {}``.

The router performs zero network I/O: test code mutates ``index``/``sitemaps``/
``pages`` to build edge cases, ``failures`` injects exceptions, ``requests``
records every requested URL, and anything the fixtures do not cover fails
loudly with :class:`SourceFetchError` — mimicking an upstream 404 — instead of
silently looking like an empty page.
"""
from __future__ import annotations

from pathlib import Path

from ..sources import SourceFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "tubi"

SITEMAP_INDEX_URL = "https://tubitv.com/sitemap.xml"
MOVIES_1 = "https://tubitv.com/sitemaps/movies-1.xml"
MOVIES_2 = "https://tubitv.com/sitemaps/movies-2.xml"
TV_SHOWS_1 = "https://tubitv.com/sitemaps/tv-shows-1.xml"
# Editorial children of the index: never requested by the source.
CATEGORY_PAGES = "https://tubitv.com/sitemaps/category-pages.xml"
STATIC_PAGES = "https://tubitv.com/static-pages.xml"

I_ROBOT = "https://tubitv.com/movies/578912/i-robot"
CLEARCUT = "https://tubitv.com/movies/654137/clearcut"   # answers with the shell
PERMISSION = "https://tubitv.com/movies/582004/permission"
ABOUT = "https://tubitv.com/about"                        # not a title URL
BLUE_WATER_HIGH = "https://tubitv.com/series/02185/blue-water-high"
ONE_EIGHT_HUNDRED = "https://tubitv.com/series/01076/1-800-missing"
ANCIENT_ALIENS = "https://tubitv.com/series/4258/ancient-aliens"

# Unique title URLs of the fixture catalogue, in the order the source walks them.
TITLE_URLS = [
    I_ROBOT,          # movies-1
    BLUE_WATER_HIGH,  # tv-shows-1 (interleaved before the rest of movies-1)
    CLEARCUT,         # movies-1, SPA shell
    ONE_EIGHT_HUNDRED,
    PERMISSION,
    ANCIENT_ALIENS,
    # ABOUT is the only fixture entry that is never a title URL.
]

PAGE_FILES = {
    I_ROBOT: "pages/i_robot.html",
    CLEARCUT: "pages/shell.html",
    PERMISSION: "pages/permission.html",
    BLUE_WATER_HIGH: "pages/blue_water_high.html",
    ONE_EIGHT_HUNDRED: "pages/one_eight_hundred_missing.html",
    ANCIENT_ALIENS: "pages/ancient_aliens.html",
}
SITEMAP_FILES = {
    MOVIES_1: "sitemaps/movies-1.xml",
    MOVIES_2: "sitemaps/movies-2.xml",
    TV_SHOWS_1: "sitemaps/tv-shows-1.xml",
}


def load_index() -> str:
    return (FIXTURES / "sitemap_index.xml").read_text(encoding="utf-8")


def load_sitemaps() -> dict:
    return {
        url: (FIXTURES / relative).read_text(encoding="utf-8")
        for url, relative in SITEMAP_FILES.items()
    }


def load_pages() -> dict:
    return {
        url: (FIXTURES / relative).read_text(encoding="utf-8")
        for url, relative in PAGE_FILES.items()
    }


def url_entry(loc: str, lastmod: str = "2026-09-30") -> str:
    """One ``<url>`` element, for tests that extend a recorded sitemap."""
    return f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>\n"


class FakeTubiApi:
    """Routes Tubi URLs to recorded fixtures.

    - Unknown URLs raise :class:`SourceFetchError` (the real site answers 404),
      so an over-probing client fails the test instead of seeing "empty".
    - ``failures`` maps a URL substring to an exception to inject mid-run.
    - Every requested URL is recorded in ``requests`` for assertions.
    """

    def __init__(self, index=None, sitemaps=None, pages=None):
        self.index = load_index() if index is None else index
        self.sitemaps = load_sitemaps() if sitemaps is None else dict(sitemaps)
        self.pages = load_pages() if pages is None else dict(pages)
        self.failures: dict = {}
        self.requests: list[str] = []

    def __call__(self, url: str) -> str:
        self.requests.append(url)
        for fragment, exc in self.failures.items():
            if fragment in url:
                raise exc
        if url == SITEMAP_INDEX_URL:
            return self.index
        if url in self.sitemaps:
            return self.sitemaps[url]
        if url in self.pages:
            return self.pages[url]
        raise SourceFetchError(f"Tubi fixture missing for {url}")

    @property
    def detail_requests(self) -> list[str]:
        """Only the detail-page requests (no sitemap documents)."""
        return [
            url for url in self.requests
            if url != SITEMAP_INDEX_URL and url not in self.sitemaps
        ]
