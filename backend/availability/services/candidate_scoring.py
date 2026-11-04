"""Deterministic scoring and categorization engine for ProviderContent TMDb candidates."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..domain.enums import CandidateResolutionCategory, CandidateReviewState, ContentType
from ..models import ProviderContent, ProviderContentCandidate

logger = logging.getLogger(__name__)

# Scoring constants
CONFIDENCE_THRESHOLD = 0.6
HIGH_CONFIDENCE_THRESHOLD = 0.8
TIE_MARGIN = 0.05


def normalize_title(value: Optional[str]) -> str:
    """Case, punctuation, and whitespace-insensitive alphanumeric title form."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def is_token_run(shorter: list[str], longer: list[str]) -> bool:
    """True when *shorter* appears as a contiguous word sequence in *longer*."""
    if not shorter or len(shorter) > len(longer):
        return False
    return any(
        longer[start : start + len(shorter)] == shorter
        for start in range(len(longer) - len(shorter) + 1)
    )


def compute_title_relation(left: str, right: str) -> str:
    """Compute the strongest structural relationship between two titles.

    Returns: 'exact', 'contains', 'overlap', 'weak_overlap', or 'none'.
    """
    norm_left = normalize_title(left)
    norm_right = normalize_title(right)
    if not norm_left or not norm_right:
        return "none"
    if norm_left == norm_right:
        return "exact"

    words_left = norm_left.split()
    words_right = norm_right.split()

    if is_token_run(words_left, words_right) or is_token_run(words_right, words_left):
        return "contains"

    common_words = set(words_left) & set(words_right)
    if not common_words:
        return "none"

    overlap = len(common_words) / max(len(set(words_left)), len(set(words_right)))
    if overlap >= 0.5:
        return "overlap"
    return "weak_overlap"


def score_candidate(content: ProviderContent, raw_item: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Deterministically score a TMDb search result against a ProviderContent entry.

    Compares localized and original titles, applies year agreement or conflict penalties,
    and returns (confidence_score, scoring_evidence).
    """
    evidence: dict[str, Any] = {
        "content_title": content.title,
        "content_type": content.content_type,
        "candidate_title": raw_item.get("title") or "",
        "candidate_original_title": raw_item.get("original_title") or "",
        "candidate_tmdb_id": raw_item.get("tmdb_id"),
    }

    primary_title = raw_item.get("title") or ""
    original_title = raw_item.get("original_title") or ""

    meta = content.metadata or {}
    alternate_titles = [content.title]
    if isinstance(meta.get("original_title"), str):
        alternate_titles.append(meta["original_title"])
    if isinstance(meta.get("localized_titles"), list):
        alternate_titles.extend(t for t in meta["localized_titles"] if isinstance(t, str))

    best_relation = "none"
    matched_target = "title"

    for alt_title in alternate_titles:
        rel_primary = compute_title_relation(alt_title, primary_title)
        if rel_primary == "exact":
            best_relation = "exact"
            matched_target = "primary_title"
            break
        rel_orig = compute_title_relation(alt_title, original_title)
        if rel_orig == "exact":
            best_relation = "exact"
            matched_target = "original_title"
            break

        priority = {"contains": 4, "overlap": 3, "weak_overlap": 2, "none": 1}
        for rel, target in ((rel_primary, "primary_title"), (rel_orig, "original_title")):
            if priority.get(rel, 1) > priority.get(best_relation, 1):
                best_relation = rel
                matched_target = target

    evidence["title_relation"] = best_relation
    evidence["matched_target"] = matched_target

    title_score_map = {
        "exact": 0.6,
        "contains": 0.4,
        "overlap": 0.35,
        "weak_overlap": 0.2,
        "none": 0.0,
    }
    score = title_score_map.get(best_relation, 0.0)

    content_year = content.release_date.year if content.release_date else None
    cand_year = raw_item.get("year")
    evidence["content_year"] = content_year
    evidence["candidate_year"] = cand_year

    if content_year is not None and cand_year is not None:
        year_diff = abs(content_year - cand_year)
        evidence["year_diff"] = year_diff
        if year_diff == 0:
            score += 0.4
            evidence["year_match"] = "exact"
        elif year_diff == 1:
            score += 0.2
            evidence["year_match"] = "adjacent"
        else:
            if content.content_type == ContentType.MOVIE and year_diff > 1:
                score = max(0.0, score - 0.3)
                evidence["year_match"] = "conflict"
            else:
                evidence["year_match"] = "discrepancy"
    elif content_year is None:
        score += 0.2
        evidence["year_match"] = "unknown_content_year"
    else:
        evidence["year_match"] = "unknown_candidate_year"

    final_score = round(min(1.0, max(0.0, score)), 4)
    evidence["final_score"] = final_score
    return final_score, evidence


def categorize_candidates(
    content: ProviderContent,
    scored_candidates: list[dict[str, Any]],
) -> str:
    """Categorize discovery result into resolution categories.

    Returns one of:
    - NO_RESULTS
    - UNIQUE_CANDIDATE
    - AMBIGUOUS
    - CONFLICT
    """
    if not scored_candidates:
        return CandidateResolutionCategory.NO_RESULTS

    sorted_cands = sorted(
        scored_candidates,
        key=lambda c: c["confidence_score"],
        reverse=True,
    )
    top = sorted_cands[0]
    top_score = top["confidence_score"]

    if top_score < CONFIDENCE_THRESHOLD:
        evidence = top.get("scoring_evidence", {})
        if evidence.get("year_match") == "conflict":
            return CandidateResolutionCategory.CONFLICT
        return CandidateResolutionCategory.CONFLICT

    distinct_top_ids = {
        c["tmdb_id"]
        for c in sorted_cands
        if abs(c["confidence_score"] - top_score) <= TIE_MARGIN
    }

    if len(distinct_top_ids) > 1:
        return CandidateResolutionCategory.AMBIGUOUS

    evidence = top.get("scoring_evidence", {})
    if evidence.get("title_relation") in ("exact", "contains") and top_score >= CONFIDENCE_THRESHOLD:
        return CandidateResolutionCategory.UNIQUE_CANDIDATE

    return CandidateResolutionCategory.AMBIGUOUS


def persist_candidates(
    content: ProviderContent,
    scored_candidates: list[dict[str, Any]],
    resolution_category: str,
) -> list[ProviderContentCandidate]:
    """Persist ProviderContentCandidate records while preserving explicit rejections.

    - Updates existing records without overwriting REJECTED review_state and rejection_reason.
    - Creates new records for previously unseen candidates.
    """
    persisted: list[ProviderContentCandidate] = []

    for cand_dict in scored_candidates:
        tmdb_id = cand_dict["tmdb_id"]
        existing = ProviderContentCandidate.objects.filter(
            provider_content=content,
            tmdb_id=tmdb_id,
        ).first()

        if existing:
            if existing.review_state == CandidateReviewState.REJECTED:
                existing.confidence_score = cand_dict["confidence_score"]
                existing.scoring_evidence = cand_dict["scoring_evidence"]
                existing.save(update_fields=["confidence_score", "scoring_evidence", "updated_at"])
                persisted.append(existing)
                continue

            if existing.review_state == CandidateReviewState.PENDING:
                if resolution_category == CandidateResolutionCategory.UNIQUE_CANDIDATE:
                    if cand_dict["confidence_score"] >= HIGH_CONFIDENCE_THRESHOLD:
                        existing.review_state = CandidateReviewState.ACCEPTED
                    else:
                        existing.review_state = CandidateReviewState.PENDING
                elif resolution_category == CandidateResolutionCategory.AMBIGUOUS:
                    existing.review_state = CandidateReviewState.AMBIGUOUS
                elif resolution_category == CandidateResolutionCategory.CONFLICT:
                    existing.review_state = CandidateReviewState.CONFLICT

            existing.resolution_category = resolution_category
            existing.confidence_score = cand_dict["confidence_score"]
            existing.scoring_evidence = cand_dict["scoring_evidence"]
            existing.title = cand_dict.get("title", existing.title)
            existing.year = cand_dict.get("year", existing.year)
            existing.save()
            persisted.append(existing)
        else:
            review_state = CandidateReviewState.PENDING
            if resolution_category == CandidateResolutionCategory.UNIQUE_CANDIDATE:
                if cand_dict["confidence_score"] >= HIGH_CONFIDENCE_THRESHOLD:
                    review_state = CandidateReviewState.ACCEPTED
                else:
                    review_state = CandidateReviewState.PENDING
            elif resolution_category == CandidateResolutionCategory.AMBIGUOUS:
                review_state = CandidateReviewState.AMBIGUOUS
            elif resolution_category == CandidateResolutionCategory.CONFLICT:
                review_state = CandidateReviewState.CONFLICT

            created = ProviderContentCandidate.objects.create(
                provider_content=content,
                tmdb_id=tmdb_id,
                title=cand_dict.get("title") or "",
                year=cand_dict.get("year"),
                content_type=cand_dict.get("content_type") or content.content_type,
                confidence_score=cand_dict["confidence_score"],
                scoring_evidence=cand_dict["scoring_evidence"],
                review_state=review_state,
                resolution_category=resolution_category,
                rejection_reason="",
            )
            persisted.append(created)

    return persisted
