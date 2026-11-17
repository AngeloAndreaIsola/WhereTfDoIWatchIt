"""Dry-run-first classification and matching proposals for discoveries."""
import json

from django.core.management.base import BaseCommand

from tracker.services.catalogue_reconciliation import reconcile_catalogue


class Command(BaseCommand):
    help = "Report conservative catalogue classifications and canonical match proposals."

    def add_arguments(self, parser):
        parser.add_argument("--provider", action="append", dest="providers", help="Provider slug; may be repeated.")
        parser.add_argument("--limit", type=int, help="Maximum records total, ordered by provider and record ID.")
        parser.add_argument("--apply", action="store_true", help="Persist confident classifications and safe canonical matches.")
        parser.add_argument("--summary-only", action="store_true", help="Omit per-record proposals from JSON output.")

    def handle(self, *args, **options):
        result = reconcile_catalogue(
            provider_slugs=options.get("providers"),
            limit=options.get("limit"),
            apply=options["apply"],
        )
        if options["summary_only"]:
            result.pop("records", None)
        self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False, default=str))
