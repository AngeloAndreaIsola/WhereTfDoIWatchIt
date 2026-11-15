"""Remove ``availability.OfferEvidence`` rows that must no longer exist.

Background
----------

``OfferEvidence`` intentionally has **no** database ``ForeignKey`` to ``Offer``
or ``Provider``. The reason is migration safety:
``tracker.0033_merge_duplicate_providers`` runs *before*
``availability.0010_offerevidence`` on a fresh database and deletes providers
through the live model registry. A relation pointing at ``Provider`` - or a
``post_delete`` receiver that queries this table - would make that migration
touch a table that does not exist yet. Integrity is therefore maintained by the
stable canonical identity (``offer_identity`` is exactly ``Offer.identity``) and
by the refresh lifecycle, not by a database cascade.

The trade-off is that deleting a canonical ``Offer`` by hand leaves its
evidence rows behind, and retiring a ``Source`` (which cannot be deleted while
evidence exists, because the FK is ``PROTECT``) needs its evidence removed.

What this does
--------------

By default it deletes exactly the evidence rows whose ``offer_identity`` is not
present in ``Offer`` - the same orphan rule as ``purge_orphan_offer_prices``.
With ``--source <slug>`` it instead removes every evidence row for that source
and recomputes the affected canonical offers from the remaining evidence, so a
source that must stop contributing cannot leave misleading availability behind.
Dry run by default; pass ``--apply`` to write.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from availability.models import Offer, OfferEvidence, Source
from availability.persistence.repositories import OfferEvidenceRepository


class Command(BaseCommand):
    help = (
        "Remove OfferEvidence rows that must no longer exist: orphaned by a "
        "deleted Offer (default), or all evidence for one Source "
        "(--source). Dry run by default; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Perform the deletions. Without it nothing is written.",
        )
        parser.add_argument(
            "--source",
            default=None,
            help=(
                "Purge every evidence row for this Source slug and recompute "
                "the affected offers from the remaining evidence."
            ),
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        source_slug = options["source"]
        repository = OfferEvidenceRepository()

        self.stdout.write(f"mode                : {'APPLY' if apply else 'DRY RUN'}")
        self.stdout.write(f"evidence rows (total): {OfferEvidence.objects.count()}")

        if source_slug:
            return self._purge_source(source_slug, repository, apply=apply)
        return self._purge_orphans(repository, apply=apply)

    def _purge_source(self, source_slug, repository, *, apply):
        source = Source.objects.filter(slug=source_slug).first()
        if source is None:
            self.stderr.write(f"No Source with slug {source_slug!r}.")
            return
        scope = OfferEvidence.objects.filter(source=source)
        count = scope.count()
        self.stdout.write(f"source              : {source.slug}")
        self.stdout.write(f"source evidence rows: {count}")

        if not apply:
            self.stdout.write(
                "\nDry run: no rows were deleted. Re-run with --apply to write."
            )
            return

        with transaction.atomic():
            # Re-evaluate inside the transaction: evidence written between the
            # count and the delete must be removed too.
            deleted, recomputed = repository.purge_for_source(source)

        self.stdout.write(self.style.SUCCESS(
            f"\nDeleted {deleted} evidence row(s) for {source.slug}; "
            f"recomputed {recomputed} canonical offer row(s)."
        ))

    def _purge_orphans(self, repository, *, apply):
        orphans = OfferEvidence.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        )
        count = orphans.count()
        self.stdout.write(f"orphan evidence rows: {count}")

        if not apply:
            self.stdout.write(
                "\nDry run: no rows were deleted. Re-run with --apply to write."
            )
            return

        if not count:
            self.stdout.write("\nNothing to purge.")
            return

        with transaction.atomic():
            # Re-evaluate inside the transaction: an offer recreated between
            # the count and the delete must keep its evidence rows.
            deleted, _ = OfferEvidence.objects.exclude(
                offer_identity__in=Offer.objects.values("identity")
            ).delete()

        self.stdout.write(self.style.SUCCESS(
            f"\nDeleted {deleted} orphan evidence row(s)."
        ))
