"""Admin API views for ProviderContent candidate review and verified acceptance/rejection."""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from tracker.models import Provider
from availability.domain.enums import CandidateReviewState, ContentType
from availability.models import (
    CandidateReviewAudit,
    ContentMapping,
    Offer,
    ProviderContent,
    ProviderContentCandidate,
)
from availability.services.candidate_acceptance import (
    CandidateAcceptanceError,
    CandidateAcceptanceService,
    CandidateRejectionService,
    check_recovery_supported,
    is_candidate_legacy_unverified,
)

logger = logging.getLogger(__name__)


def _is_trusted_origin_or_host(url_str: str, request_host: str) -> bool:
    """Verify whether the origin or referer matches local/allowed hosts."""
    if not url_str:
        return True
    try:
        parsed = urlparse(url_str)
        host = (parsed.netloc or "").split(":")[0].lower()
        if not host:
            return False
        # Local loopback hosts
        if host in ("localhost", "127.0.0.1", "testserver"):
            return True
        # Request host
        req_netloc = request_host.split(":")[0].lower()
        if host == req_netloc:
            return True
        # Allowed hosts from settings
        for allowed in getattr(settings, "ALLOWED_HOSTS", []):
            allowed = allowed.split(":")[0].strip().lower()
            if allowed and (allowed == "*" or host == allowed or (allowed.startswith(".") and host.endswith(allowed))):
                return True
        return False
    except Exception:
        return False


def verify_operator_mutation_guard(request: HttpRequest) -> tuple[bool, Optional[JsonResponse]]:
    """Enforce operator authorization and cross-origin protections for privileged mutations.

    Protections:
    1. Browser Sec-Fetch-Site: reject 'cross-site' requests.
    2. Origin / Referer validation: must match trusted local/allowed origins.
    3. Content-Type validation: state-changing mutations must specify application/json.
    4. Operator authentication / authorization seam:
       - If user is authenticated in Django session, must have is_staff=True.
       - If no login session (single-user personal deployment model), require an explicit
         operator header (e.g. 'X-Operator-Role: admin', 'X-Requested-With', or
         'X-Operator-Action: confirm') to prevent ambient browser form/tag cross-site forgery.
    """
    sec_fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
    if sec_fetch_site == "cross-site":
        logger.warning("Operator mutation rejected: Sec-Fetch-Site cross-site attempt from %s", request.META.get("REMOTE_ADDR"))
        return False, JsonResponse(
            {"error": "Cross-origin requests are forbidden on admin mutation endpoints", "code": "FORBIDDEN_CROSS_ORIGIN"},
            status=403,
        )

    req_host = request.get_host()

    origin = request.headers.get("Origin")
    if origin and not _is_trusted_origin_or_host(origin, req_host):
        logger.warning("Operator mutation rejected: Untrusted Origin %r", origin)
        return False, JsonResponse(
            {"error": "Untrusted request Origin", "code": "FORBIDDEN_ORIGIN"},
            status=403,
        )

    referer = request.headers.get("Referer")
    if referer and not _is_trusted_origin_or_host(referer, req_host):
        logger.warning("Operator mutation rejected: Untrusted Referer %r", referer)
        return False, JsonResponse(
            {"error": "Untrusted request Referer", "code": "FORBIDDEN_REFERER"},
            status=403,
        )

    content_type = request.content_type or ""
    if "application/json" not in content_type.lower():
        return False, JsonResponse(
            {"error": "Expected application/json Content-Type", "code": "INVALID_CONTENT_TYPE"},
            status=415,
        )

    # Operator authorization check:
    # If a session user is logged in, check is_staff.
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        if not user.is_staff:
            return False, JsonResponse(
                {"error": "Operator staff privileges required", "code": "FORBIDDEN_NOT_STAFF"},
                status=403,
            )
        return True, None

    # In local/no-auth mode, require proof of non-ambient operator context (custom header)
    has_operator_header = any(
        [
            request.headers.get("X-Operator-Role") == "admin",
            request.headers.get("X-Requested-With") in ("XMLHttpRequest", "fetch", "WhereTfDoIWatchIt-Client"),
            request.headers.get("X-Operator-Action") == "confirm",
            bool(request.headers.get("X-Correlation-ID")),
        ]
    )
    if not has_operator_header:
        # If neither custom header nor session is present, reject ambient cross-site requests
        logger.warning("Operator mutation rejected: Missing operator confirmation header in unauthenticated context")
        return False, JsonResponse(
            {
                "error": "Operator confirmation header required for unauthenticated privileged mutation (e.g. X-Operator-Role: admin or X-Requested-With)",
                "code": "OPERATOR_AUTHORIZATION_REQUIRED",
            },
            status=403,
        )

    return True, None


def _serialize_candidate(candidate: ProviderContentCandidate) -> dict[str, Any]:
    legacy_unverified = is_candidate_legacy_unverified(candidate)
    effective_review_state = (
        "legacy_unverified" if legacy_unverified else candidate.review_state
    )
    return {
        "id": candidate.id,
        "tmdb_id": candidate.tmdb_id,
        "title": candidate.title,
        "year": candidate.year,
        "content_type": candidate.content_type,
        "confidence_score": candidate.confidence_score,
        "scoring_evidence": candidate.scoring_evidence,
        "review_state": effective_review_state,
        "stored_review_state": candidate.review_state,
        "is_legacy_unverified": legacy_unverified,
        "resolution_category": candidate.resolution_category,
        "rejection_reason": candidate.rejection_reason,
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    }


@require_http_methods(["GET"])
def provider_content_candidates_list(request):
    """GET /api/admin/provider-content/candidates/

    Collection endpoint for candidates across provider content entries.
    Supports:
    - pagination (page, page_size)
    - filters: provider (slug), status (review_state), content_type
    - search: title query
    - returns unresolved counts and summary metrics.
    """
    qs = ProviderContent.objects.select_related("provider", "source").order_by("-updated_at", "id")

    provider_slug = request.GET.get("provider")
    if provider_slug:
        qs = qs.filter(provider__slug=provider_slug)

    status_filter = request.GET.get("status")
    if status_filter:
        if status_filter == "unmapped":
            qs = qs.filter(mappings__isnull=True)
        elif status_filter == "mapped":
            qs = qs.filter(mappings__isnull=False)
        else:
            qs = qs.filter(tmdb_candidates__review_state=status_filter)

    search = request.GET.get("search")
    if search:
        qs = qs.filter(title__icontains=search.strip())

    content_type = request.GET.get("content_type")
    if content_type:
        qs = qs.filter(content_type=content_type)

    total_unresolved = ProviderContent.objects.filter(mappings__isnull=True).count()
    total_unmapped_with_candidates = (
        ProviderContent.objects.filter(mappings__isnull=True, tmdb_candidates__isnull=False)
        .distinct()
        .count()
    )

    page_number = int(request.GET.get("page", 1))
    page_size = min(100, max(1, int(request.GET.get("page_size", 25))))

    paginator = Paginator(qs.distinct(), page_size)
    page_obj = paginator.get_page(page_number)

    items = []
    # Bulk fetch mappings and offer counts
    content_list = list(page_obj.object_list)
    content_ids = [c.id for c in content_list]

    mappings_by_content = {
        m.content_id: m
        for m in ContentMapping.objects.filter(content_id__in=content_ids).select_related("provider")
    }

    offers_counts = (
        Offer.objects.filter(
            provider__in=[c.provider for c in content_list],
            provider_content_id__in=[c.provider_content_id for c in content_list],
        )
        .values("provider_id", "provider_content_id")
        .annotate(
            total_offers=Count("id"),
            active_offers=Count("id", filter=Q(currently_available=True)),
        )
    )
    counts_map = {
        (o["provider_id"], o["provider_content_id"]): (o["total_offers"], o["active_offers"])
        for o in offers_counts
    }

    for c in content_list:
        mapping = mappings_by_content.get(c.id)
        if not mapping:
            # Fallback to provider + provider_content_id lookup
            mapping = ContentMapping.objects.filter(
                provider=c.provider, provider_content_id=c.provider_content_id
            ).first()

        total_offers, active_offers = counts_map.get((c.provider_id, c.provider_content_id), (0, 0))

        candidates_qs = c.tmdb_candidates.all().order_by("-confidence_score", "id")
        serialized_candidates = [_serialize_candidate(cand) for cand in candidates_qs]

        items.append(
            {
                "id": c.id,
                "provider": c.provider.slug,
                "provider_content_id": c.provider_content_id,
                "title": c.title,
                "content_type": c.content_type,
                "year": c.release_date.year if c.release_date else None,
                "provider_url": c.provider_url,
                "currently_available": c.currently_available,
                "recovery_supported": check_recovery_supported(c),
                "offer_count": total_offers,
                "active_offer_count": active_offers,
                "existing_mapping": {
                    "id": mapping.id,
                    "tmdb_id": mapping.tmdb_id,
                    "content_type": mapping.content_type,
                    "revision": mapping.revision,
                    "match_method": mapping.match_method,
                    "confidence": mapping.confidence,
                }
                if mapping
                else None,
                "candidates_count": len(serialized_candidates),
                "candidates": serialized_candidates,
            }
        )

    return JsonResponse(
        {
            "items": items,
            "page": page_obj.number,
            "pages": paginator.num_pages,
            "total_items": paginator.count,
            "summary": {
                "total_unresolved_provider_content": total_unresolved,
                "total_unmapped_with_candidates": total_unmapped_with_candidates,
            },
        }
    )


@require_http_methods(["GET"])
def provider_content_candidates_detail(request, pk: int):
    """GET /api/admin/provider-content/{id}/candidates/

    Returns:
    - Provider content identity, title, type, year and URL.
    - Existing mapping or null (including revision).
    - Candidate list with scores, reasons and review states (with legacy_unverified handling).
    - Offer counts and active-offer counts.
    - Whether recovery is supported.
    """
    try:
        content = ProviderContent.objects.select_related("provider", "source").get(id=pk)
    except ProviderContent.DoesNotExist:
        return JsonResponse({"error": f"ProviderContent {pk} not found", "code": "NOT_FOUND"}, status=404)

    mapping = (
        ContentMapping.objects.filter(provider=content.provider, provider_content_id=content.provider_content_id)
        .first()
    )

    total_offers = Offer.objects.filter(
        provider=content.provider, provider_content_id=content.provider_content_id
    ).count()

    active_offers = Offer.objects.filter(
        provider=content.provider, provider_content_id=content.provider_content_id, currently_available=True
    ).count()

    recovery_supported = check_recovery_supported(content)

    candidates = [
        _serialize_candidate(c)
        for c in content.tmdb_candidates.all().order_by("-confidence_score", "id")
    ]

    response_data = {
        "id": content.id,
        "provider": content.provider.slug,
        "provider_content_id": content.provider_content_id,
        "title": content.title,
        "content_type": content.content_type,
        "year": content.release_date.year if content.release_date else None,
        "release_date": content.release_date.isoformat() if content.release_date else None,
        "provider_url": content.provider_url,
        "currently_available": content.currently_available,
        "existing_mapping": {
            "id": mapping.id,
            "provider": mapping.provider.slug,
            "provider_content_id": mapping.provider_content_id,
            "tmdb_id": mapping.tmdb_id,
            "content_type": mapping.content_type,
            "revision": mapping.revision,
            "match_method": mapping.match_method,
            "confidence": mapping.confidence,
        }
        if mapping
        else None,
        "offer_count": total_offers,
        "active_offer_count": active_offers,
        "recovery_supported": recovery_supported,
        "candidates": candidates,
    }
    return JsonResponse(response_data)


@csrf_exempt
@require_http_methods(["POST"])
def candidate_accept(request, pk: int, tmdb_id: int):
    """POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/accept/

    Request:
    - reason (mandatory, non-empty)
    - expected_revision (explicit null for unmapped, integer for existing)
    - authorize_network (optional bool)
    - max_http_requests (optional int budget when authorize_network is True)
    """
    allowed, err_resp = verify_operator_mutation_guard(request)
    if not allowed and err_resp is not None:
        return err_resp

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"error": "Invalid JSON body", "code": "INVALID_JSON"}, status=400)

    reason = body.get("reason")
    has_expected_revision = "expected_revision" in body
    expected_revision = body.get("expected_revision")
    authorize_network = bool(body.get("authorize_network", False))
    max_http_requests = body.get("max_http_requests")

    reviewer_id = (
        request.user.username
        if getattr(request, "user", None) and request.user.is_authenticated and request.user.username
        else "local_operator"
    )

    service = CandidateAcceptanceService()
    try:
        result = service.accept(
            provider_content_id=pk,
            tmdb_id=tmdb_id,
            reason=reason,
            expected_revision=expected_revision,
            has_expected_revision=has_expected_revision,
            authorize_network=authorize_network,
            max_http_requests=max_http_requests,
            reviewer_id=reviewer_id,
            correlation_id=request.headers.get("X-Correlation-ID"),
        )
        return JsonResponse(result, status=200)
    except CandidateAcceptanceError as exc:
        data = {
            "error": exc.message,
            "code": exc.code,
            **exc.extra,
        }
        return JsonResponse(data, status=exc.status_code)
    except Exception as exc:
        logger.exception("Unexpected internal error in candidate_accept: %s", exc)
        return JsonResponse(
            {"error": "An internal server error occurred while processing the candidate acceptance.", "code": "INTERNAL_ERROR"},
            status=500,
        )


@csrf_exempt
@require_http_methods(["POST"])
def candidate_reject(request, pk: int, tmdb_id: int):
    """POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/reject/

    Request:
    - reason (mandatory non-empty string)
    """
    allowed, err_resp = verify_operator_mutation_guard(request)
    if not allowed and err_resp is not None:
        return err_resp

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"error": "Invalid JSON body", "code": "INVALID_JSON"}, status=400)

    reason = body.get("reason")
    reviewer_id = (
        request.user.username
        if getattr(request, "user", None) and request.user.is_authenticated and request.user.username
        else "local_operator"
    )

    service = CandidateRejectionService()
    try:
        result = service.reject(
            provider_content_id=pk,
            tmdb_id=tmdb_id,
            reason=reason,
            reviewer_id=reviewer_id,
            correlation_id=request.headers.get("X-Correlation-ID"),
        )
        return JsonResponse(result, status=200)
    except CandidateAcceptanceError as exc:
        data = {
            "error": exc.message,
            "code": exc.code,
            **exc.extra,
        }
        return JsonResponse(data, status=exc.status_code)
    except Exception as exc:
        logger.exception("Unexpected internal error in candidate_reject: %s", exc)
        return JsonResponse(
            {"error": "An internal server error occurred while processing the candidate rejection.", "code": "INTERNAL_ERROR"},
            status=500,
        )
