"""Regression checks for the disposable-clone guard and first-party decisions."""
import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

import audit_issue97_sample
from issue97_page_signals import Page, verify_page
from verify_issue97_evidence import DOCS, records, verify_first_party


class DatabaseGuardTests(unittest.TestCase):
    def test_unintended_read_only_database_aborts_before_audit_or_output(self):
        db = MagicMock(vendor="postgresql")
        db.settings_dict = {"NAME": "authorized_disposable"}
        cursor = db.cursor.return_value.__enter__.return_value
        results = {"SHOW default_transaction_read_only": "on", "SELECT current_database()": "other_database"}
        cursor.execute.side_effect = lambda sql: setattr(cursor.fetchone, "return_value", (results[sql],))
        command = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "new-parent/replay"
            args = argparse.Namespace(expected_database="authorized_disposable", output=path, audit_code_sha="revision")
            with patch.object(audit_issue97_sample, "sample") as sample, patch.object(audit_issue97_sample, "measure") as measure:
                with redirect_stderr(io.StringIO()) as message, self.assertRaises(SystemExit) as error:
                    audit_issue97_sample.run(args, db, command, argparse.ArgumentParser())
                self.assertEqual(error.exception.code, 2)
                self.assertIn("other_database", message.getvalue())
                self.assertEqual(list(Path(tmp).iterdir()), [])
                sample.assert_not_called()
                measure.assert_not_called()
                command.assert_not_called()
                cursor.execute.assert_called_with("SELECT current_database()")


class FirstPartyTests(unittest.TestCase):
    def setUp(self):
        self.rows = records("issue97-accepted-observations.csv")
        self.rejections = records("issue97-rejected-observations.csv")
        self.summary = json.loads((DOCS / "issue97-reconciliation-summary.json").read_text())
        self.targets = [dict(row, target_id=row["priority_pair"], provider_slug=row["canonical_provider"],
                             app_offer_type=row["offer_class"]) for row in self.rows if row["priority_pair"]]

    def verify(self):
        verify_first_party(self.rows, self.rejections, self.targets, self.summary)

    def test_published_totals(self):
        self.verify()

    def test_inconsistent_class_summary(self):
        self.summary["first_party"]["by_class"]["rent"] -= 1
        with self.assertRaisesRegex(ValueError, "by_class"):
            self.verify()

    def test_duplicate_observation_id(self):
        self.rejections[0]["id"] = self.rows[0]["id"]
        with self.assertRaisesRegex(ValueError, "Duplicate observation ID"):
            self.verify()

    def test_same_offer_under_new_id(self):
        self.rows.append(dict(self.rows[0], id="different-id"))
        with self.assertRaisesRegex(ValueError, "Duplicate advertised offer"):
            self.verify()

    def test_swapped_priority_pairs(self):
        first, second = self.rows[:2]
        first["priority_pair"], second["priority_pair"] = second["priority_pair"], first["priority_pair"]
        with self.assertRaisesRegex(ValueError, "Priority pair binding"):
            self.verify()

    def test_unobserved_playback_cannot_be_promoted(self):
        self.rows[0]["playback"] = "confirmed"
        with self.assertRaisesRegex(ValueError, "Access grade"):
            self.verify()

    def test_only_title_scoped_netflix_plan_flag_counts(self):
        row = self.rows[0]
        entry = {"@type": "TVSeries", "name": row["page_title"], "url": row["public_url"]}
        title = {"isPlayableOnAdsPlan": False,
                 "metaData": {"isAvailable": True, "topLevelVideoId": row["provider_title_id"]}}
        market = {"membership_status": "ANONYMOUS", "country": "NL"}
        models = {"nmTitleGQL": {"data": title}, "adtech": {"data": {"iframeData": market}},
                  "otherTitle": {"isPlayableOnAdsPlan": True}}

        def page():
            return Page('<script type="application/ld+json">' + json.dumps(entry) + '</script>'
                        + '<script>netflix.reactContext = ' + json.dumps({"models": models}) + ';</script>')
        with self.assertRaisesRegex(ValueError, "signals differ"):
            verify_page(page(), row)
        title["isPlayableOnAdsPlan"] = True
        verify_page(page(), row)
        market["country"] = "IT"
        with self.assertRaisesRegex(ValueError, "signals differ"):
            verify_page(page(), row)


if __name__ == "__main__":
    unittest.main()
