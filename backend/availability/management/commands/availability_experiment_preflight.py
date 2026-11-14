import json

from availability.services.experiment_runner import read_only_preflight
from ._experiment_command import ExperimentCommand


class Command(ExperimentCommand):
    help = "Inspect Stage 2G readiness using read-only database queries."

    def handle(self, *args, **options):
        report = read_only_preflight(self.load_manifest(options["manifest"]))
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
