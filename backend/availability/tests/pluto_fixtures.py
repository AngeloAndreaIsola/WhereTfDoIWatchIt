"""Recorded Pluto TV hubs-GraphQL payloads + an in-process router for tests.

Fixtures under ``fixtures/pluto_tv/`` are trimmed recordings of the site's
own BFF: one ``collection`` payload per hub and one ``carouselData`` grid page
per start offset. The router performs zero network I/O: test code mutates the
loaded copies to build edge cases, and anything the fixtures do not cover
fails loudly with ``SourceFetchError`` — mimicking an upstream GraphQL error —
instead of silently looking like an empty page.

Token handling mirrors production: the client percent-encodes the grid token
before sending it, so the router unquotes it before the fixture lookup and
records the raw variables (including the encoded token) for assertions.
"""
from __future__ import annotations

import copy
import json
import urllib.parse
from pathlib import Path

from ..sources import SourceFetchError
from ..sources.pluto_tv import MOVIES_HUB, SHOWS_HUB

FIXTURES = Path(__file__).parent / "fixtures" / "pluto_tv"


def load_payload(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def collection_payload(hub: str) -> dict:
    """Full recorded ``collection`` response for a hub slug."""
    return load_payload(f"collection_{'movies' if hub == MOVIES_HUB else 'shows'}")


def grid_page_payload(hub: str, page: int) -> dict:
    """Full recorded ``carouselData`` response for hub page (1-based)."""
    return load_payload(f"grid_{'movies' if hub == MOVIES_HUB else 'shows'}_p{page}")


def collection_body(payload: dict) -> dict:
    """The ``collection`` object inside a recorded collection payload."""
    return payload["data"]["collection"]


def grid_token(hub: str) -> str:
    """The raw (unencoded) grid token recorded for a hub."""
    return collection_payload(hub)["data"]["collection"]["hubCarousels"][0]["token"]


def grid_result(payload: dict) -> dict:
    """The ``carouselData.result`` object inside a recorded grid payload."""
    return payload["data"]["carouselData"]["result"]


class FakePlutoTvApi:
    """Routes ``(query, variables)`` GraphQL calls to recorded fixtures.

    - Unknown hubs, missing grid pages, and injected ``failures`` raise
      ``SourceFetchError`` (upstream errors are failures, never empty pages).
    - ``failures`` maps a query/variables fragment to an exception to inject.
    - Every call is recorded as ``(operation, variables)`` in ``requests``.
    """

    def __init__(self, collections=None, grids=None):
        self.collections = copy.deepcopy(collections) if collections is not None else {
            MOVIES_HUB: collection_payload(MOVIES_HUB),
            SHOWS_HUB: collection_payload(SHOWS_HUB),
        }
        self.grids = copy.deepcopy(grids) if grids is not None else {
            grid_token(MOVIES_HUB): {
                "0": grid_page_payload(MOVIES_HUB, 1),
                "24": grid_page_payload(MOVIES_HUB, 2),
            },
            grid_token(SHOWS_HUB): {
                "0": grid_page_payload(SHOWS_HUB, 1),
            },
        }
        self.failures: dict = {}
        self.requests: list = []

    def __call__(self, query: str, variables: dict) -> dict:
        operation = "collection" if "collection(" in query else "carouselData"
        self.requests.append((operation, copy.deepcopy(variables)))
        for fragment, exc in self.failures.items():
            if fragment in query or fragment in json.dumps(variables, sort_keys=True):
                raise exc
        if operation == "collection":
            slug = variables.get("slug")
            payload = self.collections.get(slug)
            if payload is None:
                raise SourceFetchError(f"Simulated upstream rejection for collection {slug!r}")
            return payload
        token = urllib.parse.unquote(str(variables.get("token") or ""))
        start = str(variables["carouselParams"]["start"])
        pages = self.grids.get(token)
        payload = pages.get(start) if pages else None
        if payload is None:
            raise SourceFetchError(
                f"Simulated upstream rejection for grid {token!r} start={start}"
            )
        return payload

    def requests_matching(self, operation: str) -> list:
        return [variables for op, variables in self.requests if op == operation]

    def grid_starts(self, token: str | None = None) -> list:
        """The ``start`` offsets requested for a grid, in order.

        ``token`` filters on the raw (unencoded) token; without it every
        grid request is returned (movies pages first, then shows).
        """
        starts = []
        for variables in self.requests_matching("carouselData"):
            raw = urllib.parse.unquote(str(variables.get("token") or ""))
            if token is None or raw == token:
                starts.append(str(variables["carouselParams"]["start"]))
        return starts
