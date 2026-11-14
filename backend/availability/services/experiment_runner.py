"""Fail-closed, manifest-scoped Stage 2G experiment execution.

The execution path deliberately bypasses queue selection and constructs only
the two supported title clients. Every transport invocation reserves a durable
ordinal before touching the network. The persisted reservation is the budget
fact; an interrupted call with no outcome is permanently spent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import replace
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID, NAMESPACE_URL, uuid5

import requests
from django.db import connections, transaction
from django.utils import timezone as django_timezone

from availability.models import (
    AvailabilityExperiment,
    AvailabilityExperimentAttempt,
    AvailabilityExperimentAttemptOutcome,
    AvailabilityExperimentScope,
    Country,
    Offer,
    Source,
)
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import JustWatchClient
from availability.sources.justwatch_client import SEARCH_TITLES_QUERY
from availability.services.refresh import AvailabilityRefreshService
from tracker.models import Movie, RefreshRequest
from tracker.services.refresh_requests import title_drain_lock
from tracker.services.bulk_refresh import (
    _catalogue_lock_key,
    release_source_lock,
    try_source_lock,
)

GLOBAL_LIMIT = 6
SOURCE_LIMITS = {
    "justwatch": 6,
    "mediaset_infinity": 0,
    "provider_evidence": 0,
    "streaming_availability": 0,
    "watchmode": 0,
    "legacy_scraper": 0,
}
DENIED_SOURCES = frozenset({
    "mediaset_infinity", "provider_evidence", "streaming_availability",
    "watchmode", "legacy_scraper",
})
OPERATION_LIMITS = {
    "justwatch": {"GetSearchTitles": 6},
    "mediaset_infinity": {},
    "provider_evidence": {},
    "streaming_availability": {},
    "watchmode": {},
    "legacy_scraper": {},
}
METHOD = "POST"
APPROVED_SCOPES = [
    {
        "scope_id": "inception-nl-justwatch", "tmdb_id": 27205, "media_type": "movie",
        "country": "NL", "source_slug": "justwatch", "title": "Inception",
        "year": 2010, "operation": "title_refresh", "maximum_attempts": 3,
        "endpoint": "https://apis.justwatch.com/graphql",
        "transport_operations": ["GetSearchTitles"],
        "expected_offer_type": "subscription",
    },
    {
        "scope_id": "dune-nl-justwatch", "tmdb_id": 438631, "media_type": "movie",
        "country": "NL", "source_slug": "justwatch", "title": "Dune",
        "year": 2021, "operation": "title_refresh", "maximum_attempts": 3,
        "endpoint": "https://apis.justwatch.com/graphql",
        "transport_operations": ["GetSearchTitles"],
        "expected_offer_type": "subscription",
    },
]
APPROVED_MANIFEST = {
    "schema_version": 1,
    "experiment_id": "stage2e-availability-refresh",
    "maximum_attempts": GLOBAL_LIMIT,
    "scopes": APPROVED_SCOPES,
}


class ExperimentBlocked(RuntimeError):
    pass


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def manifest_digest(manifest):
    return hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()


def validate_manifest(manifest):
    """Validate the deliberately small v1 schema; reject extensions by default."""
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "experiment_id", "maximum_attempts", "scopes"
    }:
        raise ExperimentBlocked("manifest has unknown or missing top-level fields")
    if manifest["schema_version"] != 1 or manifest["experiment_id"] != "stage2e-availability-refresh":
        raise ExperimentBlocked("unsupported manifest schema or experiment id")
    if manifest["maximum_attempts"] != GLOBAL_LIMIT:
        raise ExperimentBlocked("manifest maximum must equal the fixed 6-attempt ceiling")
    if manifest != APPROVED_MANIFEST:
        raise ExperimentBlocked("manifest differs from the fixed approved scope set")
    return True


def validate_scope(scope):
    if scope not in APPROVED_SCOPES:
        raise ExperimentBlocked("scope is not an exact member of the approved manifest")
    return True


def database_identity(alias="default"):
    cfg = connections[alias].settings_dict
    if "postgresql" in cfg.get("ENGINE", ""):
        with connections[alias].cursor() as cursor:
            cursor.execute(
                "SELECT database.oid, control.system_identifier "
                "FROM pg_database AS database CROSS JOIN pg_control_system() AS control "
                "WHERE database.datname = current_database()"
            )
            actual = cursor.fetchone()
        if not actual:
            raise ExperimentBlocked("could not establish actual PostgreSQL database identity")
        return hashlib.sha256(_canonical_json({"backend": "postgresql", "actual": actual}).encode()).hexdigest()
    stable = {key: cfg.get(key) for key in ("ENGINE", "NAME", "HOST", "PORT")}
    return hashlib.sha256(_canonical_json(stable).encode()).hexdigest()


def issue_authorization(manifest, *, expires_at):
    """Sign explicit authorization; this does not create a DB row or execute work."""
    validate_manifest(manifest)
    key = os.environ.get("AVAILABILITY_EXPERIMENT_AUTH_KEY", "")
    if len(key) < 32:
        raise ExperimentBlocked("AVAILABILITY_EXPERIMENT_AUTH_KEY must contain at least 32 characters")
    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ExperimentBlocked("authorization expiry must be a future timezone-aware timestamp")
    payload = {
        # Stable across process restarts and re-issued approvals. This makes
        # the same experiment's exhausted ledger impossible to reset by
        # minting a second authorization token.
        "authorization_id": str(uuid5(
            NAMESPACE_URL,
            f"stage2e-availability-refresh:{database_identity()}"
        )),
        "manifest_digest": manifest_digest(manifest),
        "database_identity": database_identity(),
        "maximum_attempts": GLOBAL_LIMIT,
        "expires_at": expiry.astimezone(timezone.utc).isoformat(),
    }
    encoded = base64.urlsafe_b64encode(_canonical_json(payload).encode()).decode().rstrip("=")
    signature = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def read_authorization(token, manifest):
    key = os.environ.get("AVAILABILITY_EXPERIMENT_AUTH_KEY", "")
    if len(key) < 32:
        raise ExperimentBlocked("AVAILABILITY_EXPERIMENT_AUTH_KEY must contain at least 32 characters")
    try:
        encoded, signature = token.strip().split(".", 1)
        expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if payload["manifest_digest"] != manifest_digest(manifest):
            raise ExperimentBlocked("authorization is for a different manifest digest")
        if payload["database_identity"] != database_identity():
            raise ExperimentBlocked("authorization is for a different database environment")
        if payload["maximum_attempts"] != GLOBAL_LIMIT:
            raise ExperimentBlocked("authorization budget does not match fixed ceiling")
        if datetime.fromisoformat(payload["expires_at"]) <= datetime.now(timezone.utc):
            raise ExperimentBlocked("authorization has expired")
        expected_id = uuid5(
            NAMESPACE_URL,
            f"stage2e-availability-refresh:{payload['database_identity']}",
        )
        if UUID(payload["authorization_id"]) != expected_id:
            raise ExperimentBlocked("authorization does not use the durable experiment identity")
        return payload
    except ExperimentBlocked:
        raise
    except Exception as exc:
        raise ExperimentBlocked("invalid or unverifiable authorization token") from exc


def _scope_totals(manifest):
    return {scope["scope_id"]: scope["maximum_attempts"] for scope in manifest["scopes"]}


class ReservedTransport:
    """Only approved POST operations reach requests; redirects are returned, never followed."""

    def __init__(self, *, authorization, scope, source_slug):
        validate_scope(scope)
        if authorization.get("manifest_digest") != manifest_digest(APPROVED_MANIFEST):
            raise ExperimentBlocked("authorization is not bound to the approved manifest")
        if source_slug != scope["source_slug"]:
            raise ExperimentBlocked("transport source differs from approved scope")
        self.authorization = authorization
        self.scope = scope
        self.source_slug = source_slug
        self.operation_attempts = {}
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))

    def __call__(self, url, payload=None, *, json=None, headers=None, timeout=None):
        if payload is not None and json is not None:
            raise ExperimentBlocked("ambiguous request payload")
        if payload is not None:
            json = payload
        if not isinstance(json, dict):
            raise ExperimentBlocked("request body must be a recognized JSON object")
        scope = self.scope
        operation = (json or {}).get("operationName")
        expected_host = urlsplit(scope["endpoint"]).hostname
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment) != (
            urlsplit(scope["endpoint"]).scheme,
            urlsplit(scope["endpoint"]).netloc,
            urlsplit(scope["endpoint"]).path,
            "", "",
        ):
            raise ExperimentBlocked("unexpected endpoint")
        if expected_host != "apis.justwatch.com":
            raise ExperimentBlocked("unrecognized destination host")
        if self.source_slug != scope["source_slug"] or operation not in scope["transport_operations"]:
            raise ExperimentBlocked("unrecognized source operation")
        if self.authorization.get("manifest_digest") != manifest_digest(APPROVED_MANIFEST):
            raise ExperimentBlocked("authorization manifest digest is not approved")
        variables = (json or {}).get("variables") or {}
        expected_filter = {
            "includeTitlesWithoutUrl": True,
            "packages": None,
            "objectTypes": ["MOVIE"],
            "releaseYear": {"min": None, "max": None},
            "searchQuery": scope["title"],
        }
        expected_variables = {
            "filter": {"bestOnly": True},
            "formatOfferIcon": "PNG",
            "formatPoster": "JPG",
            "profile": "S718",
            "backdropProfile": "S1920",
            "country": scope["country"],
            "language": "en",
            "first": 20,
            "offset": None,
            "popularTitlesFilter": expected_filter,
            "searchTitlesFilter": expected_filter,
        }
        if json.get("query") != SEARCH_TITLES_QUERY or variables != expected_variables:
            raise ExperimentBlocked("JustWatch title or country escaped manifest scope")
        self.operation_attempts[operation] = self.operation_attempts.get(operation, 0) + 1
        operation_limit = scope["maximum_attempts"]
        if self.operation_attempts[operation] > operation_limit:
            raise ExperimentBlocked("per-operation attempt limit exhausted")

        attempt = _reserve_attempt(self.authorization, scope, operation, "POST", expected_host)
        try:
            response = self.session.post(
                scope["endpoint"], json=json, headers=headers, timeout=timeout,
                allow_redirects=False, verify=True,
            )
        except requests.RequestException as exc:
            # A timeout/reset can happen after upstream acceptance. Stop the
            # source client's retry loop and require reconciliation. No outcome
            # is written because no response was received.
            raise ExperimentBlocked("ambiguous interrupted attempt is spent; reconciliation required") from exc
        except BaseException:
            # Process interruption/no response remains a bare durable reserve.
            raise
        _record_outcome(attempt, "success" if 200 <= response.status_code < 300 else "failure", response.status_code)
        if self.source_slug == "justwatch" and response.status_code == 429:
            raise ExperimentBlocked("JustWatch HTTP 429 rate limit; attempt spent and scope stopped")
        if 300 <= response.status_code < 400:
            raise ExperimentBlocked("redirect response denied; no redirect hop was followed")
        return response


def _reserve_attempt(authorization, scope, operation, method, host):
    if method != METHOD:
        raise ExperimentBlocked("HTTP method is not permitted")
    alias = "default"
    if "postgresql" not in connections[alias].settings_dict.get("ENGINE", ""):
        raise ExperimentBlocked("execution requires PostgreSQL durable reservations")
    if authorization.get("manifest_digest") != manifest_digest(APPROVED_MANIFEST):
        raise ExperimentBlocked("reservation authorization is not bound to the approved manifest")
    validate_scope(scope)
    if (scope["source_slug"] in DENIED_SOURCES
            or scope["source_slug"] not in SOURCE_LIMITS
            or SOURCE_LIMITS[scope["source_slug"]] <= 0
            or scope["source_slug"] != "justwatch"):
        raise ExperimentBlocked("source is not enabled in experiment policy")
    if host != urlsplit(scope["endpoint"]).hostname or operation not in scope["transport_operations"]:
        raise ExperimentBlocked("reservation destination or operation differs from approved scope")
    if not connections[alias].get_autocommit():
        raise ExperimentBlocked("attempt reservation requires database autocommit")
    if connections[alias].in_atomic_block:
        raise ExperimentBlocked("attempt reservation requires an independent durable transaction")
    try:
        if datetime.fromisoformat(authorization["expires_at"]) <= datetime.now(timezone.utc):
            raise ExperimentBlocked("execution authorization expired before attempt reservation")
    except ExperimentBlocked:
        raise
    except Exception as exc:
        raise ExperimentBlocked("execution authorization expiry is invalid") from exc
    with transaction.atomic(using=alias, durable=True):
        experiment, _ = AvailabilityExperiment.objects.using(alias).get_or_create(
            authorization_id=authorization["authorization_id"],
            defaults={
                "manifest_digest": authorization["manifest_digest"],
                "database_identity": authorization["database_identity"],
                "maximum_attempts": authorization["maximum_attempts"],
            },
        )
        experiment = AvailabilityExperiment.objects.using(alias).select_for_update().get(
            authorization_id=authorization["authorization_id"]
        )
        if (experiment.manifest_digest != authorization["manifest_digest"]
                or experiment.database_identity != authorization["database_identity"]
                or experiment.maximum_attempts != GLOBAL_LIMIT):
            raise ExperimentBlocked("durable experiment state conflicts with authorization")
        current = AvailabilityExperimentAttempt.objects.using(alias).filter(experiment=experiment)
        ordinal = current.count() + 1
        if ordinal > GLOBAL_LIMIT:
            raise ExperimentBlocked("global attempt budget exhausted")
        if current.filter(source_slug=scope["source_slug"]).count() >= SOURCE_LIMITS[scope["source_slug"]]:
            raise ExperimentBlocked("per-source attempt budget exhausted")
        if current.filter(scope_id=scope["scope_id"]).count() >= scope["maximum_attempts"]:
            raise ExperimentBlocked("per-scope attempt budget exhausted")
        operation_limit = OPERATION_LIMITS.get(scope["source_slug"], {}).get(operation)
        if operation_limit is None or current.filter(operation=operation).count() >= operation_limit:
            raise ExperimentBlocked("per-operation attempt budget exhausted")
        return AvailabilityExperimentAttempt.objects.using(alias).create(
            experiment=experiment, ordinal=ordinal, scope_id=scope["scope_id"],
            source_slug=scope["source_slug"], operation=operation, method=method,
            destination_host=host,
        )


def _record_outcome(attempt, result, http_status):
    # OneToOne insert is a single outcome event; interruption before here leaves
    # the immutable reservation as an ambiguous, spent attempt.
    with transaction.atomic():
        AvailabilityExperimentAttemptOutcome.objects.create(
            attempt=attempt, outcome=result, http_status=http_status,
        )


def read_only_preflight(manifest):
    """Return sanitized readiness facts. It performs SELECTs only."""
    validate_manifest(manifest)
    alias = "default"
    cfg = connections[alias].settings_dict
    active_title_lock_count = 0
    active_refresh_runs = 0
    if "postgresql" in str(cfg.get("ENGINE", "")):
        with connections[alias].cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                "AND granted AND objid=%s AND objsubid=1 "
                "AND database=(SELECT oid FROM pg_database WHERE datname=current_database())",
                [0x7A9ED41C],
            )
            active_title_lock_count = cursor.fetchone()[0]
    from tracker.models import RefreshRun
    active_refresh_runs = RefreshRun.objects.using(alias).filter(finished_at__isnull=True).count()
    selected_sources = {scope["source_slug"] for scope in manifest["scopes"]}
    quota_evidence = _quota_evidence(selected_sources)
    access_permission_evidence = _access_permission_evidence(selected_sources)
    scoped_queue_overlap = 0
    source_locks = _source_lock_states(manifest["scopes"]) if "postgresql" in str(cfg.get("ENGINE", "")) else {}
    try:
        actual_database_identity = database_identity(alias)
    except Exception:
        actual_database_identity = None
    reservation_rows = AvailabilityExperimentAttempt.objects.using(alias).all()
    outcome_ids = set(AvailabilityExperimentAttemptOutcome.objects.using(alias).values_list("attempt_id", flat=True))
    reservations_by_source = {}
    ambiguous_reservations = 0
    for attempt in reservation_rows:
        reservations_by_source[attempt.source_slug] = reservations_by_source.get(attempt.source_slug, 0) + 1
        if attempt.pk not in outcome_ids:
            ambiguous_reservations += 1
    current_experiment_id = str(uuid5(
        NAMESPACE_URL,
        f"stage2e-availability-refresh:{actual_database_identity}" if actual_database_identity else "stage2e-availability-refresh:unknown",
    ))
    current_attempts = AvailabilityExperimentAttempt.objects.using(alias).filter(
        experiment_id=current_experiment_id
    )
    current_outcomes = set(AvailabilityExperimentAttemptOutcome.objects.using(alias).filter(
        attempt__experiment_id=current_experiment_id
    ).values_list("attempt_id", flat=True))
    current_ambiguous = current_attempts.exclude(pk__in=current_outcomes).count()
    current_attempt_count = current_attempts.count()
    current_attempts_by_source = {}
    current_attempts_by_operation = {}
    current_attempts_by_scope = {}
    for attempt in current_attempts.values("scope_id", "source_slug", "operation"):
        current_attempts_by_source[attempt["source_slug"]] = current_attempts_by_source.get(attempt["source_slug"], 0) + 1
        key = (attempt["source_slug"], attempt["operation"])
        current_attempts_by_operation[key] = current_attempts_by_operation.get(key, 0) + 1
        current_attempts_by_scope[attempt["scope_id"]] = current_attempts_by_scope.get(attempt["scope_id"], 0) + 1
    current_incomplete_scopes = _incomplete_scope_ids(current_experiment_id)
    result = {
        "database_engine": cfg.get("ENGINE", "unknown"),
        "database_name": str(cfg.get("NAME", "unknown")),
        "database_host_configured": bool(cfg.get("HOST")),
        "database_identity": actual_database_identity,
        "readiness": "blocked",
        "scopes": [],
        "queue_pending": RefreshRequest.objects.using(alias).filter(status="pending").count(),
        "active_title_drain": {"held": bool(active_title_lock_count), "sessions": active_title_lock_count},
        "active_source_locks": source_locks,
        "worker_state": {"active_refresh_runs": active_refresh_runs, "process_presence": "not observable from PostgreSQL"},
        "quota_evidence": quota_evidence,
        "access_permission_evidence": access_permission_evidence,
        "reservations": {
            "total": sum(reservations_by_source.values()),
            "by_source": reservations_by_source,
            "ambiguous_without_outcome": ambiguous_reservations,
            "current_experiment_total": current_attempt_count,
            "current_experiment_ambiguous": current_ambiguous,
            "current_experiment_by_source": current_attempts_by_source,
            "current_experiment_incomplete_scopes": sorted(current_incomplete_scopes),
        },
        "destination_policy": "fixed exact HTTPS endpoints; redirects denied",
        "expected_maximum_attempts": GLOBAL_LIMIT,
        "blockers": [],
    }
    for scope in manifest["scopes"]:
        source = Source.objects.using(alias).filter(slug=scope["source_slug"]).first()
        movie = Movie.objects.using(alias).filter(
            tmdb_id=scope["tmdb_id"], content_type=scope["media_type"]
        ).first()
        country = Country.objects.using(alias).filter(code=scope["country"]).first()
        offers = Offer.objects.using(alias).filter(
            tmdb_id=scope["tmdb_id"], content_type=scope["media_type"],
            country__code=scope["country"],
        ).count()
        scoped_offers = Offer.objects.using(alias).filter(
            tmdb_id=scope["tmdb_id"], content_type=scope["media_type"],
            country__code=scope["country"], source__slug=scope["source_slug"],
        ).select_related("source")
        from availability.freshness import freshness_state
        freshness_values = sorted({
            str(freshness_state(row.source.slug, row.last_seen, configuration=row.source.configuration))
            for row in scoped_offers
        }) or ["no_source_offer"]
        pending_scope = RefreshRequest.objects.using(alias).filter(
            movie_id=movie.pk if movie else None, source=scope["source_slug"],
            country=scope["country"], status="pending",
        ).count()
        scoped_queue_overlap += pending_scope
        scope_data = {
            "scope_id": scope["scope_id"], "source_registered": scope["source_slug"] in _registered_source_slugs(),
            "source_row_present": source is not None,
            "source_enabled": source.enabled if source else None,
            "source_configuration": _safe_configuration(source.configuration) if source else None,
            "movie_present": movie is not None,
            "current_title": movie.title if movie else None,
            "current_year": movie.year if movie else None,
            "country_present": bool(country and country.enabled),
            "canonical_offer_count": offers,
            "selected_source_freshness": freshness_values,
            "pending_exact_scope_requests": pending_scope,
            "quota": quota_evidence.get(scope["source_slug"], "missing"),
        }
        result["scopes"].append(scope_data)
        if not source or not source.enabled or scope["source_slug"] not in _registered_source_slugs() or not movie or not country or not country.enabled:
            result["blockers"].append(f"scope {scope['scope_id']} has missing/disabled DB configuration")
        if movie and (movie.title != scope["title"] or movie.year != scope["year"]):
            result["blockers"].append(f"scope {scope['scope_id']} title/year do not match manifest")
        if scope["source_slug"] not in quota_evidence:
            result["blockers"].append(
                f"scope {scope['scope_id']} has no current quota/rate-policy evidence for six requests"
            )
        if scope["source_slug"] not in access_permission_evidence:
            result["blockers"].append(
                f"scope {scope['scope_id']} has no current express permission evidence "
                "for the JustWatch GraphQL endpoint"
            )
        if pending_scope:
            result["blockers"].append(f"scope {scope['scope_id']} overlaps a pending ordinary refresh")
        if source_locks.get(scope["source_slug"], {}).get("held"):
            result["blockers"].append(f"scope {scope['scope_id']} overlaps an active source catalogue refresh")
    result["queue_scope_overlap"] = scoped_queue_overlap
    if active_title_lock_count:
        result["blockers"].append("shared A7 title-drain advisory lock is currently held")
    if active_refresh_runs:
        result["blockers"].append("unfinished worker/catalogue refresh run exists")
    if "postgresql" not in str(cfg.get("ENGINE", "")):
        result["blockers"].append("preflight DB is not PostgreSQL")
    if not actual_database_identity:
        result["blockers"].append("actual PostgreSQL cluster/database identity is unavailable")
    if current_ambiguous:
        result["blockers"].append("current experiment has ambiguous reservations requiring manual reconciliation")
    if current_incomplete_scopes:
        result["blockers"].append(
            "current experiment has reserved scopes without completion markers requiring reconciliation"
        )
    if current_attempt_count >= manifest["maximum_attempts"]:
        result["blockers"].append("current experiment attempt budget is exhausted")
    if any(count > SOURCE_LIMITS.get(source_slug, -1) for source_slug, count in current_attempts_by_source.items()):
        result["blockers"].append("current experiment source attempt budget is exhausted or inconsistent")
    for (source_slug, operation), count in current_attempts_by_operation.items():
        if count > OPERATION_LIMITS.get(source_slug, {}).get(operation, -1):
            result["blockers"].append("current experiment operation attempt budget is exhausted or inconsistent")
    for scope in manifest["scopes"]:
        scope_id = scope["scope_id"]
        if scope_id in current_incomplete_scopes and current_attempts_by_scope.get(scope_id, 0) >= scope["maximum_attempts"]:
            result["blockers"].append(f"scope {scope_id} exhausted its per-scope attempts and needs reconciliation")
    for (source_slug, operation), count in current_attempts_by_operation.items():
        if count >= OPERATION_LIMITS.get(source_slug, {}).get(operation, GLOBAL_LIMIT) and any(
            scope["source_slug"] == source_slug and scope["scope_id"] in current_incomplete_scopes
            for scope in manifest["scopes"]
        ):
            result["blockers"].append(f"{source_slug} {operation} exhausted its operation attempts")
    result["readiness"] = "ready" if not result["blockers"] else "blocked"
    return result


def _registered_source_slugs():
    from availability.sources import source_registry
    return set(source_registry.slugs())


def _quota_evidence(source_slugs):
    """Read short-lived quota and rate-policy evidence; never infer a balance or limit."""
    raw = os.environ.get("AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE", "")
    try:
        evidence = json.loads(raw)
        if not isinstance(evidence, dict):
            return {}
        now = datetime.now(timezone.utc)
        valid = {}
        for slug in source_slugs:
            if slug not in SOURCE_LIMITS or SOURCE_LIMITS[slug] <= 0:
                continue
            item = evidence.get(slug)
            if not isinstance(item, dict):
                continue
            checked = datetime.fromisoformat(item["checked_at"].replace("Z", "+00:00"))
            age = (now - checked.astimezone(timezone.utc)).total_seconds()
            if age < 0 or age > 3600 or item.get("status") not in ("unmetered_public", "available"):
                continue
            policy = item.get("rate_policy_evidence")
            if not isinstance(policy, dict):
                continue
            policy_checked = datetime.fromisoformat(policy["checked_at"].replace("Z", "+00:00"))
            policy_age = (now - policy_checked.astimezone(timezone.utc)).total_seconds()
            max_burst = policy.get("max_burst")
            if (
                policy_age < 0
                or policy_age > 3600
                or not str(policy.get("evidence_reference", "")).strip()
                or isinstance(max_burst, bool)
                or not isinstance(max_burst, int)
                or max_burst < SOURCE_LIMITS[slug]
            ):
                continue
            if item["status"] == "available" and (
                isinstance(item.get("remaining"), bool)
                or not isinstance(item.get("remaining"), int)
                or item["remaining"] < SOURCE_LIMITS[slug]
            ):
                continue
            valid[slug] = {
                "status": item["status"],
                "remaining": item.get("remaining"),
                "checked_at": checked.astimezone(timezone.utc).isoformat(),
                "rate_policy_checked_at": policy_checked.astimezone(timezone.utc).isoformat(),
                "max_burst": max_burst,
            }
        return valid
    except Exception:
        return {}


def _access_permission_evidence(source_slugs):
    """Require current operator-reviewed permission for the exact GraphQL use."""
    raw = os.environ.get("AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE", "")
    try:
        evidence = json.loads(raw)
        if not isinstance(evidence, dict):
            return {}
        now = datetime.now(timezone.utc)
        valid = {}
        for slug in source_slugs:
            item = evidence.get(slug)
            if not isinstance(item, dict):
                continue
            permission = item.get("access_permission")
            if not isinstance(permission, dict) or permission.get("status") != "authorized":
                continue
            checked = datetime.fromisoformat(permission["checked_at"].replace("Z", "+00:00"))
            age = (now - checked.astimezone(timezone.utc)).total_seconds()
            maximum_requests = permission.get("maximum_requests")
            expected_scope_ids = sorted(
                scope["scope_id"] for scope in APPROVED_MANIFEST["scopes"]
                if scope["source_slug"] == slug
            )
            scope_ids = permission.get("scope_ids")
            if (
                age < 0
                or age > 3600
                or not str(permission.get("evidence_reference", "")).strip()
                or permission.get("endpoint") != "https://apis.justwatch.com/graphql"
                or isinstance(maximum_requests, bool)
                or not isinstance(maximum_requests, int)
                or maximum_requests < GLOBAL_LIMIT
                or not isinstance(scope_ids, list)
                or sorted(scope_ids) != expected_scope_ids
            ):
                continue
            valid[slug] = {
                "status": "authorized",
                "checked_at": checked.astimezone(timezone.utc).isoformat(),
                "endpoint": permission["endpoint"],
                "maximum_requests": maximum_requests,
                "scope_ids": expected_scope_ids,
                "evidence_reference_present": True,
            }
        return valid
    except Exception:
        return {}


def _safe_configuration(value):
    if not isinstance(value, dict):
        return {"state": "unrecognized"}
    safe = {}
    for key, item in value.items():
        name = str(key).lower()
        if any(secret in name for secret in ("token", "key", "secret", "password", "credential", "authorization")):
            safe[str(key)] = "[redacted]"
        elif isinstance(item, (int, float, bool)) or item is None:
            safe[str(key)] = item
        else:
            safe[str(key)] = "[redacted]"
    return safe


def _source_lock_states(scopes):
    keys = {scope["source_slug"]: _catalogue_lock_key(scope["source_slug"]) for scope in scopes}
    pairs = [(key >> 32 & 0xFFFFFFFF, key & 0xFFFFFFFF) for key in keys.values()]
    where = " OR ".join("(classid=%s AND objid=%s)" for _ in pairs)
    params = [value for pair in pairs for value in pair]
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT classid, objid, count(*) FROM pg_locks WHERE locktype='advisory' "
            f"AND granted AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
            f"AND ({where}) GROUP BY classid, objid",
            params,
        )
        counts = {(classid, objid): count for classid, objid, count in cursor.fetchall()}
    return {
        slug: {
            "held": bool(counts.get((key >> 32 & 0xFFFFFFFF, key & 0xFFFFFFFF), 0)),
            "sessions": counts.get((key >> 32 & 0xFFFFFFFF, key & 0xFFFFFFFF), 0),
        }
        for slug, key in keys.items()
    }


class ScopedSource:
    """Validate source output before it reaches the existing persistence service."""

    def __init__(self, source, scope):
        self._source = source
        self.scope = scope
        self.slug = source.slug
        self.capabilities = source.capabilities
        self.name = source.name
        self.source_type = source.source_type

    def __getattr__(self, name):
        return getattr(self._source, name)

    def fetch(self, content=None, *, provider=None, country=None, cursor=None, **kwargs):
        if content is None or content.tmdb_id != self.scope["tmdb_id"]:
            raise ExperimentBlocked("catalogue or unlisted title operation denied")
        if content.content_type != self.scope["media_type"] or country != self.scope["country"]:
            raise ExperimentBlocked("media type or country escaped manifest scope")
        result = self._source.fetch(content, provider=provider, country=country, cursor=cursor, **kwargs)
        if result.country and str(result.country).upper() != self.scope["country"]:
            raise ExperimentBlocked("source result declared a different country")
        offers = []
        for item in result.offers:
            if (item.tmdb_id != self.scope["tmdb_id"]
                    or str(item.content_type) != self.scope["media_type"]
                    or str(item.country).upper() != self.scope["country"]
                    or item.season_number is not None or item.episode_number is not None):
                raise ExperimentBlocked("source result escaped manifest identity")
            offers.append(item)
        wanted_offer_type = self.scope["expected_offer_type"]
        if wanted_offer_type:
            result = replace(
                result,
                offers=[item for item in offers if str(item.offer_type) == wanted_offer_type],
            )
        else:
            result = replace(result, offers=offers)
        # ProviderContent mappings and source withdrawals are outside this
        # experiment's title-country offer objective; dropping both prevents
        # a source payload from changing unrelated catalogue identities.
        return replace(
            result, contents=[], offer_withdrawals=[], authoritative=False,
            complete_snapshot=False, cursor=None, country=self.scope["country"],
        )


def execute_manifest(manifest, authorization):
    """Run only the two listed JustWatch title scopes; never drain queue work."""
    validate_manifest(manifest)
    if database_identity() != authorization["database_identity"]:
        raise ExperimentBlocked("database environment changed")
    readiness = read_only_preflight(manifest)
    if readiness["readiness"] != "ready":
        raise ExperimentBlocked("preflight blocked: " + "; ".join(readiness["blockers"]))
    summaries = []
    # Hold the shared title-drain lock across the entire manifest. This makes
    # per-scope completion reads and reservations serial across restarts/runners.
    with title_drain_lock() as acquired:
        if not acquired:
            raise ExperimentBlocked("shared A7 title-drain lock is held")
        experiment, _ = AvailabilityExperiment.objects.get_or_create(
            authorization_id=authorization["authorization_id"],
            defaults={
                "manifest_digest": authorization["manifest_digest"],
                "database_identity": authorization["database_identity"],
                "maximum_attempts": GLOBAL_LIMIT,
            },
        )
        if (experiment.manifest_digest != authorization["manifest_digest"]
                or experiment.database_identity != authorization["database_identity"]
                or experiment.maximum_attempts != GLOBAL_LIMIT):
            raise ExperimentBlocked("durable experiment record conflicts with authorization")
        for scope in manifest["scopes"]:
            # Read durable recovery state only after owning the cross-process
            # lock; a stale process snapshot can never trigger a duplicate call.
            completed = _completed_scope_ids(authorization["authorization_id"])
            if scope["scope_id"] in completed:
                summaries.append({"scope_id": scope["scope_id"], "status": "already_completed"})
                continue
            incomplete = _incomplete_scope_ids(authorization["authorization_id"], completed)
            if incomplete:
                raise ExperimentBlocked(
                    "reserved incomplete scope requires manual reconciliation: "
                    + ", ".join(sorted(incomplete))
                )
            if not try_source_lock(scope["source_slug"]):
                raise ExperimentBlocked("shared per-source catalogue lock is held")
            transport = None
            try:
                movie = Movie.objects.get(tmdb_id=scope["tmdb_id"], content_type=scope["media_type"])
                if movie.title != scope["title"] or movie.year != scope["year"]:
                    raise ExperimentBlocked("current Movie title/year differs from manifest")
                transport = ReservedTransport(
                    authorization=authorization, scope=scope, source_slug=scope["source_slug"]
                )
                if scope["source_slug"] == "justwatch":
                    client = JustWatchClient(http_post=transport, retries=scope["maximum_attempts"])
                    source = JustWatchSource(client=client)
                else:
                    raise ExperimentBlocked("no dedicated instrumented client for source")
                AvailabilityRefreshService(source=ScopedSource(source, scope)).refresh_content(
                    movie, country=scope["country"]
                )
                with transaction.atomic():
                    experiment = AvailabilityExperiment.objects.select_for_update().get(
                        authorization_id=authorization["authorization_id"]
                    )
                    AvailabilityExperimentScope.objects.get_or_create(
                        experiment=experiment, scope_id=scope["scope_id"]
                    )
                summaries.append({"scope_id": scope["scope_id"], "status": "completed"})
            finally:
                if transport is not None:
                    transport.session.close()
                release_source_lock(scope["source_slug"])
    return summaries


def _completed_scope_ids(authorization_id):
    return set(AvailabilityExperimentScope.objects.filter(
        experiment_id=authorization_id
    ).values_list("scope_id", flat=True))


def _incomplete_scope_ids(authorization_id, completed=None):
    completed = completed if completed is not None else _completed_scope_ids(authorization_id)
    return set(AvailabilityExperimentAttempt.objects.filter(
        experiment_id=authorization_id
    ).exclude(scope_id__in=completed).values_list("scope_id", flat=True))
