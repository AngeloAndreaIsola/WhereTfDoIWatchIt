import json

from django.core.management.base import BaseCommand

from tracker.services.catalogue_staging import import_catalogue_inventory


class Command(BaseCommand):
    help = "Checksum-verify and idempotently import a raw catalogue staging export."

    def add_arguments(self, parser):
        parser.add_argument("--data", required=True)
        parser.add_argument("--manifest")

    def handle(self, *args, **options):
        result = import_catalogue_inventory(options["data"], options.get("manifest"))
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
