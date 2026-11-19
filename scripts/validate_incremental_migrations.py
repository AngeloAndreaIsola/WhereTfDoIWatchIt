#!/usr/bin/env python3
"""Apply/reverse/reapply incremental migrations on an explicitly named clone.

Restore a live pg_dump into a separate PostgreSQL container first. This script
requires the restored tracker 0056/availability 0016 baseline. It never runs
bulk reconciliation or external HTTP. Evidence contains hashes/counts only.
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve earlier evidence")
    backend = Path(__file__).resolve().parents[1] / "backend"
    sys.path.insert(0, str(backend if backend.exists() else Path.cwd()))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.db import connection, transaction
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder
    from django.utils import timezone
    from tracker.services.incremental_catalogue_reconciliation import eligible_records

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        actual = cursor.fetchone()[0]
    if (connection.vendor != "postgresql" or not args.database.startswith("incremental_")
            or actual != args.database or connection.settings_dict["HOST"] != args.host):
        parser.error("Require the exact expected disposable incremental_* PostgreSQL clone and host")

    def snapshot():
        result = {}
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                           "AND (tablename LIKE 'tracker_%' OR tablename LIKE 'availability_%') ORDER BY tablename")
            for (table,) in cursor.fetchall():
                cursor.execute(f"SELECT to_jsonb(t)::text FROM {connection.ops.quote_name(table)} t "
                               "ORDER BY to_jsonb(t)::text")
                rows = [row[0] for row in cursor.fetchall()]
                result[table] = {"count": len(rows), "sha256": hashlib.sha256(
                    json.dumps(rows).encode()).hexdigest()}
        return result

    def check_preserved(before, after):
        changed = [table for table, value in before.items() if after.get(table) != value]
        assert not changed, f"Existing application data changed: {changed}"

    def check_baseline():
        from tracker.models import CatalogueReconciliationRecord, CatalogueReconciliationRun
        from availability.models import Offer
        counts = {status: CatalogueReconciliationRecord.objects.filter(status=status).count()
                  for status in ("BASELINED", "ASSOCIATED")}
        assert counts == {"BASELINED": 326, "ASSOCIATED": 4769}, counts
        assert not CatalogueReconciliationRun.objects.exists()
        assert not CatalogueReconciliationRecord.objects.filter(next_attempt_at__isnull=False).exists()
        assert not CatalogueReconciliationRecord.objects.exclude(attempts=0).exists()
        for now in (timezone.now(), timezone.now() + timedelta(days=3650)):
            for slug in ("hydrahd", "animeworld"):
                assert not list(eligible_records(slug, now=now)), f"Unexpected matching work for {slug}"
        assert not Offer.objects.exists()
        return counts

    applied = MigrationRecorder(connection).applied_migrations()
    leaves = {app: sorted(name for app_name, name in applied if app_name == app)[-1]
              for app in ("tracker", "availability")}
    assert leaves == {"tracker": "0056_movie_tmdb_content_type_identity",
                      "availability": "0016_contentmapping_match_evidence"}, leaves
    before = snapshot()
    executor = MigrationExecutor(connection)
    target = executor.loader.graph.leaf_nodes()
    plan = [(migration.app_label, migration.name, backwards)
            for migration, backwards in executor.migration_plan(target)]
    assert plan == [("tracker", "0057_incremental_catalogue_reconciliation", False),
                    ("tracker", "0058_baseline_completed_catalogue", False)], plan
    with patch("requests.Session.request", side_effect=AssertionError("Migration must never use HTTP")):
        executor.migrate(target)
        migrated = snapshot()
        check_preserved(before, migrated)
        counts = check_baseline()
        executor = MigrationExecutor(connection)
        executor.migrate([("tracker", "0056_movie_tmdb_content_type_identity")])
        reversed_snapshot = snapshot()
        assert reversed_snapshot == before, "Reverse must restore all original application tables"
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        reapplied = snapshot()
        check_preserved(before, reapplied)
        assert migrated == reapplied, "Reapply must reproduce the same baseline rows"
        check_baseline()
    report = {"status": "PASSED", "database": actual, "restored_migration_leaves": leaves,
              "migration_plan": plan, "original_application_tables_preserved": len(before),
              "migrated_application_tables": len(migrated), "baseline": counts,
              "baseline_attempts": 0, "eligible_now_and_in_ten_years": 0,
              "reverse_and_reapply": "PASSED", "offers_created": 0,
              "before": before, "after": reapplied}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in {"before", "after"}}, indent=2))


if __name__ == "__main__":
    main()
