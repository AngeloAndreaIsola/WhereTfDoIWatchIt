"""Verify the published #97 panel against retained evidence without network or DB access."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs/audits/stage2b"


def records(name):
    with (DOCS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def utc(value):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    check(instant.utcoffset() == timedelta(0), f"Expected UTC: {value}")
    return instant


def verify(args):
    manifest = json.loads((DOCS / "issue97-evidence-manifest.json").read_text())
    summary = json.loads((DOCS / "issue97-reconciliation-summary.json").read_text())
    start, end = map(utc, summary["reference_window_utc"])
    for name, expected in manifest["published_outputs"].items():
        check(digest(DOCS / name) == expected, f"Changed published output: {name}")
    evidence = {}
    for name, record in manifest["prior_raw_files"].items():
        check(digest(args.prior_pages / name) == record["sha256"], f"Changed capture: {name}")
        if name.endswith(".html"):
            stamp = utc(record["intake_utc"])
            screenshot = manifest["prior_raw_files"][Path(name).with_suffix(".png").name]
            check(stamp <= utc(record["filesystem_modified_utc"]) <= utc(screenshot["filesystem_modified_utc"]),
                  f"Invalid original capture order: {name}")
            evidence[name] = (record["public_url"], stamp, record["sha256"])
    requests = manifest["additional_document_GETs"]
    for request in requests:
        name = request["id"] + ".html"
        check(digest(args.page_dir / name) == request["sha256"], f"Changed response: {name}")
        stamp = utc(request["end"])
        check(start <= utc(request["start"]) <= stamp < end, f"Request outside panel: {name}")
        evidence[name] = (request["url"], stamp, request["sha256"])
    for row in records("identity_manifest_20261122.csv"):
        movie = row["media_type"] == "movie"
        name = f'{row["media_type"]}_{row["tmdb_id"]}.json'
        path = args.tmdb_dir / name
        check(digest(path) == row["evidence_sha256"], f"Changed TMDb evidence: {name}")
        check(manifest["tmdb_raw_files"][name] == {"sha256": row["evidence_sha256"],
                                                   "observed_utc": row["observed_utc"]},
              f"TMDb provenance differs: {name}")
        raw = json.loads(path.read_text())
        check((str(raw["id"]), raw["title" if movie else "name"], raw["release_date" if movie else "first_air_date"][:4])
              == (row["tmdb_id"], row["canonical_title"], row["year"]), f"Typed identity mismatch: {name}")
        utc(row["observed_utc"])
    positives = records("issue97-accepted-observations.csv")
    rejected = records("issue97-rejected-observations.csv")
    matrix = records("issue97-provider-reconciliation.csv")
    public = {r["ref"]: r for r in matrix if r["layer"] == "first_party"}
    check(len(public) == len(positives) + len(rejected), "Observation/matrix cardinality mismatch")
    for row in positives + rejected:
        instant = utc(row["observed_utc"])
        check(evidence[row["raw_file"]] == (row["public_url"], instant, row["raw_sha256"]),
              f'Observation provenance mismatch: {row["id"]}')
        check(start <= instant < end and start <= utc(row["app_read_utc"]) < end, "Time outside panel")
        entry = public[row["id"]]
        for a, b in [("tmdb_id", "tmdb_id"), ("country", "country"), ("media_type", "media_type"),
                     ("canonical_provider", "provider_slug"), ("offer_class", "offer_class"), ("decision", "decision")]:
            check(row[a] == entry[b], f'Observation/matrix mismatch: {row["id"]} {a}')
    pilot = [r for r in matrix if r["layer"] == "watchmode_pilot"]
    check(Counter(r["decision"] for r in pilot) == summary["reviewed_historical_pilot"]["decision_counts"],
          "Pilot decisions differ")
    check(all(utc(r["observed_utc"]) < start and r["accuracy_pair"] == "no" for r in pilot),
          "Historical pilot incorrectly compared")
    check({r["priority_pair"] for r in positives if r["priority_pair"]} == {f"T{i}" for i in range(1, 9)},
          "Priority pair coverage differs")
    check(all(r["entitlement"] == r["playback"] == "untested" for r in positives + rejected),
          "Access classification differs")
    check(len(requests) <= 40, "Public page budget exceeded")
    print(f"{len(manifest['prior_raw_files'])} original files, {len(requests)} new responses, "
          f"{len(manifest['tmdb_raw_files'])} TMDb identities, {len(pilot)} pilot rows, "
          f"{len(positives)} advertised pairs and {len(rejected)} rejections verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-pages", required=True, type=Path)
    parser.add_argument("--page-dir", required=True, type=Path)
    parser.add_argument("--tmdb-dir", required=True, type=Path)
    verify(parser.parse_args())
