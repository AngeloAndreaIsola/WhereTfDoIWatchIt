"""Guarded, auditable remediation of the historical confidence-0.7 mappings.

Background
----------

A read-only audit found six confidence-0.7 ``ContentMapping`` rows whose
provider-native content was resolved to the wrong TMDb title (a title collision
or a conflicting release year), together with the 20 ``Offer`` rows and one
``OfferEvidence`` row that hang off them. The audit also identified eight
regional release-year mappings that are *plausible and out of scope* and must
never be touched by this operation.

Why this is a separate, two-phase tool
--------------------------------------

``ContentMapping`` and ``Offer`` have **no** database foreign key between them,
``OfferEvidence``/``OfferPrice`` key on the ``offer_identity`` string with no FK
to ``Offer``, and all 20 affected offers belong to non-authoritative sync runs
(so ``OfferEvidenceRepository`` deactivation is not available). Deleting a
mapping therefore hides nothing and cascades nowhere: the offers must be
removed explicitly, and the dependent evidence row must be removed with them.

Because the operation is destructive and irreversible once committed, it is
split into two phases:

1. **Generation (read-only).** :func:`build_manifest` reads the exact rows the
   approved plan names and captures *all* their database columns plus the
   protected mappings' semantic fields, the schema/version marker, a timestamp
   and a credential-free database identity that includes the PostgreSQL server's
   reported address/port (and cluster identifier when readable). A canonical
   SHA-256 digest covers the mutation contract — the plan, every target row,
   the protected mappings and the database identity — while excluding volatile
   provenance such as ``generated_at``, so the digest is reproducible across
   dry runs. An edited material field is still detectable.
2. **Mutation (explicit).** :func:`apply_remediation` consumes that exact
   manifest, refuses to regenerate it, re-validates every row against the live
   database inside one transaction, updates mapping ``399`` first, deletes only
   the allow-listed rows, and asserts every invariant before commit. Any
   mismatch aborts the whole transaction.

Fail-closed rules
-----------------

The module never broadens an operation to rows that merely share a title,
provider or year: every delete is keyed by an explicit primary key from the
approved plan. A changed target row, a changed protected mapping, an extra row
in the targeted scope, a pre-existing orphan, a missing row, a manifest edited
after review, a manifest from another database, or a plan that differs from the
approved one all raise :class:`RemediationError`/``ManifestError`` and roll the
transaction back.

The worker-deployment and concurrency preconditions are *not* silently assumed:

* :func:`verify_local_matcher_fix` checks the code actually loaded in the
  process performing the mutation;
* :func:`acquire_maintenance_barrier` takes the exclusive PostgreSQL advisory
  lock shared by every :class:`AvailabilityRefreshService` write
  (``write_barrier``). A point-in-time ``pg_locks`` count is only diagnostic:
  the exclusive lock is what prevents a new refresh from starting.
* Worker-container contents cannot be inspected from here, so that check is
  documented as an operator step in the runbook and is not claimed by this
  module. Advisory locks can only exclude writers deployed with the same
  barrier code.

The remediation no longer calls the repository-wide ``purge_orphans()``
helpers. It deletes only the explicitly approved evidence row and refuses to
run at all if any unrelated orphan exists, so it cannot clean up data outside
the reviewed plan.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from ..domain.enums import MatchMethod
from ..models import ContentMapping, Offer, OfferEvidence, OfferPrice
from ..persistence.repositories import MappingRepository
from .write_barrier import (
    MaintenanceBarrier,
    acquire_maintenance_barrier,
)

#: Manifest contract version. A manifest carrying another value is rejected.
#: v3 makes the digest a canonical hash of the mutation contract (everything
#: except the volatile ``generated_at`` and the digest itself); v1/v2 manifests
#: embedded the generation timestamp in the digest, so the digest changed on
#: every otherwise-identical dry run. They must be regenerated during a
#: separately approved read-only operation.
MANIFEST_SCHEMA = "remediate_mapping_audit/manifest-v3"

#: Database-identity contract version embedded in the manifest's ``database``
#: section (the manifest schema itself is :data:`MANIFEST_SCHEMA`).
DATABASE_IDENTITY_SCHEMA = "remediate_mapping_audit/database-identity-v2"

#: Source-file marker for the deployed movie-matcher fix. The matcher must
#: leave a Movie with a known, conflicting year unmapped; the comment carrying
#: this text lives beside that branch (``availability/services/matching.py``).
MATCHER_FIX_SENTINEL = "A known, conflicting year is evidence against the"

#: Source-file marker for the deployed refresh write barrier. Every container
#: that can write availability rows must carry it (see the runbook).
WRITER_BARRIER_SENTINEL = "AVAILABILITY_MAINTENANCE_BARRIER_V1"

#: Number of hex characters of the manifest digest an operator must type as
#: part of ``--confirm``. Long enough to make an accidental match negligible,
#: short enough to compare by eye.
CONFIRMATION_DIGEST_PREFIX = 12

#: Confirmations that are too generic to prove operator intent.
_GENERIC_CONFIRMATIONS = frozenset(
    {
        "", "y", "yes", "yeah", "ok", "okay", "true", "confirm", "confirmed",
        "apply", "go", "doit", "do-it", "none", "n/a", "na", "test", "ticket",
        "1",
    }
)

#: A ticket-like operator confirmation: starts alphanumeric, at least four
#: characters, no spaces. "AVAIL-1234", "GH-42" and a bare issue number such as
#: "1234" all qualify.
_CONFIRMATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{2,}$")
_DIGEST_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{%d}$" % CONFIRMATION_DIGEST_PREFIX)



class RemediationError(Exception):
    """A precondition, manifest or invariant failed; nothing may be written."""


class ManifestError(RemediationError):
    """The manifest is missing, malformed, edited, or from another database."""


# --- approved plan ---------------------------------------------------------


def _offer_state(
    offer_id: int,
    provider: str,
    provider_content_id: str,
    tmdb_id: int,
    content_type: str,
    *,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    offer_type: str = "free",
    country: str = "GB",
) -> dict:
    return {
        "id": offer_id,
        "provider": provider,
        "provider_content_id": provider_content_id,
        "tmdb_id": tmdb_id,
        "content_type": content_type,
        "season_number": season,
        "episode_number": episode,
        "offer_type": offer_type,
        "country": country,
    }


def build_approved_plan() -> dict:
    """Return the fixed, audited remediation plan.

    Every dict key that names a row is a string because the same structure is
    embedded in the JSON manifest and must compare equal after a round trip.
    """
    office_pcid = "b00jd68z"
    offers: dict[str, dict] = {
        "3504": _offer_state(3504, "bbc_iplayer", office_pcid, 2316, "series"),
        "3505": _offer_state(
            3505, "bbc_iplayer", office_pcid, 2316, "season", season=1
        ),
        "3512": _offer_state(
            3512, "bbc_iplayer", office_pcid, 2316, "season", season=2
        ),
        "3519": _offer_state(3519, "bbc_iplayer", "b04v89fn", 10601, "movie"),
        "3455": _offer_state(3455, "bbc_iplayer", "b06s2h64", 12092, "movie"),
        "3457": _offer_state(3457, "bbc_iplayer", "m000c74q", 527634, "movie"),
        "1": _offer_state(1, "cbc_gem", "drive", 64690, "movie", country="CA"),
        "10820": _offer_state(
            10820, "tubi", "608226", 1247, "movie",
            offer_type="free_with_ads", country="US",
        ),
    }
    for episode in range(1, 7):
        offers[str(3505 + episode)] = _offer_state(
            3505 + episode, "bbc_iplayer", office_pcid, 2316, "episode",
            season=1, episode=episode,
        )
        offers[str(3512 + episode)] = _offer_state(
            3512 + episode, "bbc_iplayer", office_pcid, 2316, "episode",
            season=2, episode=episode,
        )

    def mapping(mid, provider, pcid, content_type, tmdb_id):
        return {
            "id": mid,
            "provider": provider,
            "provider_content_id": pcid,
            "content_type": content_type,
            "tmdb_id": tmdb_id,
            "match_method": MatchMethod.TITLE_YEAR.value,
            "confidence": 0.7,
        }

    mappings = {
        "399": mapping(399, "bbc_iplayer", office_pcid, "series", 2316),
        "400": mapping(400, "bbc_iplayer", "b04v89fn", "movie", 10601),
        "386": mapping(386, "bbc_iplayer", "b06s2h64", "movie", 12092),
        "388": mapping(388, "bbc_iplayer", "m000c74q", "movie", 527634),
        "1": mapping(1, "cbc_gem", "drive", "movie", 64690),
        "4760": mapping(4760, "tubi", "608226", "movie", 1247),
    }

    delete_offer_ids = sorted(int(key) for key in offers)
    return {
        "plan_version": 1,
        "update_mapping": {
            "id": 399,
            "tmdb_id": 2996,
            "match_method": MatchMethod.MANUAL.value,
            "confidence": 1.0,
        },
        "delete_mapping_ids": [400, 386, 388, 1, 4760],
        "delete_offer_ids": delete_offer_ids,
        "delete_evidence_ids": [1],
        "protected_mapping_ids": [128, 133, 146, 151, 419, 4369, 4375, 4376],
        "expected_counts": {
            "mappings": 6,
            "offers": 20,
            "evidence": 1,
            "offer_prices": 0,
            "protected_mappings": 8,
        },
        "expected_mapping_state": mappings,
        "expected_offer_state": offers,
        "expected_evidence_state": {
            "1": {
                "id": 1,
                "provider_slug": "tubi",
                "offer_identity": "tubi|US|movie|1247|||free_with_ads|608226",
                "source": "tubi",
            }
        },
    }


#: The single approved plan this command is allowed to execute.
APPROVED_PLAN = build_approved_plan()


def _plan(plan: Optional[dict]) -> dict:
    return plan if plan is not None else APPROVED_PLAN


# --- serialization helpers -------------------------------------------------


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def serialize_columns(instance) -> dict:
    """Every concrete database column of ``instance`` as JSON-safe values.

    Uses ``field.attname`` so foreign keys appear as their stored ``*_id``
    column (the actual database column), exactly like a row dump.
    """
    return {
        field.attname: _serialize_value(getattr(instance, field.attname))
        for field in instance._meta.concrete_fields
    }


def _mapping_associations(mapping: ContentMapping) -> dict:
    return {
        "id": mapping.id,
        "provider": mapping.provider.slug,
        "provider_content_id": mapping.provider_content_id,
        "content_type": mapping.content_type,
        "tmdb_id": mapping.tmdb_id,
        "match_method": mapping.match_method,
        "confidence": float(mapping.confidence),
    }


def _offer_associations(offer: Offer) -> dict:
    return {
        "id": offer.id,
        "provider": offer.provider.slug,
        "provider_content_id": offer.provider_content_id,
        "tmdb_id": offer.tmdb_id,
        "content_type": offer.content_type,
        "season_number": offer.season_number,
        "episode_number": offer.episode_number,
        "offer_type": offer.offer_type,
        "country": offer.country.code,
    }


def _evidence_associations(evidence: OfferEvidence) -> dict:
    return {
        "id": evidence.id,
        "provider_slug": evidence.provider_slug,
        "offer_identity": evidence.offer_identity,
        "source": evidence.source.slug,
    }


# --- database introspection -------------------------------------------------


def _postgres_server_identity() -> dict:
    """Identity reported by the PostgreSQL server itself (no credentials).

    ``inet_server_addr()``/``inet_server_port()`` are NULL for Unix-socket
    connections.  In that case the cluster identifier from
    ``pg_control_system()`` is used when the current role may read it; if
    neither is available the identity is reported as ``weak`` and the mutation
    refuses to run rather than treating a configured hostname as proof.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_database(),
                   inet_server_addr()::text,
                   inet_server_port(),
                   current_setting('server_version')
            """
        )
        database, address, port, version = cursor.fetchone()

        system_identifier = None
        cursor.execute(
            """
            SELECT has_function_privilege(
                       current_user, 'pg_catalog.pg_control_system()', 'EXECUTE'
                   )
            """
        )
        may_read_control = bool(cursor.fetchone()[0])
        if may_read_control:
            try:
                cursor.execute(
                    "SELECT system_identifier::text FROM pg_control_system()"
                )
                system_identifier = cursor.fetchone()[0]
            except DatabaseError:
                # Permission assumptions differ across PostgreSQL builds; an
                # unreadable control file only weakens the identity, it never
                # substitutes a configured hostname for a server fact.
                system_identifier = None

    if system_identifier:
        strength = "cluster"
    elif address and port:
        strength = "network"
    else:
        strength = "weak"
    return {
        "database": database,
        "address": address,
        "port": int(port) if port is not None else None,
        "version": version,
        "system_identifier": system_identifier,
        "strength": strength,
    }


def database_identity() -> dict:
    """Credential-free identifier for the database this process is using.

    Deliberately excludes ``USER``/``PASSWORD``/connection strings.  On
    PostgreSQL the server-reported database name, address and port are the
    authoritative fields; the configured host/port are recorded only as
    context.  A manifest generated for a different server with the same
    database name therefore does not match.
    """
    settings_dict = connection.settings_dict
    identity = {
        "schema": DATABASE_IDENTITY_SCHEMA,
        "vendor": connection.vendor,
        "name": str(settings_dict.get("NAME", "")),
        "configured": {
            "host": str(settings_dict.get("HOST") or ""),
            "port": str(settings_dict.get("PORT") or ""),
        },
        "server": None,
        "strength": "local",
    }
    if connection.vendor == "postgresql":
        server = _postgres_server_identity()
        identity.update(
            name=server["database"],
            server={
                "address": server["address"],
                "port": server["port"],
                "version": server["version"],
                "system_identifier": server["system_identifier"],
            },
            strength=server["strength"],
        )
    return identity


def verify_local_matcher_fix() -> bool:
    """True when the code loaded in *this* process contains the matcher fix.

    This is a real check of the deployed module source, not a claim about the
    worker containers: they cannot be inspected from here, and the runbook
    documents how an operator verifies them separately.
    """
    import availability.services.matching as matching_module

    try:
        source = Path(matching_module.__file__).read_text(encoding="utf-8")
    except OSError:
        return False
    return MATCHER_FIX_SENTINEL in source


def verify_local_writer_barrier() -> bool:
    """True when this process carries the shared refresh write barrier code."""
    try:
        source = Path(__file__).with_name("write_barrier.py").read_text(
            encoding="utf-8"
        )
    except OSError:
        return False
    return WRITER_BARRIER_SENTINEL in source


def unverified_barrier_allowed() -> bool:
    """Test-only opt-in for SQLite, where advisory locks do not exist.

    Production settings never define this, so the default is fail closed.
    """
    return bool(
        getattr(
            settings, "AVAILABILITY_REMEDIATION_ALLOW_UNVERIFIED_BARRIER", False
        )
    )


def confirmation_digest_prefix(manifest_digest: str) -> str:
    """The human-verifiable prefix of a ``sha256:...`` digest."""
    value = (manifest_digest or "").strip()
    if value.startswith("sha256:"):
        value = value[len("sha256:") :]
    return value[:CONFIRMATION_DIGEST_PREFIX]


def validate_confirmation(value: Optional[str], *, manifest_digest: str) -> str:
    """Return ``TICKET@DIGEST_PREFIX`` for the exact manifest, or raise.

    The ticket alone is not a confirmation: it identifies the change, but not
    the content the operator reviewed.  The digest prefix makes the confirmation
    content-bound, so editing any manifest field invalidates the previous value
    even when the attacker recomputes the manifest's own digest.  This binds a
    confirmation to content, not to a person.
    """
    confirmation = (value or "").strip()
    if "@" not in confirmation:
        raise RemediationError(
            "operator confirmation must be TICKET@DIGEST_PREFIX, where "
            f"DIGEST_PREFIX is the first {CONFIRMATION_DIGEST_PREFIX} hex "
            "characters of the manifest digest (not a bare ticket id)"
        )
    ticket, _, prefix = confirmation.rpartition("@")
    ticket = ticket.strip()
    if (
        len(ticket) < 4
        or ticket.lower() in _GENERIC_CONFIRMATIONS
        or not _CONFIRMATION_RE.match(ticket)
    ):
        raise RemediationError(
            "operator confirmation must start with a meaningful ticket id "
            "(e.g. AVAIL-1234), not a generic word"
        )
    if not _DIGEST_PREFIX_RE.match(prefix or ""):
        raise RemediationError(
            f"digest prefix must be exactly {CONFIRMATION_DIGEST_PREFIX} hex "
            "characters, taken from the manifest digest shown by the command"
        )
    expected = confirmation_digest_prefix(manifest_digest)
    if prefix.lower() != expected.lower():
        raise RemediationError(
            "confirmation digest prefix does not match this manifest; the "
            "manifest content or digest changed after it was reviewed"
        )
    return f"{ticket}@{prefix.lower()}"



# --- target loading and validation -----------------------------------------


def _target_mapping_ids(plan: dict) -> list:
    return [plan["update_mapping"]["id"], *plan["delete_mapping_ids"]]


def _target_scope_pairs(plan: dict) -> set:
    """The ``(provider, provider_content_id)`` pairs the plan owns.

    Deletion is still keyed by primary key; this scope exists only to detect an
    *extra* row a refresh may have created inside the targeted content, which
    must abort the run rather than be silently ignored.
    """
    return {
        (state["provider"], state["provider_content_id"])
        for state in plan["expected_mapping_state"].values()
    }


def _load_targets(plan: dict):
    """Load and validate every allow-listed row against the approved plan.

    Read-only. Raises :class:`RemediationError` when a row is missing, an
    association differs from the plan, the counts differ, an extra row exists
    in the targeted scope, a targeted price row appears, or any orphan exists.
    Returns the model instances the manifest captures.
    """
    counts = plan["expected_counts"]
    mappings: dict[str, ContentMapping] = {}
    for mapping_id in _target_mapping_ids(plan):
        row = (
            ContentMapping.objects.select_related("provider", "source", "content")
            .filter(id=mapping_id)
            .first()
        )
        if row is None:
            raise RemediationError(f"target mapping {mapping_id} is missing")
        mappings[str(mapping_id)] = row

    offers: dict[str, Offer] = {}
    for offer_id in plan["delete_offer_ids"]:
        row = (
            Offer.objects.select_related("provider", "country", "source", "sync_run")
            .filter(id=offer_id)
            .first()
        )
        if row is None:
            raise RemediationError(f"target offer {offer_id} is missing")
        offers[str(offer_id)] = row

    evidence: dict[str, OfferEvidence] = {}
    for evidence_id in plan["delete_evidence_ids"]:
        row = (
            OfferEvidence.objects.select_related("source")
            .filter(id=evidence_id)
            .first()
        )
        if row is None:
            raise RemediationError(f"target evidence {evidence_id} is missing")
        evidence[str(evidence_id)] = row

    if len(mappings) != counts["mappings"]:
        raise RemediationError(
            f"expected {counts['mappings']} target mappings, saw {len(mappings)}"
        )
    if len(offers) != counts["offers"]:
        raise RemediationError(
            f"expected {counts['offers']} target offers, saw {len(offers)}"
        )
    if len(evidence) != counts["evidence"]:
        raise RemediationError(
            f"expected {counts['evidence']} target evidence rows, saw {len(evidence)}"
        )

    for mapping_id, row in mappings.items():
        expected = plan["expected_mapping_state"].get(mapping_id)
        if expected is None:
            raise RemediationError(f"plan has no expectation for mapping {mapping_id}")
        actual = _mapping_associations(row)
        if actual != expected:
            raise RemediationError(
                f"mapping {mapping_id} does not match the plan:\n"
                f"  expected {expected}\n  actual   {actual}"
            )

    for offer_id, row in offers.items():
        expected = plan["expected_offer_state"].get(offer_id)
        if expected is None:
            raise RemediationError(f"plan has no expectation for offer {offer_id}")
        actual = _offer_associations(row)
        if actual != expected:
            raise RemediationError(
                f"offer {offer_id} does not match the plan:\n"
                f"  expected {expected}\n  actual   {actual}"
            )

    for evidence_id, row in evidence.items():
        expected = plan["expected_evidence_state"].get(evidence_id)
        if expected is None:
            raise RemediationError(f"plan has no expectation for evidence {evidence_id}")
        actual = _evidence_associations(row)
        if actual != expected:
            raise RemediationError(
                f"evidence {evidence_id} does not match the plan:\n"
                f"  expected {expected}\n  actual   {actual}"
            )

    # No other offer may live inside the targeted content.
    scope_query = Q()
    for provider_slug, provider_content_id in sorted(_target_scope_pairs(plan)):
        scope_query |= Q(
            provider__slug=provider_slug, provider_content_id=provider_content_id
        )
    in_scope = set(
        Offer.objects.filter(scope_query).values_list("id", flat=True)
    )
    allowed_offers = set(plan["delete_offer_ids"])
    if in_scope != allowed_offers:
        extra = sorted(in_scope - allowed_offers)
        missing = sorted(allowed_offers - in_scope)
        raise RemediationError(
            "targeted offer scope does not match the plan "
            f"(extra ids {extra}, missing ids {missing})"
        )

    # Dependent rows must be exactly the allow-listed evidence and no prices.
    target_identities = {row.identity for row in offers.values()}
    scoped_evidence = set(
        OfferEvidence.objects.filter(offer_identity__in=target_identities)
        .values_list("id", flat=True)
    )
    if scoped_evidence != set(plan["delete_evidence_ids"]):
        raise RemediationError(
            "targeted evidence rows do not match the plan "
            f"(expected {plan['delete_evidence_ids']}, saw {sorted(scoped_evidence)})"
        )
    scoped_prices = list(
        OfferPrice.objects.filter(offer_identity__in=target_identities)
        .values_list("id", flat=True)
    )
    if scoped_prices:
        raise RemediationError(
            f"unexpected OfferPrice rows for targeted offers: {scoped_prices}"
        )

    _assert_no_orphans()

    protected: dict[str, ContentMapping] = {}
    for mapping_id in plan["protected_mapping_ids"]:
        row = (
            ContentMapping.objects.select_related("provider")
            .filter(id=mapping_id)
            .first()
        )
        if row is None:
            raise RemediationError(f"protected mapping {mapping_id} is missing")
        protected[str(mapping_id)] = row
    if len(protected) != counts["protected_mappings"]:
        raise RemediationError(
            f"expected {counts['protected_mappings']} protected mappings, "
            f"saw {len(protected)}"
        )

    return mappings, offers, evidence, protected


def _assert_no_orphans():
    orphan_evidence = OfferEvidence.objects.exclude(
        offer_identity__in=Offer.objects.values("identity")
    )
    if orphan_evidence.exists():
        raise RemediationError(
            "orphaned OfferEvidence rows already exist; this command does not "
            "purge unrelated data. Investigate and clean them in a separately "
            "approved operation before retrying"
        )
    orphan_prices = OfferPrice.objects.exclude(
        offer_identity__in=Offer.objects.values("identity")
    )
    if orphan_prices.exists():
        raise RemediationError(
            "orphaned OfferPrice rows already exist; this command does not "
            "purge unrelated data. Investigate and clean them in a separately "
            "approved operation before retrying"
        )


def _targets_payload(plan: dict) -> dict:
    mappings, offers, evidence, protected = _load_targets(plan)
    return {
        "mappings": [
            {
                "id": row.id,
                "columns": serialize_columns(row),
                "associations": _mapping_associations(row),
            }
            for _, row in sorted(mappings.items(), key=lambda item: int(item[0]))
        ],
        "offers": [
            {
                "id": row.id,
                "columns": serialize_columns(row),
                "associations": _offer_associations(row),
            }
            for _, row in sorted(offers.items(), key=lambda item: int(item[0]))
        ],
        "evidence": [
            {
                "id": row.id,
                "columns": serialize_columns(row),
                "associations": _evidence_associations(row),
            }
            for _, row in sorted(evidence.items(), key=lambda item: int(item[0]))
        ],
        "protected_mappings": [
            {
                "id": row.id,
                "associations": _mapping_associations(row),
            }
            for _, row in sorted(protected.items(), key=lambda item: int(item[0]))
        ],
        "offer_prices": {"expected": 0, "found": 0, "ids": []},
    }


# --- manifest ---------------------------------------------------------------


#: Manifest fields excluded from the canonical digest. ``generated_at`` is
#: provenance shown to the operator; it is not part of what will be mutated, so
#: two dry runs over the same plan must produce the same digest. ``digest`` is
#: the output, never an input.
VOLATILE_MANIFEST_FIELDS = frozenset({"digest", "generated_at"})

#: Plan fields that are *sets* of row ids: their order carries no meaning.
_UNORDERED_PLAN_ID_FIELDS = (
    "delete_mapping_ids",
    "delete_offer_ids",
    "delete_evidence_ids",
    "protected_mapping_ids",
)

#: Manifest target sections that are sets of rows keyed by primary key.
_TARGET_SECTIONS = ("mappings", "offers", "evidence", "protected_mappings")


def _id_sort_key(value) -> tuple:
    """Order numeric ids numerically, anything else after them by text."""
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def _sorted_ids(values) -> list:
    return sorted(values, key=_id_sort_key)


def canonical_manifest_body(manifest: dict) -> dict:
    """The part of a manifest that actually defines the mutation.

    Everything else in the manifest is either provenance (``generated_at``) or
    the digest itself. Inside the contract, collections that are semantically
    unordered are normalized so a regenerated manifest with the same plan and
    the same live rows hashes identically regardless of row-return order:
    id lists are sorted, target sections are ordered by primary key, and
    ``offer_prices.ids`` is sorted.
    """
    body = {
        key: value
        for key, value in manifest.items()
        if key not in VOLATILE_MANIFEST_FIELDS
    }

    plan = body.get("plan")
    if isinstance(plan, dict):
        plan = dict(plan)
        for field in _UNORDERED_PLAN_ID_FIELDS:
            if isinstance(plan.get(field), list):
                plan[field] = _sorted_ids(plan[field])
        body["plan"] = plan

    targets = body.get("targets")
    if isinstance(targets, dict):
        targets = dict(targets)
        for section in _TARGET_SECTIONS:
            rows = targets.get(section)
            if isinstance(rows, list):
                targets[section] = sorted(
                    rows, key=lambda row: _id_sort_key(row.get("id"))
                )
        prices = targets.get("offer_prices")
        if isinstance(prices, dict):
            prices = dict(prices)
            if isinstance(prices.get("ids"), list):
                prices["ids"] = _sorted_ids(prices["ids"])
            targets["offer_prices"] = prices
        body["targets"] = targets
    return body


def compute_digest(payload: dict) -> str:
    """Canonical SHA-256 over the mutation contract.

    Volatile metadata is excluded (see :func:`canonical_manifest_body`), so two
    dry runs of the same plan at different times produce the same digest. Every
    material field is included: the approved plan, the live column snapshot of
    every target row, the protected mappings, and the database identity. Edits
    to any of them change the digest and invalidate the reviewed confirmation.
    """
    body = canonical_manifest_body(payload)
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_manifest(*, plan: Optional[dict] = None, now=None) -> dict:
    """Read-only: capture and validate the approved plan against the database.

    Raises before producing a manifest when the database does not match the
    plan, so a manifest can never be generated from a drifted database.
    """
    plan = _plan(plan)
    now = now or timezone.now()
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": now.isoformat(),
        "database": database_identity(),
        "plan": plan,
        "targets": _targets_payload(plan),
    }
    manifest["digest"] = compute_digest(manifest)
    return manifest


def write_manifest(path, manifest: dict) -> str:
    """Write the reviewed manifest as pretty JSON. Returns the path written."""
    target = Path(path)
    target.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return str(target)


def load_manifest(path) -> dict:
    """Parse a manifest file, rejecting anything that is not a JSON object."""
    location = Path(path)
    if not location.exists():
        raise ManifestError(f"manifest not found: {location}")
    try:
        data = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest is not valid JSON ({location}): {exc}")
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    return data


_REQUIRED_TOP_LEVEL = {
    "schema", "generated_at", "database", "plan", "targets", "digest",
}
_REQUIRED_TARGET_SECTIONS = {
    "mappings", "offers", "evidence", "protected_mappings", "offer_prices",
}
_REQUIRED_DATABASE_KEYS = {
    "schema", "vendor", "name", "configured", "server", "strength",
}


def _check_shape(manifest: dict):
    missing = _REQUIRED_TOP_LEVEL - set(manifest)
    if missing:
        raise ManifestError(f"manifest is missing keys: {sorted(missing)}")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ManifestError(
            f"unsupported manifest schema {manifest['schema']!r}; "
            f"expected {MANIFEST_SCHEMA!r}. Manifests generated before the "
            "canonical-digest contract (v1 and v2) are not accepted: their "
            "digest embeds the generation timestamp. Regenerate a v3 manifest "
            "during a separately approved read-only operation."
        )
    targets = manifest["targets"]
    if not isinstance(targets, dict) or (_REQUIRED_TARGET_SECTIONS - set(targets)):
        raise ManifestError("manifest targets section is malformed")
    if not isinstance(manifest["plan"], dict):
        raise ManifestError("manifest plan section is malformed")
    database = manifest["database"]
    if not isinstance(database, dict) or (
        _REQUIRED_DATABASE_KEYS - set(database)
    ):
        raise ManifestError("manifest database section is malformed")


def validate_manifest(manifest: dict, *, plan: Optional[dict] = None) -> None:
    """Structural, digest, plan and database checks. Read-only."""
    plan = _plan(plan)
    _check_shape(manifest)
    if manifest["digest"] != compute_digest(manifest):
        raise ManifestError(
            "manifest digest mismatch: the file was edited after review"
        )
    if manifest["plan"] != plan:
        raise ManifestError(
            "manifest plan differs from the approved plan this command knows"
        )
    live_identity = database_identity()
    if (
        live_identity["vendor"] == "postgresql"
        and live_identity.get("strength") == "weak"
    ):
        raise ManifestError(
            "cannot prove which PostgreSQL server this process is connected "
            "to: the server reports no network address and no readable "
            "pg_control_system() identifier. Connect over TCP (or grant the "
            "role permission to read the cluster identifier) and regenerate "
            "the manifest."
        )
    if manifest["database"] != live_identity:
        raise ManifestError(
            "manifest was generated for a different database instance "
            f"(manifest {manifest['database']!r}, live {live_identity!r})"
        )



def _first_difference(expected, actual, path="$"):
    """Return a short human-readable path to the first differing value."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                return f"{path}.{key}: unexpected {actual[key]!r}"
            if key not in actual:
                return f"{path}.{key}: missing (expected {expected[key]!r})"
            found = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{path}: length {len(expected)} != {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual)):
            found = _first_difference(left, right, f"{path}[{index}]")
            if found:
                return found
        return None
    if expected != actual:
        return f"{path}: expected {expected!r}, actual {actual!r}"
    return None


def revalidate_targets(manifest: dict, *, plan: Optional[dict] = None) -> None:
    """Re-read every allow-listed row and prove it is byte-identical to the
    manifest, plus that scope, dependent rows, prices and protected mappings are
    unchanged. Read-only; call again inside the mutation transaction.
    """
    plan = _plan(plan)
    fresh = _targets_payload(plan)
    if manifest["targets"] != fresh:
        difference = _first_difference(manifest["targets"], fresh)
        raise RemediationError(
            "manifest no longer matches the live database; regenerate and "
            f"re-review it (first difference: {difference})"
        )


# --- mutation ---------------------------------------------------------------


def _non_target_counts(plan: dict) -> dict:
    """Counts of every row the plan does *not* own, for the unchanged assertion."""
    target_mapping_ids = set(_target_mapping_ids(plan))
    return {
        "non_target_offers": Offer.objects.exclude(
            id__in=plan["delete_offer_ids"]
        ).count(),
        "non_target_mappings": ContentMapping.objects.exclude(
            id__in=target_mapping_ids
        ).count(),
        "non_target_evidence": OfferEvidence.objects.exclude(
            id__in=plan["delete_evidence_ids"]
        ).count(),
        "total_prices": OfferPrice.objects.count(),
    }


def _update_mapping(plan: dict, *, now=None):
    spec = plan["update_mapping"]
    mapping = (
        ContentMapping.objects.select_related("provider", "source", "content")
        .filter(id=spec["id"])
        .first()
    )
    if mapping is None:
        raise RemediationError(f"mapping {spec['id']} disappeared before update")
    MappingRepository().record(
        provider=mapping.provider,
        source=mapping.source,
        provider_content_id=mapping.provider_content_id,
        content_type=mapping.content_type,
        tmdb_id=spec["tmdb_id"],
        confidence=spec["confidence"],
        match_method=spec["match_method"],
        content=mapping.content,
        now=now,
    )


def _delete_targets(plan: dict):
    evidence_ids = plan["delete_evidence_ids"]
    present = OfferEvidence.objects.filter(id__in=evidence_ids).count()
    if present != len(evidence_ids):
        raise RemediationError(
            f"expected {len(evidence_ids)} evidence rows to delete, found {present}"
        )
    OfferEvidence.objects.filter(id__in=evidence_ids).delete()

    offer_ids = plan["delete_offer_ids"]
    present = Offer.objects.filter(id__in=offer_ids).count()
    if present != len(offer_ids):
        raise RemediationError(
            f"expected {len(offer_ids)} offers to delete, found {present}"
        )
    Offer.objects.filter(id__in=offer_ids).delete()

    mapping_ids = plan["delete_mapping_ids"]
    present = ContentMapping.objects.filter(id__in=mapping_ids).count()
    if present != len(mapping_ids):
        raise RemediationError(
            f"expected {len(mapping_ids)} mappings to delete, found {present}"
        )
    ContentMapping.objects.filter(id__in=mapping_ids).delete()


def _manifest_rows(manifest: dict, section: str) -> dict:
    return {str(row["id"]): row for row in manifest["targets"][section]}


def _assert_invariants(manifest, plan, before):
    """Every pre-commit check. Collects all results, aborts on the first failure."""
    results = []
    failures = []

    def check(name, ok, detail=""):
        ok = bool(ok)
        results.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            failures.append(name)

    spec = plan["update_mapping"]
    updated = ContentMapping.objects.filter(id=spec["id"]).first()
    check(
        "mapping_399_is_manual_2996",
        updated is not None
        and updated.tmdb_id == spec["tmdb_id"]
        and updated.match_method == spec["match_method"]
        and float(updated.confidence) == spec["confidence"],
        f"tmdb={getattr(updated, 'tmdb_id', None)} "
        f"method={getattr(updated, 'match_method', None)} "
        f"confidence={getattr(updated, 'confidence', None)}",
    )
    manifest_399 = _manifest_rows(manifest, "mappings").get(str(spec["id"]))
    if updated is not None and manifest_399 is not None:
        check(
            "mapping_399_first_seen_preserved",
            serialize_columns(updated)["first_seen"]
            == manifest_399["columns"]["first_seen"],
        )
        check(
            "mapping_399_key_preserved",
            updated.provider_content_id == manifest_399["associations"]["provider_content_id"]
            and updated.content_type == manifest_399["associations"]["content_type"],
        )

    check(
        "deleted_mappings_absent",
        not ContentMapping.objects.filter(id__in=plan["delete_mapping_ids"]).exists(),
    )
    check(
        "deleted_offers_absent",
        not Offer.objects.filter(id__in=plan["delete_offer_ids"]).exists(),
    )
    check(
        "deleted_evidence_absent",
        not OfferEvidence.objects.filter(id__in=plan["delete_evidence_ids"]).exists(),
    )

    scope_query = Q()
    for provider_slug, provider_content_id in sorted(_target_scope_pairs(plan)):
        scope_query |= Q(
            provider__slug=provider_slug, provider_content_id=provider_content_id
        )
    check(
        "targeted_scope_empty",
        not Offer.objects.filter(scope_query).exists(),
    )

    target_identities = {
        row["columns"]["identity"]
        for row in manifest["targets"]["offers"]
        if "identity" in row.get("columns", {})
    }
    check(
        "targeted_prices_absent",
        not OfferPrice.objects.filter(
            offer_identity__in=target_identities
        ).exists(),
    )

    check(
        "no_evidence_orphans",
        not OfferEvidence.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        ).exists(),
    )
    check(
        "no_price_orphans",
        not OfferPrice.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        ).exists(),
    )

    after = _non_target_counts(plan)
    for key, value in before.items():
        check(f"unrelated_{key}_unchanged", after[key] == value, f"{value} -> {after[key]}")

    manifest_protected = _manifest_rows(manifest, "protected_mappings")
    for mapping_id in plan["protected_mapping_ids"]:
        row = (
            ContentMapping.objects.select_related("provider")
            .filter(id=mapping_id)
            .first()
        )
        expected = manifest_protected.get(str(mapping_id))
        check(
            f"protected_mapping_{mapping_id}_unchanged",
            row is not None
            and expected is not None
            and _mapping_associations(row) == expected["associations"],
        )

    if failures:
        raise RemediationError(
            "pre-commit invariants failed, rolling back: " + ", ".join(failures)
        )
    return {"checks": results, "before": before, "after": after}


def apply_remediation(
    manifest: dict,
    *,
    plan: Optional[dict] = None,
    now=None,
    barrier: Optional[MaintenanceBarrier] = None,
) -> dict:
    """Perform the mutation inside one transaction, or raise and roll back.

    The manifest is consumed exactly as approved: it is validated and rechecked
    against the live database, mapping 399 is updated first, then only the
    allow-listed rows are deleted and every invariant is asserted. Nothing is
    caught-and-ignored: any error propagates and rolls the whole transaction
    back.

    ``barrier`` is the exclusive maintenance token acquired by the command.
    When omitted (service-level callers) it is acquired and released here; the
    command passes its own token so the lock also covers post-commit checks.
    An unverified token (SQLite tests only) is accepted solely when the
    test-only setting is enabled.
    """
    plan = _plan(plan)
    owns_barrier = barrier is None
    if owns_barrier:
        barrier = acquire_maintenance_barrier()
    if not barrier.verified and not unverified_barrier_allowed():
        if owns_barrier:
            barrier.release()
        raise RemediationError(
            "refusing: this backend cannot prove that availability refresh "
            "writers are excluded (no PostgreSQL advisory locks). Run the "
            "mutation against PostgreSQL with every worker on this image."
        )
    if barrier.verified and barrier.held is False:
        if owns_barrier:
            barrier.release()
        raise RemediationError(
            "refusing: the maintenance write barrier is not held; acquire it "
            "before applying remediation"
        )
    try:
        validate_manifest(manifest, plan=plan)
        now = now or timezone.now()
        with transaction.atomic():
            revalidate_targets(manifest, plan=plan)
            before = _non_target_counts(plan)
            _update_mapping(plan, now=now)
            _delete_targets(plan)
            report = _assert_invariants(manifest, plan, before)
    finally:
        if owns_barrier:
            barrier.release()
    report["counts"] = {
        "updated_mapping": 1,
        "deleted_offers": len(plan["delete_offer_ids"]),
        "deleted_mappings": len(plan["delete_mapping_ids"]),
        "deleted_evidence": len(plan["delete_evidence_ids"]),
    }
    return report



# --- post-commit verification ----------------------------------------------


#: ``(tmdb_id, season, provider_slug)`` reads that must no longer surface the
#: corrected provider content. Series reads go through the season path, which is
#: what the HTTP detail read uses.
_READ_PATH_PROBES = (
    (10601, None, "bbc_iplayer"),
    (12092, None, "bbc_iplayer"),
    (527634, None, "bbc_iplayer"),
    (64690, None, "cbc_gem"),
    (1247, None, "tubi"),
    (2316, 1, "bbc_iplayer"),
    (2316, 2, "bbc_iplayer"),
)


def post_commit_checks(manifest: dict, *, plan: Optional[dict] = None) -> list:
    """Machine-checkable results after the transaction commits.

    Separate from :func:`apply_remediation`'s in-transaction invariants: these
    read the committed state and the local read path. A failure here means the
    committed state is wrong (for example a refresh raced the mutation); it does
    not roll anything back.
    """
    plan = _plan(plan)
    results = []

    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})

    spec = plan["update_mapping"]
    mapping = ContentMapping.objects.filter(id=spec["id"]).first()
    check(
        "post_mapping_399_is_manual_2996",
        mapping is not None
        and mapping.tmdb_id == spec["tmdb_id"]
        and mapping.match_method == spec["match_method"]
        and float(mapping.confidence) == spec["confidence"],
    )
    check(
        "post_deleted_mappings_absent",
        not ContentMapping.objects.filter(id__in=plan["delete_mapping_ids"]).exists(),
    )
    check(
        "post_deleted_offers_absent",
        not Offer.objects.filter(id__in=plan["delete_offer_ids"]).exists(),
    )
    check(
        "post_deleted_evidence_absent",
        not OfferEvidence.objects.filter(id__in=plan["delete_evidence_ids"]).exists(),
    )
    check(
        "post_no_orphans",
        not OfferEvidence.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        ).exists()
        and not OfferPrice.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        ).exists(),
    )

    manifest_protected = _manifest_rows(manifest, "protected_mappings")
    for mapping_id in plan["protected_mapping_ids"]:
        row = (
            ContentMapping.objects.select_related("provider")
            .filter(id=mapping_id)
            .first()
        )
        expected = manifest_protected.get(str(mapping_id))
        check(
            f"post_protected_mapping_{mapping_id}_unchanged",
            row is not None
            and expected is not None
            and _mapping_associations(row) == expected["associations"],
        )

    results.extend(_read_path_checks())
    return results


def _read_path_checks() -> list:
    """Read the public local-offer path; skip probes with no tracker row."""
    from tracker.models import Movie
    from tracker.services.availability_reads import get_local_offers

    results = []
    for tmdb_id, season, provider_slug in _READ_PATH_PROBES:
        label = f"{provider_slug}:{tmdb_id}" + (f":s{season}" if season else "")
        movie = Movie.objects.filter(tmdb_id=tmdb_id).first()
        if movie is None:
            results.append(
                {
                    "name": f"read_path_{label}",
                    "ok": True,
                    "detail": "skipped: no local Movie row",
                }
            )
            continue
        try:
            offers = (
                get_local_offers(movie, season=season)
                if season is not None
                else get_local_offers(movie)
            )
        except Exception as exc:  # read-path probe must never hide a failure
            results.append(
                {"name": f"read_path_{label}", "ok": False, "detail": f"error: {exc}"}
            )
            continue
        offending = sorted({offer.service for offer in offers if offer.service == provider_slug})
        results.append(
            {
                "name": f"read_path_{label}",
                "ok": not offending,
                "detail": (
                    "no false offer visible"
                    if not offending
                    else f"still visible: {offending}"
                ),
            }
        )
    return results
