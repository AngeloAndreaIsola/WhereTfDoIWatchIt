"""Offline regressions for the owner-observation intake tool (#97)."""
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ingest_owner_observations import HEADER, ingest


REPO = Path(__file__).resolve().parents[1]
COHORT = Path("docs/audits/stage2b/nl_pilot_cohort_20261122_v2.csv")
MANIFEST = Path("docs/audits/stage2b/identity_manifest_20261122.csv")


class IngestOwnerObservationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.evidence = self.root / "evidence.html"
        self.evidence.write_bytes(b"<html>advertised listing</html>")
        self.digest = hashlib.sha256(self.evidence.read_bytes()).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, **overrides):
        row = {
            "observation_id": "OBS-NL-01", "tmdb_id": "1396", "media_type": "series",
            "country": "NL", "provider_slug": "netflix", "offer_type": "subscription",
            "observed_at_utc": "2026-11-24T10:15:00+00:00",
            "observation_kind": "advertised", "source_name": "owner-browser",
            "source_permission": "anonymous-public-page",
            "evidence_ref": str(self.evidence), "evidence_sha256": "",
            "url_sanitized": "https://www.netflix.com/nl/title/70143836", "notes": "clear",
        }
        row.update(overrides)
        return row

    def write(self, rows):
        path = self.root / "observations.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_ingest(self, rows, *, cohort=REPO / COHORT, identity_manifest=REPO / MANIFEST,
                   app_snapshot=None):
        return ingest(SimpleNamespace(
            observations=self.write(rows), output_dir=self.root / "out", cohort=cohort,
            identity_manifest=identity_manifest, app_snapshot=app_snapshot,
        ))

    def run_cli(self, rows, *, identity_manifest=MANIFEST):
        return subprocess.run([
            sys.executable, "scripts/ingest_owner_observations.py",
            "--observations", str(self.write(rows)), "--cohort", str(COHORT),
            "--identity-manifest", str(identity_manifest), "--output-dir", str(self.root / "out"),
        ], cwd=REPO, capture_output=True, text=True)

    def test_blank_digest_is_computed_and_row_accepted(self):
        summary = self.run_ingest([self.row()])
        self.assertEqual(summary["rows_accepted"], 1)
        with (self.root / "out" / "accepted.csv").open(encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["evidence_sha256"], self.digest)
        self.assertEqual(row["digest_origin"], "computed")

    def test_supplied_digest_mismatch_is_rejected(self):
        summary = self.run_ingest([self.row(evidence_sha256="0" * 64)])
        self.assertEqual(summary["rows_accepted"], 0)
        self.assertEqual(summary["rejected_by_reason"]["EVIDENCE_DIGEST_MISMATCH"], 1)

    def test_naive_timestamp_and_query_string_are_rejected(self):
        summary = self.run_ingest([self.row(
            observed_at_utc="2026-11-24T10:15:00",
            url_sanitized="https://www.netflix.com/nl/title/70143836?x=1",
        )])
        reasons = summary["rejected_by_reason"]
        self.assertEqual(reasons["NAIVE_OR_MISSING_UTC"], 1)
        self.assertEqual(reasons["UNSANITIZED_URL"], 1)

    def test_duplicate_ids_and_typed_offer_keys_are_rejected(self):
        row = self.row()
        summary = self.run_ingest([row, row])
        reasons = summary["rejected_by_reason"]
        self.assertEqual(reasons["DUPLICATE_ID"], 1)
        self.assertEqual(reasons["DUPLICATE_TYPED_OFFER_KEY"], 1)

    def test_same_source_echo_is_rejected(self):
        snapshot = self.root / "application_snapshot.csv"
        with snapshot.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "tmdb_id", "media_type", "country", "provider_slug", "offer_type", "source_slug"
            ])
            writer.writeheader()
            writer.writerow({
                "tmdb_id": "1396", "media_type": "series", "country": "NL",
                "provider_slug": "netflix", "offer_type": "subscription",
                "source_slug": "owner-browser",
            })
        summary = self.run_ingest([self.row()], app_snapshot=snapshot)
        self.assertEqual(summary["rejected_by_reason"]["SAME_SOURCE_ECHO"], 1)

    def test_documented_cli_respects_all_reviewed_cohort_identities(self):
        with (REPO / COHORT).open(newline="", encoding="utf-8") as handle:
            rows = [self.row(observation_id=f"TEST-{row['tmdb_id']}", tmdb_id=row["tmdb_id"],
                             media_type=row["media_type"], country=row["country"],
                             url_sanitized="https://example.org/test", notes="Synthetic regression row")
                    for row in csv.DictReader(handle)]
        result = self.run_cli(rows)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["rows_supplied"], 15)
        self.assertEqual(summary["rows_accepted"], 14)
        self.assertEqual(summary["rows_rejected"], 1)
        self.assertEqual(summary["rejected_by_reason"], {"IDENTITY_BLOCKED": 1, "YEAR_MISMATCH": 1})
        self.assertEqual(summary["fresh_independent_comparable_pairs"], 0)

        with (self.root / "out" / "accepted.csv").open(newline="", encoding="utf-8") as handle:
            accepted = {(row["tmdb_id"], row["media_type"]) for row in csv.DictReader(handle)}
        # The historical replay's 13 passing identities plus the reviewed US Office alias.
        historical = {(key, "movie") for key in (
            "238", "27205", "496243", "603", "438631", "619264", "840430", "565310", "116149",
        )} | {(key, "series") for key in ("1396", "70523", "66732", "1438")}
        self.assertEqual(accepted, historical | {("2316", "series")})
        with (self.root / "out" / "rejected.csv").open(newline="", encoding="utf-8") as handle:
            [blocked] = csv.DictReader(handle)
        self.assertEqual((blocked["tmdb_id"], blocked["media_type"]), ("220289", "movie"))
        self.assertEqual(blocked["reject_reasons"], "IDENTITY_BLOCKED;YEAR_MISMATCH")

    def test_manifest_with_wrong_schema_aborts_cli_without_output(self):
        manifest = self.root / "bad_manifest.csv"
        manifest.write_text("tmdb_id,media_type,decision\n1396,series,BLOCKED\n", encoding="utf-8")
        result = self.run_cli([self.row()], identity_manifest=manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Identity manifest is missing required columns", result.stderr)
        self.assertFalse((self.root / "out").exists())

    def test_missing_manifest_entry_blocks_cohort_identity(self):
        manifest = self.root / "empty_manifest.csv"
        header = (REPO / MANIFEST).read_text(encoding="utf-8").splitlines()[0]
        manifest.write_text(header + "\n", encoding="utf-8")
        summary = self.run_ingest([self.row()], identity_manifest=manifest)
        self.assertEqual(summary["rows_accepted"], 0)
        self.assertEqual(summary["rejected_by_reason"], {"IDENTITY_BLOCKED": 1, "IDENTITY_MANIFEST_MISSING": 1})

    def test_unreviewed_office_title_blocks_identity(self):
        cohort = self.root / "wrong_office.csv"
        cohort.write_text("tmdb_id,title,media_type,year,country\n2316,The Office UK,series,2005,NL\n",
                          encoding="utf-8")
        summary = self.run_ingest([self.row(tmdb_id="2316")], cohort=cohort)
        self.assertEqual(summary["rows_accepted"], 0)
        self.assertEqual(summary["rejected_by_reason"]["IDENTITY_BLOCKED"], 1)
        self.assertEqual(summary["rejected_by_reason"]["TITLE_ALIAS_REVIEW"], 1)


if __name__ == "__main__":
    unittest.main()
