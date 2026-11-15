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

Bounded, year-partitioned runs (P8)::

    python manage.py refresh_justwatch_catalogue --country NL --provider netflix \
        --content-type MOVIE --year-start 2026 --year-end 2020 --pages 2

How the bounds combine
----------------------
``--country``/``--provider`` select which catalogue partitions exist at all
(one per enabled country x mapped provider). ``--content-type`` and the year
bounds then *subdivide* each selected partition. Without any year bound the
command behaves exactly as it did before P8: one unbounded partition per
country/provider pair.

``--pages`` bounds the upstream requests per partition visit. A partition is
never walked unbounded by raising it — the walk stops at the first empty page
and at the measured offset ceiling.

The partition cap
-----------------
A partition cap **always** applies, so no invocation can walk the whole
catalogue by accident:

- ``--max-partitions N`` runs at most N partitions;
- omitting it runs at most ``DEFAULT_MAX_PARTITIONS`` — the same bound the
  coordinator uses for an unplanned run.

This is the P8 fix. The command used to build an unbounded plan and hand it to
the coordinator with ``max_partitions=None``, which the coordinator deliberately
honours verbatim ("an operator asking for one specific partition must get it").
The result was that a bare ``refresh_justwatch_catalogue`` walked every
generated partition — 72 of them — instead of the configured handful.

Filtering still happens before the cap, so naming a partition that sits past the
cap's reach still works: ``--country NL --provider netflix --max-partitions 1``
runs that one partition rather than truncating to ``AU/abc_iview`` first.

``--max-partitions 0`` is rejected. Zero previously fell through the truthiness
check, producing an empty plan and a misleading "no partitions match" warning
instead of an error.
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

from availability.domain.enums import ContentType

from tracker.services.justwatch_sync import (
    DEFAULT_MAX_PARTITIONS,
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
            "--content-type", default=None,
            help="Restrict to MOVIE or SERIES (default: MOVIE, as before).",
        )
        parser.add_argument(
            "--year-start", type=int, default=None,
            help=(
                "Newest release year of the window to walk. Generation descends, "
                "so this is the *upper* bound: --year-start 2026 --year-end 2020 "
                "walks 2026 down to 2020."
            ),
        )
        parser.add_argument(
            "--year-end", type=int, default=None,
            help="Oldest release year of the window to walk.",
        )
        parser.add_argument(
            "--year-window", type=int, default=None,
            help="Split the year range into windows of this many years.",
        )
        parser.add_argument(
            "--pages", type=int, default=None,
            help="Upstream pages walked per partition visit (default: 1).",
        )
        parser.add_argument(
            "--list-only", action="store_true",
            help="Print the partition plan without fetching anything.",
        )

    @staticmethod
    def _describe(plan, content_type, year_start, year_end, year_window, pages):
        """The plan as the operator will see it, subdivision included."""
        from tracker.services.justwatch_sync import _partition_id, year_windows

        described = []
        for country, provider in plan:
            for min_year, max_year in (
                year_windows(year_start, year_end, year_window)
                if year_start is not None else [(None, None)]
            ):
                described.append({
                    "country": country,
                    "provider": provider,
                    "content_type": str((content_type or ContentType.MOVIE).value),
                    "min_year": min_year,
                    "max_year": max_year,
                    "pages_per_visit": pages or 1,
                    "partition_id": _partition_id(
                        country=country, provider=provider,
                        content_type=content_type or ContentType.MOVIE,
                        min_year=min_year, max_year=max_year,
                    ),
                })
        return described

    def handle(self, *args, **options):
        country = options["country"]
        provider = options["provider"]
        max_partitions = options["max_partitions"]
        pages = options.get("pages")
        year_start = options.get("year_start")
        year_end = options.get("year_end")
        year_window = options.get("year_window")

        # ``< 1`` rather than ``< 0``: 0 previously fell through the truthiness
        # check below and silently produced an empty plan, so an operator asking
        # for "no partitions" got a confusing warning instead of an error.
        if max_partitions is not None and max_partitions < 1:
            raise CommandError(
                f"--max-partitions must be at least 1, got {max_partitions}."
            )
        if pages is not None and pages < 1:
            raise CommandError("--pages must be at least 1.")
        if year_window is not None and year_window < 1:
            raise CommandError("--year-window must be at least 1.")
        if (year_start is None) != (year_end is None):
            raise CommandError(
                "--year-start and --year-end must be given together."
            )
        if year_start is not None and year_end is not None and year_end > year_start:
            raise CommandError(
                f"--year-end ({year_end}) must not be newer than --year-start "
                f"({year_start}); the window is walked newest first."
            )
        # ContentType values are lowercase ("movie"/"series") while the CLI
        # convention here is uppercase, so match on the name, case-insensitively.
        content_type = None
        requested_type = options.get("content_type")
        if requested_type:
            requested = requested_type.strip().upper()
            matches = [c for c in ContentType if c.name == requested]
            if not matches:
                raise CommandError(
                    f"--content-type must be one of "
                    f"{', '.join(c.name for c in ContentType)}, got "
                    f"{options['content_type']!r}."
                )
            content_type = matches[0]

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
        # The cap always applies. Before P8 the command passed
        # ``max_partitions=None`` downstream together with an explicit plan, and
        # the coordinator honours an explicit plan verbatim — so a bare
        # invocation walked every generated partition. Filtering still precedes
        # the cap (see above), so the cap bounds what the operator asked for
        # rather than hiding the partition they named.
        plan = plan[:(max_partitions or DEFAULT_MAX_PARTITIONS)]
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
                + json.dumps(self._describe(plan, content_type, year_start,
                                            year_end, year_window, pages),
                             indent=2)
            ))
            return

        # Hand the filtered plan over as an explicit list. Rebuilding the global
        # plan here is what previously made `--provider netflix` vanish: Netflix
        # is not near the head of the country-first ordering, so a capped global
        # plan never contained it even though the operator named it. Already
        # capped above, hence max_partitions=None.
        # ``max_partitions=None`` means "run exactly this list verbatim" to the
        # coordinator. Safe now because the cap above has already truncated the
        # list; passing the cap again would be a second, invisible limit.
        summary = refresh_justwatch_partitions(
            partitions=plan,
            max_partitions=None,
            content_type=content_type or ContentType.MOVIE,
            pages_per_partition=pages or 1,
            year_start=year_start,
            year_end=year_end,
            year_window=year_window,
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