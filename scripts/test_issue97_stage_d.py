"""Check the Stage D cohort against preserved evidence without network or a DB."""

import argparse
import csv
import json
import unittest
from collections import Counter
from pathlib import Path

from availability.audit_identity import load_identity_manifest, normalized_title, titles_match


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "docs/audits/stage2b/nl_pilot_cohort_20261122_v2.csv"
IDENTITIES = PILOT.with_name("identity_manifest_20261122.csv")
REMAINING = PILOT.parent.parent / "issue97/stage-d-remaining-cohort.csv"
CANARY = ROOT / "runs/jw-canary-2026-11-24/coverage.json"
CANARY_KEYS = {(27205, "movie"), (438631, "movie")}


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def typed_key(row):
    return int(row["tmdb_id"]), row["media_type"]


class StageDManifestTests(unittest.TestCase):
    cohort_path = PILOT
    evidence_dir = None

    @classmethod
    def setUpClass(cls):
        cls.pilot = read_rows(cls.cohort_path)
        cls.remaining = read_rows(REMAINING)
        cls.identities = load_identity_manifest(IDENTITIES)
        cls.identity_rows = {typed_key(row): row for row in read_rows(IDENTITIES)}

    def test_remaining_scope_is_exact_cohort_minus_collected_canaries(self):
        self.assertEqual(self.cohort_path.read_bytes(), PILOT.read_bytes())
        coverage = json.loads(CANARY.read_text(encoding="utf-8"))["returned_markets"]
        canaries = [(row["title"], row["market"]) for row in coverage if row["records"] > 0]
        self.assertCountEqual(canaries, [("Inception", "nl"), ("Dune", "nl")])
        self.assertEqual({typed_key(row) for row in self.pilot}, self.identities.keys())
        expected = [row for row in self.pilot if typed_key(row) not in CANARY_KEYS]
        self.assertEqual(len(expected), 13)
        self.assertEqual(len({row["scope_id"] for row in self.remaining}), 13)
        actual = [
            dict(tmdb_id=row["tmdb_id"], title=row["pilot_title"], media_type=row["media_type"],
                 year=row["pilot_year"], country=row["country"])
            for row in self.remaining
        ]
        self.assertEqual(actual, expected)

    def test_identity_values_and_provenance_are_preserved(self):
        for row in self.remaining:
            key = typed_key(row)
            with self.subTest(scope=row["scope_id"]):
                identity = self.identities[key]
                original = self.identity_rows[key]
                self.assertEqual(row["country"], "NL")
                self.assertEqual(row["scope_id"], f"{key[1]}-{key[0]}-nl")
                self.assertEqual((row["canonical_title"], int(row["canonical_year"])),
                                 (identity.canonical_title, identity.year))
                for field in ("source", "evidence_sha256", "observed_utc", "provenance"):
                    self.assertEqual(row[f"identity_{field}"], original[field])

    def test_only_reviewed_alias_is_accepted_and_year_conflict_stays_blocked(self):
        for row in self.remaining:
            tmdb_id, media_type = typed_key(row)
            year = int(row["canonical_year"])
            with self.subTest(scope=row["scope_id"]):
                if tmdb_id == 220289:
                    self.assertEqual((row["pilot_year"], year), ("2013", 2014))
                    self.assertEqual(row["identity_status"], "BLOCKED_YEAR_MISMATCH")
                    continue
                self.assertEqual(int(row["pilot_year"]), year)
                scope = dict(tmdb_id=tmdb_id, media_type=media_type, year=year)
                if tmdb_id == 2316:
                    self.assertEqual(row["identity_status"], "VERIFIED_REVIEWED_ALIAS")
                    self.assertEqual((row["pilot_title"], row["canonical_title"]),
                                     ("The Office US", "The Office"))
                    self.assertTrue(titles_match(scope, "the office us", "the office"))
                else:
                    self.assertEqual(row["identity_status"], "VERIFIED_EXACT")
                    self.assertEqual(normalized_title(row["pilot_title"]),
                                     normalized_title(row["canonical_title"]))

    def test_operation_matrix_budgets_title_and_season_separately(self):
        self.assertEqual(Counter(row["media_type"] for row in self.remaining), {"movie": 8, "series": 5})
        for row in self.remaining:
            self.assertEqual(row["title_operation"], "GetSearchTitles")
            self.assertEqual(row["season_operation"], "GetTitleNode" if row["media_type"] == "series" else "")
        admitted = [row for row in self.remaining if row["identity_status"].startswith("VERIFIED_")]
        self.assertEqual(len(admitted), 12)
        self.assertEqual(sum(bool(row["season_operation"]) for row in admitted), 5)

    def test_raw_tmdb_bytes_match_manifest_and_payloads(self):
        if self.evidence_dir is None:
            self.skipTest("Pass --tmdb-evidence-dir for the retained raw responses")
        self.assertEqual(load_identity_manifest(IDENTITIES, evidence_dir=self.evidence_dir), self.identities)
        for (tmdb_id, media_type), identity in self.identities.items():
            path = self.evidence_dir / f"{media_type}_{tmdb_id}.json"
            with self.subTest(response=path.name):
                payload = json.loads(path.read_bytes())
                title_field, date_field = (
                    ("title", "release_date") if media_type == "movie" else ("name", "first_air_date")
                )
                self.assertEqual(payload["id"], tmdb_id)
                self.assertEqual(payload[title_field], identity.canonical_title)
                self.assertEqual(int(payload[date_field][:4]), identity.year)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, default=PILOT)
    parser.add_argument("--tmdb-evidence-dir", type=Path)
    args, test_args = parser.parse_known_args()
    StageDManifestTests.cohort_path = args.cohort
    StageDManifestTests.evidence_dir = args.tmdb_evidence_dir
    unittest.main(argv=[__file__, *test_args])
