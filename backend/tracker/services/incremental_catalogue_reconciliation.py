"""Eligibility and durable orchestration around the existing canonical verifier.

Scanning input fingerprints is cheap and makes admin/import edits visible too.
Only eligible records enter the verifier; the acknowledged bulk inventory never
does. Provider leases use the existing PostgreSQL advisory-lock implementation.
"""
from collections import Counter, defaultdict
from datetime import timedelta
import logging
import math
import uuid

import requests
from django.conf import settings
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from availability.models import ContentMapping, TmdbDetailsCache, TmdbSearchCache
from availability.services.candidate_gateway import (
    CandidateDiscoveryBudget, CandidateDiscoveryBudgetExceeded, TMDbCandidateGateway,
)
from tracker.models import (
    CanonicalBootstrapEvidence, CatalogueReconciliationRecord, CatalogueReconciliationRun,
    DiscoveredCatalogueRecord, Movie, Provider,
)
from tracker.services.bulk_refresh import try_source_lock, release_source_lock
from tracker.services.canonical_association_validation import canonical_association_conflict
from tracker.services.canonical_verification import CanonicalVerifier, evidence_agrees
from tracker.services.catalogue_reconciliation import (
    _apply_classification, _extract_year, _normal_title, _provider_content_id,
    _trusted_external_tmdb_id, _type_evidence,
)
from tracker.services.catalogue_reconciliation_inputs import fingerprint_v1

logger = logging.getLogger(__name__)
PROVIDERS = {"hydrahd", "animeworld"}
LEASE_SECONDS = 900


def _fresh_caches(now):
    searches = {(c.query, c.year, c.content_type): c for c in TmdbSearchCache.objects.filter(
        expires_at__gt=now, locale="en-US", endpoint_version="v3",
    )}
    details = {(c.tmdb_id, c.content_type): c for c in TmdbDetailsCache.objects.filter(
        expires_at__gt=now, locale="en-US", endpoint_version="v3", is_negative=False,
    )}
    return searches, details


def eligible_records(provider_slug, *, record_ids=None, review=False, now=None):
    """Select new/changed/due observations, or unresolved ones with new proof.

    A missing association alone does not reset a previously unresolved row.
    Explicit review is deliberately restricted to a bounded ID selection.
    """
    now = now or timezone.now()
    searches, details = _fresh_caches(now)
    details_by_title = defaultdict(list)
    for cache in details.values():
        data = cache.details if isinstance(cache.details, dict) else {}
        for title in {data.get("title"), data.get("original_title")}:
            if title:
                details_by_title[(_normal_title(title), data.get("year"), cache.content_type)].append(cache)
    mappings = {(m.provider_id, m.provider_content_id) for m in ContentMapping.objects.filter(
        provider__slug=provider_slug).only("provider_id", "provider_content_id")}
    qs = DiscoveredCatalogueRecord.objects.filter(provider__slug=provider_slug).select_related(
        "provider", "reconciliation", "canonical_bootstrap_evidence__movie").order_by("pk")
    if record_ids is not None:
        qs = qs.filter(pk__in=record_ids)
    for record in qs.iterator(chunk_size=500):
        state = getattr(record, "reconciliation", None)
        associated = (record.provider_id, _provider_content_id(record)) in mappings
        reason = None
        if state is None:
            reason = "new_discovery"
        elif state.input_fingerprint != fingerprint_v1(record):
            reason = "meaningful_metadata_changed"
        elif review:
            reason = "explicit_review"
        elif state.next_attempt_at and state.next_attempt_at <= now:
            reason = "retry_due"
        elif state.status == "ASSOCIATED" and not associated:
            reason = "association_missing"
        elif not associated:
            media = _type_evidence(record)[0]
            year = _extract_year(record)[0]
            search = searches.get((TMDbCandidateGateway._normalize_query(record.title), year, media))
            ids = {c.get("tmdb_id") for c in (search.results if search and not search.is_negative else []) if isinstance(c, dict)}
            trusted = _trusted_external_tmdb_id(record)
            if trusted:
                ids.add(int(trusted))
            proof = getattr(record, "canonical_bootstrap_evidence", None)
            if ((search and not search.is_negative and search.updated_at > state.processed_at)
                    or any(details.get((pk, media)) and details[(pk, media)].updated_at > state.processed_at for pk in ids)
                    or any(c.updated_at > state.processed_at for c in details_by_title.get(
                        (_normal_title(record.title), year, media), []))
                    or (proof and proof.verified_at > state.processed_at and evidence_agrees(record, proof))):
                reason = "new_verification_evidence"
        if reason:
            yield record, reason


def _retry_at(status, attempts, *, media, year, now):
    if status == "ERROR":
        return now + timedelta(seconds=min(86400, 300 * 2 ** min(attempts - 1, 9)))
    if status == "BUDGET_EXHAUSTED":
        return now + timedelta(minutes=1)
    if status == "UNMATCHED" and media and year:
        return now + timedelta(days=30)
    # Missing type/year, ambiguity, conflicts and cache misses require changed
    # metadata, new evidence or an explicit review. No automatic hot loop.
    return None


def _assert_owner(run_id, owner):
    run = CatalogueReconciliationRun.objects.select_for_update().get(pk=run_id)
    if run.lease_owner != owner or not run.lease_expires_at or run.lease_expires_at <= timezone.now():
        raise RuntimeError("Reconciliation lease lost; uncommitted work must be retried")
    return run


def _validate_association(record, media, year, result, *, lock=False):
    if result["status"] != "VERIFIED":
        return
    conflict = canonical_association_conflict(record, media, year, result["tmdb_id"], lock=lock)
    if conflict:
        result.pop("verified_details", None)
        result.update(status="CONFLICTING", association_conflict=True,
                      action=conflict["action"], evidence=result["evidence"] + conflict["evidence"])


def _commit_record(record, result, *, run_id, owner, verifier, totals):
    """Evidence, association, outcome and successful cursor commit together."""
    with transaction.atomic():
        run = _assert_owner(run_id, owner)
        locked = DiscoveredCatalogueRecord.objects.select_for_update().select_related("provider").get(pk=record.pk)
        if fingerprint_v1(locked) != fingerprint_v1(record):
            raise CommandError("Discovery changed during verification; retry current metadata")
        media, year = _type_evidence(record)[0], _extract_year(record)[0]
        _validate_association(locked, media, year, result, lock=True)
        if result["status"] == "VERIFIED":
            details = result.pop("verified_details", None)
            if details is not None:
                verifier.persist_verified(record, media, year, details,
                                          _trusted_external_tmdb_id(record), totals, result, refresh_evidence=True)
            proof = CanonicalBootstrapEvidence.objects.select_related("movie").get(discovered_record=record)
            if not evidence_agrees(record, proof):
                raise CommandError("Verification evidence changed or expired before commit")
            action = _apply_classification(record, media, year, {
                "status": "MATCH_VERIFIED", "confidence": 1.0,
                "evidence": result["evidence"],
                "candidates": [{"movie_id": proof.movie_id, "tmdb_id": proof.movie.tmdb_id,
                                "title": proof.movie.title, "year": year, "content_type": media}],
            })
            result["action"] = action
            if action == "existing_canonical_mapping_conflict":
                # A mapping inserted by another writer after our initial read
                # must roll back candidate identity/proof creation as well.
                raise CommandError("Existing canonical mapping changed during verification")
            result["status"] = "ASSOCIATED"
        elif (media and record.title.strip() and result["status"] not in {"BLOCKED", "ERROR"}
              and not result.get("association_conflict")
              and result.get("action") != "existing_canonical_mapping_conflict"):
            _apply_classification(record, media, year, {"status": "UNMATCHED"})
        previous = CatalogueReconciliationRecord.objects.filter(discovered_record=record).first()
        attempts = (previous.attempts if previous else 0) + 1
        now = timezone.now()
        CatalogueReconciliationRecord.objects.update_or_create(discovered_record=record, defaults={
            "input_fingerprint": fingerprint_v1(record), "status": result["status"],
            "processed_at": now, "attempts": attempts,
            "next_attempt_at": _retry_at(result["status"], attempts, media=media, year=year, now=now),
            "result": result,
        })
        if result["status"] not in {"ERROR", "BUDGET_EXHAUSTED", "BLOCKED"}:
            run.last_completed_record_id = record.pk
            run.last_successful_checkpoint = now
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        run.save()


def run_incremental_reconciliation(provider_slug, *, limit=25, request_budget=50,
                                   allow_network=False, apply=False, interval=0.25,
                                   retries=1, record_ids=None, review=False):
    if provider_slug not in PROVIDERS:
        raise ValueError("Incremental reconciliation supports HydraHD and permitted AnimeWorld RSS observations only")
    return _run_reconciliation(provider_slug, limit=limit, request_budget=request_budget,
        allow_network=allow_network, apply=apply, interval=interval, retries=retries,
        record_ids=record_ids, review=review)


def _run_reconciliation(provider_slug, *, limit=25, request_budget=50,
                        allow_network=False, apply=False, interval=0.25,
                        retries=1, record_ids=None, review=False):
    """Shared engine; public entry points own their provider/scope/permission gates."""
    if (not 1 <= limit <= 250 or not 1 <= request_budget <= 500
            or not 0 <= retries <= 2 or not math.isfinite(interval) or not 0 <= interval <= 60):
        raise ValueError("Require limit 1..250, request_budget 1..500, retries 0..2 and interval 0..60")
    if review and (not record_ids or len(set(record_ids)) > limit):
        raise ValueError("Explicit review requires bounded record_ids within limit")
    if allow_network and not settings.TMDB_API_KEY:
        raise ValueError("TMDb network access requires TMDB_API_KEY")
    budget = CandidateDiscoveryBudget(request_budget)
    provider = Provider.objects.get(slug=provider_slug)
    lock = f"unofficial-canonical:{provider_slug}"
    if not try_source_lock(lock):
        return {"provider": provider_slug, "status": "LOCKED", "records": [], "offers_created": 0,
                "tmdb_budget": budget.to_dict()}
    run, owner = None, uuid.uuid4().hex
    report = {"provider": provider_slug, "status": "COMPLETED", "mode": "APPLY" if apply else "DRY_RUN",
              "records": [], "offers_created": 0}
    try:
        now = timezone.now()
        if apply:
            with transaction.atomic():
                run, _ = CatalogueReconciliationRun.objects.select_for_update().get_or_create(provider=provider)
                if run.lease_owner and run.lease_expires_at and run.lease_expires_at > now:
                    report["status"] = "LOCKED"
                    return report
                if run.cooldown_until and run.cooldown_until > now:
                    report.update(status="COOLDOWN", cooldown_until=run.cooldown_until.isoformat())
                    return report
                report["recovered_expired_lease"] = bool(run.lease_owner)
                run.lease_owner, run.started_at, run.status = owner, now, "RUNNING"
                run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                run.save()
        else:
            # A preview can still spend HTTP requests. Respect durable guards
            # without creating or updating progress, leases or cache rows.
            persisted = CatalogueReconciliationRun.objects.filter(provider=provider).first()
            if persisted:
                if persisted.lease_owner and persisted.lease_expires_at and persisted.lease_expires_at > now:
                    report["status"] = "LOCKED"
                    return report
                if persisted.cooldown_until and persisted.cooldown_until > now:
                    report.update(status="COOLDOWN", cooldown_until=persisted.cooldown_until.isoformat())
                    return report
        # Freeze a bounded selection. Progress is per observation, never a
        # high-water ID that would strand older rows after their metadata edit.
        from itertools import islice
        selected = list(islice(eligible_records(provider_slug, record_ids=record_ids, review=review), limit))
        report["selected"] = len(selected)
        if not selected:
            return report
        gateway = TMDbCandidateGateway(budget=budget, min_request_interval_seconds=interval,
                                      allow_cache_writes=apply)
        gateway.max_retries, gateway.stop_on_rate_limit = retries, True
        verifier = CanonicalVerifier()
        verifier.title_groups = Counter(
            (r.provider_id, _type_evidence(r)[0], _extract_year(r)[0], _normal_title(r.title))
            for r in DiscoveredCatalogueRecord.objects.select_related("provider").iterator(chunk_size=500)
        )
        # Reuse canonical identities only with fresh, agreeing details proof.
        searches_cache, details_cache = _fresh_caches(timezone.now())
        canonical = defaultdict(dict)
        for movie in Movie.objects.only("tmdb_id", "content_type", "title", "year").iterator():
            canonical[(_normal_title(movie.title), movie.year, movie.content_type)][movie.pk] = movie
            cache = details_cache.get((movie.tmdb_id, movie.content_type))
            data = cache.details if cache and isinstance(cache.details, dict) else {}
            if data.get("year") == movie.year and data.get("content_type") == movie.content_type and data.get("tmdb_id") == movie.tmdb_id:
                for title in (data.get("title"), data.get("original_title")):
                    if title:
                        canonical[(_normal_title(title), movie.year, movie.content_type)][movie.pk] = movie
        for record, reason in selected:
            media, year = _type_evidence(record)[0], _extract_year(record)[0]
            result = {"record_id": record.pk, "reason": reason, "status": "UNMATCHED", "evidence": []}
            totals = Counter()
            before = budget.attempted
            try:
                if record.access_policy_state in {"BLOCKED", "RESTRICTED", "DENIED"}:
                    result.update(status="BLOCKED", evidence=["source access policy prohibits processing"])
                elif not media or not year or not _normal_title(record.title):
                    result.update(status="AMBIGUOUS" if not media else "UNMATCHED",
                                  evidence=_type_evidence(record)[2] + [_extract_year(record)[1]])
                else:
                    proof = getattr(record, "canonical_bootstrap_evidence", None)
                    if proof and evidence_agrees(record, proof):
                        result.update(status="VERIFIED", tmdb_id=proof.movie.tmdb_id,
                                      evidence=["fresh record-specific TMDb verification reused"])
                    else:
                        candidates = list(canonical.get((_normal_title(record.title), year, media), {}).values())
                        cached = []
                        search = searches_cache.get((TMDbCandidateGateway._normalize_query(record.title), year, media))
                        if (search is None and len(candidates) == 1
                                and (candidates[0].tmdb_id, media) in details_cache):
                            cached = [{"tmdb_id": candidates[0].tmdb_id, "title": candidates[0].title,
                                       "year": year, "content_type": media}]
                        verifier.evaluate(record, media, year, gateway, {
                            "apply": False, "allow_network": allow_network,
                            "return_details": True, "cached_candidates": cached,
                        }, totals, result)
                _validate_association(record, media, year, result)
                result["tmdb_requests"] = budget.attempted - before
                if apply:
                    _commit_record(record, result, run_id=run.pk, owner=owner, verifier=verifier, totals=totals)
            except CandidateDiscoveryBudgetExceeded:
                result.update(status="BUDGET_EXHAUSTED", evidence=["request budget exhausted; resume from committed outcomes"])
                result["tmdb_requests"] = budget.attempted - before
                report["status"] = "PARTIAL"
                if apply:
                    _commit_record(record, result, run_id=run.pk, owner=owner, verifier=verifier, totals=totals)
            except (requests.RequestException, CommandError, ValueError, TypeError, KeyError, IntegrityError) as exc:
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                restricted = status in (401, 403, 429)
                result.pop("verified_details", None)
                error = (f"TMDb HTTP {status}" if status else
                         f"TMDb transport/response error ({type(exc).__name__})" if isinstance(exc, requests.RequestException) else
                         f"{type(exc).__name__}: {exc}")
                result.update(status="BLOCKED" if restricted else "CONFLICTING" if isinstance(exc, CommandError) else "ERROR",
                              evidence=[error])
                result["tmdb_requests"] = budget.attempted - before
                if restricted:
                    wait = TMDbCandidateGateway._parse_retry_after(response.headers.get("Retry-After"))
                    report["cooldown_until"] = (timezone.now() + timedelta(seconds=max(300, wait or 0))).isoformat()
                    report["status"] = "BLOCKED"
                    if apply:
                        CatalogueReconciliationRun.objects.filter(pk=run.pk, lease_owner=owner).update(
                            cooldown_until=timezone.now() + timedelta(seconds=max(300, wait or 0)))
                elif result["status"] == "ERROR":
                    report["status"] = "PARTIAL"
                if apply:
                    _commit_record(record, result, run_id=run.pk, owner=owner, verifier=verifier, totals=totals)
                    if restricted:
                        CatalogueReconciliationRecord.objects.filter(discovered_record=record).update(
                            next_attempt_at=timezone.now() + timedelta(seconds=max(300, wait or 0)))
            result.pop("verified_details", None)
            result["tmdb_requests"] = budget.attempted - before
            report["records"].append(result)
            if result["status"] in {"BUDGET_EXHAUSTED", "BLOCKED"}:
                report["status"] = "BLOCKED" if result["status"] == "BLOCKED" else "PARTIAL"
                break
        return report
    except BaseException as exc:
        report["status"] = "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED"
        report["last_error"] = type(exc).__name__
        raise
    finally:
        report["tmdb_budget"] = budget.to_dict()
        try:
            if apply and run is not None:
                CatalogueReconciliationRun.objects.filter(pk=run.pk, lease_owner=owner).update(
                    status=report["status"], lease_owner="", lease_expires_at=None,
                    report=report, last_error=report.get("last_error", ""))
            logger.info("incremental_catalogue_reconciliation %s", report)
        finally:
            release_source_lock(lock)
