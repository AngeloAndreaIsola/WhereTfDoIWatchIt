"""Offline, evidence-pinned provisioning plan for Stage D typed title identities.

The runner's identity gate (issue97_stage_d_runner._identity_blockers) requires an
exact typed Movie row (tmdb_id, content_type, title, year) for every admitted
scope.  A freshly migrated disposable target has none, so preflight reports one
blocker per scope.  This module derives the exact rows from the committed,
SHA-256-pinned cohort and identity manifest; it invents no identity and never
admits the held Coherence row.
"""

import csv
import hashlib
import json
import os
from pathlib import Path

from django.conf import settings

from availability.audit_identity import load_identity_manifest
from availability.services.issue97_stage_d_policy import MANIFEST_SHA256, load_policy

DOCS_ROOT_ENV = "ISSUE97_STAGE_D_DOCS_ROOT"
COHORT_RELATIVE_PATH = Path("docs/audits/issue97/stage-d-remaining-cohort.csv")
IDENTITY_MANIFEST_RELATIVE_PATH = Path("docs/audits/stage2b/identity_manifest_20261122.csv")
COHORT_SHA256 = "f573eff980127bb08943ea2c101d8354c6f18a6fd659e24ca727ae0576354e30"
IDENTITY_MANIFEST_SHA256 = "ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925"
HELD_SCOPE_ID = "movie-220289-nl"
HELD_REASON = "BLOCKED_YEAR_MISMATCH"


def docs_root():
    """Resolve the repository docs root without depending on the module's depth.

    The runtime image deliberately excludes repository docs, so an explicit
    mount is injected with ``ISSUE97_STAGE_D_DOCS_ROOT``.  Outside a container
    the backend ``BASE_DIR`` is the ``backend/`` directory, so its parent is the
    checkout root that owns ``docs/``.
    """
    configured = os.environ.get(DOCS_ROOT_ENV)
    if configured and configured.strip():
        return Path(configured).resolve()
    return Path(settings.BASE_DIR).resolve().parent


def pinned_paths():
    root = docs_root()
    return root / COHORT_RELATIVE_PATH, root / IDENTITY_MANIFEST_RELATIVE_PATH


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cohort_rows(cohort):
    with cohort.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def provisioning_plan(*, tmdb_evidence_dir=None):
    """Build and validate the exact 12-row provisioning plan from pinned evidence.

    Returns a serializable dict.  Raises ValueError on any pin mismatch, so a
    caller can never provision from altered or assumed data.
    """
    cohort, identity_manifest = pinned_paths()
    if _digest(cohort) != COHORT_SHA256 or _digest(identity_manifest) != IDENTITY_MANIFEST_SHA256:
        raise ValueError("Stage D identity pins differ from the reviewed cohort or manifest")
    policy = load_policy()
    identities = load_identity_manifest(
        identity_manifest, evidence_dir=tmdb_evidence_dir,
    )
    rows = _cohort_rows(cohort)
    by_scope = {row["scope_id"]: row for row in rows}
    if len(by_scope) != len(rows) or len(rows) != 13:
        raise ValueError("Stage D cohort must contain exactly 13 distinct rows")

    plan = []
    for scope in policy["scopes"]:
        scope_id = scope["scope_id"]
        row = by_scope.get(scope_id)
        if row is None:
            raise ValueError(f"Runner scope {scope_id} is absent from the pinned cohort")
        if not row["identity_status"].startswith("VERIFIED_"):
            raise ValueError(f"Runner scope {scope_id} is not an admitted identity")
        key = (scope["tmdb_id"], scope["media_type"])
        identity = identities.get(key)
        if identity is None:
            raise ValueError(f"Runner scope {scope_id} has no reviewed identity manifest entry")
        expected = {
            "tmdb_id": scope["tmdb_id"],
            "content_type": scope["media_type"],
            "title": identity.canonical_title,
            "year": identity.year,
        }
        observed = {
            "tmdb_id": int(row["tmdb_id"]),
            "content_type": row["media_type"],
            "title": row["canonical_title"],
            "year": int(row["canonical_year"]),
        }
        if observed != expected:
            raise ValueError(f"Runner scope {scope_id} disagrees with the pinned cohort identity")
        if identity.evidence_sha256 != scope["identity_evidence_sha256"]:
            raise ValueError(f"Runner scope {scope_id} evidence SHA-256 disagrees with the manifest")
        plan.append({
            "scope_id": scope_id,
            "country": row["country"],
            "identity_status": row["identity_status"],
            "identity_evidence_sha256": identity.evidence_sha256,
            **expected,
        })

    held = by_scope.get(HELD_SCOPE_ID)
    if held is None or held["identity_status"] != HELD_REASON:
        raise ValueError("The held Coherence row is missing or no longer held")
    if any(entry["scope_id"] == HELD_SCOPE_ID for entry in plan):
        raise ValueError("The held Coherence row must never enter the provisioning plan")

    return {
        "cohort_sha256": COHORT_SHA256,
        "identity_manifest_sha256": IDENTITY_MANIFEST_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "evidence_checked": bool(tmdb_evidence_dir),
        "held": {
            "scope_id": HELD_SCOPE_ID,
            "identity_status": HELD_REASON,
            "pilot_year": int(held["pilot_year"]),
            "canonical_year": int(held["canonical_year"]),
        },
        "rows": plan,
    }
