"""Generic scraper architecture, normalized output data structures, and content matcher."""
from __future__ import annotations

import logging
import re
import requests
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from django.conf import settings

from tracker.languages import normalize_language_code
from tracker.models import Movie, Provider, ScrapedContentMapping
from tracker.services.tmdb import TMDbClient, TMDbMovie

logger = logging.getLogger(__name__)

QUALITY_MAPPINGS = {
    "2160p": "4K",
    "4k": "4K",
    "uhd": "4K",
    "1080p": "1080p",
    "fhd": "1080p",
    "hd": "1080p",
    "720p": "720p",
    "480p": "480p",
    "sd": "480p",
}


def normalize_quality(raw_quality: Optional[str]) -> Optional[str]:
    """Normalize raw quality string into canonical representations (4K, 1080p, 720p, 480p)."""
    if not raw_quality:
        return None
    cleaned = str(raw_quality).strip().lower()
    for key, val in QUALITY_MAPPINGS.items():
        if key in cleaned:
            return val
    return None


def normalize_language_list(raw_langs: Sequence[Any]) -> list[str]:
    """Normalize a list of raw language inputs using the canonical language map."""
    cleaned: list[str] = []
    seen = set()
    for item in raw_langs or []:
        if not item:
            continue
        norm = normalize_language_code(str(item).strip())
        if norm and norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    return cleaned


@dataclass
class ScrapedItem:
    title: str
    provider_slug: str
    original_title: Optional[str] = None
    year: Optional[int] = None
    content_type: str = "movie"  # "movie" | "series"
    season: Optional[int] = None
    episode: Optional[int] = None
    source_url: str = ""
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    quality: Optional[str] = None
    availability: bool = True
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.title = self.title.strip() if self.title else ""
        if self.original_title:
            self.original_title = self.original_title.strip()
        self.audio_languages = normalize_language_list(self.audio_languages)
        self.subtitle_languages = normalize_language_list(self.subtitle_languages)
        self.quality = normalize_quality(self.quality)


class BaseScraper:
    provider_slug: str = ""
    timeout: int = 10
    max_retries: int = 2

    def __init__(self, timeout: Optional[int] = None):
        if timeout:
            self.timeout = timeout
        self.session = requests.Session()

    def fetch_url(self, url: str, headers: Optional[dict] = None) -> Optional[dict | str]:
        """Fetch URL with retries, timeout, and error handling."""
        for attempt in range(1 + self.max_retries):
            try:
                resp = self.session.get(url, timeout=self.timeout, headers=headers)
                resp.raise_for_status()
                try:
                    return resp.json()
                except ValueError:
                    return resp.text
            except Exception as exc:
                logger.warning(
                    "Scraper %s attempt %d for %s failed: %s",
                    self.provider_slug,
                    attempt + 1,
                    url,
                    exc,
                )
        return None

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        """Override in subclasses to convert site data to ScrapedItem list."""
        raise NotImplementedError

    def run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        """Execute scraper run, catching failures gracefully."""
        try:
            return self._execute_run(query)
        except Exception as exc:
            logger.exception("Scraper %s failed during run: %s", self.provider_slug, exc)
            return []

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        raise NotImplementedError


class ContentMatcher:
    """Matches scraped items against canonical TMDb movies, supporting confidence scores and confirmation persistence."""

    def __init__(self, tmdb_client: Optional[TMDbClient] = None):
        self.tmdb_client = tmdb_client or TMDbClient(settings.TMDB_API_KEY)

    def match_item(
        self, item: ScrapedItem
    ) -> tuple[Optional[Movie], float, list[dict[str, Any]], bool]:
        """Matches a ScrapedItem against database / TMDb.

        Returns: (movie, confidence_score, candidates, is_confirmed)
        """
        provider = Provider.objects.filter(slug=item.provider_slug).first()
        if not provider:
            logger.warning("Provider with slug %s not found", item.provider_slug)
            return None, 0.0, [], False

        # 1. Check existing ScrapedContentMapping in database
        existing = ScrapedContentMapping.objects.filter(
            provider=provider,
            scraped_title__iexact=item.title,
            scraped_year=item.year,
        ).first()

        if existing and existing.movie:
            return existing.movie, existing.confidence_score, [], existing.is_confirmed

        # Check local DB Movie table before external API
        exact_movie = Movie.objects.filter(title__iexact=item.title, year=item.year).first()
        if exact_movie:
            self._upsert_mapping(provider, item, movie=exact_movie, confidence=1.0)
            return exact_movie, 1.0, [], False

        # 2. Search TMDb
        candidates_raw: list[TMDbMovie] = []
        try:
            candidates_raw = self.tmdb_client.search(item.title, year=item.year)
        except Exception as exc:
            logger.warning("TMDb search failed for scraped title '%s': %s", item.title, exc)

        if not candidates_raw:
            self._upsert_mapping(provider, item, confidence=0.0)
            return None, 0.0, [], False

        # Score candidates
        candidate_list = []
        best_movie = None
        best_score = 0.0

        for cand in candidates_raw[:5]:
            score = self.calculate_confidence(item, cand)
            cand_dict = {
                "tmdb_id": cand.tmdb_id,
                "title": cand.title,
                "year": cand.year,
                "confidence_score": score,
            }
            candidate_list.append(cand_dict)
            if score > best_score:
                best_score = score
                best_movie = cand

        # Auto-associate if confidence is reasonable (>= 0.6)
        # Scraped items often lack year info, capping score at ~0.8 even for exact title matches.
        matched_movie_obj = None
        if best_score >= 0.6 and best_movie:
            matched_movie_obj, _ = Movie.objects.get_or_create(
                tmdb_id=best_movie.tmdb_id,
                defaults={"title": best_movie.title, "year": best_movie.year},
            )
            self._upsert_mapping(provider, item, movie=matched_movie_obj, confidence=best_score)
            return matched_movie_obj, best_score, candidate_list, False

        # Save ambiguous mapping
        self._upsert_mapping(provider, item, confidence=best_score)
        return None, best_score, candidate_list, False

    @staticmethod
    def _upsert_mapping(provider, item, movie=None, confidence=0.0):
        """Safely update-or-create a ScrapedContentMapping, deduping on conflict."""
        try:
            ScrapedContentMapping.objects.update_or_create(
                provider=provider,
                scraped_title=item.title,
                scraped_year=item.year,
                defaults={
                    "scraped_url": item.source_url,
                    "movie": movie,
                    "is_confirmed": False,
                    "confidence_score": confidence,
                    "raw_payload": item.raw_payload,
                },
            )
        except ScrapedContentMapping.MultipleObjectsReturned:
            ScrapedContentMapping.objects.filter(
                provider=provider,
                scraped_title=item.title,
                scraped_year=item.year,
            ).exclude(id=ScrapedContentMapping.objects.filter(
                provider=provider,
                scraped_title=item.title,
                scraped_year=item.year,
            ).values_list('id', flat=True).first()).delete()
            ScrapedContentMapping.objects.update_or_create(
                provider=provider,
                scraped_title=item.title,
                scraped_year=item.year,
                defaults={
                    "scraped_url": item.source_url,
                    "movie": movie,
                    "is_confirmed": False,
                    "confidence_score": confidence,
                    "raw_payload": item.raw_payload,
                },
            )

    @staticmethod
    def calculate_confidence(item: ScrapedItem, candidate: TMDbMovie) -> float:
        """Calculate confidence score between 0.0 and 1.0."""
        score = 0.0
        import re
        def normalize(t):
            return re.sub(r'[^a-z0-9]', ' ', t.lower()).strip()
            
        s_title = normalize(item.title)
        c_title = normalize(candidate.title)
        
        s_words = set(s_title.split())
        c_words = set(c_title.split())
        
        if s_title == c_title:
            score += 0.6
        elif s_title in c_title or c_title in s_title:
            score += 0.4
        elif s_words and c_words:
            overlap = len(s_words.intersection(c_words))
            max_len = max(len(s_words), len(c_words))
            if overlap / max_len >= 0.5:
                score += 0.35
            elif overlap > 0:
                score += 0.2
        else:
            score += 0.1

        if item.year and candidate.year:
            if item.year == candidate.year:
                score += 0.4
            elif abs(item.year - candidate.year) == 1:
                score += 0.2
        elif not item.year:
            score += 0.2

        return min(1.0, score)
