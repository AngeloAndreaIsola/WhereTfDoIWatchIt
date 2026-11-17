import os
from pathlib import Path

from django.core.management.base import CommandError

from availability.services.experiment_runner import (
    ExperimentBlocked,
    GLOBAL_LIMIT,
    database_identity,
    issue_authorization,
    manifest_digest,
)
from ._experiment_command import ExperimentCommand


class Command(ExperimentCommand):
    help = "Issue a short-lived, manifest- and database-bound execution authorization."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--confirm-live-execution", action="store_true")
        parser.add_argument("--confirm-manifest-digest", required=True)
        parser.add_argument("--confirm-database-identity", required=True)
        parser.add_argument("--maximum-attempts", type=int, required=True)
        parser.add_argument("--expires-at", required=True, help="ISO-8601 timestamp with timezone")
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        manifest = self.load_manifest(options["manifest"])
        if not options["confirm_live_execution"]:
            raise CommandError("authorization requires --confirm-live-execution")
        if options["confirm_manifest_digest"] != manifest_digest(manifest):
            raise CommandError("confirmed manifest digest does not match")
        if options["confirm_database_identity"] != database_identity():
            raise CommandError("confirmed database identity does not match this environment")
        if options["maximum_attempts"] != GLOBAL_LIMIT:
            raise CommandError(f"maximum attempts must be exactly {GLOBAL_LIMIT}")
        try:
            token = issue_authorization(manifest, expires_at=options["expires_at"])
            path = Path(options["output"])
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(path, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                target.write(token + "\n")
            self.stdout.write(f"Authorization written with mode 0600: {path}")
        except (OSError, ExperimentBlocked) as exc:
            raise CommandError(str(exc)) from exc
