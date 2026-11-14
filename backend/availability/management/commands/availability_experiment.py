import json
from pathlib import Path

from django.core.management.base import CommandError

from availability.services.experiment_runner import (
    ExperimentBlocked,
    execute_manifest,
    read_authorization,
    read_only_preflight,
)
from ._experiment_command import ExperimentCommand


class Command(ExperimentCommand):
    help = "Run a Stage 2G availability experiment in dry-run mode (default)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("mode", choices=("dry-run", "execute"), nargs="?", default="dry-run")
        parser.add_argument("--authorization-file")

    def handle(self, *args, **options):
        manifest = self.load_manifest(options["manifest"])
        if options["mode"] == "dry-run":
            report = read_only_preflight(manifest)
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
            self.stdout.write("Dry run only: no provider request or application write was issued.")
            return
        auth_path = options.get("authorization_file")
        if not auth_path:
            raise CommandError("execute requires --authorization-file from the separate authorization step")
        try:
            auth = read_authorization(Path(auth_path).read_text(encoding="utf-8"), manifest)
            self.stdout.write(json.dumps(execute_manifest(manifest, auth), indent=2))
        except (OSError, ExperimentBlocked) as exc:
            raise CommandError(str(exc)) from exc
