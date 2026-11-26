"""Regression checks for the offline Stage E historical document bindings."""

import re
import unittest

from scripts import audit_issue97_stage_e as audit


class DocumentScopeTests(unittest.TestCase):
    def setUp(self):
        self.candidates = audit.rows((audit.ROOT / audit.CANDIDATE).read_bytes())
        self.observations = audit.unique_index(audit.rows((audit.ROOT / audit.ACCEPTED).read_bytes()), "id")

    def test_frozen_controls_and_prime_channel_share_reviewed_documents(self):
        audit.verify_document_scopes(self.candidates, self.observations)

    def test_authorization_preparation_lists_every_frozen_batch_a_candidate(self):
        packet = audit.ROOT / "docs/audits/issue97/stage-e-source-authorization-readiness.md"
        documented = re.findall(
            r"^\| (E\d+) \| (E-C\d+) \| (\w+) \| (\w+) \|$",
            packet.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        fields = ("candidate_id", "capture_scope", "offer_class", "fresh_status")
        expected = [tuple(row[field] for field in fields) for row in self.candidates if row["batch"] == "A"]
        self.assertCountEqual(documented, expected)

    def test_free_control_cannot_borrow_other_inception_document(self):
        control = next(row for row in self.candidates if row["candidate_id"] == "E08")
        control["capture_scope"] = "E-C1"
        with self.assertRaises(AssertionError):
            audit.verify_document_scopes(self.candidates, self.observations)

    def test_same_provider_cannot_borrow_different_typed_title(self):
        control = next(row for row in self.candidates if row["candidate_id"] == "E09")
        control["capture_scope"] = "E-C8"
        with self.assertRaises(AssertionError):
            audit.verify_document_scopes(self.candidates, self.observations)

    def test_store_and_channel_evidence_must_share_exact_capture(self):
        observation = self.observations["OBS-01-max_amazon-addon"]
        for field in ("public_url", "provider_title_id", "raw_sha256", "observed_utc"):
            with self.subTest(field=field):
                original = observation[field]
                observation[field] += "-different"
                with self.assertRaisesRegex(AssertionError, field):
                    audit.verify_document_scopes(self.candidates, self.observations)
                observation[field] = original
