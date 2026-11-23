"""Verify the published #97 panel against retained evidence without network or DB access."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from issue97_page_signals import Page, verify_page

DOCS = Path(__file__).resolve().parents[1] / "docs/audits/stage2b"


def records(name):
    return read_csv(DOCS / name)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
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


def case(row):
    return row["tmdb_id"], row["media_type"], row["country"]


def offer(row):
    return (*case(row), row["canonical_provider"], row["offer_class"])


def verify_first_party(positives, rejected, targets, summary):
    all_rows = positives + rejected
    check(len({r["id"] for r in all_rows}) == len(all_rows), "Duplicate observation ID")
    check(len({offer(r) for r in positives}) == len(positives), "Duplicate advertised offer")
    priority = [r for r in positives if r["priority_pair"]]
    expected = {r["target_id"]: (*case(r), r["provider_slug"], r["app_offer_type"]) for r in targets}
    check(len(expected) == len(targets) == len(priority) == 8 and
          {r["priority_pair"]: offer(r) for r in priority} == expected, "Priority pair binding differs")
    check(all(r["advertised"] == "positive" and r["decision"] == "ADVERTISED_ONLY" for r in positives)
          and all(r["advertised"] == "unknown" for r in rejected), "Advertised evidence grade differs")
    check(all(r["entitlement"] == r["playback"] == "untested" for r in all_rows), "Access grade differs")
    expected_counts = {
        "advertised_positive_pairs": len(positives),
        "priority_pairs_covered": len(priority),
        "by_class": Counter(r["offer_class"] for r in positives),
        "by_market": Counter(r["country"] for r in positives),
        "rejected_observations": Counter(r["decision"] for r in rejected),
        "confirmed_negative_pairs": 0, "entitlements_observed": 0, "playbacks_observed": 0,
    }
    for key, value in expected_counts.items():
        check(summary["first_party"][key] == value, f"First-party summary differs: {key}")


def verify_reconciliation(root, positives, public, summary):
    display = {case(r): r for r in json.loads((root / "panel/display.json").read_text())}
    states = {}
    for provider, kind in sorted({offer(r)[-2:] for r in positives}):
        batch = root / "pair-audits" / f"{provider}-{kind}"
        rows = read_csv(batch / "comparison.csv")
        for r in rows:
            key = (*case(r), provider, kind)
            check(key not in states and r["external_explicit_positive"] == "True", "Invalid audit batch")
            states[key] = r["comparison_state"]
    check(set(states) == {offer(r) for r in positives}, "Frozen pair audit coverage differs")
    omissions = Counter()
    for row in positives:
        shown = display[case(row)]
        check(str(shown["http"]) == row["view_http"] and shown["read_utc"] == row["app_read_utc"]
              and shown["catalogue_status"] == row["app_display_status"], "Frozen display differs")
        for field, column in [("offers", "app_current_same_offer"), ("last_known_offers", "app_history_same_offer")]:
            found = any(r[:2] == list(offer(row)[-2:]) for r in shown.get(field, []))
            check(str(found) == row[column], f"Frozen display offer differs: {row['id']}")
        check(row["app_current_same_offer"] == "False" and public[row["id"]]["accuracy_pair"] == "no",
              "Unexpected eligible current offer")
        omissions[shown["http"]] += 1
        check(public[row["id"]]["django_pair_comparison"] == states[offer(row)], "Frozen audit state differs")
    coverage = summary["clone_response_coverage"]
    check(Counter(states.values()) == summary["django_first_party_pair_decisions"], "Django totals differ")
    check((len(positives), omissions[200], omissions[404], 0, 0) ==
          tuple(coverage[k] for k in ("known_positive_pairs", "omitted_with_200", "omitted_with_404",
                                      "returned_current", "known_negative_pairs")), "Clone coverage differs")
    accuracy = summary["canonical_accuracy"]
    check(accuracy["unknown_of_advertised_positives"] == len(positives) and accuracy["rate"] is None
          and all(accuracy[k] == 0 for k in ("agreements", "conflicts", "fp", "fn",
                                             "eligible_independent_same_window_pairs")), "Accuracy grade differs")


def verify(root):
    manifest = json.loads((DOCS / "issue97-evidence-manifest.json").read_text())
    summary = json.loads((DOCS / "issue97-reconciliation-summary.json").read_text())
    for name, expected in manifest["private_archive"]["members_sha256"].items():
        check(digest(root / name) == expected, f"Changed restored input: {name}")
    start, end = map(utc, summary["reference_window_utc"])
    for name, expected in manifest["published_outputs"].items():
        check(digest(DOCS / name) == expected, f"Changed published output: {name}")
    evidence = {}
    for name, record in manifest["prior_raw_files"].items():
        check(digest(root / "prior-pages" / name) == record["sha256"], f"Changed capture: {name}")
        if name.endswith(".html"):
            stamp = utc(record["intake_utc"])
            screenshot = manifest["prior_raw_files"][Path(name).with_suffix(".png").name]
            check(stamp <= utc(record["filesystem_modified_utc"]) <= utc(screenshot["filesystem_modified_utc"]),
                  f"Invalid original capture order: {name}")
            evidence[name] = (record["public_url"], stamp, record["sha256"])
    requests = manifest["additional_document_GETs"]
    for request in requests:
        name = request["id"] + ".html"
        check(digest(root / "pages" / name) == request["sha256"], f"Changed response: {name}")
        stamp = utc(request["end"])
        check(start <= utc(request["start"]) <= stamp < end, f"Request outside panel: {name}")
        evidence[name] = (request["url"], stamp, request["sha256"])
    for row in records("identity_manifest_20261122.csv"):
        movie = row["media_type"] == "movie"
        name = f'{row["media_type"]}_{row["tmdb_id"]}.json'
        path = root / "tmdb" / name
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
    entries = [r for r in matrix if r["layer"] == "first_party"]
    public = {r["ref"]: r for r in entries}
    check(len(public) == len(entries) == len(positives) + len(rejected), "Observation/matrix cardinality mismatch")
    verify_first_party(positives, rejected, read_csv(root / "previous/eight_pair_targets.csv"), summary)
    verify_reconciliation(root, positives, public, summary)
    pages = {}
    for row in positives:
        name = row["raw_file"]
        if name not in pages:
            folder = "prior-pages" if name in manifest["prior_raw_files"] else "pages"
            pages[name] = Page((root / folder / name).read_text(encoding="utf-8"))
        verify_page(pages[name], row)
    first = summary["first_party"]
    check(sum(n in manifest["prior_raw_files"] for n in pages) == first["accepted_original_pages"]
          and sum(r["raw_file"] not in manifest["prior_raw_files"] for r in positives if r["priority_pair"])
          == first["new_direct_priority_pages"], "Intake/direct page coverage differs")
    for key, name in [("prior_intake", "owner_supplied_observations.csv"),
                      ("prior_accepted", "issue97-accepted-observations.csv"),
                      ("prior_rejected", "issue97-rejected-observations.csv")]:
        check(len(read_csv(root / "previous" / name)) == first[key], f"Historical intake differs: {key}")
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
    check(len(requests) <= 40, "Public page budget exceeded")
    print(f"{len(manifest['prior_raw_files'])} original files, {len(requests)} new responses, "
          f"{len(manifest['tmdb_raw_files'])} TMDb identities, {len(pilot)} pilot rows, "
          f"{len(positives)} advertised pairs and {len(rejected)} rejections verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restored", required=True, type=Path, help="Root restored by restore_issue97_archive.py")
    verify(parser.parse_args().restored)
