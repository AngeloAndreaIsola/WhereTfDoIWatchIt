"""Apply migrations only after the operator confirms the exact DB target."""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Apply schema migrations after verifying and confirming the database target."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")
        parser.add_argument("--expected-database", required=True)
        parser.add_argument("--expected-host", required=True)
        parser.add_argument("--confirm-target", required=True)
        parser.add_argument("--app-label")
        parser.add_argument("--target")

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"Unknown database alias: {alias}")
        actual = connections[alias].settings_dict
        actual_name = str(actual.get("NAME") or "")
        actual_host = str(actual.get("HOST") or "")
        expected_name = options["expected_database"]
        expected_host = options["expected_host"]
        confirmation = f"{expected_name}@{expected_host}"

        if actual_name != expected_name:
            raise CommandError(
                f"Database name mismatch: configured={actual_name!r}, expected={expected_name!r}"
            )
        if actual_host != expected_host:
            raise CommandError(
                f"Database host mismatch: configured={actual_host!r}, expected={expected_host!r}"
            )
        if options["confirm_target"] != confirmation:
            raise CommandError(
                f"Confirmation must equal the exact target token {confirmation!r}"
            )
        if bool(options["target"]) != bool(options["app_label"]):
            raise CommandError("--app-label and --target must be supplied together")

        migration_args = []
        if options["app_label"]:
            migration_args.extend([options["app_label"], options["target"]])
        self.stdout.write(f"Applying migrations to {actual_name}@{actual_host} (alias={alias})")
        call_command("migrate", *migration_args, database=alias, interactive=False)
