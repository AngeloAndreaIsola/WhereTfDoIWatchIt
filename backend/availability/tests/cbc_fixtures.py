"""Recorded CBC Gem payloads + an in-process API router for tests.

Fixtures under ``fixtures/cbc_gem/`` are trimmed recordings of the real
public API (category indices + one detail payload per catalogue item). The
router performs zero network I/O: test code mutates the loaded copies to
build edge cases, and anything the fixtures do not cover fails loudly with
``SourceFetchError`` — mimicking the real API's HTTP 400/404 — instead of
silently looking like an empty page.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from ..sources import SourceFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "cbc_gem"


def load_categories() -> dict:
    return json.loads((FIXTURES / "categories.json").read_text())


def load_detail(slug: str) -> dict:
    return json.loads(
        (FIXTURES / "details" / f"{slug.replace('-', '_')}.json").read_text()
    )


def load_details() -> dict:
    return {
        path.stem.replace("_", "-"): json.loads(path.read_text())
        for path in sorted((FIXTURES / "details").glob("*.json"))
    }


class FakeCbcApi:
    """Routes CBC Gem API URLs to recorded fixtures.

    - Unknown categories, pages beyond a category's ``totalPages``, and
      unknown detail slugs raise ``SourceFetchError`` (the real API rejects
      them), so an over-probing client fails the test instead of seeing
      "empty".
    - ``failures`` maps a URL substring to an exception to inject mid-run.
    - Every requested URL is recorded in ``requests`` for assertions.
    """

    def __init__(self, categories=None, details=None):
        self.categories = copy.deepcopy(categories) if categories is not None else load_categories()
        self.details = copy.deepcopy(details) if details is not None else load_details()
        self.failures: dict = {}
        self.requests: list[str] = []

    def __call__(self, url: str) -> dict:
        self.requests.append(url)
        for fragment, exc in self.failures.items():
            if fragment in url:
                raise exc
        match = re.search(r"/category/([^?]+)", url)
        if match:
            category = match.group(1)
            page = int(re.search(r"pageNumber=(\d+)", url).group(1))
            pages = self.categories.get(category)
            if pages is None or str(page) not in pages:
                raise SourceFetchError(f"Simulated upstream rejection for {url}")
            return {"content": [{"items": pages[str(page)]}]}
        match = re.search(r"/show/([^?]+)", url)
        if match:
            slug = match.group(1)
            if slug not in self.details:
                raise SourceFetchError(f"Simulated upstream rejection for {url}")
            return self.details[slug]
        raise AssertionError(f"Unexpected CBC Gem URL: {url}")

    def urls_containing(self, fragment: str) -> list:
        return [url for url in self.requests if fragment in url]
