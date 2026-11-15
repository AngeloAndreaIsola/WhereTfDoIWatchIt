"""Recorded BBC iPlayer payloads + an in-process page router for tests.

Fixtures under ``fixtures/bbc_iplayer/`` are trimmed recordings of the real
public pages:

- ``atoz.json`` — one A-Z index state per letter (``0-9`` plus ``a``..``z``),
  exactly as the site embeds it;
- ``pages.json`` — one brand/programme page state per URL (path plus query
  string), so slice selection and pagination are explicit fixture URLs.

The router wraps each state in the same HTML shell the site serves (the
``tvip-script-app-store`` script tag holding
``window.__IPLAYER_REDUX_STATE__ = {...}``), so the client's real parser is
exercised end to end. No test performs network I/O: test code mutates the
loaded copies to build edge cases, and anything the fixtures do not cover
fails loudly with ``SourceFetchError`` — mimicking an upstream 404 — instead
of silently looking like an empty catalogue.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import urlparse

from ..sources import SourceFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "bbc_iplayer"

PAGE_SHELL = (
    "<!DOCTYPE html><html lang=\"en-GB\"><head><meta charset=\"utf-8\">"
    "<title>BBC iPlayer</title></head><body>"
    "<script id=\"tvip-script-app-store\" type=\"text/javascript\">"
    "window.__IPLAYER_REDUX_STATE__ = {state};"
    "</script></body></html>"
)


def load_atoz() -> dict:
    return json.loads((FIXTURES / "atoz.json").read_text())


def load_pages() -> dict:
    return json.loads((FIXTURES / "pages.json").read_text())


def render_page(state: dict) -> str:
    """The HTML shell the client parses; ``state`` stays the fixture payload."""
    return PAGE_SHELL.format(state=json.dumps(state))


class FakeBBCIPlayer:
    """Routes BBC iPlayer page URLs to recorded fixtures.

    - Unknown letters and URLs with no fixture raise ``SourceFetchError`` (the
      real site answers 404), so an over-probing client fails the test instead
      of seeing "empty".
    - ``failures`` maps a URL substring to an exception to inject mid-run.
    - Every requested URL is recorded in ``requests`` for assertions.
    """

    def __init__(self, atoz=None, pages=None):
        self.atoz = copy.deepcopy(atoz) if atoz is not None else load_atoz()
        self.pages = copy.deepcopy(pages) if pages is not None else load_pages()
        self.failures: dict = {}
        self.requests: list[str] = []

    def __call__(self, url: str) -> str:
        self.requests.append(url)
        for fragment, exc in self.failures.items():
            if fragment in url:
                raise exc
        parsed = urlparse(url)
        if parsed.path.startswith("/iplayer/a-z/"):
            if parsed.query:
                raise AssertionError(
                    f"A-Z fixtures are fetched without a query string: {url}"
                )
            letter = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            state = self.atoz.get(letter)
            if state is None:
                raise SourceFetchError(f"Simulated upstream rejection for {url}")
            return render_page(state)
        key = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        state = self.pages.get(key)
        if state is None:
            raise SourceFetchError(f"Simulated upstream rejection for {url}")
        return render_page(state)

    def urls_containing(self, fragment: str) -> list:
        return [url for url in self.requests if fragment in url]
