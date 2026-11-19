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
from tracker.services.scraper_policy import ScraperExecutionIneligible, require_legacy_execution

logger = logging.getLogger(__name__)

# Several providers WAF-block the default `python-requests` user agent.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

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

#: Minimum confidence for an automatic scrape -> TMDb association. Confidence
#: is necessary but no longer sufficient: ``ContentMatcher.match_item`` also
#: demands identity-grade title evidence and a unique winning candidate, so a
#: weak title signal can never buy an association just by accumulating points
#: (notably the old substring 0.4 + missing-year 0.2 = 0.6 path).
AUTO_MATCH_CONFIDENCE = 0.6


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


class _LegacyScraperSession(requests.Session):
    """Cover direct request/send calls as well as the fetch convenience method."""

    def __init__(self, scraper):
        super().__init__()
        self.scraper = scraper

    def request(self, *args, **kwargs):
        require_legacy_execution(self.scraper.provider_slug)
        return super().request(*args, **kwargs)

    def send(self, *args, **kwargs):
        require_legacy_execution(self.scraper.provider_slug)
        return super().send(*args, **kwargs)


class BaseScraper:
    provider_slug: str = ""
    timeout: int = 10
    max_retries: int = 2

    def __init__(self, timeout: Optional[int] = None):
        if timeout:
            self.timeout = timeout
        self.session = _LegacyScraperSession(self)

    def fetch_url(self, url: str, headers: Optional[dict] = None) -> Optional[dict | str]:
        """Fetch URL with retries, timeout, and error handling."""
        require_legacy_execution(self.provider_slug)
        request_headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers:
            request_headers.update(headers)
        for attempt in range(1 + self.max_retries):
            try:
                resp = self.session.get(url, timeout=self.timeout, headers=request_headers)
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
        require_legacy_execution(self.provider_slug)
        try:
            return self._execute_run(query)
        except ScraperExecutionIneligible:
            raise
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
        require_legacy_execution(provider or item.provider_slug)
        if not provider:
            logger.warning("Provider with slug %s not found", item.provider_slug)
            return None, 0.0, [], False

        # 1. Check existing ScrapedContentMapping in database
        existing = ScrapedContentMapping.objects.filter(
            provider=provider,
            scraped_title__iexact=item.title,
            scraped_year=item.year,
            scraped_content_type=item.content_type,
        ).first()

        if existing and existing.movie:
            return existing.movie, existing.confidence_score, [], existing.is_confirmed

        # Check local DB Movie table before external API. A series-declared scrape
        # must never link to a movie row: an exact title/year match of the other
        # type is treated as "not found" and falls through to the TMDb search for
        # the declared type.
        exact_qs = Movie.objects.filter(title__iexact=item.title, year=item.year)
        exact_qs = exact_qs.filter(content_type=item.content_type)
        exact_movie = exact_qs.first()
        if exact_movie:
            self._upsert_mapping(provider, item, movie=exact_movie, confidence=1.0)
            return exact_movie, 1.0, [], False

        # 2. Search TMDb — in the scraped item's content type, so a series scrape
        # hits the TV search endpoint instead of the movie one.
        candidates_raw: list[TMDbMovie] = []
        try:
            candidates_raw = self.tmdb_client.search(
                item.title, year=item.year, content_type=item.content_type
            )
        except Exception as exc:
            logger.warning("TMDb search failed for scraped title '%s': %s", item.title, exc)

        if not candidates_raw:
            self._upsert_mapping(provider, item, confidence=0.0)
            return None, 0.0, [], False

        # Score candidates
        candidate_list = []
        scored_candidates = []
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
            scored_candidates.append((score, cand))
            if score > best_score:
                best_score = score
                best_movie = cand

        # Auto-associate only when the evidence is identity-grade. The score
        # alone is not enough: a weak title relation plus a missing year can
        # still add up to the threshold, and a tied best score means the
        # search did not identify one title at all. Either case is persisted
        # as an unresolved mapping instead (``movie=None``), which keeps the
        # scrape visible for review without attaching availability to a guess.
        distinct_winners = {
            cand.tmdb_id for score, cand in scored_candidates
            if score == best_score
        }
        matched_movie_obj = None
        if (
            best_score >= AUTO_MATCH_CONFIDENCE
            and best_movie
            and len(distinct_winners) == 1
            and self._is_confident_title_match(item, best_movie)
        ):
            matched_movie_obj, _ = Movie.objects.get_or_create(
                tmdb_id=best_movie.tmdb_id,
                content_type=best_movie.content_type or "movie",
                defaults={
                    "title": best_movie.title,
                    "year": best_movie.year,
                    # Never flip an existing row: get_or_create only applies this
                    # content_type when the row is created.
                    "content_type": best_movie.content_type or "movie",
                },
            )
            # Backfill poster from the TMDb candidate so catalogs show artwork
            # without first requiring a detail-page enrichment pass.
            if not matched_movie_obj.poster_path and best_movie.poster_path:
                matched_movie_obj.poster_path = best_movie.poster_path
                matched_movie_obj.save(update_fields=["poster_path"])
            self._upsert_mapping(provider, item, movie=matched_movie_obj, confidence=best_score)
            return matched_movie_obj, best_score, candidate_list, False

        # Save ambiguous mapping
        self._upsert_mapping(provider, item, confidence=best_score)
        return None, best_score, candidate_list, False

    @staticmethod
    def _upsert_mapping(provider, item, movie=None, confidence=0.0):
        """Safely update-or-create a ScrapedContentMapping, deduping on conflict.

        The mapping table has no ``content_type`` column, so the item's type is
        stamped into ``raw_payload`` — it is what the unresolved-review flow reads
        back to search TMDb in the right type.

        Every write is projected onto the canonical availability models through
        the legacy ingestion bridge, so ``ScrapedContentMapping`` and
        ``ProviderContent``/``ContentMapping`` stay in lockstep and the unified
        catalogue (which reads only the canonical tables) never lags behind a
        scrape or a manual confirmation.
        """
        # Imported lazily to keep the scraper base independent of the service
        # package during Django app initialization.
        from tracker.services.scraped_mapping import persist_scraped_item

        require_legacy_execution(provider)
        persist_scraped_item(
            provider=provider,
            item=item,
            movie=movie,
            confidence=confidence,
            apply_match=True,
        )

    @staticmethod
    def _normalize_title(value) -> str:
        """Case/punctuation/whitespace-insensitive title form."""
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

    @staticmethod
    def _is_token_run(shorter: list[str], longer: list[str]) -> bool:
        """True when *shorter* appears as a contiguous word run in *longer*.

        Character-level containment is what let ``Her`` match ``Hereditary``:
        a five-letter title is a substring of hundreds of unrelated words. A
        word-run comparison keeps legitimate variants (``Matrix`` in ``The
        Matrix``) while refusing accidental character overlaps.
        """
        if not shorter or len(shorter) > len(longer):
            return False
        return any(
            longer[start:start + len(shorter)] == shorter
            for start in range(len(longer) - len(shorter) + 1)
        )

    @classmethod
    def _title_relation(cls, left, right) -> str:
        """Strongest title relation between two raw titles.

        ``exact`` is the only relation strong enough to confirm a mapping on
        its own; the weaker relations only contribute to the review score.
        """
        normalized_left = cls._normalize_title(left)
        normalized_right = cls._normalize_title(right)
        if not normalized_left or not normalized_right:
            return "none"
        if normalized_left == normalized_right:
            return "exact"
        left_words = normalized_left.split()
        right_words = normalized_right.split()
        if cls._is_token_run(left_words, right_words) or cls._is_token_run(
            right_words, left_words
        ):
            return "contains"
        overlap = len(set(left_words) & set(right_words)) / max(
            len(set(left_words)), len(set(right_words))
        )
        if overlap >= 0.5:
            return "overlap"
        if overlap > 0:
            return "weak_overlap"
        return "none"

    @classmethod
    def _is_confident_title_match(cls, item: ScrapedItem, candidate: TMDbMovie) -> bool:
        """Whether the title evidence justifies an automatic association.

        Only normalized equality qualifies. A known conflicting year
        disqualifies a movie (a remake is a different TMDb identity); series
        keep the established policy where the provider year can be a catalogue
        re-add date rather than a first-air year. Containment/overlap title
        relations stay unresolved for review no matter how the score adds up.
        """
        if cls._title_relation(item.title, candidate.title) != "exact":
            return False
        if item.content_type == "series":
            return True
        if item.year and candidate.year and abs(item.year - candidate.year) > 1:
            return False
        return True

    @staticmethod
    def calculate_confidence(item: ScrapedItem, candidate: TMDbMovie) -> float:
        """Calculate confidence score between 0.0 and 1.0.

        The score is review evidence, not the acceptance decision: weak title
        relations are intentionally capped below :data:`AUTO_MATCH_CONFIDENCE`
        so a missing year cannot lift a substring/word-overlap match over the
        line, and :meth:`match_item` additionally requires an exact normalized
        title (and, for movies, compatible years).
        """
        score = 0.0
        relation = ContentMatcher._title_relation(item.title, candidate.title)
        if relation == "exact":
            score += 0.6
        elif relation == "contains":
            score += 0.4
        elif relation == "overlap":
            score += 0.35
        elif relation == "weak_overlap":
            score += 0.2

        if item.year and candidate.year:
            if item.year == candidate.year:
                score += 0.4
            elif abs(item.year - candidate.year) == 1:
                score += 0.2
        elif not item.year:
            score += 0.2

        return min(1.0, score)
