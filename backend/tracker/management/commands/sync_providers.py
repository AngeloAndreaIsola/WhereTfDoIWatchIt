"""Sync the local provider catalogue from JustWatch's ``GetProviders`` list.

Repeatable and idempotent: running it twice produces no duplicate providers,
no duplicate mappings and never touches ``Subscription`` rows (see
``tracker.services.provider_catalogue`` for the full contract).

Examples::

    python manage.py sync_providers                 # every enabled country
    python manage.py sync_providers --country NL --country IT
    python manage.py sync_providers --json          # machine-readable report
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from tracker.services.provider_catalogue import run_sync_provider_catalogue


class Command(BaseCommand):
    help = (
        "Reconcile tracker.Provider rows and JustWatch external mappings from "
        "JustWatch's provider catalogue. Idempotent; never modifies user "
        "subscriptions. Newly discovered providers are created DISABLED."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--country",
            action="append",
            dest="countries",
            default=None,
            help="Limit to one market (repeatable). Default: every enabled country.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the raw JSON report instead of a human summary.",
        )

    def handle(self, *args, **options):
        report = run_sync_provider_catalogue(options.get("countries"))

        if options.get("json"):
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        else:
            self._print_summary(report)

        if report["errors"]:
            # Non-zero so cron notices, but not a CommandError: a partial run is
            # still useful, and the report is the actionable artifact.
            self.stderr.write(
                self.style.WARNING(
                    f"{len(report['errors'])} country fetch(es) failed."
                )
            )
            raise SystemExit(1)

    def _print_summary(self, report: dict) -> None:
        self.stdout.write(
            f"Providers touched: {len(report['providers_touched'])} "
            f"across {len(report['countries'])} countries "
            f"({report['packages_seen']} packages seen)."
        )
        created = report["created_providers"]
        self.stdout.write(
            f"New providers created (disabled): {len(created)}"
            + (f" — {', '.join(created)}" if created else "")
        )
        self.stdout.write(f"Mappings created: {len(report['mappings_created'])}")
        unmapped = report["unmapped"]
        if unmapped:
            self.stdout.write(
                self.style.WARNING(f"Unmapped packages: {len(unmapped)}")
            )
            for item in unmapped:
                markets = ",".join(item["countries"])
                self.stdout.write(
                    f"  {item['technical_name']:32s} "
                    f"{item['clear_name']:32s} [{markets}]"
                )
        else:
            self.stdout.write(self.style.SUCCESS("No unmapped packages."))
