"""TMDb API client.

Uses TMDb v3 endpoints (search / details / trending) to resolve and enrich
movies. Reads TMDB_API_KEY from Django settings and accepts either credential
TMDb hands out: a classic v3 API key (query param) or a v4 API Read Access
Token (JWT, sent as a Bearer header) — see ``TMDbClient._auth``.
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
class TMDbItem:
    tmdb_id: int
    title: str
    year: Optional[int]
    content_type: str = "movie"  # "movie" or "series"
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    genre_ids: list[int] | None = None
    genres: list[dict] | None = None  # [{'id': 28, 'name': 'Action'}]
    cast: list[dict] | None = None    # [{'id': 1234, 'name': 'Ryan Gosling', 'character': '...'}]
    seasons_data: list[dict] | None = None # [{'season_number': 1, 'episode_count': 10, 'name': 'Season 1'}]

    def __post_init__(self):
        if self.genre_ids is None:
            self.genre_ids = []
        if self.genres is None:
            self.genres = []
        if self.cast is None:
            self.cast = []
        if self.seasons_data is None:
            self.seasons_data = []

TMDbMovie = TMDbItem


class TMDbClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or getattr(settings, "TMDB_API_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            logger.warning("TMDB_API_KEY not configured; TMDbClient calls will fail.")

    @staticmethod
    def is_configured() -> bool:
        """Return True if TMDB_API_KEY is set and non-empty, otherwise False."""
        return bool(getattr(settings, "TMDB_API_KEY", ""))

    def _auth(self) -> tuple[dict, dict]:
        """Return (query_params, headers) carrying TMDb credentials.

        TMDb issues two credential shapes. A classic v3 API key (32-char hex)
        authenticates via the ``api_key`` query param. A v4 "API Read Access
        Token" is a JWT (starts with ``eyJ``) and must be sent as a Bearer
        header — passing it as ``api_key`` returns 401. Detect the JWT and route
        it to the header so either credential the user pastes just works.
        """
        if self.api_key.startswith("eyJ"):
            return {}, {"Authorization": f"Bearer {self.api_key}"}
        return {"api_key": self.api_key}, {}

    def search(self, query: str, year: Optional[int] = None, content_type: Optional[str] = None) -> list[TMDbItem]:
        """Search TMDb by title, for movies, TV series, or both."""
        params, headers = self._auth()
        params.update({"query": query, "include_adult": "false"})

        results: list[TMDbItem] = []

        if content_type is None or content_type == "movie":
            m_params = dict(params)
            if year is not None:
                m_params["year"] = year
            url = f"{TMDB_BASE}/search/movie"
            try:
                resp = requests.get(url, params=m_params, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                movie_results = resp.json().get("results", [])
                results.extend([self._parse_search_result(r, "movie") for r in movie_results])
            except Exception as exc:
                logger.exception("TMDb search movie failed: %s", exc)

        if content_type is None or content_type == "series" or content_type == "tv":
            tv_params = dict(params)
            if year is not None:
                tv_params["first_air_date_year"] = year
            url = f"{TMDB_BASE}/search/tv"
            try:
                resp = requests.get(url, params=tv_params, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                tv_results = resp.json().get("results", [])
                results.extend([self._parse_search_result(r, "series") for r in tv_results])
            except Exception as exc:
                logger.exception("TMDb search TV failed: %s", exc)

        return results

    def search_person(self, query: str) -> list[dict]:
        """Search TMDb for actors/cast members."""
        if not query or len(query.strip()) < 2:
            return []
        params, headers = self._auth()
        params.update({"query": query.strip(), "include_adult": "false"})
        url = f"{TMDB_BASE}/search/person"
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            out = []
            for r in results[:10]:
                out.append({
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "profile_path": r.get("profile_path"),
                    "known_for_department": r.get("known_for_department"),
                })
            return out
        except Exception as exc:
            logger.exception("TMDb search person failed: %s", exc)
            return []

    def details(self, tmdb_id: int, content_type: str = "movie") -> TMDbItem:
        """Fetch full movie or TV series details by TMDb id including credits."""
        params, headers = self._auth()
        params["append_to_response"] = "credits"
        endpoint = "tv" if content_type in ("series", "tv") else "movie"
        url = f"{TMDB_BASE}/{endpoint}/{tmdb_id}"
        resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        title = data.get("title") or data.get("name") or data.get("original_title") or data.get("original_name") or ""
        release_date = data.get("release_date") or data.get("first_air_date")
        year = self._extract_year(release_date)

        genres = [{"id": g["id"], "name": g["name"]} for g in data.get("genres", [])]
        genre_ids = [g["id"] for g in genres]

        cast = []
        credits_data = data.get("credits", {})
        for c in credits_data.get("cast", [])[:15]:
            cast.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "character": c.get("character"),
            })

        seasons_data = []
        if endpoint == "tv" and "seasons" in data:
            for s in data.get("seasons", []):
                seasons_data.append({
                    "season_number": s.get("season_number"),
                    "episode_count": s.get("episode_count"),
                    "name": s.get("name"),
                    "poster_path": s.get("poster_path"),
                })

        return TMDbItem(
            tmdb_id=data["id"],
            title=title,
            year=year,
            content_type="series" if endpoint == "tv" else "movie",
            overview=data.get("overview"),
            poster_path=data.get("poster_path"),
            genre_ids=genre_ids,
            genres=genres,
            cast=cast,
            seasons_data=seasons_data,
        )

    def health(self) -> dict:
        """Return health status of TMDb client."""
        return {
            'api_key_configured': self.is_configured(),
            'timeout': self.timeout,
        }

    def popular(self, limit: int = 24) -> list[TMDbMovie]:
        """Fetch trending/popular movies from TMDb (trending/movie/week)."""
        params, headers = self._auth()
        url = f"{TMDB_BASE}/trending/movie/week"
        resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
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
    def _parse_search_result(raw: dict, content_type: str = "movie") -> TMDbItem:
        title = raw.get("title") or raw.get("name") or raw.get("original_title") or raw.get("original_name") or ""
        release_date = raw.get("release_date") or raw.get("first_air_date")
        return TMDbItem(
            tmdb_id=raw["id"],
            title=title,
            year=TMDbClient._extract_year(release_date),
            content_type=content_type,
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