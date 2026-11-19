import json

from django.core.management.base import BaseCommand

from tracker.services.catalogue_staging import export_catalogue_inventory


class Command(BaseCommand):
    help = "Export pending and classified raw catalogue observations to versioned, checksummed JSONL."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", required=True)

    def handle(self, *args, **options):
        result = export_catalogue_inventory(options["output_dir"])
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
