"""Offline regressions for the owner-observation intake tool (#97)."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ingest_owner_observations import HEADER, ingest


class FakeArgs:
    def __init__(self, observations, output_dir, cohort=None, identity_manifest=None,
                 app_snapshot=None):
        self.observations = observations
        self.output_dir = output_dir
        self.cohort = cohort
        self.identity_manifest = identity_manifest
        self.app_snapshot = app_snapshot


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

    def run_ingest(self, rows, **kwargs):
        out = self.root / "out"
        return ingest(FakeArgs(self.write(rows), out, **kwargs))

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

    def test_identity_manifest_block_is_respected(self):
        manifest = self.root / "identity_manifest.csv"
        with manifest.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["tmdb_id", "media_type", "decision"])
            writer.writeheader()
            writer.writerow({"tmdb_id": "1396", "media_type": "series",
                             "decision": "YEAR_MISMATCH"})
        summary = self.run_ingest([self.row()], identity_manifest=manifest)
        self.assertEqual(summary["rejected_by_reason"]["IDENTITY_BLOCKED"], 1)
        self.assertEqual(summary["fresh_independent_comparable_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
