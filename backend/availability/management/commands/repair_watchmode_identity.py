"""Split collapsed Watchmode provider-content identities (F4 repair).

Dry run (default, read-only)::

    python manage.py repair_watchmode_identity
    python manage.py repair_watchmode_identity --json

Apply a reviewed plan (workers stopped first)::

    python manage.py repair_watchmode_identity --apply \
        --expect-digest <sha256-from-the-dry-run> --assume-workers-paused

The digest covers exactly the rows that will change; if the database moved
between review and apply the command refuses.
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from availability.services.watchmode_identity_repair import (
    RepairError,
    apply_plan,
    build_plan,
)


class Command(BaseCommand):
    help = (
        "Split collapsed Watchmode provider-level ProviderContent identities "
        "(wm:<provider>) into per-title identities."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the plan. Requires --expect-digest and --assume-workers-paused.",
        )
        parser.add_argument(
            "--expect-digest",
            default="",
            help="Digest printed by the reviewed dry run.",
        )
        parser.add_argument(
            "--assume-workers-paused",
            action="store_true",
            help="Acknowledge that the watchmode refresh workers are stopped.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the plan as JSON instead of a human summary.",
        )

    def handle(self, *args, **options):
        plan = build_plan()
        if options["json"]:
            self.stdout.write(json.dumps(plan.as_dict(), indent=2, default=str))
        else:
            self._print_summary(plan)

        if not options["apply"]:
            if plan.is_empty:
                self.stdout.write("Nothing to repair: no collapsed identities remain.")
            else:
                self.stdout.write(
                    f"Dry run only. Review digest {plan.digest()} and re-run with "
                    "--apply --expect-digest <digest> --assume-workers-paused."
                )
            return

        try:
            result = apply_plan(
                plan,
                expected_digest=options["expect_digest"],
                workers_paused=options["assume_workers_paused"],
            )
        except RepairError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"Applied {result['digest']}: "
            f"{json.dumps(result['counts'], sort_keys=True)}"
        ))

    # ------------------------------------------------------------------ output
    def _print_summary(self, plan):
        counts = plan.counts()
        if plan.is_empty and not plan.blocked:
            self.stdout.write("No collapsed Watchmode identities found.")
            return
        self.stdout.write("Watchmode identity repair plan")
        self.stdout.write("=" * 34)
        for key, value in counts.items():
            self.stdout.write(f"{key:34s} {value}")

        if plan.retire:
            self.stdout.write("\nCollapsed rows to retire:")
            for row in plan.retire:
                self.stdout.write(
                    f"  #{row.content_id} {row.provider_slug}:{row.old_content_id}"
                )

        if plan.contents:
            self.stdout.write("\nPer-title rows:")
            for content in plan.contents:
                mapping = (
                    f"move mapping {content.move_mapping_id}"
                    if content.move_mapping_id else "create mapping"
                )
                self.stdout.write(
                    f"  {content.provider_slug}:{content.new_content_id} "
                    f"tmdb {content.tmdb_id} ({len(content.offer_ids)} offers, {mapping})"
                )

        if plan.blocked:
            self.stdout.write(self.style.WARNING("\nBlocked rows (require review):"))
            for entry in plan.blocked:
                self.stdout.write(f"  {json.dumps(entry, default=str)}")

        self.stdout.write(f"\nDigest: {plan.digest()}")
