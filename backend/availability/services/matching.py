"""Local title/year matching for sources that expose no external ids (Phase 5).

CBC Gem (and any future provider-native collector) publishes no TMDb ids, so
provider content -> TMDb resolution goes through the local tracker catalog:

    existing ContentMapping (refresh service, manual mappings win)
        -> match_local_catalog() here
        -> unresolved: no offers persisted (``Offer.tmdb_id`` is non-null),
           content logged, never guessed — but its provider-native
           ``ProviderContent`` row already exists (Phase 7), so nothing the
           source discovered is lost while it waits for a mapping

Deliberately conservative — a wrong mapping silently attaches offers to the
wrong movie forever, so only unambiguous exact-title candidates ever match and
confidence encodes how much evidence backed the match. ``FUZZY_TITLE_YEAR`` is
never produced here; fuzzy matching is reserved for a future manual-review
flow, not for automatic persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tracker.models import Movie

from ..domain.enums import ContentType, MatchMethod

# Tiered confidence: exact title + year uniquely resolved beats a title-only
# resolution (the provider year may be unknown, or — for series — a catalog
# re-add date rather than a first-air year).
TITLE_YEAR_EXACT = 0.9
TITLE_ONLY = 0.7

# Only movie/series exist as tracker rows; season/episode content can never
# match a local catalog entry.
MATCHABLE_CONTENT_TYPES = (ContentType.MOVIE, ContentType.SERIES)


@dataclass(frozen=True)
class MatchCandidate:
    """One unambiguous local-catalog match, ready for MappingRepository.record."""

    tmdb_id: int
    confidence: float
    method: str


def match_local_catalog(content) -> Optional[MatchCandidate]:
    """Match one discovered provider content against local ``tracker.Movie`` rows.

    Deterministic tiers (first unambiguous winner):

    1. exact title (case-insensitive) + ``release_date.year``, resolving to a
       single distinct TMDb id -> ``TITLE_YEAR`` @ 0.9;
    2. exact title alone resolving to a single distinct TMDb id -> ``TITLE_YEAR``
       @ 0.7 (covers unknown provider years, ambiguous years, and series);
    3. otherwise ``None``: ambiguous or unknown titles are left unmapped.

    Candidates are always constrained by ``content_type`` (a series never
    matches a movie row) and uniqueness is judged on distinct TMDb ids, so two
    local rows pointing at the same TMDb id are not ambiguous.
    """
    title = (content.title or "").strip()
    if not title or content.content_type not in MATCHABLE_CONTENT_TYPES:
        return None

    year = content.release_date.year if content.release_date else None
    candidates = Movie.objects.filter(content_type=content.content_type, title__iexact=title)

    if year is not None:
        same_year = set(candidates.filter(year=year).values_list("tmdb_id", flat=True))
        if len(same_year) == 1:
            return MatchCandidate(
                tmdb_id=same_year.pop(),
                confidence=TITLE_YEAR_EXACT,
                method=MatchMethod.TITLE_YEAR,
            )

    by_title = set(candidates.values_list("tmdb_id", flat=True))
    if len(by_title) == 1:
        return MatchCandidate(
            tmdb_id=by_title.pop(),
            confidence=TITLE_ONLY,
            method=MatchMethod.TITLE_YEAR,
        )
    return None
