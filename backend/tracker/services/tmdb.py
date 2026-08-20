"""TMDb API client.

Uses TMDb v3 search endpoint to resolve title/year -> tmdb_id.
Reads TMDB_API_KEY from Django settings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
DEFAULT_TIMEOUT = 10


@dataclass
class TMDbMovie:
    tmdb_id: int
    title: str
    year: Optional[int]
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    genre_ids: list[int] | None = None

    def __post_init__(self):
        if self.genre_ids is None:
            self.genre_ids = []


class TMDbClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or getattr(settings, "TMDB_API_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            logger.warning("TMDB_API_KEY not configured; TMDbClient calls will fail.")

    def search(self, query: str, year: Optional[int] = None) -> list[TMDbMovie]:
        """Search TMDb by title, optionally filtered by year."""
        params = {"api_key": self.api_key, "query": query, "include_adult": "false"}
        if year is not None:
            params["year"] = year
        url = f"{TMDB_BASE}/search/movie"
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [self._parse_search_result(r) for r in results]

    def details(self, tmdb_id: int) -> TMDbMovie:
        """Fetch full movie details by TMDb id."""
        url = f"{TMDB_BASE}/movie/{tmdb_id}"
        resp = requests.get(url, params={"api_key": self.api_key}, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return TMDbMovie(
            tmdb_id=data["id"],
            title=data.get("title") or data.get("original_title", ""),
            year=self._extract_year(data.get("release_date")),
            overview=data.get("overview"),
            poster_path=data.get("poster_path"),
        )

    def popular(self, limit: int = 24) -> list[TMDbMovie]:
        """Fetch trending/popular movies from TMDb (trending/movie/week)."""
        url = f"{TMDB_BASE}/trending/movie/week"
        resp = requests.get(url, params={"api_key": self.api_key}, timeout=self.timeout)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        movies = [self._parse_search_result(r) for r in results]
        return movies[:limit]

    def resolve(self, query: str, year: Optional[int] = None) -> Optional[TMDbMovie]:
        """Resolve a single movie; returns first match or None.

        Returns None when no match. Does not auto-pick on ambiguity;
        callers must use search() for ambiguous input.
        """
        results = self.search(query, year=year)
        return results[0] if results else None

    @staticmethod
    def _parse_search_result(raw: dict) -> TMDbMovie:
        return TMDbMovie(
            tmdb_id=raw["id"],
            title=raw.get("title") or raw.get("original_title", ""),
            year=TMDbClient._extract_year(raw.get("release_date")),
            overview=raw.get("overview"),
            poster_path=raw.get("poster_path"),
            genre_ids=raw.get("genre_ids") or [],
        )

    @staticmethod
    def _extract_year(release_date: Optional[str]) -> Optional[int]:
        if not release_date or len(release_date) < 4:
            return None
        try:
            return int(release_date[:4])
        except ValueError:
            return None