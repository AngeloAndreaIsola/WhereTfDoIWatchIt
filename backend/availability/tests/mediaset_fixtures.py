"""Recorded Mediaset Infinity fixtures + fixture router (zero network).

``SEARCHES`` maps a requested title to its recorded ``MediasetSearch``
payload; ``MOVIES`` maps a matched guid to its recorded ``MediasetMovie``
payload. Every payload is a live response of the collector's reduced GraphQL
documents (see ``sources.mediaset_infinity``), trimmed to the cards the
behaviour under test needs — decoy cards ride alongside so title verification
is actually exercised.

``FakeMediasetApi`` mirrors the YouTube router: it answers by operation name,
raises :class:`SourceFetchError` for undeclared keys (the gateway rejects
unknown documents, so an unprovisioned test fails loudly instead of seeing
"empty"), honours ``failures`` injections, and records every call in
``requests`` for assertions.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from availability.sources.base import SourceFetchError
from availability.sources.mediaset_infinity import FRONTIER_URL
from availability.services.normalize import normalize_title

FIXTURES = Path(__file__).parent / "fixtures" / "mediaset_infinity"

SEARCHES = {
    "Karol un uomo diventato Papa": "search_karol.json",
    "Matrix": "search_matrix.json",
    "Troppo rischio per un uomo solo": "search_troppo.json",
    "Lupin III - La partita italiana": "search_lupin.json",
    "Un Titolo Che Non Esiste": "search_nomatch.json",
}

MOVIES = {
    "F011844503000101": "movie_avod.json",
    "F000567001000101": "movie_svod.json",
    "F010285001000102": "movie_tvod.json",
    "F311059101000102": "movie_avod_expiring.json",
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeMediasetApi:
    """Routes Mediaset frontier requests to recorded fixtures.

    - ``post(url, payload)`` answers a search by its title query and a movie
      detail by its guid; an undeclared key raises ``SourceFetchError``.
    - ``failures`` maps a substring (of the search title, the guid, or the
      operation name) to an exception to inject.
    - Every call is recorded in ``requests`` for assertions.
    """

    def __init__(self, searches: dict | None = None, movies: dict | None = None):
        self.searches = (
            {query: load_fixture(name) for query, name in SEARCHES.items()}
            if searches is None
            else searches
        )
        self.movies = (
            {guid: load_fixture(name) for guid, name in MOVIES.items()}
            if movies is None
            else movies
        )
        self.failures: dict = {}
        self.requests: list[str] = []
        self.payloads: list[dict] = []

    def post(self, url: str, payload: dict) -> dict:
        if not str(url).startswith(FRONTIER_URL):
            raise AssertionError(f"Unexpected Mediaset frontier URL: {url}")
        self.payloads.append(copy.deepcopy(payload))
        operation = str(payload.get("operationName") or "")
        variables = payload.get("variables") or {}
        if operation == "MediasetSearch":
            raw_key = str(variables.get("query") or "")
            table = self.searches
            self.requests.append(f"POST search {raw_key}")
        elif operation == "MediasetMovie":
            raw_key = str(variables.get("id") or "")
            table = self.movies
            self.requests.append(f"POST movie {raw_key}")
        else:
            raise SourceFetchError(
                f"Simulated gateway rejection for operation {operation!r}"
            )
        for fragment, exc in self.failures.items():
            if fragment in raw_key or fragment in operation:
                raise exc
        response = table.get(raw_key)
        if response is None and operation == "MediasetSearch":
            # The gateway matches titles fuzzily (case/punctuation); mirror
            # that so a title-verification test can query a variant spelling.
            wanted = normalize_title(raw_key)
            for key_title, fixture_payload in table.items():
                if normalize_title(key_title) == wanted:
                    response = fixture_payload
                    break
        if response is None:
            raise SourceFetchError(
                f"Simulated gateway rejection for {operation} {raw_key!r}"
            )
        return copy.deepcopy(response)

    def requests_matching(self, fragment: str) -> list[str]:
        return [record for record in self.requests if fragment in record]
