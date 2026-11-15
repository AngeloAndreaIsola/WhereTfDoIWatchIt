"""Declare a freshness window for test-only source slugs.

Production sources all declare a window (``availability.freshness``); an
undeclared slug reads as ``unknown`` freshness, which is never "verified".
Tests that exercise the current-offer read path with ad-hoc slugs declare one
so their observations model a real, recently refreshed source.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from availability.freshness import SOURCE_FRESHNESS


def declare_freshness(testcase, *slugs: str, window: timedelta = timedelta(hours=24)) -> None:
    patcher = patch.dict(SOURCE_FRESHNESS, {slug: window for slug in slugs})
    patcher.start()
    testcase.addCleanup(patcher.stop)
