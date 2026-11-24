#!/usr/bin/env python3
"""Offline intake for owner-supplied availability observations (#97).

No DB, no network, no provider requests. Reads a CSV in the exact contract
documented in docs/audits/stage2b/manual-verification-sheet.md, hashes every
referenced artifact, and writes accepted/rejected rows plus a machine-readable
summary. A blank ``evidence_sha256`` is computed from ``evidence_ref`` so the
owner does not have to do manual hashing; a supplied digest must match.

Same-source echoes are never independent evidence: an app offer written by the
same source as the external observation cannot corroborate it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HEADER = [
    "observation_id", "tmdb_id", "media_type", "country", "provider_slug",
    "offer_type", "observed_at_utc", "observation_kind", "source_name",
    "source_permission", "evidence_ref", "evidence_sha256", "url_sanitized",
    "notes",
]
OFFER_TYPES = {"subscription", "addon", "free", "free_with_ads", "rent", "buy"}
KINDS = {"advertised", "entitled", "playback"}
MEDIA = {"movie", "series"}
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def iso_utc(value: str) -> bool:
    if not value or not value.endswith("+00:00"):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def load_cohort(path: Path | None) -> set[tuple[int, str, str]]:
    if not path:
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (int(row["tmdb_id"]), row["media_type"].strip().lower(),
             row["country"].strip().upper())
            for row in csv.DictReader(handle)
            if row.get("tmdb_id", "").strip().isdigit()
        }


def load_blocked_identities(path: Path | None) -> set[tuple[int, str]]:
    if not path:
        return set()
    blocked = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            status = (row.get("decision") or row.get("status") or "").upper()
            if status in {"YEAR_MISMATCH", "TITLE_ALIAS_REVIEW", "BLOCKED"}:
                blocked.add((int(row["tmdb_id"]), row["media_type"].strip().lower()))
    return blocked


def load_app_echo_keys(path: Path | None) -> set[tuple[int, str, str, str, str, str]]:
    """App offers keyed with their source slug; a matching external source is circular."""
    if not path:
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        if not {"source_slug"}.issubset(fields):
            return set()
        keys = set()
        for row in reader:
            if not (row.get("tmdb_id") or "").strip().isdigit():
                continue
            keys.add((
                int(row["tmdb_id"]),
                (row.get("media_type") or "movie").strip().lower(),
                (row.get("country") or "").strip().upper(),
                (row.get("provider_slug") or row.get("provider") or "").strip().lower(),
                (row.get("offer_type") or "").strip().lower(),
                (row.get("source_slug") or "").strip().lower(),
            ))
        return keys


def ingest(args) -> dict:
    with args.observations.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != HEADER:
            raise SystemExit(
                f"Bad header. Expected exactly: {','.join(HEADER)}\n"
                f"Got: {','.join(reader.fieldnames or [])}"
            )
        rows = list(reader)

    cohort = load_cohort(args.cohort)
    blocked = load_blocked_identities(args.identity_manifest)
    echo_keys = load_app_echo_keys(args.app_snapshot)
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple] = set()

    for row in rows:
        oid = (row["observation_id"] or "").strip()
        reasons: list[str] = []
        if not oid:
            reasons.append("EMPTY_ID")
        elif oid in seen_ids:
            reasons.append("DUPLICATE_ID")
        seen_ids.add(oid)

        try:
            tmdb_id = int(row["tmdb_id"])
        except ValueError:
            tmdb_id = -1
            reasons.append("NON_TYPED_IDENTITY")
        media = (row["media_type"] or "").strip().lower()
        country = (row["country"] or "").strip().upper()
        if media not in MEDIA:
            reasons.append("BAD_MEDIA_TYPE")
        if not re.fullmatch(r"[A-Z]{2}", country):
            reasons.append("BAD_COUNTRY")

        key = (tmdb_id, media, country)
        if cohort and key not in cohort:
            reasons.append("NOT_IN_COHORT")
        if (tmdb_id, media) in blocked:
            reasons.append("IDENTITY_BLOCKED")

        provider = (row["provider_slug"] or "").strip().lower()
        offer_type = (row["offer_type"] or "").strip().lower()
        kind = (row["observation_kind"] or "").strip().lower()
        if not SLUG.fullmatch(provider):
            reasons.append("BAD_PROVIDER_SLUG")
        if offer_type not in OFFER_TYPES:
            reasons.append("BAD_OFFER_CLASS")
        if kind not in KINDS:
            reasons.append("BAD_OBSERVATION_KIND")
        if not iso_utc(row["observed_at_utc"]):
            reasons.append("NAIVE_OR_MISSING_UTC")
        if not (row["source_permission"] or "").strip():
            reasons.append("MISSING_SOURCE_PERMISSION")

        url = (row["url_sanitized"] or "").strip()
        parts = urlsplit(url)
        if parts.query or parts.fragment or "@" in parts.netloc:
            reasons.append("UNSANITIZED_URL")

        evidence_ref = Path((row["evidence_ref"] or "").strip())
        computed = ""
        if not evidence_ref.is_file():
            reasons.append("EVIDENCE_MISSING")
        else:
            computed = digest(evidence_ref)
            supplied = (row["evidence_sha256"] or "").strip().lower()
            if supplied and supplied != computed:
                reasons.append("EVIDENCE_DIGEST_MISMATCH")

        offer_key = (tmdb_id, media, country, provider, offer_type)
        if offer_key in seen_keys:
            reasons.append("DUPLICATE_TYPED_OFFER_KEY")
        seen_keys.add(offer_key)
        source = (row["source_name"] or "").strip().lower()
        if source and (tmdb_id, media, country, provider, offer_type, source) in echo_keys:
            reasons.append("SAME_SOURCE_ECHO")

        clean = dict(row)
        clean["evidence_sha256"] = computed
        clean["digest_origin"] = "computed" if not (row["evidence_sha256"] or "").strip() else "supplied"
        if reasons:
            clean["reject_reasons"] = ";".join(reasons)
            rejected.append(clean)
        else:
            accepted.append(clean)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, records in (("accepted.csv", accepted), ("rejected.csv", rejected)):
        with (args.output_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER + ["digest_origin", "reject_reasons"])
            writer.writeheader()
            for record in records:
                writer.writerow({k: record.get(k, "") for k in writer.fieldnames})

    counts: dict[str, int] = {}
    for record in rejected:
        for reason in record["reject_reasons"].split(";"):
            counts[reason] = counts.get(reason, 0) + 1
    summary = {
        "rows_supplied": len(rows),
        "rows_accepted": len(accepted),
        "rows_rejected": len(rejected),
        "rejected_by_reason": counts,
        "matched_typed_keys": sorted({tuple(a[k] for k in ("tmdb_id", "media_type", "country",
                                                          "provider_slug", "offer_type"))
                                      for a in accepted}),
        "fresh_independent_comparable_pairs": 0,
        "note": "Ingestion validates provenance only; accuracy requires a separate comparator run.",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--identity-manifest", type=Path)
    parser.add_argument("--app-snapshot", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(ingest(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
