"""Safety rules for ``purge_orphan_offer_evidence``.

The command exists because ``OfferEvidence`` has no database ``ForeignKey`` to
``Offer`` (migration ``tracker.0033`` runs before ``availability.0010`` and would
query a table that does not exist yet), and because a ``Source`` cannot be
deleted while evidence exists (the FK is ``PROTECT``). Its value is that it
removes *only* evidence that must go:

  * a dry run must write nothing;
  * a row whose offer still exists is never touched;
  * applying twice changes nothing the second time;
  * ``--source`` removes one source's evidence and recomputes the affected
    offers from the remaining evidence.
"""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from availability.models import Country, Offer, OfferEvidence, Source
from availability.persistence.repositories import OfferEvidenceRepository
from tracker.models import Provider


def _fixtures():
    provider, _ = Provider.objects.get_or_create(
        slug="purge-ev-netflix", defaults={"name": "Netflix"}
    )
    country, _ = Country.objects.get_or_create(
        code="NL", defaults={"name": "Netherlands"}
    )
    source, _ = Source.objects.get_or_create(
        slug="purge-ev-src", defaults={"name": "Source"}
    )
    return provider, country, source


def _offer(provider, country, source, pcid):
    return Offer.objects.create(
        provider=provider, country=country, source=source,
        content_type="movie", tmdb_id=550, offer_type="subscription",
        provider_content_id=pcid,
    )


def _evidence(offer, source):
    OfferEvidenceRepository().record(
        offer_identity=offer.identity, source=source,
        provider_slug=offer.provider.slug, country_code=offer.country.code,
        content_type=offer.content_type, tmdb_id=offer.tmdb_id,
        offer_type=offer.offer_type, provider_content_id=offer.provider_content_id,
    )


class PurgeOrphanOfferEvidenceTests(TestCase):
    def test_dry_run_writes_nothing(self):
        provider, country, source = _fixtures()
        offer = _offer(provider, country, source, "gone")
        _evidence(offer, source)
        Offer.objects.filter(pk=offer.pk).delete()

        out = StringIO()
        call_command("purge_orphan_offer_evidence", stdout=out)

        self.assertEqual(OfferEvidence.objects.count(), 1)
        self.assertIn("Dry run", out.getvalue())

    def test_apply_deletes_only_orphans(self):
        provider, country, source = _fixtures()
        orphan = _offer(provider, country, source, "gone")
        _evidence(orphan, source)
        kept = _offer(provider, country, source, "kept")
        _evidence(kept, source)
        Offer.objects.filter(pk=orphan.pk).delete()

        call_command("purge_orphan_offer_evidence", "--apply", stdout=StringIO())

        self.assertEqual(OfferEvidence.objects.count(), 1)
        self.assertEqual(OfferEvidence.objects.get().offer_identity, kept.identity)

    def test_applying_twice_is_idempotent(self):
        provider, country, source = _fixtures()
        orphan = _offer(provider, country, source, "gone")
        _evidence(orphan, source)
        Offer.objects.filter(pk=orphan.pk).delete()

        call_command("purge_orphan_offer_evidence", "--apply", stdout=StringIO())
        call_command("purge_orphan_offer_evidence", "--apply", stdout=StringIO())

        self.assertEqual(OfferEvidence.objects.count(), 0)

    def test_source_option_purges_and_recomputes(self):
        provider, country, source = _fixtures()
        offer = _offer(provider, country, source, "shared")
        _evidence(offer, source)
        other, _ = Source.objects.get_or_create(
            slug="purge-ev-src-b", defaults={"name": "Source B"}
        )
        _evidence(offer, other)

        call_command(
            "purge_orphan_offer_evidence", "--apply",
            "--source", source.slug, stdout=StringIO(),
        )

        self.assertFalse(OfferEvidence.objects.filter(source=source).exists())
        # Another source still observes it, so it stays available.
        offer.refresh_from_db()
        self.assertTrue(offer.currently_available)
        # The other source's row is untouched.
        self.assertTrue(OfferEvidence.objects.filter(source=other).exists())

    def test_source_option_targets_only_that_source(self):
        provider, country, source = _fixtures()
        offer = _offer(provider, country, source, "only-a")
        _evidence(offer, source)

        call_command(
            "purge_orphan_offer_evidence", "--apply",
            "--source", source.slug, stdout=StringIO(),
        )

        # The sole source is gone: the offer is recomputed unavailable.
        offer.refresh_from_db()
        self.assertFalse(offer.currently_available)

    def test_orphan_purge_never_touches_a_live_offers_evidence(self):
        provider, country, source = _fixtures()
        orphan = _offer(provider, country, source, "gone")
        _evidence(orphan, source)
        live = _offer(provider, country, source, "live")
        _evidence(live, source)
        other, _ = Source.objects.get_or_create(
            slug="purge-ev-src-live", defaults={"name": "Live"}
        )
        _evidence(live, other)
        Offer.objects.filter(pk=orphan.pk).delete()

        call_command("purge_orphan_offer_evidence", "--apply", stdout=StringIO())

        # Only the orphan is removed; both rows of the live offer survive,
        # including the other source's.
        identities = set(
            OfferEvidence.objects.values_list("offer_identity", flat=True)
        )
        self.assertEqual(identities, {live.identity})
        self.assertEqual(OfferEvidence.objects.filter(source=other).count(), 1)
