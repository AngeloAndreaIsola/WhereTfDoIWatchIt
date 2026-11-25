"""Execute the pinned Stage D title policy with a durable shared send ledger."""

import os
from contextlib import contextmanager
from datetime import datetime
from time import sleep
from urllib.parse import urlsplit

import requests
from django.db import connection, transaction
from django.utils import timezone

from availability.models import (
    AvailabilityExperiment,
    AvailabilityExperimentAttempt,
    AvailabilityExperimentAttemptOutcome,
    AvailabilityExperimentScope,
    AvailabilityStageDAttemptEvent,
    AvailabilityStageDRun,
    Country,
    JustWatchRequestBudget,
    Source,
    SyncRun,
)
from availability.services.experiment_runner import (
    ExperimentBlocked, database_identity, manifest_digest, search_payload_for_scope,
)
from availability.services.issue97_stage_d_observation import observe_title
from availability.services.issue97_stage_d_policy import (
    MANIFEST_SHA256, authorization_scope, batch_bounds, check_authorization, load_policy,
    review_evidence, sha256, stable_run_id,
)
from availability.sources.justwatch_client import DEFAULT_HEADERS, DEFAULT_TIMEOUT
from availability.sources.justwatch_transport import (
    charge_request_budget, check_request_budget, post_once, record_request_budget_response,
)
from availability.sources.base import SourceFetchStopped, SourceQuotaExceeded
from tracker.models import CatalogueRefreshRequest, Movie, RefreshRequest, RefreshRun
from tracker.services.bulk_refresh import _catalogue_lock_key, release_source_lock, try_source_lock
from tracker.services.refresh_requests import _TITLE_DRAIN_LOCK_KEY, title_drain_lock

SOURCE = "justwatch"
DISPOSABLE_ENV = "AVAILABILITY_STAGE_D_DISPOSABLE"


def _target(expected):
    if connection.vendor != "postgresql":
        raise ExperimentBlocked("Stage D requires PostgreSQL")
    actual = database_identity()
    if expected is not None and actual != expected:
        raise ExperimentBlocked("confirmed database identity differs from the actual PostgreSQL target")
    return actual


def _confirmed_target(expected):
    if os.environ.get(DISPOSABLE_ENV) != "1" or not expected:
        raise ExperimentBlocked("Stage D writes require an explicitly confirmed disposable database")
    return _target(expected)


def _check_autocommit():
    if connection.in_atomic_block or not connection.get_autocommit():
        raise ExperimentBlocked("Stage D requires independently committed transactions")


@contextmanager
def _run_locks():
    with title_drain_lock() as title_acquired:
        if not title_acquired:
            raise ExperimentBlocked("shared title-drain lock is held")
        if not try_source_lock(SOURCE):
            raise ExperimentBlocked("shared JustWatch source lock is held")
        try:
            yield
        finally:
            release_source_lock(SOURCE)


def _lock_states():
    with connection.cursor() as cursor:
        keys = (_TITLE_DRAIN_LOCK_KEY, _catalogue_lock_key(SOURCE))
        cursor.execute(
            "SELECT classid, objid FROM pg_locks WHERE locktype='advisory' AND granted "
            "AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database())",
        )
        held = set(cursor.fetchall())
    return {
        name: (key >> 32 & 0xFFFFFFFF, key & 0xFFFFFFFF) in held
        for name, key in zip(("title_drain", SOURCE), keys)
    }


def _queue_blockers():
    blockers = []
    if (RefreshRequest.objects.filter(status="pending").exists()
            or CatalogueRefreshRequest.objects.filter(status="pending").exists()):
        blockers.append("refresh queue is pending")
    if (RefreshRun.objects.filter(finished_at__isnull=True).exists()
            or SyncRun.objects.filter(completed_at__isnull=True, source__slug=SOURCE).exists()):
        blockers.append("refresh or JustWatch synchronization is unfinished")
    return blockers


def _identity_blockers(scopes):
    if not Country.objects.filter(code="NL", enabled=True).exists():
        return ["enabled NL country row is missing"]
    return [
        f"{scope['scope_id']} requires the exact typed Movie title and year"
        for scope in scopes
        if not Movie.objects.filter(
            tmdb_id=scope["tmdb_id"], content_type=scope["media_type"],
            title=scope["title"], year=scope["year"],
        ).exists()
    ]


def _ledger(policy, database):
    """Read durable progress and check consistency across the linked ledgers."""
    run_id = stable_run_id(policy, database)
    if (AvailabilityStageDRun.objects.exclude(pk=run_id).exists()
            or AvailabilityExperimentAttempt.objects.filter(source_slug=SOURCE).exclude(experiment_id=run_id).exists()):
        raise ExperimentBlocked("another JustWatch experiment owns or has spent on this target")
    run = AvailabilityStageDRun.objects.select_related("experiment", "grant").filter(pk=run_id).first()
    attempts = list(AvailabilityExperimentAttempt.objects.filter(experiment_id=run_id)
                    .order_by("ordinal").select_related("outcome").prefetch_related("stage_d_events"))
    scopes = set(AvailabilityExperimentScope.objects.filter(experiment_id=run_id)
                 .values_list("scope_id", flat=True))
    if run is None:
        if attempts or scopes or AvailabilityExperiment.objects.filter(pk=run_id).exists():
            raise ExperimentBlocked("Stage D experiment has no exclusive ledger ownership")
        return run, [], {}

    experiment = run.experiment
    if (experiment.manifest_digest != MANIFEST_SHA256 or experiment.database_identity != database
            or experiment.maximum_attempts != policy["maximum_attempts"]
            or run.grant.attempts != run.initial_grant_attempts + len(attempts)
            or len(attempts) > policy["maximum_attempts"]):
        raise ExperimentBlocked("Stage D experiment and shared receipt accounting disagree")

    entries, completed = [], {}
    for attempt, scope in zip(attempts, policy["scopes"]):
        events = {event.kind: event for event in attempt.stage_d_events.all()}
        outcome = getattr(attempt, "outcome", None)
        response, checkpoint = events.get("response"), events.get("checkpoint")
        if (attempt.ordinal != len(entries) + 1 or attempt.scope_id != scope["scope_id"]
                or attempt.source_slug != SOURCE or attempt.operation != policy["operation"]
                or attempt.method != policy["method"]
                or attempt.destination_host != urlsplit(policy["endpoint"]).hostname
                or "reservation" not in events or bool(response) != bool(outcome)
                or (response and ("handoff" not in events or outcome.http_status != response.evidence.get("status")))
                or (checkpoint and (not response or "observation" not in response.evidence
                                    or checkpoint.evidence.get("observation_sha256")
                                    != response.evidence.get("observation_sha256")))):
            raise ExperimentBlocked("Stage D event ledger is inconsistent")
        entries.append((attempt, events))
        if checkpoint:
            completed[attempt.scope_id] = checkpoint.evidence
    if (set(completed) != scopes or len(attempts) - len(completed) > 1
            or any(attempt.scope_id not in completed for attempt, _ in entries[:-1])
            or run.grant.in_progress != bool(entries and "response" not in entries[-1][1])):
        raise ExperimentBlocked("Stage D receipt, attempt and checkpoint state disagree")
    return run, entries, completed


def _plan(policy, batch, entries, completed):
    start, end = batch_bounds(policy, batch)
    prior = [scope["scope_id"] for scope in policy["scopes"][:start]]
    if not all(scope_id in completed for scope_id in prior):
        raise ExperimentBlocked("previous title batch needs complete checkpoints and review")
    if entries and entries[-1][0].scope_id not in completed:
        _, events = entries[-1]
        response = events.get("response")
        if response and "observation" in response.evidence:
            raise ExperimentBlocked("confirmed response requires offline checkpoint recovery")
        if response:
            raise ExperimentBlocked("confirmed response stopped Stage D and requires review")
        raise ExperimentBlocked("spent attempt has unknown transmission and requires reconciliation")
    digest = manifest_digest([completed[scope_id] for scope_id in prior])
    return policy["scopes"][max(start, len(entries)):end], digest


def _grant_for_receipt(run, receipt):
    reference = receipt["evidence_reference"].strip()
    if run and run.grant.evidence_reference != reference:
        raise ExperimentBlocked("Stage D cannot switch the durable grant reference")
    if JustWatchRequestBudget.objects.exclude(evidence_reference=reference).filter(attempts__gt=0).exists():
        raise ExperimentBlocked("other receipt spend on this target prevents exclusive grant accounting")
    return JustWatchRequestBudget.objects.filter(evidence_reference=reference).first()


def _available_attempts(grant, evidence, receipt):
    used = grant.attempts if grant else 0
    limits = [receipt["maximum_requests"] - used]
    quota = evidence["quota"]
    if quota["status"] == "available":
        spent_at_review = quota["spent_at_review"]
        if spent_at_review > used:
            raise ExperimentBlocked("quota evidence spend exceeds the shared receipt ledger")
        limits.append(quota["remaining"] - (used - spent_at_review))
    return min(limits)


def _pacing_reference(entries, grant, evidence):
    if entries:
        return entries[-1][1]["response"].occurred_at
    if grant and grant.attempts:
        idle_since = evidence.get("prior_receipt_idle_since")
        if idle_since is None:
            raise ExperimentBlocked("prior receipt spend requires a reviewed grant-idle timestamp")
        idle_since = datetime.fromisoformat(idle_since)
        if grant.last_reserved_at is None or idle_since < grant.last_reserved_at:
            raise ExperimentBlocked("prior receipt idle timestamp does not cover the latest reservation")
        return idle_since
    return None


def _check_budget(grant, receipt, now):
    try:
        check_request_budget(grant or JustWatchRequestBudget(), receipt, now)
    except (SourceFetchStopped, SourceQuotaExceeded) as exc:
        raise ExperimentBlocked(str(exc)) from exc


def _review(policy, database, batch, source, digest, now):
    evidence, receipt, expiry = review_evidence(policy, database, source, now)
    scope = authorization_scope(policy, database, batch, digest, evidence, receipt)
    return evidence, receipt, expiry, scope


def preflight(batch):
    """Read-only operator report. It performs SELECTs and opens no HTTP client."""
    policy = load_policy()
    start, end = batch_bounds(policy, batch)
    database = _target(None)
    blockers = _queue_blockers() + _identity_blockers(policy["scopes"])
    if os.environ.get(DISPOSABLE_ENV) != "1":
        blockers.append("disposable database has not been attested")
    locks = _lock_states()
    blockers.extend(f"{name} lock is held" for name, held in locks.items() if held)
    report = {
        "database_identity": database,
        "manifest_digest": MANIFEST_SHA256,
        "batch": batch,
        "scope_ids": [scope["scope_id"] for scope in policy["scopes"][start:end]],
        "operation": policy["operation"],
        "endpoint": policy["endpoint"],
        "document_sha256": policy["document_sha256"],
        "maximum_attempts": policy["maximum_attempts"],
        "maximum_retries": policy["maximum_retries"],
        "locks": locks,
        "blockers": blockers,
    }
    try:
        run, entries, completed = _ledger(policy, database)
        report["accounting"] = _accounting(entries, completed)
        pending, digest = _plan(policy, batch, entries, completed)
        source = Source.objects.filter(slug=SOURCE).first()
        now = timezone.now()
        evidence, receipt, expiry, scope = _review(policy, database, batch, source, digest, now)
        grant = _grant_for_receipt(run, receipt)
        if pending:
            _check_budget(grant, receipt, now)
            _pacing_reference(entries, grant, evidence)
            if _available_attempts(grant, evidence, receipt) < len(pending):
                raise ExperimentBlocked("reviewed quota or receipt has insufficient balance for the batch")
        if not blockers:
            report["authorization_scope"] = scope
            report["evidence_expires_at"] = expiry.isoformat()
    except ExperimentBlocked as exc:
        blockers.append(str(exc))
    report["readiness"] = "blocked" if blockers else "ready_for_authorization_review"
    return report


def _accounting(entries, completed):
    confirmed = sum("response" in events for _, events in entries)
    handoffs = sum("handoff" in events for _, events in entries)
    return {
        "http_reservations_spent": len(entries),
        "http_client_handoffs": handoffs,
        "http_transmitted_confirmed": confirmed,
        "http_transmission_unknown": len(entries) - confirmed,
        "http_handoff_without_confirmation": handoffs - confirmed,
        "checkpointed_scope_ids": list(completed),
    }


def _authorization(policy, database, batch, digest, source, token, now):
    evidence, receipt, expiry, expected = _review(policy, database, batch, source, digest, now)
    expiry, token_digest = check_authorization(token, expected, expiry, now)
    return evidence, receipt, expiry, token_digest


def _reserve(policy, database, batch, scope, digest, token):
    payload = search_payload_for_scope(scope)
    while True:
        with transaction.atomic(durable=True):
            source = Source.objects.select_for_update().filter(slug=SOURCE).first()
            now = timezone.now()
            evidence, receipt, expiry, token_digest = _authorization(
                policy, database, batch, digest, source, token, now,
            )
            run, entries, completed = _ledger(policy, database)
            pending, current_digest = _plan(policy, batch, entries, completed)
            if not pending or pending[0] != scope or current_digest != digest:
                raise ExperimentBlocked("scope is outside the remaining signed batch or has already spent its slot")
            blockers = _queue_blockers() + _identity_blockers([scope])
            if blockers:
                raise ExperimentBlocked(blockers[0])

            grant = _grant_for_receipt(run, receipt)
            _check_budget(grant, receipt, now)
            if _available_attempts(grant, evidence, receipt) < len(pending):
                raise ExperimentBlocked("reviewed quota or receipt has insufficient balance for the batch")
            interval = max(policy["minimum_interval_seconds"], receipt["minimum_interval_seconds"],
                           evidence["rate_policy"]["minimum_interval_seconds"])
            last = _pacing_reference(entries, grant, evidence)
            wait = interval - (now - last).total_seconds() if last else 0
            if wait <= 0:
                if not grant:
                    grant = JustWatchRequestBudget.objects.create(evidence_reference=receipt["evidence_reference"].strip())
                if not run:
                    experiment = AvailabilityExperiment.objects.create(
                        authorization_id=stable_run_id(policy, database), manifest_digest=MANIFEST_SHA256,
                        database_identity=database, maximum_attempts=policy["maximum_attempts"],
                    )
                    run = AvailabilityStageDRun.objects.create(
                        experiment=experiment, grant=grant, initial_grant_attempts=grant.attempts,
                    )
                charge_request_budget(grant, now)
                attempt = AvailabilityExperimentAttempt.objects.create(
                    experiment_id=run.pk, ordinal=len(entries) + 1, scope_id=scope["scope_id"],
                    source_slug=SOURCE, operation=policy["operation"], method=policy["method"],
                    destination_host=urlsplit(policy["endpoint"]).hostname,
                )
                _event(attempt, "reservation", now, {
                    "phase": policy["phase"], "batch": batch, "scope": scope,
                    "manifest_sha256": MANIFEST_SHA256, "document_sha256": policy["document_sha256"],
                    "variables_sha256": manifest_digest(payload["variables"]),
                    "evidence_sha256": manifest_digest(evidence), "runtime_receipt_sha256": manifest_digest(receipt),
                    "authorization_sha256": token_digest, "retry_of_ordinal": None,
                    "quota_window": evidence["quota"].get("window_id"),
                })
                return attempt, grant, payload
            if wait >= 60 or wait >= (expiry - now).total_seconds():
                raise ExperimentBlocked("pacing window requires a later authorized resume")
        sleep(wait)


def _event(attempt, kind, now, data):
    return AvailabilityStageDAttemptEvent.objects.create(
        attempt=attempt, kind=kind, occurred_at=now, evidence=data,
    )


def _record_response(attempt, grant, scope, response, received_at):
    status = response.status_code
    record = {"status": status, "response_sha256": sha256(response.content)}
    if 200 <= status < 300:
        try:
            observation = observe_title(scope, response)
            record["observation"] = observation
            record["observation_sha256"] = manifest_digest(observation)
        except ExperimentBlocked as exc:
            record["stop_reason"] = str(exc)
    else:
        record["stop_reason"] = f"JustWatch returned HTTP {status}"
    with transaction.atomic(durable=True):
        Source.objects.select_for_update().get(slug=SOURCE)
        grant = JustWatchRequestBudget.objects.select_for_update().get(pk=grant.pk)
        record_request_budget_response(grant, status, response.headers, received_at)
        record["cooldown_until"] = grant.blocked_until.isoformat() if status == 429 else None
        record["denied"] = grant.denied
        AvailabilityExperimentAttemptOutcome.objects.create(
            attempt=attempt, outcome="failure" if "stop_reason" in record else "success", http_status=status,
        )
        return _event(attempt, "response", received_at, record)


def _checkpoint(attempt, response):
    record = response.evidence
    observation = record.get("observation")
    if (not isinstance(observation, dict) or observation.get("scope_id") != attempt.scope_id
            or manifest_digest(observation) != record.get("observation_sha256")
            or "stop_reason" in record):
        raise ExperimentBlocked("response has no intact validated observation for offline checkpointing")
    checkpoint = {
        "ordinal": attempt.ordinal, "scope_id": attempt.scope_id,
        "response_sha256": record["response_sha256"], "observation_sha256": record["observation_sha256"],
        "observed_at": response.occurred_at.isoformat(),
    }
    with transaction.atomic(durable=True):
        _event(attempt, "checkpoint", timezone.now(), checkpoint)
        AvailabilityExperimentScope.objects.create(experiment_id=attempt.experiment_id, scope_id=attempt.scope_id)
    return checkpoint


def execute(batch, token, confirmed_database):
    """Run one signed batch, with one HTTP client call per committed reservation."""
    _check_autocommit()
    policy = load_policy()
    database = _confirmed_target(confirmed_database)
    with _run_locks():
        _, entries, completed = _ledger(policy, database)
        pending, digest = _plan(policy, batch, entries, completed)
        source = Source.objects.filter(slug=SOURCE).first()
        _authorization(policy, database, batch, digest, source, token, timezone.now())
        blockers = _queue_blockers() + _identity_blockers(policy["scopes"])
        if blockers:
            raise ExperimentBlocked(blockers[0])
        for scope in pending:
            attempt, grant, payload = _reserve(policy, database, batch, scope, digest, token)
            with transaction.atomic(durable=True):
                source = Source.objects.select_for_update().filter(slug=SOURCE).first()
                now = timezone.now()
                _authorization(policy, database, batch, digest, source, token, now)
                blockers = _queue_blockers() + _identity_blockers([scope])
                if blockers:
                    raise ExperimentBlocked(blockers[0])
                _event(attempt, "handoff", now, {})
            try:
                response = post_once(policy["endpoint"], json=payload, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
            except requests.RequestException as exc:
                raise ExperimentBlocked("JustWatch send is unconfirmed; charged reservation needs reconciliation") from exc
            observed = _record_response(attempt, grant, scope, response, timezone.now())
            if "stop_reason" in observed.evidence:
                raise ExperimentBlocked(observed.evidence["stop_reason"])
            _checkpoint(attempt, observed)
        _, entries, completed = _ledger(policy, database)
        return _accounting(entries, completed)


def recover_checkpoint(confirmed_database):
    """Checkpoint an existing validated response without opening an HTTP client."""
    _check_autocommit()
    policy = load_policy()
    database = _confirmed_target(confirmed_database)
    with _run_locks():
        _, entries, completed = _ledger(policy, database)
        if not entries or entries[-1][0].scope_id in completed:
            raise ExperimentBlocked("there is no unfinished Stage D checkpoint")
        attempt, events = entries[-1]
        if "response" not in events:
            raise ExperimentBlocked("unknown transmission requires reconciliation and remains spent")
        return _checkpoint(attempt, events["response"])
