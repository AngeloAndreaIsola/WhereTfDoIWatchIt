"""Recorded RaiPlay sitemap + page fixtures and an in-process URL router.

Fixtures under ``fixtures/rai_play/`` are recordings of the public pages
captured during Phase 12 research:

- ``sitemap_index.xml`` — the index as the server returns it (namespaced XML),
  reduced to the real children in their real order: three editorial children,
  two monthly programme sitemaps, one ``collezioni`` and one ``video`` child
  (the last two are never requested by the source);
- ``sitemaps/*.xml`` — the two monthly ``<urlset>``s. ``09`` repeats
  ``follemente`` (the same programme listed by a second month) and carries
  ``programma-ritirato``, a stale entry whose page answers 404;
- ``pages/*.html`` — one shell per page embedding ``window.WashiContext``
  **verbatim** (programme pages) or trimmed to the keys the source reads
  (video pages, 58-93 KB down to ~5 KB — the dropped keys are editorial
  furniture: rails, players, analytics);
- ``pages/video_follemente_record.json`` — the *other* answer a ``.json``
  video URL gives today: a bare JSON document (``application/json``, no
  ``data_type``/``schema``), recorded live 2026-09-30 and served by
  ``serve_video_record`` together with the player page its ``weblink``
  points at.

Fixture catalogue, in the order the source walks it (round-robin across the
two programme sitemaps, then de-duplicated):

    follemente       movie   2024   video present  -> content + offer
    normalpeople     series  2020   video present  -> content + offer
    programma-ritirato        (page is gone)       -> nothing
    sulcammino…      series  2026   video is gone  -> content, no offer
    settembre        movie   2022   video present  -> content + offer

The router performs zero network I/O: test code mutates ``index``/``sitemaps``/
``pages`` to build edge cases (a URL mapped to ``None`` answers 404, like the
real site), ``failures`` injects exceptions, ``requests`` records every
requested URL, and anything the fixtures do not cover fails loudly with
:class:`SourceFetchError` instead of silently looking like an empty page.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..sources import SourceFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "rai_play"
STATE_MARKER = "window.WashiContext"
RAIPLAY_BASE = "https://www.raiplay.it"

SITEMAP_INDEX_URL = "https://www.raiplay.it/sitemap.xml"
# Editorial / other-granularity children of the index: never requested.
GENERICHE = "https://www.raiplay.it/sitemap.generiche.xml"
DIRETTE = "https://www.raiplay.it/sitemap.dirette.xml"
SPECIALI = "https://www.raiplay.it/sitemap.speciali.xml"
COLLEZIONI_09 = "https://www.raiplay.it/sitemap.raiplay.collezioni.09.2026.xml"
VIDEO_SITEMAP_09 = "https://www.raiplay.it/sitemap.raiplay.video.09.2026.xml"
# The only two children the source requests.
PROGRAMME_SITEMAP_08 = "https://www.raiplay.it/sitemap.raiplay.programmi.08.2026.xml"
PROGRAMME_SITEMAP_09 = "https://www.raiplay.it/sitemap.raiplay.programmi.09.2026.xml"
PROGRAMME_SITEMAPS = [PROGRAMME_SITEMAP_08, PROGRAMME_SITEMAP_09]

FOLLEMENTE = "https://www.raiplay.it/programmi/follemente"
NORMAL_PEOPLE = "https://www.raiplay.it/programmi/normalpeople"
SUL_CAMMINO = "https://www.raiplay.it/programmi/sulcamminodifrancesco-lavitadibiagioconte"
RETIRED = "https://www.raiplay.it/programmi/programma-ritirato"
SETTEMBRE = "https://www.raiplay.it/programmi/settembre"

FOLLEMENTE_VIDEO = "https://www.raiplay.it/video/2026/09/Follemente-05d711e6-e70a-401f-8dc4-982fa42e85ae.json"
# The ``weblink`` the recorded Follemente video record points at: the player
# page that carries the hydration object when the ``.json`` URL answers with
# the bare record instead (see ``serve_video_record``).
FOLLEMENTE_PLAYER = "https://www.raiplay.it/video/2026/09/Follemente-05d711e6-e70a-401f-8dc4-982fa42e85ae.html"
NORMAL_PEOPLE_VIDEO = "https://www.raiplay.it/video/2023/05/Normal-People-S1E1-09e5a33f-6d83-4728-a470-1b7f064a7d4c.json"
SETTEMBRE_VIDEO = "https://www.raiplay.it/video/2023/11/Settembre-3387d4e1-6aac-44fd-91f7-5483dfa91800.json"
SUL_CAMMINO_VIDEO = "https://www.raiplay.it/video/2026/09/Sul-cammino-di-Francesco-La-vita-di-Biagio-Conte-Backstage-1-a24ec6c6-0802-4ef4-9afb-2445a850a5ac.json"

# Unique programme URLs in walk order, after interleaving the two monthly
# sitemaps and de-duplicating the repeated ``follemente`` entry.
PROGRAMME_URLS = [FOLLEMENTE, NORMAL_PEOPLE, RETIRED, SUL_CAMMINO, SETTEMBRE]

# RaiPlay's own ids for those pages, read from the recorded hydration objects
# and identical on both page kinds (programme ``uniquename`` ==
# video ``program_uniquename``), so one value keys content, offer and mapping.
FOLLEMENTE_ID = "ContentItem-076d295f-5438-4ab5-b669-fb3355ddb416"
NORMAL_PEOPLE_ID = "ContentItem-ca69570b-243c-4b2c-bf1f-7ee2e92a2ae8"
SUL_CAMMINO_ID = "ContentItem-7ded67bb-871b-4895-b7b9-660381385650"
SETTEMBRE_ID = "ContentItem-299d2015-0ea4-444d-a51c-565c8bd1a0e9"
FOLLEMENTE_VIDEO_ID = "ContentItem-05d711e6-e70a-401f-8dc4-982fa42e85ae"
SETTEMBRE_VIDEO_ID = "ContentItem-3387d4e1-6aac-44fd-91f7-5483dfa91800"

PAGE_FILES = {
    FOLLEMENTE: "pages/follemente.html",
    NORMAL_PEOPLE: "pages/normalpeople.html",
    SETTEMBRE: "pages/settembre.html",
    SUL_CAMMINO: "pages/sul_cammino.html",
    FOLLEMENTE_VIDEO: "pages/video_follemente.html",
    NORMAL_PEOPLE_VIDEO: "pages/video_normalpeople.html",
    SETTEMBRE_VIDEO: "pages/video_settembre.html",
}
SITEMAP_FILES = {
    PROGRAMME_SITEMAP_08: "sitemaps/programmi.08.2026.xml",
    PROGRAMME_SITEMAP_09: "sitemaps/programmi.09.2026.xml",
}
# The bare JSON document a ``.json`` video URL answers with today (recorded
# live 2026-09-30, ``application/json``): same URL as the player page above,
# different body — no ``data_type``, no ``schema``, no access model.
RECORD_FILES = {
    FOLLEMENTE_VIDEO: "pages/video_follemente_record.json",
}
# Stale entries: the fixture knows the URL and answers 404, exactly like the
# live site does for a programme retired after the sitemap was generated.
GONE_URLS = {
    RETIRED: None,             # programme page retired
    SUL_CAMMINO_VIDEO: None,   # programme alive, its video no longer serves
}


def load_index() -> str:
    return (FIXTURES / "sitemap_index.xml").read_text(encoding="utf-8")


def load_sitemaps() -> dict:
    return {
        url: (FIXTURES / relative).read_text(encoding="utf-8")
        for url, relative in SITEMAP_FILES.items()
    }


def load_pages() -> dict:
    pages = {
        url: (FIXTURES / relative).read_text(encoding="utf-8")
        for url, relative in PAGE_FILES.items()
    }
    pages.update(GONE_URLS)
    return pages


def load_record(url: str = FOLLEMENTE_VIDEO) -> str:
    """The recorded bare-JSON body of one ``.json`` video URL."""
    return (FIXTURES / RECORD_FILES[url]).read_text(encoding="utf-8")


def serve_video_record(api: FakeRaiPlayApi, *, url: str = FOLLEMENTE_VIDEO) -> str:
    """Make ``url`` answer with the bare record, and host its player page.

    Returns the player URL read from the record's own ``weblink`` — the URL
    the source has to discover there rather than construct.
    """
    record = load_record(url)
    weblink = json.loads(record).get("weblink")
    if not isinstance(weblink, str) or not weblink.startswith("/"):
        raise SourceFetchError("Fixture video record carries no weblink")
    player_url = RAIPLAY_BASE + weblink
    api.pages[player_url] = load_pages()[url]
    api.pages[url] = record
    return player_url


def url_entry(loc: str, lastmod: str = "2026-09-30") -> str:
    """One ``<url>`` element, for tests that extend a recorded sitemap."""
    return f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>\n"


def page_state(html: str) -> dict:
    """Decode ``window.WashiContext`` out of one recorded page."""
    marker = html.find(STATE_MARKER)
    if marker < 0:
        raise SourceFetchError(f"Fixture page carries no {STATE_MARKER}: …")
    start = html.find("{", marker)
    state, _end = json.JSONDecoder().raw_decode(html, start)
    if not isinstance(state, dict):
        raise SourceFetchError("Fixture page state is not an object")
    return state


def with_state(html: str, mutate) -> str:
    """Return the same page with ``mutate(state)`` applied to its state.

    Test code uses this to reach states the recording never shipped (an
    unknown access category, a region other than ``IT``, an already-expired
    window, a layout RaiPlay never publishes) without hand-writing JSON.
    """
    state = page_state(html)
    mutate(state)
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # never let a string close the script
    title = str(state.get("title") or "")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="it">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        f"<title>{title} - RaiPlay</title>\n"
        "</head>\n"
        "<body>\n"
        f"<script>{STATE_MARKER} = {payload};</script>\n"
        "</body>\n"
        "</html>\n"
    )


def access_spec(state: dict) -> dict:
    """The access spec of a recorded video state (for mutation helpers)."""
    return state["schema"]["potentialAction"]["actionAccessibilityRequirement"]


class FakeRaiPlayApi:
    """Routes RaiPlay URLs to recorded fixtures.

    - Unknown URLs raise :class:`SourceFetchError` (an over-probing client
      fails the test instead of seeing "empty").
    - ``failures`` maps a URL substring to an exception to inject mid-run.
    - Every requested URL is recorded in ``requests`` for assertions.
    """

    def __init__(self, index=None, sitemaps=None, pages=None):
        self.index = load_index() if index is None else index
        self.sitemaps = load_sitemaps() if sitemaps is None else dict(sitemaps)
        self.pages = load_pages() if pages is None else dict(pages)
        self.failures: dict = {}
        self.requests: list[str] = []

    def __call__(self, url: str):
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
        raise SourceFetchError(f"RaiPlay fixture missing for {url}")

    @property
    def detail_requests(self) -> list[str]:
        """Only the page requests (no sitemap documents)."""
        return [
            url for url in self.requests
            if url != SITEMAP_INDEX_URL and url not in self.sitemaps
        ]

    @property
    def programme_requests(self) -> list[str]:
        return [url for url in self.detail_requests if "/programmi/" in url]

    @property
    def video_requests(self) -> list[str]:
        return [url for url in self.detail_requests if "/video/" in url]
