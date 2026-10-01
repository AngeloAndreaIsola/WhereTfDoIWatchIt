"""Full-catalogue refresh for catalogue-capable availability sources (Phase 5).

Cron entry point for provider-specific collectors (CBC Gem first): one fetch
per source covering its whole configured catalogue, no API budget. The
streaming-availability budget stays with ``refresh_availability``.

Scheduling contract (Phase 6): the command discovers sources by capability
(``supports_catalogue_sync``) minus disabled database ``Source`` rows, runs
each under a per-source lock, isolates failures between sources, and exits
with a status a scheduler can act on:

- ``0`` success — every attempted source succeeded (including a legitimate
  empty catalogue, and including runs where sources were skipped cleanly
  because they were disabled or already running);
- ``1`` complete failure — every attempted source failed;
- ``2`` partial failure — some sources succeeded, some failed.

Unknown/non-catalogue ``--source`` values are usage errors and propagate
before anything is recorded (non-zero exit with a traceback).
"""
from __future__ import annotations

import json
import logging

from django.core.management.base import BaseCommand, CommandError

from ...services.bulk_refresh import process_catalogue_refresh

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Refresh the full catalogue of catalogue-capable availability sources "
        "(e.g. CBC Gem). One fetch per source; no API budget applies. "
        "Exit codes: 0 success, 1 all attempted sources failed, "
        "2 partial failure."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=None,
            help="Restrict to one source slug (default: all catalogue-capable sources).",
        )

    def handle(self, *args, **options):
        summary = process_catalogue_refresh(source_slug=options["source"])

        summary_str = json.dumps(summary, indent=2)
        errors = summary.get("errors", 0)
        attempted = summary.get("attempted", 0)
        refreshed = summary.get("refreshed", 0)

        if errors and refreshed:
            # Partial failure: some sources succeeded, so the run is neither a
            # clean success nor a total loss — cron gets its own exit code.
            self.stdout.write(self.style.WARNING(
                f"Catalogue refresh partially failed.\nSummary:\n{summary_str}"
            ))
            raise CommandError(
                f"{errors} of {attempted} catalogue sources failed "
                f"({refreshed} succeeded).",
                returncode=2,
            )
        if errors:
            self.stdout.write(self.style.ERROR(
                f"Catalogue refresh failed.\nSummary:\n{summary_str}"
            ))
            raise CommandError(
                f"All {attempted} attempted catalogue sources failed.",
                returncode=1,
            )
        self.stdout.write(self.style.SUCCESS(
            f"Catalogue refresh completed.\nSummary:\n{summary_str}"
        ))
