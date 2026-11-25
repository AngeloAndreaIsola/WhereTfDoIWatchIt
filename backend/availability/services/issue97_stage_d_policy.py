"""Fixed Stage D title policy and independent authorization inputs."""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from availability.services.experiment_runner import ExperimentBlocked, manifest_digest
from availability.services.source_permissions import reviewed_permission
from availability.sources.justwatch_client import GRAPHQL_URL, SEARCH_TITLES_QUERY

MANIFEST_SHA256 = "d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727"
MANIFEST_FILE = Path(__file__).with_name("issue97_stage_d_titles.json")
EVIDENCE_ENV = "AVAILABILITY_STAGE_D_EVIDENCE"
KEY_ENV = "AVAILABILITY_STAGE_D_AUTH_KEY"
FRESHNESS = timedelta(hours=1)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_policy():
    try:
        policy = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExperimentBlocked("Stage D policy could not be loaded") from exc
    if (manifest_digest(policy) != MANIFEST_SHA256
            or policy["endpoint"] != GRAPHQL_URL
            or sha256(SEARCH_TITLES_QUERY.encode()) != policy["document_sha256"]):
        raise ExperimentBlocked("Stage D manifest or GraphQL document differs from the pinned policy")
    return policy


def stable_run_id(policy, database):
    return uuid5(NAMESPACE_URL, f"{policy['experiment_id']}:{database}")


def batch_bounds(policy, batch):
    sizes = policy["batch_sizes"]
    if type(batch) is not int or not 1 <= batch <= len(sizes):
        raise ExperimentBlocked("Stage D requires an exact title batch from 1 through 3")
    start = sum(sizes[:batch - 1])
    return start, start + sizes[batch - 1]


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("timestamp lacks timezone")
    return parsed


def _has_reference(item):
    return all(isinstance(item.get(key), str) and item[key].strip()
               for key in ("evidence_reference", "reviewed_by"))


def review_evidence(policy, database, source, now):
    """Check both permission layers, the reconciled quota, and the rate policy."""
    receipt = reviewed_permission(source, "NL", ["search"], now=now)
    if not receipt or receipt["countries"] != ["NL"] or receipt["operations"] != ["search"]:
        raise ExperimentBlocked("current runtime source receipt must cover NL search only")
    grant = receipt["evidence_reference"].strip()
    try:
        evidence = json.loads(os.environ.get(EVIDENCE_ENV, ""))
        grant_evidence, quota, rate = (evidence[key] for key in ("access_permission", "quota", "rate_policy"))
        records = (grant_evidence, quota, rate)
        checked = [_timestamp(item["checked_at"]) for item in records]
        if not all(now - FRESHNESS < stamp <= now for stamp in checked):
            raise ExperimentBlocked("permission, quota or rate evidence is stale or future-dated")
        if (not all(_has_reference(item) for item in records)
                or evidence.get("database_identity") != database
                or evidence.get("grant_reference") != grant
                or evidence.get("exclusive_grant_use") is not True):
            raise ExperimentBlocked("reviewed evidence must identify the grant, exclusive use and database")
        if (grant_evidence.get("status") != "authorized"
                or grant_evidence["evidence_reference"].strip() != grant
                or grant_evidence.get("endpoint") != policy["endpoint"]
                or grant_evidence.get("country") != "NL"
                or grant_evidence.get("operations") != [policy["operation"]]
                or grant_evidence.get("document_sha256") != policy["document_sha256"]
                or grant_evidence.get("scope_ids") != [scope["scope_id"] for scope in policy["scopes"]]
                or type(grant_evidence.get("maximum_requests")) is not int
                or grant_evidence["maximum_requests"] < policy["maximum_attempts"]
                or type(grant_evidence.get("maximum_retries")) is not int
                or grant_evidence["maximum_retries"] != policy["maximum_retries"]
                or not _timestamp(grant_evidence["valid_from"]) <= now < _timestamp(grant_evidence["valid_until"])):
            raise ExperimentBlocked("independent access evidence does not authorize the exact title policy")
        if (type(rate.get("maximum_in_flight")) is not int
                or rate["maximum_in_flight"] < policy["maximum_in_flight"]):
            raise ExperimentBlocked("rate evidence must allow one in-flight request")
        interval = rate.get("minimum_interval_seconds")
        if (type(rate.get("max_burst")) is not int or rate["max_burst"] < policy["burst"]
                or type(interval) not in (int, float) or not isfinite(interval) or not 0 <= interval <= 86400):
            raise ExperimentBlocked("reviewed burst or minimum interval is missing or invalid")
        if quota.get("status") not in ("available", "unmetered_public"):
            raise ExperimentBlocked("current quota classification is unknown")
        if quota["status"] == "available" and (
                not isinstance(quota.get("window_id"), str) or not quota["window_id"].strip()
                or type(quota.get("remaining")) is not int or quota["remaining"] < 0
                or type(quota.get("spent_at_review")) is not int or quota["spent_at_review"] < 0):
            raise ExperimentBlocked("metered evidence requires a reconciled balance and upstream window")
        if "prior_receipt_idle_since" in evidence and _timestamp(evidence["prior_receipt_idle_since"]) > now:
            raise ExperimentBlocked("reviewed prior-grant idle timestamp is future-dated")
        expires = min(
            _timestamp(receipt["valid_until"]), _timestamp(grant_evidence["valid_until"]),
            _timestamp(quota["valid_until"]), _timestamp(rate["valid_until"]),
            *(stamp + FRESHNESS for stamp in checked),
        )
        if now >= expires:
            raise ExperimentBlocked("permission or quota evidence expired")
        return evidence, receipt, expires
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ExperimentBlocked("permission, quota or rate evidence is missing or malformed") from exc


def authorization_scope(policy, database, batch, checkpoint_digest, evidence, receipt):
    start, end = batch_bounds(policy, batch)
    return {
        "experiment_id": policy["experiment_id"],
        "authorization_id": str(stable_run_id(policy, database)),
        "manifest_digest": MANIFEST_SHA256,
        "database_identity": database,
        "phase": policy["phase"],
        "batch": batch,
        "scope_ids": [scope["scope_id"] for scope in policy["scopes"][start:end]],
        "country": "NL",
        "endpoint": policy["endpoint"],
        "method": policy["method"],
        "operation": policy["operation"],
        "document_sha256": policy["document_sha256"],
        "maximum_attempts": policy["maximum_attempts"],
        "maximum_batch_attempts": end - start,
        "maximum_retries": policy["maximum_retries"],
        "prior_checkpoints_sha256": checkpoint_digest,
        "evidence_sha256": manifest_digest(evidence),
        "runtime_receipt_sha256": manifest_digest(receipt),
    }


def check_authorization(token, expected, evidence_expiry, now):
    key = os.environ.get(KEY_ENV, "")
    if len(key.encode()) < 32:
        raise ExperimentBlocked("Stage D HMAC key is missing or too short")
    try:
        encoded, signature = token.strip().split(".")
        hmac_value = hmac.new(key.encode(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(hmac_value, signature):
            raise ValueError("signature mismatch")
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        payload = json.loads(decoded)
        expiry = _timestamp(payload.pop("expires_at"))
        if manifest_digest(payload) != manifest_digest(expected):
            raise ExperimentBlocked("authorization does not match the manifest, batch, database, budget, or evidence")
        if not now < expiry <= evidence_expiry:
            raise ExperimentBlocked("authorization is expired or outlives reviewed evidence")
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ExperimentBlocked("invalid Stage D authorization") from exc
    return expiry, sha256(token.strip().encode())
