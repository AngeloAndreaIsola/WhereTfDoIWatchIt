"""Walk JustWatch catalogue partitions explicitly (cron entry point).

The generic ``refresh_provider_catalogue`` cannot drive JustWatch: it calls
``refresh_catalogue()`` with no scope, and JustWatch deliberately requires an
explicit country (never inferred from IP/VPN/locale) and an explicit provider
for a catalogue partition. This command plans the partitions, walks up to
``--max-partitions`` of them, resumes each from its ``SyncCursor``, and reports
a scheduler-friendly summary.

Exit codes (same contract as ``refresh_provider_catalogue``):

- ``0`` — every attempted partition succeeded (an empty partition included);
- ``1`` — every attempted partition failed;
- ``2`` — partial failure.

Usage::

    python manage.py refresh_justwatch_catalogue
    python manage.py refresh_justwatch_catalogue --country NL --provider netflix
    python manage.py refresh_justwatch_catalogue --max-partitions 10
"""
from __future__ import annotations

import json

#: Effectively-unbounded plan size used when building the pre-filter plan. The
#: number of mapped country/provider pairs is bounded by configuration (one row
#: per enabled country x mapped provider), so this is a safety valve, not a real
#: budget: ``available_partitions`` slices its result, and slicing a plan that is
#: already fully built costs nothing.
_UNBOUNDED = 100_000

from django.core.management.base import BaseCommand, CommandError

from tracker.services.justwatch_sync import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    available_partitions,
    refresh_justwatch_partitions,
)


class Command(BaseCommand):
    help = (
        "Refresh JustWatch catalogue partitions (country x provider), resuming "
        "from SyncCursor. Aggregator only: never deactivates anything."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-partitions", type=int, default=None,
            help="Cap partitions walked this run (default: service default).",
        )
        parser.add_argument(
            "--country", default=None,
            help="Restrict to one country (must be an explicit ISO code).",
        )
        parser.add_argument(
            "--provider", default=None,
            help="Restrict to one canonical provider slug.",
        )
        parser.add_argument(
            "--list-only", action="store_true",
            help="Print the partition plan without fetching anything.",
        )

    def handle(self, *args, **options):
        country = options["country"]
        provider = options["provider"]
        max_partitions = options["max_partitions"]

        # Order matters: filter FIRST, cap SECOND.
        #
        # Capping first is what made this command report "no partitions match"
        # for a request it should have run: ``available_partitions`` is
        # country-first alphabetical, so ``--max-partitions 1 --country NL
        # --provider netflix`` truncated the plan to ``AU/abc_iview`` and the NL
        # filter then had nothing left to keep. The cap is a cost limit on what
        # the operator *asked for*, not a limit on what they asked about.
        plan = available_partitions(max_partitions=_UNBOUNDED)
        if country:
            plan = [p for p in plan if p[0] == country.upper()]
        if provider:
            plan = [p for p in plan if p[1] == provider]
        if max_partitions:
            plan = plan[:max_partitions]
        # One list, shared by --list-only and execution, so what the operator is
        # shown is exactly what runs.
        if not plan:
            self.stdout.write(self.style.WARNING(
                "No JustWatch partitions match this request "
                f"(country={country!r}, provider={provider!r}). "
                "A partition exists only for an enabled country x an enabled "
                "provider that JustWatch exposes in that market."
            ))
            return

        if options["list_only"]:
            self.stdout.write(self.style.SUCCESS(
                f"{len(plan)} partitions:\n"
                + json.dumps([list(p) for p in plan], indent=2)
            ))
            return

        # Hand the filtered plan over as an explicit list. Rebuilding the global
        # plan here is what previously made `--provider netflix` vanish: Netflix
        # is not near the head of the country-first ordering, so a capped global
        # plan never contained it even though the operator named it. Already
        # capped above, hence max_partitions=None.
        summary = refresh_justwatch_partitions(
            partitions=plan, max_partitions=None
        )
        self.stdout.write(json.dumps(summary, indent=2))

        status = summary["status"]
        if status == STATUS_PARTIAL:
            raise CommandError(
                f"{summary['errors']} of {summary['attempted']} JustWatch "
                f"partitions failed ({summary['refreshed']} succeeded). "
                "Aggregate status: partial.",
                returncode=2,
            )
        if status == STATUS_FAILED:
            raise CommandError(
                f"All {summary['attempted']} attempted JustWatch partitions failed.",
                returncode=1,
            )