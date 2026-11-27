"""Offline provisioning of exact Stage D typed title identities on a disposable DB.

Read-only by default.  ``--apply`` writes only the 12 reviewed typed Movie rows
from the SHA-256-pinned cohort; it refuses to overwrite, invent or admit any
conflicting or held identity.  No network, no grants, no quota and no runtime
configuration are touched.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from availability.services.experiment_runner import database_identity
from availability.services.issue97_stage_d_identity_provisioning import provisioning_plan
from tracker.models import Movie

DISPOSABLE_ENV = "AVAILABILITY_STAGE_D_DISPOSABLE"


class Command(BaseCommand):
    help = (
        "Provision or verify the 12 evidence-pinned Stage D typed Movie identities "
        "on an attested disposable PostgreSQL target (offline only)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write missing reviewed rows (default: read-only)")
        parser.add_argument("--tmdb-evidence-dir", type=Path, help="Restricted directory of raw TMDb payloads")
        parser.add_argument("--confirm-database-identity", help="Exact database identity fingerprint for --apply")

    def handle(self, *args, **options):
        import os

        if connection.vendor != "postgresql":
            raise CommandError("Stage D identity provisioning requires PostgreSQL")
        try:
            plan = provisioning_plan(tmdb_evidence_dir=options["tmdb_evidence_dir"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        database = database_identity()
        wanted = {(entry["tmdb_id"], entry["content_type"]): entry for entry in plan["rows"]}
        existing = {
            (movie.tmdb_id, movie.content_type): movie
            for movie in Movie.objects.filter(
                tmdb_id__in=[entry["tmdb_id"] for entry in plan["rows"]],
            )
        }
        missing = []
        conflicting = []
        for key, entry in wanted.items():
            movie = existing.get(key)
            if movie is None:
                missing.append(entry)
                continue
            actual = {
                "tmdb_id": movie.tmdb_id, "content_type": movie.content_type,
                "title": movie.title, "year": movie.year,
            }
            expected = {
                "tmdb_id": entry["tmdb_id"], "content_type": entry["content_type"],
                "title": entry["title"], "year": entry["year"],
            }
            if actual != expected:
                conflicting.append({"scope_id": entry["scope_id"], "expected": expected, "actual": actual})

        report = {
            "database_identity": database,
            "manifest_sha256": plan["manifest_sha256"],
            "cohort_sha256": plan["cohort_sha256"],
            "identity_manifest_sha256": plan["identity_manifest_sha256"],
            "evidence_checked": plan["evidence_checked"],
            "held": plan["held"],
            "planned_rows": len(plan["rows"]),
            "present_exact_rows": len(wanted) - len(missing) - len(conflicting),
            "missing_rows": [entry["scope_id"] for entry in missing],
            "conflicting_rows": conflicting,
            "mode": "apply" if options["apply"] else "read-only",
        }

        if options["apply"]:
            if os.environ.get(DISPOSABLE_ENV) != "1":
                raise CommandError("Provisioning writes require an attested disposable database")
            if options["confirm_database_identity"] != database:
                raise CommandError("Confirmed database identity differs from the actual PostgreSQL target")
            if conflicting:
                raise CommandError("Conflicting typed Movie rows must be reviewed before provisioning")
            with transaction.atomic(durable=True):
                for entry in missing:
                    Movie.objects.create(
                        tmdb_id=entry["tmdb_id"],
                        content_type=entry["content_type"],
                        title=entry["title"],
                        year=entry["year"],
                        canonical_title=entry["title"],
                    )
            report["provisioned_rows"] = [entry["scope_id"] for entry in missing]
            report["missing_rows"] = []

        report["ready_for_preflight"] = not conflicting and not report["missing_rows"]
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
