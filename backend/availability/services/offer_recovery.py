"""Targeted offer recovery execution service.

Allows an existing, valid ContentMapping to obtain a canonical Offer from a
fresh, bounded provider observation without rerunning the full catalogue walk.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional

import requests
from django.db import transaction

import re

from availability.domain.enums import SyncMode
from availability.models import ContentMapping, ProviderContent, Source
from availability.services.normalize import normalize_content_type
from availability.services.refresh import AvailabilityRefreshService
from availability.services.write_barrier import writer_barrier
from availability.sources.base import SourceFetchError, SourceFetchResult, SourceQuotaExceeded
from availability.sources.registry import UnknownSourceError, source_registry

logger = logging.getLogger(__name__)


def sanitize_error_detail(exc: Exception) -> str:
    """Return a deterministic, credential-free summary of an exception for operator surfaces."""
    exc_type = type(exc).__name__

    # If exception exposes safe diagnostic (e.g. SourceQuotaExceeded)
    if hasattr(exc, "diagnostic") and callable(getattr(exc, "diagnostic")):
        raw = str(exc.diagnostic())
    else:
        status_code = None
        if hasattr(exc, "response") and getattr(exc, "response", None) is not None:
            status_code = getattr(exc.response, "status_code", None)
        elif hasattr(exc, "status_code") and isinstance(getattr(exc, "status_code"), int):
            status_code = getattr(exc, "status_code")

        msg = str(exc)
        if status_code is not None:
            raw = f"status={status_code}: {msg}"
        else:
            raw = msg

    # Strip URLs entirely
    sanitized = re.sub(r'https?://[^\s\'"<>]+', '[URL]', raw)
    # Redact Authorization: Bearer <token>
    sanitized = re.sub(r'(?i)\bauthorization\s*:\s*bearer\s+[^\s,;]+', 'Authorization: Bearer [REDACTED]', sanitized)
    # Redact generic Bearer <token>
    sanitized = re.sub(r'(?i)\bbearer\s+[A-Za-z0-9_\-\.]+', 'Bearer [REDACTED]', sanitized)
    # Redact api_key=..., token=..., secret=..., password=...
    sanitized = re.sub(
        r'(?i)\b(api[_-]?key|token|secret|password|client_secret|apikey|auth)\s*([:=])\s*([^\s&,;]+)',
        r'\1\2[REDACTED]',
        sanitized
    )

    sanitized = " ".join(sanitized.split())
    if len(sanitized) > 160:
        sanitized = sanitized[:157] + "..."

    return f"{exc_type}: {sanitized}" if not sanitized.startswith(exc_type) else sanitized


# Standardized outcome categories
OUTCOME_OBSERVED_OFFER = "observed_offer"
OUTCOME_NO_CURRENT_EVIDENCE = "no_current_evidence"
OUTCOME_UNSUPPORTED_SOURCE = "unsupported_source"
OUTCOME_MISSING_MAPPING = "missing_mapping"
OUTCOME_MAPPING_CONFLICT = "mapping_conflict"
OUTCOME_BUDGET_EXHAUSTED = "budget_exhausted"
OUTCOME_RETRYABLE_ERROR = "retryable_error"
OUTCOME_PERMANENT_ERROR = "permanent_error"


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome and diagnostics of a targeted offer recovery execution."""

    outcome: str
    provider_content_pk: int
    provider_content_id: str
    provider_slug: str
    source_slug: str
    tmdb_id: Optional[int]
    content_type: Optional[str]
    http_requests_attempted: int
    offers_created: int
    offers_updated: int
    offer_identities: list[str]
    reason_code: Optional[str]
    error_detail: Optional[str]
    applied: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_http_attempts(source_instance: Any) -> int:
    """Extract attempted HTTP requests count from source budget if available."""
    client = getattr(source_instance, "client", None)
    if client is not None:
        budget = getattr(client, "budget", None)
        if budget is not None and hasattr(budget, "attempted"):
            return int(budget.attempted)
    return 0


def recover_provider_evidence(
    provider_content_pk: int,
    *,
    max_http_requests: Optional[int] = None,
    apply: bool = False,
    refresh_service: Optional[AvailabilityRefreshService] = None,
) -> RecoveryResult:
    """Execute targeted offer recovery for an existing ProviderContent entry.

    Parameters
    ----------
    provider_content_pk: int
        Primary key of the ProviderContent row.
    max_http_requests: int, optional
        Finite HTTP budget. Mandatory if apply=True.
    apply: bool
        If False, executes in offline preview mode (no network, no DB writes).
        If True, executes live targeted fetch and persists valid fresh offers.
    refresh_service: AvailabilityRefreshService, optional
        Optional custom refresh service instance for testing.
    """
    if not isinstance(provider_content_pk, int) or provider_content_pk <= 0:
        raise ValueError("provider_content_pk must be a positive integer")

    if apply:
        if max_http_requests is None or max_http_requests <= 0:
            raise ValueError("max_http_requests must be a positive integer when apply=True")

    # 1. Load ProviderContent
    try:
        content = ProviderContent.objects.select_related("provider", "source").get(pk=provider_content_pk)
    except ProviderContent.DoesNotExist:
        return RecoveryResult(
            outcome=OUTCOME_PERMANENT_ERROR,
            provider_content_pk=provider_content_pk,
            provider_content_id="",
            provider_slug="",
            source_slug="",
            tmdb_id=None,
            content_type=None,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="provider_content_not_found",
            error_detail=f"ProviderContent with pk={provider_content_pk} does not exist.",
            applied=apply,
        )

    provider = content.provider
    provider_slug = provider.slug
    provider_content_id = content.provider_content_id
    content_type_raw = content.content_type
    stored_url = content.provider_url or ""

    try:
        norm_content_type = normalize_content_type(content_type_raw)
    except Exception as exc:
        return RecoveryResult(
            outcome=OUTCOME_PERMANENT_ERROR,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=content.source.slug if content.source else "",
            tmdb_id=None,
            content_type=content_type_raw,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="invalid_content_type",
            error_detail=f"Unsupported ProviderContent content_type: {exc}",
            applied=apply,
        )

    # 2. Load and validate existing ContentMapping
    mappings = list(
        ContentMapping.objects.filter(
            provider=provider,
            provider_content_id=provider_content_id,
        )
    )

    if not mappings:
        return RecoveryResult(
            outcome=OUTCOME_MISSING_MAPPING,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=content.source.slug if content.source else "",
            tmdb_id=None,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="missing_mapping",
            error_detail=f"No ContentMapping exists for {provider_slug}:{provider_content_id}",
            applied=apply,
        )

    if len(mappings) > 1:
        return RecoveryResult(
            outcome=OUTCOME_MAPPING_CONFLICT,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=content.source.slug if content.source else "",
            tmdb_id=None,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="multiple_mappings",
            error_detail=f"Multiple conflicting ContentMapping rows exist for {provider_slug}:{provider_content_id}",
            applied=apply,
        )

    mapping = mappings[0]
    pinned_mapping_id = mapping.pk
    pinned_tmdb_id = mapping.tmdb_id
    pinned_mapping_revision = getattr(mapping, "revision", None)

    try:
        mapping_content_type = normalize_content_type(mapping.content_type)
    except Exception as exc:
        return RecoveryResult(
            outcome=OUTCOME_MAPPING_CONFLICT,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=content.source.slug if content.source else "",
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="mapping_type_invalid",
            error_detail=f"ContentMapping content_type is invalid: {exc}",
            applied=apply,
        )

    # Inconsistent media type between ProviderContent and ContentMapping
    if mapping_content_type != norm_content_type:
        return RecoveryResult(
            outcome=OUTCOME_MAPPING_CONFLICT,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=content.source.slug if content.source else "",
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="content_type_mismatch",
            error_detail=(
                f"ProviderContent content_type '{norm_content_type}' does not match "
                f"ContentMapping content_type '{mapping_content_type}'"
            ),
            applied=apply,
        )

    # Inconsistent mapping.content foreign key if set
    if mapping.content_id is not None and mapping.content_id != content.pk:
        return RecoveryResult(
            outcome=OUTCOME_MAPPING_CONFLICT,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=content.source.slug if content.source else "",
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="mapping_content_mismatch",
            error_detail=(
                f"ContentMapping links ProviderContent pk={mapping.content_id}, "
                f"expected pk={content.pk}"
            ),
            applied=apply,
        )

    # 3. Determine source and verify capabilities
    source_candidate_slug = content.source.slug if content.source else provider_slug
    try:
        source_instance = source_registry.get(source_candidate_slug)
    except UnknownSourceError:
        # Fall back to provider_slug if content.source is not registered
        try:
            source_instance = source_registry.get(provider_slug)
        except UnknownSourceError:
            source_instance = None

    if source_instance is None:
        return RecoveryResult(
            outcome=OUTCOME_UNSUPPORTED_SOURCE,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=source_candidate_slug,
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="source_not_registered",
            error_detail=f"Source '{source_candidate_slug}' is not registered.",
            applied=apply,
        )

    capabilities = getattr(source_instance, "capabilities", None)
    if not capabilities or not getattr(capabilities, "supports_targeted_evidence", False):
        return RecoveryResult(
            outcome=OUTCOME_UNSUPPORTED_SOURCE,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=source_instance.slug,
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="source_targeted_unsupported",
            error_detail=f"Source '{source_instance.slug}' does not support targeted evidence.",
            applied=apply,
        )

    if not hasattr(source_instance, "fetch_evidence"):
        return RecoveryResult(
            outcome=OUTCOME_UNSUPPORTED_SOURCE,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=source_instance.slug,
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="source_missing_fetch_evidence",
            error_detail=f"Source '{source_instance.slug}' lacks fetch_evidence method.",
            applied=apply,
        )

    # 4. Offline preview mode (apply=False)
    if not apply:
        return RecoveryResult(
            outcome="preview",
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=source_instance.slug,
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="preview_mode",
            error_detail=None,
            applied=False,
        )

    # 5. Live targeted fetch execution outside DB locks
    # Configure finite budget on the source instance
    if hasattr(source_instance, "max_http_requests"):
        source_instance.max_http_requests = max_http_requests
    client = getattr(source_instance, "client", None)
    if client is not None and hasattr(client, "budget"):
        from availability.sources.rai_play import RaiPlayHttpBudget
        client.budget = RaiPlayHttpBudget(limit=max_http_requests)

    if not stored_url:
        return RecoveryResult(
            outcome=OUTCOME_PERMANENT_ERROR,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=source_instance.slug,
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=0,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="missing_provider_url",
            error_detail="ProviderContent has no provider_url stored.",
            applied=True,
        )

    http_attempted = 0
    try:
        fetch_result: SourceFetchResult = source_instance.fetch_evidence(
            provider_content_id=provider_content_id,
            url=stored_url,
        )
        http_attempted = _get_http_attempts(source_instance)
    except Exception as exc:
        http_attempted = _get_http_attempts(source_instance)
        # Classify the exception
        # Budget exhausted check (RaiPlayHttpBudgetExceeded or SourceQuotaExceeded)
        exc_name = type(exc).__name__
        safe_detail = sanitize_error_detail(exc)
        if "BudgetExceeded" in exc_name or isinstance(exc, SourceQuotaExceeded):
            return RecoveryResult(
                outcome=OUTCOME_BUDGET_EXHAUSTED,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="budget_exhausted",
                error_detail=safe_detail,
                applied=True,
            )

        # Retryable network errors
        if isinstance(
            exc,
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ),
        ) or (
            isinstance(exc, requests.exceptions.HTTPError)
            and getattr(exc, "response", None) is not None
            and exc.response.status_code in (429, 502, 503, 504)
        ):
            return RecoveryResult(
                outcome=OUTCOME_RETRYABLE_ERROR,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="upstream_network_transient",
                error_detail=safe_detail,
                applied=True,
            )

        # SourceFetchError may be retryable if 5xx or timeout
        if isinstance(exc, SourceFetchError):
            msg = str(exc).lower()
            if "timeout" in msg or "503" in msg or "502" in msg or "429" in msg:
                return RecoveryResult(
                    outcome=OUTCOME_RETRYABLE_ERROR,
                    provider_content_pk=provider_content_pk,
                    provider_content_id=provider_content_id,
                    provider_slug=provider_slug,
                    source_slug=source_instance.slug,
                    tmdb_id=pinned_tmdb_id,
                    content_type=norm_content_type,
                    http_requests_attempted=http_attempted,
                    offers_created=0,
                    offers_updated=0,
                    offer_identities=[],
                    reason_code="source_fetch_transient",
                    error_detail=safe_detail,
                    applied=True,
                )

        # All other unexpected errors are permanent errors
        return RecoveryResult(
            outcome=OUTCOME_PERMANENT_ERROR,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=source_instance.slug,
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=http_attempted,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="unexpected_fetch_error",
            error_detail=safe_detail,
            applied=True,
        )

    # 6. Validate source-returned native identity, provider, and media type
    returned_contents = fetch_result.contents or []
    returned_offers = fetch_result.offers or []

    service = refresh_service or AvailabilityRefreshService(source=source_instance)

    for dc in returned_contents:
        if dc.provider_content_id != provider_content_id:
            return RecoveryResult(
                outcome=OUTCOME_MAPPING_CONFLICT,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="identity_conflict",
                error_detail=(
                    f"DiscoveredContent identity '{dc.provider_content_id}' contradicts "
                    f"expected '{provider_content_id}'"
                ),
                applied=True,
            )
        resolved_dc_provider = service._provider(dc.provider_id)
        if resolved_dc_provider is None or resolved_dc_provider.pk != provider.pk:
            return RecoveryResult(
                outcome=OUTCOME_MAPPING_CONFLICT,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="provider_conflict",
                error_detail=(
                    f"DiscoveredContent provider '{dc.provider_id}' resolved to "
                    f"{resolved_dc_provider}, contradicts expected '{provider_slug}'"
                ),
                applied=True,
            )
        try:
            dc_type = normalize_content_type(dc.content_type)
        except Exception:
            dc_type = dc.content_type
        if dc_type != norm_content_type:
            return RecoveryResult(
                outcome=OUTCOME_MAPPING_CONFLICT,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="media_type_conflict",
                error_detail=(
                    f"DiscoveredContent media type '{dc_type}' contradicts "
                    f"expected '{norm_content_type}'"
                ),
                applied=True,
            )

    for po in returned_offers:
        if po.provider_content_id != provider_content_id:
            return RecoveryResult(
                outcome=OUTCOME_MAPPING_CONFLICT,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="identity_conflict",
                error_detail=(
                    f"ProviderOffer identity '{po.provider_content_id}' contradicts "
                    f"expected '{provider_content_id}'"
                ),
                applied=True,
            )
        resolved_po_provider = service._provider(po.provider_id, country=po.country)
        if resolved_po_provider is None or resolved_po_provider.pk != provider.pk:
            return RecoveryResult(
                outcome=OUTCOME_MAPPING_CONFLICT,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="provider_conflict",
                error_detail=(
                    f"ProviderOffer provider '{po.provider_id}' resolved to "
                    f"{resolved_po_provider}, contradicts expected '{provider_slug}'"
                ),
                applied=True,
            )
        try:
            po_type = normalize_content_type(po.content_type)
        except Exception:
            po_type = po.content_type
        if po_type != norm_content_type:
            return RecoveryResult(
                outcome=OUTCOME_MAPPING_CONFLICT,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="media_type_conflict",
                error_detail=(
                    f"ProviderOffer media type '{po_type}' contradicts "
                    f"expected '{norm_content_type}'"
                ),
                applied=True,
            )
        # If the source returns an explicit TMDb id, it must match the pinned mapping
        if po.tmdb_id is not None and po.tmdb_id != pinned_tmdb_id:
            return RecoveryResult(
                outcome=OUTCOME_MAPPING_CONFLICT,
                provider_content_pk=provider_content_pk,
                provider_content_id=provider_content_id,
                provider_slug=provider_slug,
                source_slug=source_instance.slug,
                tmdb_id=pinned_tmdb_id,
                content_type=norm_content_type,
                http_requests_attempted=http_attempted,
                offers_created=0,
                offers_updated=0,
                offer_identities=[],
                reason_code="tmdb_id_conflict",
                error_detail=(
                    f"ProviderOffer stated TMDb ID {po.tmdb_id} contradicts "
                    f"pinned mapping TMDb ID {pinned_tmdb_id}"
                ),
                applied=True,
            )

    # 7. Check for empty evidence (no eligible fresh offers)
    if not returned_offers:
        return RecoveryResult(
            outcome=OUTCOME_NO_CURRENT_EVIDENCE,
            provider_content_pk=provider_content_pk,
            provider_content_id=provider_content_id,
            provider_slug=provider_slug,
            source_slug=source_instance.slug,
            tmdb_id=pinned_tmdb_id,
            content_type=norm_content_type,
            http_requests_attempted=http_attempted,
            offers_created=0,
            offers_updated=0,
            offer_identities=[],
            reason_code="evidence_unobservable",
            error_detail=None,
            applied=True,
        )

    # Prepare offers for canonical persistence: attach the pinned TMDb ID
    offers_to_persist = [
        po if po.tmdb_id is not None else po.__class__(**{**po.__dict__, "tmdb_id": pinned_tmdb_id})
        for po in returned_offers
    ]

    # 8. Acquire write barrier and perform persistence under database transaction
    with writer_barrier(f"targeted recovery for {provider_slug}:{provider_content_id}"):
        with transaction.atomic():
            # Lock ProviderContent and ContentMapping rows with select_for_update()
            locked_content = (
                ProviderContent.objects.select_for_update()
                .filter(pk=provider_content_pk)
                .first()
            )
            if locked_content is None:
                return RecoveryResult(
                    outcome=OUTCOME_MAPPING_CONFLICT,
                    provider_content_pk=provider_content_pk,
                    provider_content_id=provider_content_id,
                    provider_slug=provider_slug,
                    source_slug=source_instance.slug,
                    tmdb_id=pinned_tmdb_id,
                    content_type=norm_content_type,
                    http_requests_attempted=http_attempted,
                    offers_created=0,
                    offers_updated=0,
                    offer_identities=[],
                    reason_code="provider_content_concurrently_removed",
                    error_detail="ProviderContent was deleted while recovery was in progress.",
                    applied=True,
                )

            locked_mapping = (
                ContentMapping.objects.select_for_update()
                .filter(pk=pinned_mapping_id)
                .first()
            )
            if locked_mapping is None:
                return RecoveryResult(
                    outcome=OUTCOME_MAPPING_CONFLICT,
                    provider_content_pk=provider_content_pk,
                    provider_content_id=provider_content_id,
                    provider_slug=provider_slug,
                    source_slug=source_instance.slug,
                    tmdb_id=pinned_tmdb_id,
                    content_type=norm_content_type,
                    http_requests_attempted=http_attempted,
                    offers_created=0,
                    offers_updated=0,
                    offer_identities=[],
                    reason_code="mapping_concurrently_removed",
                    error_detail="ContentMapping was removed while recovery was in progress.",
                    applied=True,
                )

            # Verify that mapping has not changed (including optimistic concurrency revision if supported)
            current_mapping_revision = getattr(locked_mapping, "revision", None)
            revision_changed = (
                pinned_mapping_revision is not None
                and current_mapping_revision != pinned_mapping_revision
            )

            if (
                locked_mapping.tmdb_id != pinned_tmdb_id
                or normalize_content_type(locked_mapping.content_type) != norm_content_type
                or locked_mapping.provider_content_id != provider_content_id
                or revision_changed
            ):
                return RecoveryResult(
                    outcome=OUTCOME_MAPPING_CONFLICT,
                    provider_content_pk=provider_content_pk,
                    provider_content_id=provider_content_id,
                    provider_slug=provider_slug,
                    source_slug=source_instance.slug,
                    tmdb_id=pinned_tmdb_id,
                    content_type=norm_content_type,
                    http_requests_attempted=http_attempted,
                    offers_created=0,
                    offers_updated=0,
                    offer_identities=[],
                    reason_code="mapping_concurrently_altered",
                    error_detail="ContentMapping target, type, or revision changed while recovery was in progress.",
                    applied=True,
                )

            source_model, _ = Source.objects.get_or_create(
                slug=source_instance.slug,
                defaults={
                    "name": getattr(source_instance, "name", source_instance.slug),
                    "type": getattr(source_instance, "source_type", "api"),
                },
            )

            created_count, updated_count, persisted_identities, _runs = (
                service.persist_targeted_offers(
                    offers_to_persist,
                    source_model=source_model,
                )
            )

    return RecoveryResult(
        outcome=OUTCOME_OBSERVED_OFFER,
        provider_content_pk=provider_content_pk,
        provider_content_id=provider_content_id,
        provider_slug=provider_slug,
        source_slug=source_instance.slug,
        tmdb_id=pinned_tmdb_id,
        content_type=norm_content_type,
        http_requests_attempted=http_attempted,
        offers_created=created_count,
        offers_updated=updated_count,
        offer_identities=persisted_identities,
        reason_code="fresh_evidence_persisted",
        error_detail=None,
        applied=True,
    )
