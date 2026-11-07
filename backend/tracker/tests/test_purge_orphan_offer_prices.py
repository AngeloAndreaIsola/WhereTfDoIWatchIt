"""Safety rules for ``purge_orphan_offer_prices``.

The command exists because ``OfferPrice`` has no database ``ForeignKey`` to
``Offer`` (migration ``tracker.0033`` runs before ``availability.0009`` and
would query a table that does not exist yet). Its whole value is that it deletes
*only* price rows whose canonical offer is truly gone:

  * a dry run must write nothing;
  * a row whose offer still exists is never touched;
  * applying twice changes nothing the second time.
"""
from __future__ import annotations

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from availability.models import Country, Offer, OfferPrice, Source
from availability.persistence.repositories import OfferPriceRepository
from tracker.models import Provider


def _fixtures():
    provider, _ = Provider.objects.get_or_create(
        slug="purge-netflix", defaults={"name": "Netflix"}
    )
    country, _ = Country.objects.get_or_create(
        code="NL", defaults={"name": "Netherlands"}
    )
    source, _ = Source.objects.get_or_create(
        slug="purge-src", defaults={"name": "Source"}
    )
    return provider, country, source


def _offer(provider, country, source, pcid):
    return Offer.objects.create(
        provider=provider, country=country, source=source,
        content_type="movie", tmdb_id=550, offer_type="rent",
        provider_content_id=pcid,
    )


class PurgeOrphanOfferPricesTests(TestCase):
    def test_dry_run_writes_nothing(self):
        provider, country, source = _fixtures()
        offer = _offer(provider, country, source, "gone")
        OfferPriceRepository().record(
            offer=offer, source=source, amount=Decimal("2.9900"), currency="EUR",
        )
        Offer.objects.filter(pk=offer.pk).delete()

        out = StringIO()
        call_command("purge_orphan_offer_prices", stdout=out)

        self.assertEqual(OfferPrice.objects.count(), 1)
        self.assertIn("Dry run", out.getvalue())

    def test_apply_deletes_only_orphans(self):
        provider, country, source = _fixtures()
        orphan = _offer(provider, country, source, "gone")
        OfferPriceRepository().record(
            offer=orphan, source=source, amount=Decimal("2.9900"), currency="EUR",
        )
        kept = _offer(provider, country, source, "kept")
        OfferPriceRepository().record(
            offer=kept, source=source, amount=Decimal("4.9900"), currency="EUR",
        )
        Offer.objects.filter(pk=orphan.pk).delete()

        call_command("purge_orphan_offer_prices", "--apply", stdout=StringIO())

        self.assertEqual(OfferPrice.objects.count(), 1)
        self.assertEqual(OfferPrice.objects.get().offer_identity, kept.identity)

    def test_applying_twice_is_idempotent(self):
        provider, country, source = _fixtures()
        orphan = _offer(provider, country, source, "gone")
        OfferPriceRepository().record(
            offer=orphan, source=source, amount=Decimal("2.9900"), currency="EUR",
        )
        Offer.objects.filter(pk=orphan.pk).delete()

        call_command("purge_orphan_offer_prices", "--apply", stdout=StringIO())
        call_command("purge_orphan_offer_prices", "--apply", stdout=StringIO())

        self.assertEqual(OfferPrice.objects.count(), 0)
