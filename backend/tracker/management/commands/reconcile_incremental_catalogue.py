"""Bounded incremental reconciliation; selection preview is the default."""
import json
from django.core.management.base import BaseCommand, CommandError

from tracker.models import CatalogueReconciliationRun, Provider
from tracker.services.incremental_catalogue_reconciliation import (
    PROVIDERS, eligible_records, run_incremental_reconciliation,
)


class Command(BaseCommand):
    help = "Preview eligible discoveries, or explicitly reconcile them with --apply. No offers."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--request-budget", type=int, default=50)
        parser.add_argument("--interval", type=float, default=0.25)
        parser.add_argument("--retries", type=int, default=1)
        parser.add_argument("--allow-network", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--record-id", action="append", type=int, dest="record_ids")
        parser.add_argument("--review", action="store_true", help="Reconsider explicit IDs, including deferred outcomes.")
        parser.add_argument("--status", action="store_true", help="Read persisted run and eligible IDs; no verification or writes.")

    def handle(self, *args, **options):
        slug = options["provider"]
        if not 1 <= options["limit"] <= 250:
            raise CommandError("limit must be 1..250")
        if options["review"] and (not options["record_ids"] or len(set(options["record_ids"])) > options["limit"]):
            raise CommandError("--review requires explicit --record-id selection within --limit")
        if options["status"]:
            from itertools import islice
            result = {
                "provider": slug,
                "run": CatalogueReconciliationRun.objects.filter(provider__slug=slug).values().first(),
                "eligible": [{"record_id": record.pk, "reason": reason} for record, reason in islice(
                    eligible_records(slug, record_ids=options["record_ids"], review=options["review"]), options["limit"])],
            }
        else:
            try:
                result = run_incremental_reconciliation(slug, **{key: options[key] for key in (
                    "limit", "request_budget", "interval", "retries", "allow_network", "apply", "record_ids", "review",
                )})
            except (ValueError, Provider.DoesNotExist) as exc:
                raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False, default=str))
