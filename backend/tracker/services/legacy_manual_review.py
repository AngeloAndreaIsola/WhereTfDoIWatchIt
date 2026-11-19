"""Stored-evidence legacy review; explicit lookup never collects provider data."""
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import caches
from django.db import transaction
from django.utils import timezone

from availability.domain.enums import MatchMethod
from availability.models import (
    CandidateReviewAudit, ContentMapping, ProviderContent, ProviderContentCandidate,
    TmdbSearchCache,
)
from availability.persistence.repositories import MappingRepository
from availability.services.candidate_gateway import CandidateDiscoveryBudget, TMDbCandidateGateway
from availability.services.write_barrier import writer_barrier
from tracker.models import Movie, ScrapedContentMapping
from .manual_review_policy import ManualReviewBlocked, require_manual_review, require_tmdb_review_grant
from .rate_limit import RateLimiter, RateLimitExceeded

MAX_CANDIDATES = 5


def _type(mapping):
    if mapping.scraped_content_type not in {"movie", "series"}:
        raise ManualReviewBlocked("manual_review_content_type_unknown")
    return mapping.scraped_content_type


def _content(mapping):
    return ProviderContent.objects.filter(
        provider_id=mapping.provider_id, provider_content_id=f"legacy-{mapping.pk}",
        content_type=_type(mapping),
    ).first()


def _same_title(left, right):
    return bool(isinstance(left, str) and isinstance(right, str) and left.strip() and right.strip()
                and TMDbCandidateGateway._normalize_query(left)
                == TMDbCandidateGateway._normalize_query(right))


def _candidates(raw, mapping, limit=MAX_CANDIDATES):
    """Untrusted cache/persisted suggestions never confer verified identity."""
    result, seen = [], set()
    for value in raw:
        if not isinstance(value, dict):
            continue
        identity = value.get("tmdb_id")
        if (type(identity) is not int or identity <= 0 or identity in seen
                or value.get("content_type") != _type(mapping)
                or not isinstance(value.get("title"), str) or not value["title"].strip()):
            continue
        year = value.get("year")
        if year is not None and (type(year) is not int or not 1870 <= year <= 2100):
            continue
        seen.add(identity)
        result.append({"tmdb_id": identity, "title": value["title"], "year": year,
                       "content_type": value["content_type"],
                       "confidence_score": value.get("confidence_score", 0.0)})
        if limit is not None and len(result) == limit:
            break
    return result


def stored_review(mapping):
    """Read only: no gateway dispatch, writes, cache refresh or projection."""
    try:
        require_manual_review(mapping.provider)
        manual = {"allowed": True, "code": None}
    except ManualReviewBlocked as exc:
        manual = {"allowed": False, "code": exc.code}
    try:
        require_tmdb_review_grant(mapping.provider)
        lookup = {"allowed": True, "code": None}
    except ManualReviewBlocked as exc:
        lookup = {"allowed": False, "code": exc.code}
    try:
        kind = _type(mapping)
    except ManualReviewBlocked as exc:
        return {"candidates": [], "candidate_state": "blocked",
                "candidate_search": {"allowed": False, "code": exc.code},
                "manual_review": {"allowed": False, "code": exc.code},
                "expected_updated_at": mapping.updated_at.isoformat()}
    now = timezone.now()
    entry = TmdbSearchCache.objects.filter(
        query=TMDbCandidateGateway._normalize_query(mapping.scraped_title),
        year_fingerprint=TMDbCandidateGateway._year_fingerprint(mapping.scraped_year),
        content_type=kind, locale="en-US", endpoint_version="v3",
    ).first()
    candidates, state, stale, ambiguous = [], "not_searched", False, False
    if entry:
        stale = entry.expires_at <= now
        if stale:
            state = "stale"
        elif entry.is_negative and not entry.results:
            state = "no_results"
        else:
            raw = entry.results if isinstance(entry.results, list) else []
            all_candidates = _candidates(raw, mapping, limit=None)
            exact = {c["tmdb_id"] for c in all_candidates if _same_title(c["title"], mapping.scraped_title)
                     and c["year"] == mapping.scraped_year}
            ambiguous = len(exact) > 1 or any(isinstance(v, dict) and v.get("search_truncated") for v in raw)
            candidates = all_candidates[:MAX_CANDIDATES]
            state = "cached" if candidates else "stale"
    # Persisted scoring records need current-input evidence and a bounded age.
    if not candidates and state != "no_results":
        content = _content(mapping)
        if content:
            values = []
            for candidate in ProviderContentCandidate.objects.filter(
                provider_content=content, content_type=kind,
            ).exclude(review_state="rejected").order_by("-confidence_score", "pk"):
                evidence = candidate.scoring_evidence if isinstance(candidate.scoring_evidence, dict) else {}
                if (min(candidate.created_at, candidate.updated_at) <= now - timedelta(days=30)
                        or not _same_title(evidence.get("content_title"), mapping.scraped_title)
                        or evidence.get("content_type") != kind
                        or evidence.get("content_year") != mapping.scraped_year):
                    stale = True
                    continue
                ambiguous = ambiguous or candidate.review_state in {"ambiguous", "conflict"}
                values.append({"tmdb_id": candidate.tmdb_id, "title": candidate.title,
                               "year": candidate.year, "content_type": candidate.content_type,
                               "confidence_score": candidate.confidence_score})
            all_candidates = _candidates(values, mapping, limit=None)
            exact = {c["tmdb_id"] for c in all_candidates if _same_title(c["title"], mapping.scraped_title)
                     and c["year"] == mapping.scraped_year}
            ambiguous = ambiguous or len(exact) > 1
            candidates = all_candidates[:MAX_CANDIDATES]
            if candidates:
                state = "persisted"
            elif stale:
                state = "stale"
    if state == "not_searched" and not lookup["allowed"]:
        # Preserve both facts: no evidence and no authority to obtain it.
        lookup["state"] = "blocked"
    return {"candidates": candidates, "candidate_state": state,
            "candidate_search": lookup, "manual_review": manual, "candidate_search_ambiguous": ambiguous,
            "expected_updated_at": mapping.updated_at.isoformat()}


def _shared_control_cache():
    """A live grant also needs a shared cache; local-memory cache cannot pace workers."""
    config = settings.CACHES.get("manual_review", {})
    if config.get("BACKEND") not in {
        "django.core.cache.backends.db.DatabaseCache", "django.core.cache.backends.redis.RedisCache",
    }:
        raise ManualReviewBlocked("tmdb_review_shared_controls_required", 403)
    return caches["manual_review"]


def explicit_lookup(mapping, tmdb_id=None):
    grant = require_tmdb_review_grant(mapping.provider)  # before cache/quota writes or HTTP
    kind = _type(mapping)
    if tmdb_id is not None and (type(tmdb_id) is not int or tmdb_id <= 0):
        raise ManualReviewBlocked("invalid_tmdb_id", 400)
    control = _shared_control_cache()
    lock_key, wait_key = "tmdb-manual-review:lock", "tmdb-manual-review:next"
    if not control.add(lock_key, True, timeout=60):
        raise ManualReviewBlocked("tmdb_review_busy", 429)
    try:
        if control.get(wait_key, 0) > timezone.now().timestamp():
            raise ManualReviewBlocked("tmdb_review_cooldown", 429)
        limiter = RateLimiter("tmdb_manual_review", daily_limit=grant.daily_limit,
                              monthly_limit=grant.monthly_limit)

        class ReviewBudget(CandidateDiscoveryBudget):
            def reserve(self, label=""):
                require_tmdb_review_grant(mapping.provider)
                super().reserve(label)
                limiter.acquire()
                control.set(wait_key, timezone.now().timestamp() + grant.min_interval, timeout=86400)

        gateway = TMDbCandidateGateway(budget=ReviewBudget(limit=1),
                                       min_request_interval_seconds=grant.min_interval)
        gateway.max_retries, gateway.stop_on_rate_limit = 0, True
        gateway.session.trust_env = False
        if not gateway.client.api_key:
            gateway.session.close()
            raise ManualReviewBlocked("tmdb_review_credentials_missing", 403)
        try:
            if tmdb_id is None:
                gateway.search(mapping.scraped_title, year=mapping.scraped_year,
                               content_type=kind, authorize_network=True)
            else:
                details = gateway.get_details(tmdb_id, content_type=kind, authorize_network=True)
        except RateLimitExceeded:
            raise ManualReviewBlocked("tmdb_review_quota_exhausted", 429) from None
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 429:
                wait = gateway._parse_retry_after(response.headers.get("Retry-After"))
                control.set(wait_key, timezone.now().timestamp() + max(300, wait or 0),
                            timeout=max(86400, int(wait or 0) + 1))
                raise ManualReviewBlocked("tmdb_review_cooldown", 429) from None
            raise ManualReviewBlocked("tmdb_review_lookup_failed", 502) from None
        finally:
            gateway.session.close()
        result = stored_review(mapping)
        result["details_cached"] = tmdb_id is not None and details is not None
        result["budget"] = gateway.budget.to_dict()
        return result
    finally:
        control.delete(lock_key)


def confirm_mapping(mapping_id, tmdb_id, content_type=None, expected_updated_at=None):
    """Explicit, cache-only acceptance; never fetch, enrich, collect or enqueue."""
    if type(tmdb_id) is not int or tmdb_id <= 0:
        raise ManualReviewBlocked("invalid_tmdb_id", 400)
    with writer_barrier("legacy manual review"), transaction.atomic():
        mapping = ScrapedContentMapping.objects.select_for_update().get(pk=mapping_id)
        require_manual_review(mapping.provider)
        kind = _type(mapping)
        if content_type is not None and content_type != kind:
            raise ManualReviewBlocked("manual_review_content_type_conflict")
        if expected_updated_at and mapping.updated_at.isoformat() != expected_updated_at:
            raise ManualReviewBlocked("manual_review_stale_observation")
        already_confirmed = bool(mapping.is_confirmed and mapping.movie_id)
        if mapping.movie_id:
            target = (mapping.movie.tmdb_id, mapping.movie.content_type)
            if target != (tmdb_id, kind):
                raise ManualReviewBlocked("manual_review_existing_mapping_conflict")
        content = _content(mapping)
        if not content:
            raise ManualReviewBlocked("manual_review_projection_required")
        content = ProviderContent.objects.select_for_update().get(pk=content.pk)
        if (not content.source_id or not _same_title(content.title, mapping.scraped_title)
                or (content.release_date.year if content.release_date else None) != mapping.scraped_year):
            raise ManualReviewBlocked("manual_review_projection_required")
        existing = ContentMapping.objects.select_for_update().filter(
            provider_id=mapping.provider_id, provider_content_id=content.provider_content_id,
        ).first()
        if existing and (existing.tmdb_id, existing.content_type) != (tmdb_id, kind):
            raise ManualReviewBlocked("manual_review_canonical_mapping_conflict")
        selected = ProviderContentCandidate.objects.filter(provider_content=content, tmdb_id=tmdb_id).first()
        if selected and selected.content_type != kind:
            raise ManualReviewBlocked("manual_review_candidate_type_conflict")
        if selected and selected.review_state == "rejected":
            raise ManualReviewBlocked("manual_review_rejected_candidate")
        if already_confirmed:
            return mapping  # preserve saved decision without new writes or cache/network access
        if ProviderContentCandidate.objects.filter(
                provider_content=content, tmdb_id=tmdb_id, content_type=kind,
                review_state__in=("rejected", "conflict", "ambiguous")).exists():
            raise ManualReviewBlocked("manual_review_candidate_decision_conflict")
        details = TMDbCandidateGateway(allow_cache_writes=False).get_details(
            tmdb_id, content_type=kind, authorize_network=False,
        )
        if (not isinstance(details, dict) or type(details.get("tmdb_id")) is not int
                or details["tmdb_id"] != tmdb_id or details.get("content_type") != kind):
            raise ManualReviewBlocked("manual_review_fresh_details_required")
        if (not any(_same_title(mapping.scraped_title, details.get(key))
                    for key in ("title", "original_title"))
                or mapping.scraped_year is None or details.get("year") != mapping.scraped_year):
            raise ManualReviewBlocked("manual_review_identity_conflict")
        # Duplicate exact title/year candidates remain a review conflict, not a guess.
        review = stored_review(mapping)
        suggestions = review["candidates"]
        exact = {c["tmdb_id"] for c in suggestions
                 if _same_title(c["title"], mapping.scraped_title) and c["year"] == mapping.scraped_year}
        if len(exact) > 1 or review.get("candidate_search_ambiguous"):
            raise ManualReviewBlocked("manual_review_ambiguous_identity")
        movie = Movie.objects.filter(tmdb_id=tmdb_id, content_type=kind).first()
        if movie and (not _same_title(movie.title, details["title"]) or movie.year != details["year"]):
            raise ManualReviewBlocked("manual_review_canonical_metadata_conflict")
        if movie is None:
            movie = Movie.objects.create(tmdb_id=tmdb_id, content_type=kind,
                                         title=details["title"], year=details["year"])
        previous_revision = existing.revision if existing else None
        canonical, _ = MappingRepository().record(
            provider=mapping.provider, source=content.source,
            provider_content_id=content.provider_content_id, content_type=kind,
            tmdb_id=tmdb_id, confidence=1.0, match_method=MatchMethod.MANUAL, content=content,
        )
        if canonical.match_method != MatchMethod.MANUAL:
            canonical.match_method = MatchMethod.MANUAL
            canonical.revision += 1
            canonical.save(update_fields=["match_method", "revision", "updated_at"])
        mapping.movie, mapping.is_confirmed, mapping.confidence_score = movie, True, 1.0
        mapping.save(update_fields=["movie", "is_confirmed", "confidence_score", "updated_at"])
        CandidateReviewAudit.objects.create(
            provider_content=content, candidate_tmdb_id=tmdb_id, action="accept",
            reason="Explicit legacy manual confirmation from fresh cached identity evidence",
            previous_review_state="pending", resulting_review_state="accepted",
            expected_revision=previous_revision,
            resulting_revision=canonical.revision, verified_tmdb_id=tmdb_id,
            verified_content_type=kind, verification_provenance="legacy_cached_details",
        )
        return mapping
