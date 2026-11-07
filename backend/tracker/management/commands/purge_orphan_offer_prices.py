"""Delete ``availability.OfferPrice`` rows whose canonical offer no longer exists.

Background
----------

``OfferPrice`` intentionally has **no** database ``ForeignKey`` to ``Offer``.
The reason is migration safety: ``tracker.0033_merge_duplicate_providers``
runs *before* ``availability.0009_offerprice`` on a fresh database and deletes
providers through the live model registry (``Offer`` is pulled in by the
collector). A relation pointing at ``Offer`` - or a ``post_delete`` receiver
that queries ``OfferPrice`` - would make that migration touch a table that does
not exist yet. Integrity is therefore maintained by the stable canonical
identity (``offer_identity`` is exactly ``Offer.identity``) and by the refresh
lifecycle, not by a database cascade.

The trade-off is that deleting a canonical ``Offer`` by hand (admin, shell, an
operator script) leaves its price rows behind. They are harmless - every read
is keyed by the identities of *live* offers, so an orphan is never displayed -
but they would accumulate indefinitely.

What this does
--------------

Deletes exactly the price rows whose ``offer_identity`` is not present in
``Offer``. It never touches a row whose offer still exists, and it is a no-op
when there are no orphans. Dry run by default; pass ``--apply`` to write.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from availability.models import Offer, OfferPrice


class Command(BaseCommand):
    help = (
        "Delete OfferPrice rows whose canonical Offer no longer exists "
        "(manual offer deletions leave them behind). Dry run by default; "
        "pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Perform the deletion. Without it nothing is written.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        orphans = OfferPrice.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        )
        count = orphans.count()

        self.stdout.write(f"mode                : {'APPLY' if apply else 'DRY RUN'}")
        self.stdout.write(f"price rows (total)  : {OfferPrice.objects.count()}")
        self.stdout.write(f"orphan price rows   : {count}")

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
            # the count and the delete must keep its price rows.
            deleted, _ = OfferPrice.objects.exclude(
                offer_identity__in=Offer.objects.values("identity")
            ).delete()

        self.stdout.write(self.style.SUCCESS(f"\nDeleted {deleted} orphan price row(s)."))
