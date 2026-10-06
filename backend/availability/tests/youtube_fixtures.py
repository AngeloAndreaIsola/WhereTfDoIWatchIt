"""Recorded YouTube payloads + an in-process API router for tests.

Fixtures under ``fixtures/youtube/`` are trimmed recordings of the real
public innertube/homepage responses (research run 2026-10, US egress), plus
derived variants that keep the recorded badge vocabulary while pinning one
behaviour each — the file names say which. The router performs zero network
I/O: it routes the homepage probe and each known search query to its fixture,
records every call, lets tests inject failures by substring, and fails loudly
with ``SourceFetchError`` for anything it does not know — mimicking an
upstream rejection instead of silently looking like "no free results".
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from ..sources import SourceFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "youtube"

#: Search query (``"<title> <year>"``, the collector's own query format) ->
#: fixture file. Every query a test may issue must be declared here first.
QUERIES: dict[str, str] = {
    "Matilda 1996": "search_matilda_free.json",
    "The Shawshank Redemption 1994": "search_no_free_match.json",
    "Inception 2010": "search_inception_paid.json",
    "Mean Girls 2004": "search_video_renderer_free.json",
    "Grease 1990": "search_year_mismatch.json",
    "The Longest Yard 2005": "search_longest_yard_free.json",
    "Broken Payload 2020": "search_malformed.json",
    "Upstream Error 2021": "search_error.json",
    # Observed live 2026-10-06: the item section now sits one level deeper,
    # under primaryContents.sectionListRenderer.
    "Deadline 2008": "search_section_list_free.json",
}


def load_homepage() -> str:
    return (FIXTURES / "homepage.html").read_text()


def load_search(filename: str) -> dict:
    return json.loads((FIXTURES / filename).read_text())


class FakeYouTubeApi:
    """Routes YouTube URLs to recorded fixtures.

    - ``get(url)`` answers the homepage probe (region detection).
    - ``post(url, payload)`` answers an innertube search by its query string;
      an undeclared query raises ``SourceFetchError`` (the real API rejects
      unknown shapes), so an unprovisioned test fails instead of seeing
      "empty".
    - ``failures`` maps a substring (of the URL for GET, of the query for
      POST) to an exception to inject.
    - Every call is recorded in ``requests`` for assertions.
    """

    def __init__(self, homepage: str | None = None, searches: dict | None = None):
        self.homepage = load_homepage() if homepage is None else homepage
        self.searches = (
            {query: load_search(name) for query, name in QUERIES.items()}
            if searches is None
            else searches
        )
        self.failures: dict = {}
        self.requests: list[str] = []

    def get(self, url: str) -> str:
        self.requests.append(f"GET {url}")
        for fragment, exc in self.failures.items():
            if fragment in url:
                raise exc
        if not url.startswith("https://www.youtube.com/"):
            raise AssertionError(f"Unexpected YouTube GET URL: {url}")
        return self.homepage

    def post(self, url: str, payload: dict) -> dict:
        query = str(payload.get("query") or "")
        self.requests.append(f"POST {url} q={query}")
        for fragment, exc in self.failures.items():
            if fragment in query or fragment in url:
                raise exc
        if not url.startswith("https://www.youtube.com/youtubei/v1/search"):
            raise AssertionError(f"Unexpected YouTube POST URL: {url}")
        response = self.searches.get(query)
        if response is None:
            raise SourceFetchError(f"Simulated upstream rejection for query {query!r}")
        return copy.deepcopy(response)

    def requests_matching(self, fragment: str) -> list[str]:
        return [record for record in self.requests if fragment in record]
