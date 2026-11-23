#!/usr/bin/env python3
"""Compare saved Watchmode offers with an offline canonical-offer snapshot.

This command never queries a database or network service. Its results are
candidate evidence overlaps, NOT streaming availability accuracy metrics.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# The shared identity parser lives in the backend so the Django command can
# use it from the backend image. This script uses it without loading Django.
sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))
from availability.audit_identity import load_identity_manifest, manifest_identity_fields, normalized_title, titles_match


COHORT_COLUMNS = {"tmdb_id", "title", "media_type", "year", "country"}
APP_COLUMNS = {
    "tmdb_id", "media_type", "country", "offer_id", "source_slug",
    "provider_slug", "provider_name", "offer_type", "evidence_freshness",
    "availability_confidence", "currently_available",
}
COMPARISON_COLUMNS = {
    "tmdb_id", "media_type", "country", "app_movie_found", "comparison_state",
}
WATCHMODE_OFFER_TYPES = {"sub": "subscription", "rent": "rent", "buy": "buy"}
# Review is specific to the NL pilot and these first-party link destinations.
# meJane has no established canonical slug in the reviewed provider inventory.
TRANSACTIONAL = {"rent": "rent", "buy": "buy"}
SUBSCRIPTION = {"sub": "subscription"}
NL_PILOT_PROVIDERS = {
    24: ("Amazon", "app.primevideo.com", "prime", TRANSACTIONAL),
    26: ("Prime Video", "app.primevideo.com", "prime", SUBSCRIPTION),
    203: ("Netflix", "www.netflix.com", "netflix", SUBSCRIPTION),
    349: ("AppleTV", "tv.apple.com", "apple_tv_store", TRANSACTIONAL),
    372: ("Disney+", "www.disneyplus.com", "disney", SUBSCRIPTION),
    387: ("HBO Max", "play.hbomax.com", "max", SUBSCRIPTION),
    417: ("Rakuten TV", "www.rakuten.tv", "rakuten_tv", TRANSACTIONAL),
    464: ("SkyShowtime", "www.skyshowtime.com", "skyshowtime", SUBSCRIPTION),
    465: ("Videoland", "v2.videoland.com", "videoland", SUBSCRIPTION),
    466: ("NLZIET", "app.nlziet.nl", "nlziet", SUBSCRIPTION),
    490: ("MAX (Via Amazon Prime)", "app.primevideo.com", "max_amazon", {"sub": "addon"}),
    637: ("Pathé Thuis", "www.pathe-thuis.nl", "pathe_thuis", TRANSACTIONAL),
    640: ("NPO Plus", "npo.nl", "npo_plus", SUBSCRIPTION),
}
WATCHMODE_TYPES = {"movie": "movie", "tv_series": "series", "series": "series"}
OUTPUT_COLUMNS = [
    "tmdb_id", "title", "media_type", "year", "country", "watchmode_id",
    "watchmode_source_id", "watchmode_provider", "watchmode_offer_type",
    "watchmode_observed_at_utc", "evidence_artifact",
    "provider_slug", "app_offer_type", "sanitized_provider_url", "candidate_state", "identity_gate",
    "app_movie_found", "identity_source", "identity_manifest_review",
    "decision", "matching_app_sources", "matching_app_offer_ids",
]


def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {', '.join(sorted(missing))}")
        return list(reader)


def identity_key(row: dict) -> tuple[int, str, str]:
    return (int(row["tmdb_id"]), str(row["media_type"]).lower(), str(row["country"]).upper())


def safe_url(value: str) -> str:
    """Keep a public provider URL without potentially sensitive query strings."""
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"


def load_cohort(path: Path) -> dict[tuple[int, str, str], dict]:
    cohort = {}
    for row in read_csv(path, COHORT_COLUMNS):
        key = identity_key(row)
        if key in cohort or key[1] not in {"movie", "series"} or len(key[2]) != 2:
            raise ValueError(f"Invalid or duplicate cohort identity: {key}")
        year = int(row["year"])
        if year < 1880 or not row["title"].strip():
            raise ValueError(f"Invalid cohort title/year for {key}")
        cohort[key] = {**row, "tmdb_id": key[0], "media_type": key[1],
                       "country": key[2], "year": year}
    return cohort


def load_pilot_results(v1_path: Path, correction_path: Path, cohort: dict) -> tuple[dict, dict]:
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    correction = json.loads(correction_path.read_text(encoding="utf-8"))
    results = {}
    for record in v1["results"]:
        key = (int(record["tmdb_id"]), str(record["media_type"]).lower(), "NL")
        if key in cohort:
            if key in results:
                raise ValueError(f"Duplicate Watchmode result for {key}")
            results[key] = {**record, "_observed_utc": v1.get("utc", ""),
                            "_artifact": "v1-original"}

    calls = correction.get("calls", [])
    if len(calls) != 2 or "search_value" not in calls[0].get("path", ""):
        raise ValueError("Correction must document the search and NL sources calls")
    query = parse_qs(urlsplit(calls[0]["path"]).query)
    tmdb_id = int(query["search_value"][0])
    search_field = query.get("search_field")
    if search_field not in (["tmdb_movie_id"], ["tmdb_tv_id"]):
        raise ValueError("Correction must use a typed TMDb search field")
    media_type = "movie" if search_field == ["tmdb_movie_id"] else "series"
    key = (tmdb_id, media_type, "NL")
    if key not in cohort or key in results:
        raise ValueError("Correction identity does not replace a missing cohort result")
    sanity = correction["sanity"]
    if sanity.get("result_count") != 1 or not sanity.get("exact_id_match") or WATCHMODE_TYPES.get(sanity.get("type")) != media_type:
        raise ValueError("Correction lacks an exact typed TMDb identity")
    sources_request = urlsplit(calls[1].get("path", ""))
    if (sources_request.path != f"/title/{sanity['wm_id']}/sources/"
            or parse_qs(sources_request.query).get("regions") != ["NL"]):
        raise ValueError("Correction must use the matching Watchmode title with NL-only sources")
    results[key] = {
        "tmdb_id": tmdb_id, "media_type": media_type,
        "expected_name": cohort[key]["title"], "expected_year": cohort[key]["year"],
        "watchmode_id": sanity["wm_id"], "watchmode_name": sanity["name"],
        "watchmode_year": sanity["year"], "watchmode_type": sanity["type"],
        "exact_tmdb_match": True, "nl_sources": correction.get("sources", []),
        "_observed_utc": correction.get("utc", ""), "_artifact": "v2-correction",
    }
    if set(results) != set(cohort):
        raise ValueError(f"Missing saved Watchmode results for {set(cohort) - set(results)}")
    return results, {"v1_sha256": sha256(v1_path), "correction_sha256": sha256(correction_path)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity_observation_ok(cohort_row: dict, observed: dict) -> bool:
    return (
        observed.get("exact_tmdb_match") is True
        and WATCHMODE_TYPES.get(observed.get("watchmode_type")) == cohort_row["media_type"]
        and int(observed.get("watchmode_year") or 0) == cohort_row["year"]
        and titles_match(cohort_row, normalized_title(observed.get("watchmode_name")),
                         normalized_title(cohort_row["title"]))
    )


def provider_index(app_rows: list[dict], provider_map_rows: list[dict] | None = None) -> dict:
    """Resolve only exact display names or explicit, type-specific mappings."""
    names = defaultdict(set)
    for row in app_rows:
        if row.get("provider_slug") and row.get("provider_name"):
            names[normalized_title(row["provider_name"])].add(row["provider_slug"])
    explicit = {}
    for row in provider_map_rows or []:
        key = (normalized_title(row["watchmode_name"]), row["watchmode_type"].strip().lower())
        target = row["app_provider_slug"].strip()
        if not target or key in explicit:
            raise ValueError(f"Duplicate or empty explicit provider mapping: {key}")
        explicit[key] = target
    return {"names": names, "explicit": explicit}


def resolve_provider(name: str, offer_type: str, index: dict) -> str:
    key = (normalized_title(name), offer_type)
    if key in index["explicit"]:
        return index["explicit"][key]
    candidates = index["names"].get(key[0], set())
    return next(iter(candidates)) if len(candidates) == 1 else ""


def reviewed_nl_provider(source: dict) -> tuple[str, str]:
    entry = NL_PILOT_PROVIDERS.get(source.get("source_id"))
    link = urlsplit(safe_url(source.get("web_url", "")))
    if not entry or source.get("region") != "NL" or link.scheme != "https":
        return "", ""
    name, host, slug, classes = entry
    if source.get("name") != name or link.hostname != host or source.get("type") not in classes:
        return "", ""
    return slug, classes[source["type"]]


def classify_offer(external: dict, app_rows: list[dict], identity_ok: bool,
                   provider_slug: str, offer_type: str | None = None) -> dict:
    if offer_type is None:
        offer_type = WATCHMODE_OFFER_TYPES.get(external.get("type"), "")
    if not provider_slug or not offer_type:
        candidate = "UNMAPPED_PROVIDER_OR_CLASS"
        matches = []
    else:
        matches = [row for row in app_rows if row.get("provider_slug") == provider_slug
                   and row.get("offer_type") == offer_type
                   and str(row.get("currently_available", "")).lower() == "true"]
        independent = [row for row in matches if row.get("source_slug", "").lower() != "watchmode"]
        # application_snapshot.csv's availability_confidence is taken from
        # the reconciled provider/class, not the underlying Source row. That
        # aggregate may inherit Watchmode's confidence, so it cannot prove
        # that any non-Watchmode observation was independently verified.
        if independent:
            candidate = "STALE_OR_UNVERIFIED_OVERLAP"
        elif matches:
            candidate = "SAME_SOURCE_ECHO"
        else:
            candidate = "NO_APP_OFFER"
    return {
        "candidate_state": candidate,
        "decision": candidate if identity_ok else "IDENTITY_UNVERIFIED",
        "matching_app_sources": ";".join(sorted({row["source_slug"] for row in matches})),
        "matching_app_offer_ids": ";".join(sorted({row["offer_id"] for row in matches})),
    }


def reconcile(cohort: dict, observations: dict, app_rows: list[dict],
              comparisons: list[dict], provider_map_rows: list[dict] | None = None, *,
              identity_manifest: dict | None = None, reviewed_nl: bool = False) -> tuple[list[dict], dict]:
    app_by_key = defaultdict(list)
    for row in app_rows:
        app_by_key[identity_key(row)].append(row)
    comparison_by_key = {}
    for row in comparisons:
        key = identity_key(row)
        if key in comparison_by_key:
            raise ValueError(f"Duplicate application comparison identity: {key}")
        comparison_by_key[key] = row
    index = provider_index(app_rows, provider_map_rows)
    output = []
    title_gates = {}
    identity_manifest = identity_manifest or {}

    for key, case in sorted(cohort.items()):
        obs = observations[key]
        local = comparison_by_key.get(key, {})
        observed_identity = identity_observation_ok(case, obs)
        movie_found = {"true": True, "false": False}.get(str(local.get("app_movie_found", "")).lower())
        local_state = local.get("local_identity_state") or ""
        trusted_local_identity = (movie_found is True and local_state in {"", "TRUSTED"}
                                  and local.get("comparison_state") != "NOT_COMPARABLE")
        entry = identity_manifest.get(key[:2])
        manifest_fields = manifest_identity_fields(case, entry)
        manifest_review = manifest_fields["identity_manifest_review"]
        identity_source = ""
        if not manifest_review:
            if trusted_local_identity:
                identity_source = "local_movie"
            elif entry and local.get("identity_source") == "tmdb_manifest" and (
                (movie_found is False and local_state in {"", "MISSING_MOVIE"})
                or (movie_found is True and local_state == "MISSING_CANONICAL_TITLE")
            ):
                identity_source = "tmdb_manifest"
        identity_ok = observed_identity and bool(identity_source)
        title_gates[str(key)] = {
            "cohort_title": case["title"],
            "cohort_year": case["year"],
            "watchmode_title_year_match": observed_identity,
            "app_movie_found": movie_found,
            "local_identity_state": local_state,
            "trusted_local_identity": trusted_local_identity,
            "identity_source": identity_source,
            "identity_gate": "PASS" if identity_ok else "BLOCKED",
            **manifest_fields,
            "nl_offer_count": len(obs.get("nl_sources", [])),
            "observation_utc": obs.get("_observed_utc", ""),
            "evidence_artifact": obs.get("_artifact", ""),
        }
        for source in obs.get("nl_sources", []):
            if source.get("region") != "NL":
                raise ValueError(f"Non-NL Watchmode source for {key}")
            if reviewed_nl:
                provider_slug, offer_type = reviewed_nl_provider(source)
            else:
                provider_slug = resolve_provider(source.get("name", ""), source.get("type", ""), index)
                offer_type = WATCHMODE_OFFER_TYPES.get(source.get("type"), "")
            classification = classify_offer(source, app_by_key[key], identity_ok, provider_slug, offer_type)
            output.append({
                "tmdb_id": case["tmdb_id"], "title": case["title"],
                "media_type": case["media_type"], "year": case["year"], "country": case["country"],
                "watchmode_id": obs["watchmode_id"],
                "watchmode_source_id": source.get("source_id", ""),
                "watchmode_provider": source.get("name", ""),
                "watchmode_offer_type": source.get("type", ""),
                "watchmode_observed_at_utc": obs.get("_observed_utc", ""),
                "evidence_artifact": obs.get("_artifact", ""),
                "provider_slug": provider_slug,
                "app_offer_type": offer_type if provider_slug else "",
                "sanitized_provider_url": safe_url(source.get("web_url", "")),
                "identity_gate": "PASS" if identity_ok else "BLOCKED",
                "app_movie_found": movie_found,
                "identity_source": identity_source,
                "identity_manifest_review": manifest_review,
                **classification,
            })

    summary = {
        "cohort_titles": len(cohort), "external_offer_rows": len(output),
        "offer_type_counts": dict(Counter(row["watchmode_offer_type"] for row in output)),
        "candidate_state_counts": dict(Counter(row["candidate_state"] for row in output)),
        "decision_counts": dict(Counter(row["decision"] for row in output)),
        "title_identity_gates": title_gates,
        "limitations": [
            "Secondary advertised offers are not entitlement or playback evidence.",
            "Same-source Watchmode echoes are not independent corroboration.",
            "TMDb manifest entries supply identity evidence only. Watchmode identity metadata may derive from TMDb.",
            "Stale overlaps do not establish current availability.",
            "Missing Watchmode listings are not authoritative unavailability evidence.",
            "These counts are not streaming accuracy rates.",
        ],
    }
    return output, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", required=True, type=Path)
    parser.add_argument("--watchmode-v1", required=True, type=Path)
    parser.add_argument("--watchmode-correction", required=True, type=Path)
    parser.add_argument("--app-snapshot", required=True, type=Path)
    parser.add_argument("--app-comparison", required=True, type=Path)
    parser.add_argument("--identity-manifest", type=Path, help="Optional reviewed TMDb identity evidence CSV")
    parser.add_argument("--manifest-evidence-dir", type=Path,
                        help="Verify manifest SHA-256 values against preserved local TMDb JSON responses")
    providers = parser.add_mutually_exclusive_group()
    providers.add_argument("--reviewed-nl-providers", action="store_true",
                           help="Use only the reviewed November NL pilot source IDs, labels, hosts and classes")
    providers.add_argument("--provider-map", type=Path,
                        help="Optional CSV: watchmode_name,watchmode_type,app_provider_slug")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--expected-cohort-sha", help="Optional pinned SHA-256 of cohort input")
    parser.add_argument("--expected-v1-sha", help="Optional pinned SHA-256 of original pilot JSON")
    parser.add_argument("--expected-correction-sha", help="Optional pinned SHA-256 of correction JSON")
    parser.add_argument("--expected-identity-manifest-sha", help="Optional pinned SHA-256 of the identity manifest")
    parser.add_argument("--snapshot-label", default="", help="Human-readable identity of the audited DB snapshot")
    parser.add_argument("--audit-code-sha", default="", help="Commit used to generate the application audit")
    args = parser.parse_args()
    if args.manifest_evidence_dir and not args.identity_manifest:
        parser.error("--manifest-evidence-dir requires --identity-manifest")

    cohort = load_cohort(args.cohort)
    observations, hashes = load_pilot_results(args.watchmode_v1, args.watchmode_correction, cohort)
    hashes["cohort_sha256"] = sha256(args.cohort)
    if args.identity_manifest:
        hashes["identity_manifest_sha256"] = sha256(args.identity_manifest)
    elif args.expected_identity_manifest_sha:
        parser.error("--expected-identity-manifest-sha requires --identity-manifest")
    for key, expected in (
        ("cohort_sha256", args.expected_cohort_sha),
        ("v1_sha256", args.expected_v1_sha),
        ("correction_sha256", args.expected_correction_sha),
        ("identity_manifest_sha256", args.expected_identity_manifest_sha),
    ):
        if expected and hashes[key].lower() != expected.lower():
            parser.error(f"Evidence SHA-256 mismatch for {key}: input is not the pinned artifact")
    hashes["app_snapshot_sha256"] = sha256(args.app_snapshot)
    hashes["app_comparison_sha256"] = sha256(args.app_comparison)
    if args.provider_map:
        hashes["provider_map_sha256"] = sha256(args.provider_map)
    app = read_csv(args.app_snapshot, APP_COLUMNS)
    comparison = read_csv(args.app_comparison, COMPARISON_COLUMNS)
    mapping = read_csv(args.provider_map, {"watchmode_name", "watchmode_type", "app_provider_slug"}) if args.provider_map else []
    manifest = None
    if args.identity_manifest:
        try:
            manifest = load_identity_manifest(args.identity_manifest, evidence_dir=args.manifest_evidence_dir)
        except (OSError, ValueError, csv.Error) as exc:
            parser.error(f"Cannot load identity manifest: {exc}")
    rows, summary = reconcile(cohort, observations, app, comparison, mapping,
                              identity_manifest=manifest, reviewed_nl=args.reviewed_nl_providers)
    summary["reviewed_nl_providers"] = args.reviewed_nl_providers
    summary["evidence_sha256"] = hashes
    summary["manifest_evidence_verified"] = args.manifest_evidence_dir is not None
    summary["snapshot_label"] = args.snapshot_label
    summary["audit_code_sha"] = args.audit_code_sha
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Offline comparison complete: {len(cohort)} titles, {len(rows)} advertised offer rows; no accuracy claim.")


if __name__ == "__main__":
    main()
