import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from availability.services.experiment_runner import ExperimentBlocked
from availability.services.issue97_stage_d_runner import execute, preflight, recover_checkpoint


class Command(BaseCommand):
    help = "Review Stage D readiness or operate a separately authorized disposable title batch."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, choices=(1, 2, 3))
        parser.add_argument("--confirm-database-identity")
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--execute-token-file", type=Path)
        action.add_argument("--recover-checkpoint", action="store_true")

    def handle(self, *args, **options):
        batch = options["batch"]
        database = options["confirm_database_identity"]
        token_file = options["execute_token_file"]
        try:
            if options["recover_checkpoint"]:
                if batch is not None:
                    raise CommandError("Checkpoint recovery uses the unfinished attempt on the confirmed database.")
                report = recover_checkpoint(database)
            elif token_file is not None:
                if batch is None or not database:
                    raise CommandError("Execution requires --batch and --confirm-database-identity.")
                try:
                    token = token_file.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise CommandError("Could not read the Stage D authorization file.") from exc
                report = execute(batch, token, database)
            else:
                report = preflight(batch if batch is not None else 1)
        except ExperimentBlocked as exc:
            raise CommandError(str(exc)) from exc
        except DatabaseError as exc:
            raise CommandError("Stage D database connection or schema check failed. Verify the target and migrations.") from exc
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
