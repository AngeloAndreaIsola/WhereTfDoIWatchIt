"""Offline Stage D identity-provisioning plan and command tests."""

import json
import os
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from availability.services.issue97_stage_d_identity_provisioning import (
    COHORT_RELATIVE_PATH, COHORT_SHA256, DOCS_ROOT_ENV, HELD_SCOPE_ID,
    IDENTITY_MANIFEST_RELATIVE_PATH, IDENTITY_MANIFEST_SHA256,
    pinned_paths, provisioning_plan,
)
from availability.services.issue97_stage_d_policy import MANIFEST_SHA256
from tracker.models import Movie


class ProvisioningPlanTests(SimpleTestCase):
    def test_plan_is_exact_twelve_rows_and_excludes_held_coherence(self):
        plan = provisioning_plan()
        self.assertEqual(plan["cohort_sha256"], COHORT_SHA256)
        self.assertEqual(plan["identity_manifest_sha256"], IDENTITY_MANIFEST_SHA256)
        self.assertEqual(plan["manifest_sha256"], MANIFEST_SHA256)
        self.assertFalse(plan["evidence_checked"])
        self.assertEqual(len(plan["rows"]), 12)
        self.assertEqual(plan["held"]["scope_id"], HELD_SCOPE_ID)
        self.assertEqual(plan["held"]["identity_status"], "BLOCKED_YEAR_MISMATCH")
        self.assertEqual((plan["held"]["pilot_year"], plan["held"]["canonical_year"]), (2013, 2014))
        self.assertNotIn(HELD_SCOPE_ID, [row["scope_id"] for row in plan["rows"]])

    def test_plan_matches_ordered_scope_ids_and_typed_identity(self):
        plan = provisioning_plan()
        self.assertEqual([row["scope_id"] for row in plan["rows"]][:2],
                         ["movie-238-nl", "series-1396-nl"])
        by_id = {row["scope_id"]: row for row in plan["rows"]}
        self.assertEqual(
            {k: by_id["movie-238-nl"][k] for k in ("tmdb_id", "content_type", "title", "year")},
            {"tmdb_id": 238, "content_type": "movie", "title": "The Godfather", "year": 1972},
        )
        self.assertEqual(
            {k: by_id["series-1396-nl"][k] for k in ("tmdb_id", "content_type", "title", "year")},
            {"tmdb_id": 1396, "content_type": "series", "title": "Breaking Bad", "year": 2008},
        )
        self.assertEqual(by_id["series-2316-nl"]["title"], "The Office")
        self.assertEqual(by_id["series-2316-nl"]["identity_status"], "VERIFIED_REVIEWED_ALIAS")

    def test_container_layout_resolves_both_pins_from_injected_docs_root(self):
        """The runtime image has no repository docs; the mounted root must win.

        Emulates the backend container (cwd inside the backend directory, all
        module paths rooted at ``/app``) and proves both pinned CSVs are found
        through ``ISSUE97_STAGE_D_DOCS_ROOT`` rather than a ``/docs/...`` path.
        """
        source_root = Path(os.environ.get(DOCS_ROOT_ENV, str(Path(settings.BASE_DIR).resolve().parent)))
        with tempfile.TemporaryDirectory() as mounted, tempfile.TemporaryDirectory() as backend:
            mounted_root = Path(mounted)
            for relative in (COHORT_RELATIVE_PATH, IDENTITY_MANIFEST_RELATIVE_PATH):
                target = mounted_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_root / relative, target)
            cwd = os.getcwd()
            try:
                os.chdir(backend)
                with patch.dict(os.environ, {DOCS_ROOT_ENV: str(mounted_root)}):
                    cohort, manifest = pinned_paths()
                    self.assertEqual(cohort, mounted_root / COHORT_RELATIVE_PATH)
                    self.assertEqual(manifest, mounted_root / IDENTITY_MANIFEST_RELATIVE_PATH)
                    self.assertTrue(cohort.is_file())
                    self.assertTrue(manifest.is_file())
                    self.assertFalse(str(cohort).startswith("/docs/"))
                    self.assertFalse(str(manifest).startswith("/docs/"))
                    plan = provisioning_plan()
            finally:
                os.chdir(cwd)
        self.assertEqual(plan["cohort_sha256"], COHORT_SHA256)
        self.assertEqual(plan["identity_manifest_sha256"], IDENTITY_MANIFEST_SHA256)
        self.assertEqual(len(plan["rows"]), 12)
        self.assertNotIn(HELD_SCOPE_ID, [row["scope_id"] for row in plan["rows"]])

    def test_plan_accepts_retained_evidence_dir_and_rejects_wrong_payloads(self):
        import shutil
        import tarfile
        import tempfile
        from pathlib import Path

        configured = os.environ.get("AVAILABILITY_STAGE_D_EVIDENCE_ARCHIVE")
        archive = Path(configured) if configured else Path.home() / (
            "Library/Application Support/WhereTfDoIWatchIt/issue97/"
            "2026-11-23-v1/issue97-evidence.tar"
        )
        if not archive.exists():
            self.skipTest("Restricted retained archive is not available in this checkout")
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(archive, "r:") as bundle:
                for member in bundle.getmembers():
                    if member.name.startswith("tmdb/"):
                        target = Path(tmp) / Path(member.name).name
                        with bundle.extractfile(member) as source, target.open("wb") as handle:
                            shutil.copyfileobj(source, handle)
            plan = provisioning_plan(tmdb_evidence_dir=Path(tmp))
            self.assertTrue(plan["evidence_checked"])
            self.assertEqual(len(plan["rows"]), 12)
            with open(Path(tmp) / "movie_238.json", "wb") as handle:
                handle.write(b"{}")
            with self.assertRaises(ValueError):
                provisioning_plan(tmdb_evidence_dir=Path(tmp))


class ProvisioningCommandTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Identity provisioning requires PostgreSQL")
        Movie.objects.all().delete()

    def test_read_only_mode_reports_all_missing_without_writes(self):
        output = StringIO()
        call_command("issue97_stage_d_identities", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["mode"], "read-only")
        self.assertEqual(report["planned_rows"], 12)
        self.assertEqual(len(report["missing_rows"]), 12)
        self.assertFalse(report["ready_for_preflight"])
        self.assertEqual(Movie.objects.count(), 0)

    def test_apply_requires_disposable_attestation_and_exact_fingerprint(self):
        from availability.services.experiment_runner import database_identity

        output = StringIO()
        with patch.dict(os.environ, {"AVAILABILITY_STAGE_D_DISPOSABLE": "0"}):
            with self.assertRaises(Exception):
                call_command("issue97_stage_d_identities", "--apply", stdout=output)
        with patch.dict(os.environ, {"AVAILABILITY_STAGE_D_DISPOSABLE": "1"}):
            with self.assertRaises(Exception):
                call_command("issue97_stage_d_identities", "--apply",
                             "--confirm-database-identity", "wrong", stdout=output)
            call_command("issue97_stage_d_identities", "--apply",
                         "--confirm-database-identity", database_identity(), stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(len(report["provisioned_rows"]), 12)
        self.assertTrue(report["ready_for_preflight"])
        self.assertEqual(Movie.objects.count(), 12)

    def test_apply_refuses_to_overwrite_a_conflicting_row(self):
        from availability.services.experiment_runner import database_identity

        Movie.objects.create(tmdb_id=238, content_type="movie", title="Wrong", year=1900)
        output = StringIO()
        with patch.dict(os.environ, {"AVAILABILITY_STAGE_D_DISPOSABLE": "1"}):
            with self.assertRaises(Exception):
                call_command("issue97_stage_d_identities", "--apply",
                             "--confirm-database-identity", database_identity(), stdout=output)
        self.assertEqual(Movie.objects.get(tmdb_id=238).title, "Wrong")

    def test_apply_is_idempotent(self):
        from availability.services.experiment_runner import database_identity

        with patch.dict(os.environ, {"AVAILABILITY_STAGE_D_DISPOSABLE": "1"}):
            for _ in range(2):
                output = StringIO()
                call_command("issue97_stage_d_identities", "--apply",
                             "--confirm-database-identity", database_identity(), stdout=output)
        self.assertEqual(Movie.objects.count(), 12)
        self.assertEqual(json.loads(output.getvalue())["provisioned_rows"], [])
