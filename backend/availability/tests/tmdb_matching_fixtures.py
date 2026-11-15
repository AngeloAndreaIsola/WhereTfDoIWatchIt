"""Synthetic TMDb matching fixtures for testing identity resolution.

Every TMDb id here is synthetic (900_xxx range) unless the existing test suite
already uses a real one (389 for 12 Angry Men, 550 for Fight Club).  Provider
content ids use a "syn-" prefix to stay clearly synthetic.

The fixtures are plain Python dicts and SimpleNamespace objects — no network,
no Django model creation.  Test classes that need ORM rows call the factory
helpers to build them from these constants.

Fixture catalogue (grouped by scenario they exercise):
─────────────────────────────────────────────────────────
  EXACT_MATCH         exact title + year → accept
  TRANSLATED_TITLE    translated vs original title
  DUPLICATE_TITLES    two local movies, same title, different years
  MISSING_YEAR        provider year unknown
  CONFLICTING_YEAR    provider year contradicts every local row
  SAME_MOVIE_TV       same title used for a movie and a series
  RAI_SINGLE_MULTI    RaiPlay single/multi classification ambiguity
  SERIES_UNRELIABLE_YEAR  series whose provider year is a re-add date
  NO_CANDIDATE        no local catalog Row exists at all
  REVIEW_REQUIRED     ambiguous – two distinct tmdb_ids share title+year
  MANUAL_CONFLICT     existing manual mapping disagrees with a new candidate
  REJECTED_REAPPEAR   previously rejected candidate surfaces again
  TMDB_429_TIMEOUT    TMDb rate-limit / timeout / cache expiry
  MAPPING_NO_OFFER    mapping exists but no Offer row yet
  CORRECTION_EVIDENCE mapping correction with former-target OfferEvidence/Price
  EPISODE_EVIDENCE    episode-level evidence that cannot generalize to series
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Synthetic TMDb IDs (900_xxx range, never collide with real TMDb)
# ---------------------------------------------------------------------------
TMDB_GODFATHER = 900_001          # movie
TMDB_GODFATHER_PART_II = 900_002  # movie, same-title variant
TMDB_PADRINO = 900_003            # Italian-titled Movie row for translated test
TMDB_DARK_MOVIE = 900_010         # "Dark" as a movie
TMDB_DARK_SERIES = 900_011        # "Dark" as a series
TMDB_AMBIGUOUS_A = 900_020        # duplicate title, year 2018
TMDB_AMBIGUOUS_B = 900_021        # duplicate title, year 2018 (different film)
TMDB_ORPHAN = 900_030             # movie with no provider counterpart
TMDB_CORRECTION_OLD = 900_040     # former target of a corrected mapping
TMDB_CORRECTION_NEW = 900_041     # correct target after mapping correction
TMDB_EPISODE_SERIES = 900_050     # series whose episode evidence can't generalize


# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------
PROVIDER_SLUG_RAIPLAY = "raiplay"
PROVIDER_SLUG_TUBI = "tubi"
PROVIDER_SLUG_SYNTHETIC = "syn-provider"
SOURCE_SLUG_WEB = "syn-web"
SOURCE_SLUG_API = "syn-api"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns(**kwargs):
    """Shorthand for SimpleNamespace."""
    return SimpleNamespace(**kwargs)


def discovered_content(
    pcid: str,
    title: str,
    content_type: str = "movie",
    year: int | None = None,
    original_title: str = "",
    provider_id: str = PROVIDER_SLUG_SYNTHETIC,
    external_ids: dict | None = None,
):
    """Build a DiscoveredContent-shaped namespace (matches domain/models.py)."""
    return _ns(
        provider_id=provider_id,
        provider_content_id=pcid,
        content_type=content_type,
        title=title,
        original_title=original_title,
        release_date=date(year, 1, 1) if year else None,
        season_number=None,
        episode_number=None,
        duration=None,
        url="",
        poster="",
        description="",
        external_ids=external_ids or {},
        source_id="",
    )


def provider_offer(
    pcid: str,
    country: str = "IT",
    content_type: str = "movie",
    tmdb_id: int | None = None,
    offer_type: str = "subscription",
    provider_id: str = PROVIDER_SLUG_SYNTHETIC,
    season_number: int | None = None,
    episode_number: int | None = None,
    quality: str = "",
    audio_languages: list[str] | None = None,
    subtitle_languages: list[str] | None = None,
    price_amount: Decimal | None = None,
    price_currency: str = "",
):
    """Build a ProviderOffer-shaped namespace."""
    return _ns(
        provider_id=provider_id,
        provider_content_id=pcid,
        country=country,
        content_type=content_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
        offer_type=offer_type,
        provider_url="",
        quality=quality,
        audio_languages=audio_languages,
        subtitle_languages=subtitle_languages,
        available_from=None,
        available_until=None,
        source_record_id="",
        source_version="",
        price_amount=price_amount,
        price_currency=price_currency,
        price_error="",
    )


# ======================================================================
# Scenario 1: EXACT TITLE + YEAR MATCH
# ======================================================================
EXACT_MATCH = {
    "label": "exact_title_year",
    "local_movies": [
        {"tmdb_id": TMDB_GODFATHER, "title": "The Godfather", "year": 1972, "content_type": "movie"},
    ],
    "provider_content": discovered_content("syn-gf-001", "The Godfather", year=1972),
    "expected": {
        "decision": "candidate_discovered",
        "tmdb_id": TMDB_GODFATHER,
        "confidence": 0.9,
        "method": "title_year",
    },
}

# ======================================================================
# Scenario 2: TRANSLATED vs. ORIGINAL TITLE
# ======================================================================
TRANSLATED_TITLE = {
    "label": "translated_vs_original_title",
    "description": (
        "The local catalog stores the English title 'The Godfather'; the provider "
        "reports the Italian title 'Il Padrino'.  Unless original_title is checked "
        "or a separate Italian-titled Movie row exists, no match should resolve."
    ),
    "local_movies": [
        {"tmdb_id": TMDB_GODFATHER, "title": "The Godfather", "year": 1972, "content_type": "movie"},
    ],
    "provider_content": discovered_content("syn-padrino-001", "Il Padrino", year=1972),
    "expected": {
        "decision": "no_candidate",
        "tmdb_id": None,
        "reason": "title_mismatch_translation",
    },
    "variant_with_local_translated_row": {
        "extra_local_movies": [
            {"tmdb_id": TMDB_GODFATHER, "title": "Il Padrino", "year": 1972, "content_type": "movie"},
        ],
        "expected": {
            "decision": "candidate_discovered",
            "tmdb_id": TMDB_GODFATHER,
            "confidence": 0.9,
            "method": "title_year",
            "note": "Two Movie rows with same tmdb_id are not ambiguous — same identity.",
        },
    },
}

# ======================================================================
# Scenario 3: DUPLICATE TITLES (two distinct films, same title+year)
# ======================================================================
DUPLICATE_TITLES = {
    "label": "duplicate_titles",
    "local_movies": [
        {"tmdb_id": TMDB_AMBIGUOUS_A, "title": "Solstice", "year": 2018, "content_type": "movie"},
        {"tmdb_id": TMDB_AMBIGUOUS_B, "title": "Solstice", "year": 2018, "content_type": "movie"},
    ],
    "provider_content": discovered_content("syn-solstice-001", "Solstice", year=2018),
    "expected": {
        "decision": "candidate_review_required",
        "tmdb_id": None,
        "reason": "ambiguous_multiple_distinct_tmdb_ids",
    },
}

# ======================================================================
# Scenario 4: MISSING YEAR
# ======================================================================
MISSING_YEAR = {
    "label": "missing_year",
    "local_movies": [
        {"tmdb_id": TMDB_GODFATHER, "title": "The Godfather", "year": 1972, "content_type": "movie"},
    ],
    "provider_content": discovered_content("syn-gf-noyear", "The Godfather", year=None),
    "expected": {
        "decision": "candidate_discovered",
        "tmdb_id": TMDB_GODFATHER,
        "confidence": 0.7,
        "method": "title_year",
        "note": "Title-only fallback at reduced confidence because year is unknown.",
    },
}

# ======================================================================
# Scenario 5: CONFLICTING YEAR (movie — must NOT fall back)
# ======================================================================
CONFLICTING_YEAR = {
    "label": "conflicting_year",
    "local_movies": [
        {"tmdb_id": TMDB_GODFATHER, "title": "The Godfather", "year": 1972, "content_type": "movie"},
    ],
    "provider_content": discovered_content("syn-gf-wrong-year", "The Godfather", year=2005),
    "expected": {
        "decision": "no_candidate",
        "tmdb_id": None,
        "reason": "year_conflict_movie",
        "note": "A movie with a known but non-matching year is evidence against, not ambiguity.",
    },
}

# ======================================================================
# Scenario 6: SAME MOVIE / TV TITLE
# ======================================================================
SAME_MOVIE_TV = {
    "label": "same_movie_tv_title",
    "local_movies": [
        {"tmdb_id": TMDB_DARK_MOVIE, "title": "Dark", "year": 2005, "content_type": "movie"},
        {"tmdb_id": TMDB_DARK_SERIES, "title": "Dark", "year": 2017, "content_type": "series"},
    ],
    "provider_content_movie": discovered_content("syn-dark-m", "Dark", content_type="movie", year=2005),
    "provider_content_series": discovered_content("syn-dark-s", "Dark", content_type="series", year=2017),
    "expected_movie": {
        "decision": "candidate_discovered",
        "tmdb_id": TMDB_DARK_MOVIE,
        "confidence": 0.9,
        "method": "title_year",
        "note": "Content_type constraint isolates the movie from the series.",
    },
    "expected_series": {
        "decision": "candidate_discovered",
        "tmdb_id": TMDB_DARK_SERIES,
        "confidence": 0.9,
        "method": "title_year",
    },
}

# ======================================================================
# Scenario 7: RaiPlay SINGLE / MULTI CLASSIFICATION AMBIGUITY
# ======================================================================
RAI_SINGLE_MULTI = {
    "label": "raiplay_single_multi_ambiguity",
    "description": (
        "A RaiPlay programme with layout='single' should be classified as 'movie'; "
        "layout='multi' as 'series'.  When a programme changes layout between "
        "crawls, the content_type must not silently flip."
    ),
    "programme_single": {
        "pcid": "ContentItem-syn-single-001",
        "title": "Follemente Synthetic",
        "layout": "single",
        "year": 2024,
        "expected_content_type": "movie",
    },
    "programme_multi": {
        "pcid": "ContentItem-syn-multi-001",
        "title": "Normal People Synthetic",
        "layout": "multi",
        "year": 2020,
        "expected_content_type": "series",
    },
    "programme_ambiguous": {
        "pcid": "ContentItem-syn-flip-001",
        "title": "Ambiguous Programme",
        "crawl_1_layout": "single",
        "crawl_2_layout": "multi",
        "expected": {
            "decision": "candidate_review_required",
            "reason": "content_type_flip_single_to_multi",
        },
    },
}

# ======================================================================
# Scenario 8: SERIES WITH UNRELIABLE RELEASE YEAR
# ======================================================================
SERIES_UNRELIABLE_YEAR = {
    "label": "series_unreliable_release_year",
    "description": (
        "Series provider year may be a catalog re-add date (2024) rather than "
        "first-air year (2017).  The matcher should still fall back to title-only "
        "for series, unlike movies."
    ),
    "local_movies": [
        {"tmdb_id": TMDB_DARK_SERIES, "title": "Dark", "year": 2017, "content_type": "series"},
    ],
    "provider_content": discovered_content(
        "syn-dark-readd", "Dark", content_type="series", year=2024,
    ),
    "expected": {
        "decision": "candidate_discovered",
        "tmdb_id": TMDB_DARK_SERIES,
        "confidence": 0.7,
        "method": "title_year",
        "note": "Series keeps title-only fallback even with a conflicting year.",
    },
}

# ======================================================================
# Scenario 9: NO CANDIDATE
# ======================================================================
NO_CANDIDATE = {
    "label": "no_candidate",
    "local_movies": [],
    "provider_content": discovered_content("syn-unknown-001", "Totally Unknown Film", year=2023),
    "expected": {
        "decision": "no_candidate",
        "tmdb_id": None,
        "reason": "no_local_catalog_match",
    },
}

# ======================================================================
# Scenario 10: REVIEW-REQUIRED CANDIDATE
# ======================================================================
REVIEW_REQUIRED = {
    "label": "review_required",
    "description": (
        "Title-only match but two distinct tmdb_ids share the title across years. "
        "Year is unknown so the matcher cannot disambiguate."
    ),
    "local_movies": [
        {"tmdb_id": TMDB_GODFATHER, "title": "The Godfather", "year": 1972, "content_type": "movie"},
        {"tmdb_id": TMDB_GODFATHER_PART_II, "title": "The Godfather", "year": 1974, "content_type": "movie"},
    ],
    "provider_content": discovered_content("syn-gf-ambig", "The Godfather", year=None),
    "expected": {
        "decision": "candidate_review_required",
        "tmdb_id": None,
        "reason": "ambiguous_multiple_distinct_tmdb_ids",
    },
}

# ======================================================================
# Scenario 11: MANUAL MAPPING CONFLICT
# ======================================================================
MANUAL_MAPPING_CONFLICT = {
    "label": "manual_mapping_conflict",
    "description": (
        "A MANUAL mapping already claims pcid 'syn-mapped-001' -> tmdb TMDB_GODFATHER. "
        "A new automatic candidate wants to map it to TMDB_GODFATHER_PART_II. "
        "The manual mapping must win; the automatic candidate must be rejected."
    ),
    "existing_mapping": {
        "provider_slug": PROVIDER_SLUG_SYNTHETIC,
        "provider_content_id": "syn-mapped-001",
        "content_type": "movie",
        "tmdb_id": TMDB_GODFATHER,
        "confidence": 1.0,
        "match_method": "manual",
    },
    "provider_content": discovered_content("syn-mapped-001", "The Godfather", year=1974),
    "new_automatic_candidate_tmdb_id": TMDB_GODFATHER_PART_II,
    "expected": {
        "decision": "mapping_accepted",
        "tmdb_id": TMDB_GODFATHER,
        "note": "Existing manual mapping preserved; automatic candidate discarded.",
    },
}

# ======================================================================
# Scenario 12: PREVIOUSLY REJECTED CANDIDATE REAPPEARS
# ======================================================================
REJECTED_REAPPEAR = {
    "label": "rejected_reappear",
    "description": (
        "A mapping for 'syn-rejected-001' was previously created and then deleted "
        "(or confidence set to 0).  The same provider content is rediscovered. "
        "The system must not silently re-create the mapping."
    ),
    "previously_rejected": {
        "provider_slug": PROVIDER_SLUG_SYNTHETIC,
        "provider_content_id": "syn-rejected-001",
        "content_type": "movie",
        "tmdb_id": TMDB_GODFATHER,
        "confidence": 0.0,
        "match_method": "title_year",
    },
    "provider_content": discovered_content("syn-rejected-001", "The Godfather", year=1972),
    "expected": {
        "decision": "candidate_review_required",
        "reason": "previously_rejected_mapping",
        "note": "Zero-confidence mapping must not be silently promoted by re-discovery.",
    },
}

# ======================================================================
# Scenario 13: TMDb 429 / TIMEOUT / CACHE EXPIRY
# ======================================================================
TMDB_RATE_LIMIT = {
    "label": "tmdb_429",
    "description": (
        "Simulates TMDb responding with 429 Too Many Requests.  The refresh must "
        "not crash; the content must be persisted as ProviderContent without a "
        "mapping, and the error must be recorded on the SyncRun."
    ),
    "api_response": {
        "status_code": 429,
        "headers": {"Retry-After": "2"},
        "body": {"status_message": "Your request count exceeds the allowed rate."},
    },
    "expected": {
        "provider_content_persisted": True,
        "mapping_created": False,
        "sync_run_error_recorded": True,
    },
}

TMDB_TIMEOUT = {
    "label": "tmdb_timeout",
    "description": "TMDb connection timeout — same expectations as 429.",
    "api_response": {
        "exception": "ConnectionTimeout",
        "message": "Connection to api.themoviedb.org timed out",
    },
    "expected": {
        "provider_content_persisted": True,
        "mapping_created": False,
        "sync_run_error_recorded": True,
    },
}

TMDB_CACHE_EXPIRY = {
    "label": "tmdb_cache_expiry",
    "description": (
        "A cached TMDb search result has expired (last_checked_at older than "
        "STALE_AFTER).  The system must attempt a fresh lookup, not silently "
        "serve stale data."
    ),
    "cached_result": {
        "tmdb_id": TMDB_GODFATHER,
        "title": "The Godfather",
        "last_checked_at": "2026-01-01T00:00:00Z",
        "stale_after_hours": 168,
    },
    "expected": {
        "refresh_attempted": True,
        "note": "If refresh fails, stale cache should still be usable as fallback.",
    },
}

# ======================================================================
# Scenario 14: MAPPING WITHOUT AN OFFER
# ======================================================================
MAPPING_NO_OFFER = {
    "label": "mapping_without_offer",
    "description": (
        "ContentMapping resolved pcid -> tmdb_id, but no provider-access "
        "observation exists yet (no Offer row).  The mapping itself is valid "
        "and should persist; the absence of an Offer is a separate concern."
    ),
    "mapping": {
        "provider_slug": PROVIDER_SLUG_RAIPLAY,
        "provider_content_id": "ContentItem-syn-noffer-001",
        "content_type": "movie",
        "tmdb_id": TMDB_GODFATHER,
        "confidence": 0.9,
        "match_method": "title_year",
    },
    "expected": {
        "mapping_exists": True,
        "offer_exists": False,
        "provider_content_exists": True,
        "note": "provider_access_observed is False — mapping is not an offer.",
    },
}

# ======================================================================
# Scenario 15: MAPPING CORRECTION WITH FORMER-TARGET EVIDENCE
# ======================================================================
CORRECTION_EVIDENCE = {
    "label": "mapping_correction_with_evidence",
    "description": (
        "A mapping was corrected from TMDB_CORRECTION_OLD to TMDB_CORRECTION_NEW. "
        "OfferEvidence rows and OfferPrice rows still reference the old Offer "
        "(which pointed at TMDB_CORRECTION_OLD).  The correction must not orphan "
        "evidence: old evidence stays linked to the old Offer, new observations "
        "create new Offer/evidence rows for the corrected target."
    ),
    "old_mapping": {
        "provider_slug": PROVIDER_SLUG_SYNTHETIC,
        "provider_content_id": "syn-corrected-001",
        "content_type": "movie",
        "tmdb_id": TMDB_CORRECTION_OLD,
        "confidence": 0.7,
        "match_method": "title_year",
    },
    "new_mapping": {
        "provider_slug": PROVIDER_SLUG_SYNTHETIC,
        "provider_content_id": "syn-corrected-001",
        "content_type": "movie",
        "tmdb_id": TMDB_CORRECTION_NEW,
        "confidence": 1.0,
        "match_method": "manual",
    },
    "old_offer": {
        "provider_slug": PROVIDER_SLUG_SYNTHETIC,
        "provider_content_id": "syn-corrected-001",
        "content_type": "movie",
        "tmdb_id": TMDB_CORRECTION_OLD,
        "country": "IT",
        "offer_type": "subscription",
        "currently_available": True,
    },
    "old_offer_evidence": {
        "source_slug": SOURCE_SLUG_WEB,
        "raw_title": "Corrected Movie",
        "raw_year": 2020,
        "raw_url": "https://syn-provider.example/corrected-movie",
        "observation_at": "2026-10-01T12:00:00Z",
    },
    "old_offer_price": {
        "source_slug": SOURCE_SLUG_WEB,
        "amount": Decimal("0.00"),
        "currency": "EUR",
    },
    "expected": {
        "old_offer_evidence_preserved": True,
        "old_offer_price_preserved": True,
        "old_offer_deactivated": True,
        "new_offer_tmdb_id": TMDB_CORRECTION_NEW,
        "new_offer_currently_available": True,
        "canonical_offer_persisted": True,
    },
}

# ======================================================================
# Scenario 16: EPISODE EVIDENCE ≠ SERIES EVIDENCE
# ======================================================================
EPISODE_EVIDENCE = {
    "label": "episode_evidence_not_generalizable",
    "description": (
        "A RaiPlay source observes one representative episode (S1E1) of a series. "
        "The evidence (duration, subtitles, quality, availability window) from "
        "that single episode cannot be generalized to the whole series. The system "
        "must create a SERIES-level Offer with minimal claims and NOT propagate "
        "episode-specific attributes."
    ),
    "provider_content": discovered_content(
        pcid="ContentItem-syn-episode-001",
        title="Normal People Synthetic",
        content_type="series",
        year=2020,
        provider_id=PROVIDER_SLUG_RAIPLAY,
    ),
    "representative_episode": {
        "pcid": "ContentItem-syn-episode-001",
        "season_number": 1,
        "episode_number": 1,
        "duration_minutes": 28,
        "quality": "hd",
        "subtitles": ["it"],
        "available_from": "2026-09-01T00:00:00Z",
        "available_until": "2027-03-01T00:00:00Z",
    },
    "local_movies": [
        {"tmdb_id": TMDB_EPISODE_SERIES, "title": "Normal People Synthetic", "year": 2020, "content_type": "series"},
    ],
    "expected": {
        "decision": "provider_access_observed",
        "offer_content_type": "series",
        "offer_season_number": None,
        "offer_episode_number": None,
        "offer_duration": None,
        "offer_quality": "",
        "offer_subtitles": None,
        "offer_available_from": None,
        "offer_available_until": None,
        "note": (
            "Episode-level facts stay on the episode observation; the series Offer "
            "only claims 'this provider has this series' without inheriting "
            "episode-specific attributes."
        ),
    },
}

# ======================================================================
# All scenarios, for iteration in test plans
# ======================================================================
ALL_SCENARIOS = [
    EXACT_MATCH,
    TRANSLATED_TITLE,
    DUPLICATE_TITLES,
    MISSING_YEAR,
    CONFLICTING_YEAR,
    SAME_MOVIE_TV,
    RAI_SINGLE_MULTI,
    SERIES_UNRELIABLE_YEAR,
    NO_CANDIDATE,
    REVIEW_REQUIRED,
    MANUAL_MAPPING_CONFLICT,
    REJECTED_REAPPEAR,
    TMDB_RATE_LIMIT,
    TMDB_TIMEOUT,
    TMDB_CACHE_EXPIRY,
    MAPPING_NO_OFFER,
    CORRECTION_EVIDENCE,
    EPISODE_EVIDENCE,
]
