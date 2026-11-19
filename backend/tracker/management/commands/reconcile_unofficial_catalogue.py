"""Dry-run-first classification and matching proposals for discoveries."""
import json

from django.core.management.base import BaseCommand, CommandError

from tracker.services.catalogue_reconciliation import reconcile_catalogue


class Command(BaseCommand):
    help = "Report conservative catalogue classifications and canonical match proposals."

    def add_arguments(self, parser):
        parser.add_argument("--provider", action="append", dest="providers", help="Provider slug; may be repeated.")
        parser.add_argument("--record-id", action="append", type=int, dest="record_ids")
        parser.add_argument("--limit", type=int, help="Maximum records total, ordered by provider and record ID.")
        parser.add_argument("--apply", action="store_true", help="Persist confident classifications and safe canonical matches.")
        parser.add_argument("--summary-only", action="store_true", help="Omit per-record proposals from JSON output.")

    def handle(self, *args, **options):
        if options.get("limit") is not None and options["limit"] < 0:
            raise CommandError("limit must be non-negative")
        if options["apply"]:
            selected = options.get("record_ids") or []
            limit = options.get("limit")
            if (not selected and limit is None) or len(set(selected)) > 250 or (limit is not None and limit > 250):
                raise CommandError("Apply requires explicit --record-id selection or --limit, capped at 250 records")
        result = reconcile_catalogue(
            record_ids=options.get("record_ids"),
            provider_slugs=options.get("providers"),
            limit=options.get("limit"),
            apply=options["apply"],
        )
        if options["summary_only"]:
            result.pop("records", None)
        self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False, default=str))
