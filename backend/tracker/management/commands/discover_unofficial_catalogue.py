"""Inspect or explicitly run one bounded unofficial catalogue discovery."""
from __future__ import annotations

import json
from django.core.management.base import BaseCommand, CommandError

from tracker.services.catalogue_discovery import PROVIDERS, discovery_status, run_discovery


class Command(BaseCommand):
    help = "Inspect or run bounded, checkpointed discovery for one unofficial provider."

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true", help="Print durable per-provider discovery reports.")
        parser.add_argument("--provider", choices=sorted(PROVIDERS))
        parser.add_argument("--mode", choices=("full", "incremental", "targeted"), default="full")
        parser.add_argument("--request-budget", type=int, default=20)
        parser.add_argument("--delay", type=float, default=1.0, help="Seconds between page requests.")
        parser.add_argument("--retry-limit", type=int, default=1, help="Retries per transient request (bounded).")
        parser.add_argument("--dry-run", action="store_true", help="Observe pages without database writes or state changes.")
        parser.add_argument("--query", help="Targeted provider search term (partial endpoint results only).")

    def handle(self, *args, **options):
        if options["status"]:
            self.stdout.write(json.dumps(discovery_status(), indent=2, default=str))
            return
        if not options["provider"]:
            raise CommandError("--provider is required unless --status is used")
        if options["mode"] == "targeted" and not options["query"]:
            raise CommandError("--query is required for targeted mode")
        try:
            result = run_discovery(
                options["provider"], mode=options["mode"],
                request_budget=options["request_budget"], delay=options["delay"],
                retry_limit=options["retry_limit"],
                dry_run=options["dry_run"], targeted_query=options["query"],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, indent=2, default=str))
