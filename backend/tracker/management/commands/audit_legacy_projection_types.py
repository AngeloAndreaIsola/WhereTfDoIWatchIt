"""Report legacy media-type drift without network access or database writes."""
import json

from django.core.management.base import BaseCommand

from availability.services.legacy_ingest import legacy_type_report
from tracker.models import ScrapedContentMapping


class Command(BaseCommand):
    help = "Read-only legacy projection type audit (JSON lines); never applies repairs."

    def add_arguments(self, parser):
        parser.add_argument("--mapping-id", type=int, help="Limit diagnosis to one legacy row")

    def handle(self, *args, **options):
        rows = ScrapedContentMapping.objects.select_related("movie").order_by("pk")
        if options["mapping_id"] is not None:
            rows = rows.filter(pk=options["mapping_id"])
        affected = 0
        for mapping in rows.iterator(chunk_size=500):
            report = legacy_type_report(mapping)
            if report["conflicts"] or report["payload_type_disagrees"]:
                affected += 1
                self.stdout.write(json.dumps(report, sort_keys=True))
        self.stdout.write(json.dumps({"affected": affected, "read_only": True}))
