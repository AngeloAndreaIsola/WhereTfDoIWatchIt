"""Recorded PBS fixtures + fixture router (zero network).

``PAGES`` maps the ``after`` cursor of an ``allShows`` walk to its recorded
payload (``None`` = first page); ``BATCHES`` maps the requested slug list
(joined in request order) to its recorded ``showsBatch`` payload. The batch
fixture deliberately returns its shows in *reverse* request order — the live
API does not preserve order, so the collector's slug-based mapping is
actually exercised.

``FakePbsApi`` mirrors the other routers: it answers by operation name,
raises :class:`SourceFetchError` for undeclared keys (an unprovisioned test
fails loudly instead of seeing "empty"), honours ``failures`` injections, and
records every call in ``requests``/``payloads`` for assertions.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from availability.sources.base import SourceFetchError
from availability.sources.pbs import GRAPHQL_URL

FIXTURES = Path(__file__).parent / "fixtures" / "pbs"

PAGES = {
    None: "allshows_p1.json",
    "CURSOR1": "allshows_p2.json",
}

#: Request-order slug list -> recorded batch payload. Page 1 walks nature,
#: nova, frontline; page 2 adds extra-show and expired-show (nature repeats
#: and is deduped), so the single batch carries exactly these five.
BATCHES = {
    "nature,nova,frontline,extra-show,expired-show": "batch_all.json",
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakePbsApi:
    """Routes PBS content-GraphQL requests to recorded fixtures.

    - ``post(url, payload)`` answers ``PbsAllShows`` by its ``after`` cursor
      and ``PbsShowsBatch`` by the requested slug list; an undeclared key
      raises ``SourceFetchError``.
    - ``failures`` maps a substring (of the cursor, of the slug list, or of
      the operation name) to an exception to inject.
    - Every call is recorded in ``requests`` (assertions) and ``payloads``
      (full request bodies, for contract assertions).
    """

    def __init__(self, pages: dict | None = None, batches: dict | None = None):
        self.pages = (
            {key: load_fixture(name) for key, name in PAGES.items()}
            if pages is None
            else pages
        )
        self.batches = (
            {key: load_fixture(name) for key, name in BATCHES.items()}
            if batches is None
            else batches
        )
        self.failures: dict = {}
        self.requests: list[str] = []
        self.payloads: list[dict] = []

    def post(self, url: str, payload: dict) -> dict:
        if not str(url).startswith(GRAPHQL_URL):
            raise AssertionError(f"Unexpected PBS GraphQL URL: {url}")
        self.payloads.append(copy.deepcopy(payload))
        operation = str(payload.get("operationName") or "")
        variables = payload.get("variables") or {}
        slugs: list[str] = []
        if operation == "PbsAllShows":
            cursor = variables.get("after")
            key = None if cursor is None else str(cursor)
            table: dict = self.pages
            self.requests.append(f"POST allShows after={key}")
        elif operation == "PbsShowsBatch":
            slugs = [str(s) for s in variables.get("slugs") or []]
            key = ",".join(slugs)
            table = self.batches
            self.requests.append(f"POST showsBatch n={len(slugs)}")
        else:
            raise SourceFetchError(
                f"Simulated PBS rejection for operation {operation!r}"
            )
        for fragment, exc in self.failures.items():
            if fragment.lower() in str(key).lower() or fragment.lower() in operation.lower():
                raise exc
        response = table.get(key)
        if response is None and operation == "PbsShowsBatch":
            # The recorded fixtures hold one aggregate batch payload for the
            # whole catalogue. A fetch that walks a bounded window asks for a
            # *subset* of those slugs, which the exact-key lookup above misses —
            # so fall back to filtering the recorded batch by the requested
            # slugs. Real PBS answers any slug list; without this the only
            # testable batch size would be "all of them", which cannot exercise
            # a resumed walk.
            response = self._filter_batch(slugs)
        if response is None:
            raise SourceFetchError(
                f"Simulated PBS rejection for {operation} key={key!r}"
            )
        return copy.deepcopy(response)

    def _filter_batch(self, slugs: list[str]) -> dict | None:
        """A recorded batch response narrowed to *slugs*, or ``None``."""
        wanted = set(slugs)
        for entry in self.batches.values():
            records = ((entry or {}).get("data") or {}).get("showsBatch")
            if not isinstance(records, list):
                continue
            selected = [
                record for record in records
                if isinstance(record, dict)
                and str((record.get("show") or {}).get("slug") or "") in wanted
            ]
            if selected:
                return {"data": {"showsBatch": selected}}
        return None

    def requests_matching(self, fragment: str) -> list[str]:
        return [record for record in self.requests if fragment in record]
