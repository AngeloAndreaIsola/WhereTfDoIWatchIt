"""Backend services for verified candidate acceptance, rejection, and recovery capability inspection."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from tracker.models import Movie
from ..domain.enums import CandidateReviewState, ContentType, MatchMethod
from ..models import (
    CandidateReviewAudit,
    ContentMapping,
    ProviderContent,
    ProviderContentCandidate,
)
from ..persistence.repositories import MappingRepository
from ..sources.registry import source_registry
from .candidate_gateway import CandidateDiscoveryBudget, TMDbCandidateGateway
from .mapping_correction_impact import MappingCorrectionImpactPlanner
from .write_barrier import writer_barrier

logger = logging.getLogger(__name__)


class CandidateAcceptanceError(Exception):
    """Base exception for candidate acceptance failures."""

    def __init__(self, message: str, code: str, status_code: int = 400, extra: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.extra = extra or {}


def check_recovery_supported(content: ProviderContent) -> bool:
    """Determine whether targeted recovery is supported for this ProviderContent.

    Recovery requires:
    - Content has a valid provider and source.
    - Source is resolvable through the source registry and enabled.
    - Source implementation declares supports_targeted_evidence=True.
    - Content has a valid provider_content_id and provider_url suitable for targeted fetch.
    """
    if not content or not content.source or not content.source.enabled:
        return False

    if not content.provider_content_id or not content.provider_url:
        return False

    source_slug = content.source.slug
    try:
        source_instance = source_registry.get(source_slug)
    except Exception:
        return False

    capabilities = getattr(source_instance, "capabilities", None)
    if not capabilities or not getattr(capabilities, "supports_targeted_evidence", False):
        return False

    return True


def is_candidate_legacy_unverified(candidate: ProviderContentCandidate) -> bool:
    """A candidate is legacy_unverified if its review_state is ACCEPTED but has no human audit entry."""
    if candidate.review_state != CandidateReviewState.ACCEPTED:
        return False
    return not CandidateReviewAudit.objects.filter(
        provider_content=candidate.provider_content,
        candidate_tmdb_id=candidate.tmdb_id,
        action="accept",
    ).exists()


def safe_enrich_or_create_movie(
    *,
    tmdb_id: int,
    verified_type: str,
    details: dict[str, Any],
) -> Movie:
    """Safe local tracker.Movie creation or enrichment without flipping content_type.

    Movie and TV IDs use distinct namespaces; enrichment always selects both.
    """
    existing = Movie.objects.filter(tmdb_id=tmdb_id, content_type=verified_type).first()
    if existing:
        # Enrich without overwriting user state or downgrading quality fields
        update_fields = []
        if not existing.title and details.get("title"):
            existing.title = details["title"]
            update_fields.append("title")
        if existing.year is None and details.get("year") is not None:
            existing.year = details["year"]
            update_fields.append("year")
        if not existing.poster_path and details.get("poster_path"):
            existing.poster_path = details["poster_path"]
            update_fields.append("poster_path")
        if not existing.overview and details.get("overview"):
            existing.overview = details["overview"]
            update_fields.append("overview")
        if not existing.genres and details.get("genres"):
            existing.genres = details["genres"]
            update_fields.append("genres")
        if not existing.genre_ids and details.get("genre_ids"):
            existing.genre_ids = details["genre_ids"]
            update_fields.append("genre_ids")
        if not existing.cast and details.get("cast"):
            existing.cast = details["cast"]
            update_fields.append("cast")
        if not existing.director and details.get("director"):
            existing.director = details["director"]
            update_fields.append("director")
        if not existing.seasons_data and details.get("seasons_data"):
            existing.seasons_data = details["seasons_data"]
            update_fields.append("seasons_data")
        if existing.runtime_minutes is None and details.get("runtime_minutes") is not None:
            existing.runtime_minutes = details["runtime_minutes"]
            update_fields.append("runtime_minutes")
        if existing.tmdb_rating is None and details.get("tmdb_rating") is not None:
            existing.tmdb_rating = details["tmdb_rating"]
            update_fields.append("tmdb_rating")

        if update_fields:
            existing.save(update_fields=update_fields)
        return existing

    # Create new Movie row
    return Movie.objects.create(
        tmdb_id=tmdb_id,
        title=details.get("title") or "",
        year=details.get("year"),
        content_type=verified_type,
        poster_path=details.get("poster_path") or "",
        overview=details.get("overview") or "",
        genres=details.get("genres") or [],
        genre_ids=details.get("genre_ids") or [],
        cast=details.get("cast") or [],
        director=details.get("director") or [],
        seasons_data=details.get("seasons_data") or [],
        runtime_minutes=details.get("runtime_minutes"),
        tmdb_rating=details.get("tmdb_rating"),
    )


class CandidateAcceptanceService:
    """Orchestrates verified acceptance of discovered TMDb candidates."""

    def __init__(
        self,
        gateway: Optional[TMDbCandidateGateway] = None,
        mapping_repo: Optional[MappingRepository] = None,
    ):
        self.gateway = gateway or TMDbCandidateGateway()
        self.mapping_repo = mapping_repo or MappingRepository()

    def accept(
        self,
        *,
        provider_content_id: int,
        tmdb_id: int,
        reason: str,
        expected_revision: Any,
        has_expected_revision: bool = True,
        authorize_network: bool = False,
        max_http_requests: Optional[int] = None,
        reviewer_id: str = "local_operator",
        correlation_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Perform verified candidate acceptance.

        Steps:
        1. Input validation (reason, expected_revision check).
        2. Remote TMDb verification outside DB locks.
        3. Writer barrier and DB transaction with row locking.
        4. Stale revision & existing mapping conflict checks.
        5. Idempotent short-circuit if identical mapping already verified.
        6. Safe tracker.Movie enrichment/creation.
        7. Mapping creation/update via MappingRepository.record.
        8. Candidate review_state projection update & append-only audit record creation.
        """
        correlation_id = correlation_id or str(uuid.uuid4())
        reason = str(reason or "").strip()
        if not reason:
            raise CandidateAcceptanceError("A non-empty reason is required", code="REASON_REQUIRED", status_code=400)

        if not has_expected_revision:
            raise CandidateAcceptanceError(
                "Missing expected_revision. Must provide null for unmapped or integer revision for existing.",
                code="EXPECTED_REVISION_REQUIRED",
                status_code=400,
            )

        if expected_revision is not None:
            if not isinstance(expected_revision, int) or expected_revision <= 0:
                raise CandidateAcceptanceError(
                    f"expected_revision must be null or a positive integer, got {expected_revision!r}",
                    code="INVALID_EXPECTED_REVISION",
                    status_code=400,
                )

        try:
            content = ProviderContent.objects.select_related("provider", "source").get(id=provider_content_id)
        except ProviderContent.DoesNotExist:
            raise CandidateAcceptanceError(
                f"ProviderContent {provider_content_id} not found",
                code="PROVIDER_CONTENT_NOT_FOUND",
                status_code=404,
            )

        candidate = ProviderContentCandidate.objects.filter(
            provider_content=content,
            tmdb_id=tmdb_id,
        ).first()
        if not candidate:
            raise CandidateAcceptanceError(
                f"Candidate TMDb {tmdb_id} not found for ProviderContent {provider_content_id}",
                code="CANDIDATE_NOT_FOUND",
                status_code=404,
            )

        target_content_type = candidate.content_type or content.content_type
        if target_content_type not in (ContentType.MOVIE, ContentType.SERIES):
            target_content_type = ContentType.MOVIE

        # Step 2: Remote TMDb verification through finite budget gateway WITHOUT holding DB locks
        gateway = self.gateway
        if authorize_network:
            if not isinstance(max_http_requests, int) or max_http_requests <= 0:
                raise CandidateAcceptanceError(
                    "max_http_requests must be a positive integer when authorize_network is true",
                    code="INVALID_BUDGET",
                    status_code=400,
                )
            gateway = TMDbCandidateGateway(
                client=self.gateway.client,
                budget=CandidateDiscoveryBudget(limit=max_http_requests),
                session=self.gateway.session,
                sleep_fn=self.gateway.sleep_fn,
                allow_cache_writes=self.gateway.allow_cache_writes,
                locale=self.gateway.locale,
            )

        details = gateway.get_details(
            tmdb_id=tmdb_id,
            content_type=target_content_type,
            authorize_network=authorize_network,
        )

        if not details:
            raise CandidateAcceptanceError(
                f"Verification failed: TMDb {tmdb_id} details could not be verified (network authorization or cache required)",
                code="VERIFICATION_REQUIRED",
                status_code=400,
                extra={"tmdb_id": tmdb_id, "content_type": target_content_type},
            )

        # Exact verification of TMDb ID and media type
        verified_tmdb_id = details["tmdb_id"]
        verified_content_type = details["content_type"]
        if int(verified_tmdb_id) != int(tmdb_id):
            raise CandidateAcceptanceError(
                f"Verified TMDb ID mismatch: expected {tmdb_id}, got {verified_tmdb_id}",
                code="VERIFICATION_MISMATCH",
                status_code=400,
            )

        # Step 3: Mutating Phase inside writer barrier and transaction
        with writer_barrier("verified candidate acceptance"), transaction.atomic():
            # Lock ProviderContent row to serialize concurrent attempts.
            # Do NOT use select_related with nullable foreign keys (e.g. source) on select_for_update,
            # because Postgres raises NotSupportedError: FOR UPDATE cannot be applied to nullable side of outer join.
            locked_content = (
                ProviderContent.objects.select_for_update()
                .get(id=content.id)
            )

            # Re-fetch candidate under lock
            locked_cand = ProviderContentCandidate.objects.select_for_update().get(id=candidate.id)

            existing_mapping = (
                ContentMapping.objects.select_for_update()
                .filter(provider=locked_content.provider, provider_content_id=locked_content.provider_content_id)
                .first()
            )

            current_revision = existing_mapping.revision if existing_mapping else None

            # Concurrency / optimistic revision check
            if expected_revision != current_revision:
                raise CandidateAcceptanceError(
                    f"Stale mapping revision: expected {expected_revision}, current is {current_revision}",
                    code="STALE_MAPPING_REVISION",
                    status_code=409,
                    extra={
                        "expected_revision": expected_revision,
                        "current_revision": current_revision,
                    },
                )

            # Check if existing mapping points to a DIFFERENT TMDb target
            if existing_mapping and existing_mapping.tmdb_id != verified_tmdb_id:
                # Must NOT silently replace. Generate impact manifest and fail closed.
                planner = MappingCorrectionImpactPlanner(
                    provider=locked_content.provider,
                    provider_content_id=locked_content.provider_content_id,
                    proposed_tmdb_id=verified_tmdb_id,
                    proposed_content_type=verified_content_type,
                )
                manifest = planner.plan()
                raise CandidateAcceptanceError(
                    f"Accepting candidate would replace existing mapping to TMDb {existing_mapping.tmdb_id}. "
                    "Explicit correction authorization is required.",
                    code="CORRECTION_AUTHORIZATION_REQUIRED",
                    status_code=409,
                    extra={
                        "manifest": manifest,
                        "digest": manifest["digest"],
                        "current_revision": existing_mapping.revision,
                    },
                )

            # Safe local tracker.Movie creation or enrichment
            movie = safe_enrich_or_create_movie(
                tmdb_id=verified_tmdb_id,
                verified_type=verified_content_type,
                details=details,
            )

            # Idempotency check: if mapping already points to identical TMDb target and is already verified
            is_already_accepted = (
                existing_mapping is not None
                and existing_mapping.tmdb_id == verified_tmdb_id
                and existing_mapping.content_type == verified_content_type
                and locked_cand.review_state == CandidateReviewState.ACCEPTED
                and not is_candidate_legacy_unverified(locked_cand)
            )

            if is_already_accepted:
                # Return 200 idempotently without creating duplicate audit record
                return {
                    "status": "already_accepted",
                    "mapping": {
                        "id": existing_mapping.id,
                        "provider": existing_mapping.provider.slug,
                        "provider_content_id": existing_mapping.provider_content_id,
                        "tmdb_id": existing_mapping.tmdb_id,
                        "content_type": existing_mapping.content_type,
                        "revision": existing_mapping.revision,
                        "match_method": existing_mapping.match_method,
                    },
                    "candidate": {
                        "id": locked_cand.id,
                        "tmdb_id": locked_cand.tmdb_id,
                        "review_state": locked_cand.review_state,
                    },
                    "movie": {
                        "id": movie.id,
                        "tmdb_id": movie.tmdb_id,
                        "title": movie.title,
                        "content_type": movie.content_type,
                    },
                }

            prev_review_state = locked_cand.review_state

            # Record or advance mapping via MappingRepository
            mapping, _created = self.mapping_repo.record(
                provider=locked_content.provider,
                source=locked_content.source,
                provider_content_id=locked_content.provider_content_id,
                content_type=verified_content_type,
                tmdb_id=verified_tmdb_id,
                confidence=1.0,
                match_method=MatchMethod.MANUAL,
                content=locked_content,
            )

            # Ensure revision is at least 1 and re-fetch mapping state
            mapping.refresh_from_db()
            resulting_revision = mapping.revision

            # Update candidate review state projection
            locked_cand.review_state = CandidateReviewState.ACCEPTED
            locked_cand.save(update_fields=["review_state", "updated_at"])

            # Create append-only CandidateReviewAudit record
            audit = CandidateReviewAudit.objects.create(
                provider_content=locked_content,
                candidate_tmdb_id=verified_tmdb_id,
                action="accept",
                reviewer_id=reviewer_id,
                reason=reason,
                previous_review_state=prev_review_state,
                resulting_review_state=CandidateReviewState.ACCEPTED,
                expected_revision=expected_revision,
                resulting_revision=resulting_revision,
                verified_tmdb_id=verified_tmdb_id,
                verified_content_type=verified_content_type,
                verification_provenance="tmdb_gateway",
                correlation_id=correlation_id,
            )

            return {
                "status": "accepted",
                "mapping": {
                    "id": mapping.id,
                    "provider": mapping.provider.slug,
                    "provider_content_id": mapping.provider_content_id,
                    "tmdb_id": mapping.tmdb_id,
                    "content_type": mapping.content_type,
                    "revision": mapping.revision,
                    "match_method": mapping.match_method,
                },
                "candidate": {
                    "id": locked_cand.id,
                    "tmdb_id": locked_cand.tmdb_id,
                    "review_state": locked_cand.review_state,
                },
                "audit": {
                    "id": audit.id,
                    "reviewer_id": audit.reviewer_id,
                    "reason": audit.reason,
                    "created_at": audit.created_at.isoformat(),
                },
                "movie": {
                    "id": movie.id,
                    "tmdb_id": movie.tmdb_id,
                    "title": movie.title,
                    "content_type": movie.content_type,
                },
            }


class CandidateRejectionService:
    """Orchestrates verified rejection of discovered TMDb candidates."""

    def reject(
        self,
        *,
        provider_content_id: int,
        tmdb_id: int,
        reason: str,
        reviewer_id: str = "local_operator",
        correlation_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Reject a candidate with a mandatory non-empty reason and append-only audit record."""
        correlation_id = correlation_id or str(uuid.uuid4())
        reason = str(reason or "").strip()
        if not reason:
            raise CandidateAcceptanceError("A non-empty reason is required", code="REASON_REQUIRED", status_code=400)

        try:
            content = ProviderContent.objects.get(id=provider_content_id)
        except ProviderContent.DoesNotExist:
            raise CandidateAcceptanceError(
                f"ProviderContent {provider_content_id} not found",
                code="PROVIDER_CONTENT_NOT_FOUND",
                status_code=404,
            )

        candidate = ProviderContentCandidate.objects.filter(
            provider_content=content,
            tmdb_id=tmdb_id,
        ).first()
        if not candidate:
            raise CandidateAcceptanceError(
                f"Candidate TMDb {tmdb_id} not found for ProviderContent {provider_content_id}",
                code="CANDIDATE_NOT_FOUND",
                status_code=404,
            )

        with writer_barrier("candidate rejection"), transaction.atomic():
            locked_cand = ProviderContentCandidate.objects.select_for_update().get(id=candidate.id)
            prev_review_state = locked_cand.review_state

            locked_cand.review_state = CandidateReviewState.REJECTED
            locked_cand.rejection_reason = reason
            locked_cand.save(update_fields=["review_state", "rejection_reason", "updated_at"])

            audit = CandidateReviewAudit.objects.create(
                provider_content=content,
                candidate_tmdb_id=tmdb_id,
                action="reject",
                reviewer_id=reviewer_id,
                reason=reason,
                previous_review_state=prev_review_state,
                resulting_review_state=CandidateReviewState.REJECTED,
                expected_revision=None,
                resulting_revision=None,
                verified_tmdb_id=tmdb_id,
                verified_content_type=candidate.content_type,
                verification_provenance="manual_review",
                correlation_id=correlation_id,
            )

            return {
                "status": "rejected",
                "candidate": {
                    "id": locked_cand.id,
                    "tmdb_id": locked_cand.tmdb_id,
                    "review_state": locked_cand.review_state,
                    "rejection_reason": locked_cand.rejection_reason,
                },
                "audit": {
                    "id": audit.id,
                    "reviewer_id": audit.reviewer_id,
                    "reason": audit.reason,
                    "created_at": audit.created_at.isoformat(),
                },
            }
