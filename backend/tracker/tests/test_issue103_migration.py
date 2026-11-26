"""Rehearse the additive schema on legacy rows with the actual migration graph."""
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from availability.models import Country, Offer, OfferPrice, Source
from availability.persistence.repositories import OfferPriceRepository
from tracker.models import EntitlementPlan, Provider, Subscription, ViewerMarketSelection
from tracker.services.availability_reads import get_title_offer_evidence
from tracker.services.access import resolve_title
from tracker.models import Movie, Preference


class EntitlementMigrationTests(TransactionTestCase):
    before = ("tracker", "0063_title_check_report_provenance")
    after = ("tracker", "0064_market_entitlements")

    def test_seeded_upgrade_idempotence_and_legacy_read_rollback(self):
        MigrationExecutor(connection).migrate([self.before])
        self.addCleanup(lambda: MigrationExecutor(connection).migrate([self.after]))

        host = Provider.objects.create(slug="legacy-aster", name="Aster")
        child = Provider.objects.create(slug="legacy-orbit", name="Hosted Orbit")
        storefront = Provider.objects.create(slug="legacy-store", name="Store")
        subscriptions = [
            Subscription.objects.create(provider=host, name="Aster", access_mode="included"),
            Subscription.objects.create(name="Legacy name", access_mode="included"),
            Subscription.objects.create(name="No catalogue match", access_mode="none"),
            Subscription.objects.create(provider=child, name="Orbit", access_mode="addon"),
            Subscription.objects.create(provider=storefront, name="Store", access_mode="rent_buy"),
        ]
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        source = Source.objects.create(slug="synthetic-legacy", name="Legacy")
        movie = Movie.objects.create(tmdb_id=910101, title="Migration test title")
        offer = Offer.objects.create(
            provider=storefront, country=country, source=source, content_type="movie",
            tmdb_id=movie.tmdb_id, offer_type="rent", currently_available=False,
        )
        price = OfferPriceRepository().record(offer=offer, source=source, amount=Decimal("3.99"), currency="EUR")
        ids = [row.pk for row in subscriptions]
        snapshot = list(Subscription.objects.filter(pk__in=ids).order_by("pk").values())

        MigrationExecutor(connection).migrate([self.after])
        MigrationExecutor(connection).migrate([self.after])
        self.assertEqual(ViewerMarketSelection.objects.count(), 0)
        self.assertEqual(EntitlementPlan.objects.count(), 0)
        self.assertEqual(Offer.objects.get(pk=offer.pk).identity, offer.identity)
        self.assertEqual(OfferPrice.objects.get(pk=price.pk).amount, Decimal("3.99"))
        self.assertEqual(get_title_offer_evidence(movie).current, [])
        self.assertEqual(resolve_title(movie, [], Preference(current_country="NL")).verdict, "unknown")

        # Rolling back only the read projection leaves additive declarations in place.
        profile = ViewerMarketSelection.objects.create(
            user_id=1, provider_slug=host.slug, billing_country="DE",
            holding="yes", plan_state="unsupported", revision=1,
        )
        self.assertEqual(list(Subscription.objects.filter(pk__in=ids).order_by("pk").values()), snapshot)
        self.assertTrue(ViewerMarketSelection.objects.filter(pk=profile.pk).exists())
