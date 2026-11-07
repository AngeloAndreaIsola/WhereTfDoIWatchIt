"""Recorded ABC iview fixtures + fixture router (zero network).

``series_list.json`` is the index walk: junk rows (a bare string, a mapping
without ``latestEpisode``, a duplicated series) followed by six real series —
``culture-by-design`` (numbered episodes across two seasons plus an
unnumbered trailer and a live stream), ``7-30`` (only unnumbered news
segments -> series-level offer only), ``9-11-stories`` (no video id
nowhere), ``empty-show`` (no ``episodes`` key), ``no-eps-show`` (empty
list), and ``gone-show`` (404 / ``None``).

``FakeAbcIviewApi`` is a ``url -> payload`` callable (the client's
``requester`` contract): it answers the index and per-slug detail routes,
raises :class:`SourceFetchError` for undeclared slugs (an unprovisioned test
fails loudly instead of seeing "empty"), honours ``failures`` injections,
and records every URL in ``requests`` for assertions. ``None`` in
``DETAILS`` stands for *HTTP 404 / series gone*.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from availability.sources.abc_iview import SERIES_LIST_URL
from availability.sources.base import SourceFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "abc_iview"

DETAILS = {
    "culture-by-design": "series_culture.json",
    "7-30": "series_7-30.json",
    "9-11-stories": "series_9-11-stories.json",
    "empty-show": "series_empty.json",
    "no-eps-show": "series_no-eps.json",
    "gone-show": None,
}


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeAbcIviewApi:
    """Routes iview catalogue requests to recorded fixtures.

    - ``list_payload`` overrides the index (defaults to
      ``series_list.json``); ``details`` maps slug -> payload, ``None`` or a
      missing entry meaning... ``None`` is a declared 404, a missing slug is
      a loud fixture failure.
    - ``failures`` maps a URL substring to an exception to inject.
    - Every requested URL lands in ``requests``.
    """

    def __init__(self, list_payload=None, details: dict | None = None,
                 failures: dict | None = None):
        self.list_payload = (
            load_fixture("series_list.json")
            if list_payload is None
            else list_payload
        )
        self.details = (
            {
                slug: load_fixture(name) if name is not None else None
                for slug, name in DETAILS.items()
            }
            if details is None
            else details
        )
        self.failures = failures or {}
        self.requests: list[str] = []

    def __call__(self, url: str):
        self.requests.append(url)
        for fragment, exc in self.failures.items():
            if fragment in url:
                raise exc
        if url == SERIES_LIST_URL:
            return copy.deepcopy(self.list_payload)
        prefix = SERIES_LIST_URL + "/"
        if url.startswith(prefix):
            slug = url[len(prefix):]
            if slug in self.details:
                payload = self.details[slug]
                return None if payload is None else copy.deepcopy(payload)
            raise SourceFetchError(
                f"Simulated ABC iview rejection for series {slug!r}"
            )
        raise SourceFetchError(f"Unexpected ABC iview URL: {url}")

    def requests_matching(self, fragment: str) -> list[str]:
        return [record for record in self.requests if fragment in record]
