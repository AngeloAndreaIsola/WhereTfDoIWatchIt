from decimal import Decimal

from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from availability.models import build_offer_identity


BEFORE_VARIANTS = ("availability", "0017_justwatch_request_budget")
WITH_VARIANTS = ("availability", "0018_offerprice_variants")
TITLE_PROVENANCE = ("tracker", "0063_title_check_report_provenance")


class OfferPriceMigrationTests(TransactionTestCase):
    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_legacy_rows_survive_upgrade_and_downgrade_but_variant_payloads_do_not(self):
        executor = MigrationExecutor(connection)
        self.addCleanup(self._migrate, executor.loader.graph.leaf_nodes())
        legacy_targets = [BEFORE_VARIANTS, TITLE_PROVENANCE]
        current_targets = [WITH_VARIANTS, TITLE_PROVENANCE]
        legacy = self._migrate(legacy_targets)

        provider = legacy.get_model("tracker", "Provider").objects.create(
            slug="migration-provider", name="Migration Provider",
        )
        country, _ = legacy.get_model("availability", "Country").objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"},
        )
        source = legacy.get_model("availability", "Source").objects.create(
            slug="migration-source", name="Migration Source", type="aggregator",
        )
        now = timezone.now()
        sync_run = legacy.get_model("availability", "SyncRun").objects.create(
            provider=provider, country=country, source=source, mode="targeted",
            status="succeeded", started_at=now, completed_at=now,
        )
        movie = legacy.get_model("tracker", "Movie").objects.create(
            tmdb_id=98004, content_type="movie", title="Migration fixture", year=2025,
        )
        scope = dict(content_type="movie", tmdb_id=movie.tmdb_id, offer_type="rent",
                     provider_content_id="tm98004", source=source)
        identity = build_offer_identity(
            provider.slug, country.code, "movie", movie.tmdb_id, None, None, "rent", "tm98004",
        )
        offer = legacy.get_model("availability", "Offer").objects.create(
            **scope, provider=provider, country=country, identity=identity, sync_run=sync_run,
            provider_url="https://example.test/rent", source_record_id="legacy-rent", observed_at=now,
        )
        price_scope = dict(**scope, offer_identity=identity, provider_slug=provider.slug,
                           country_code=country.code)
        legacy_prices = legacy.get_model("availability", "OfferPrice").objects
        price = legacy_prices.create(
            **price_scope, amount=Decimal("4.1200"), currency="EUR",
            source_offer_id="legacy-rent", observed_at=now,
        )
        evidence = legacy.get_model("availability", "OfferEvidence").objects.create(
            **price_scope, is_present=True, observed_at=now,
        )
        title_check = legacy.get_model("tracker", "TitleAvailabilityCheck").objects.create(
            movie=movie, tmdb_id=movie.tmdb_id, content_type="movie", country=country.code,
            source=source.slug, last_attempted_at=now, last_succeeded_at=now,
            successful_run_ids=[sync_run.pk],
        )

        def snapshot(apps):
            return {
                row._meta.model_name: apps.get_model(row._meta.app_label, row._meta.model_name)
                .objects.values().get(pk=row.pk)
                for row in (offer, price, evidence, sync_run, title_check)
            }

        original = snapshot(legacy)
        current = self._migrate(current_targets)
        expected = {**original, "offerprice": {**original["offerprice"], "variants": []}}
        self.assertEqual(snapshot(current), expected)

        # The 0017 ORM omits the new column when inserting and 0018 has no DB default.
        with self.assertRaises(IntegrityError) as caught:
            legacy_prices.create(**{**price_scope, "offer_identity": "old-writer-insert"})
        self.assertEqual(caught.exception.__cause__.diag.column_name, "variants")

        prices = current.get_model("availability", "OfferPrice").objects
        variants = [{"source_offer_id": "legacy-rent", "amount": "4.1200", "currency": "EUR"}]
        prices.filter(pk=price.pk).update(variants=variants)
        self.assertEqual(prices.get(pk=price.pk).variants, variants)

        self.assertEqual(snapshot(self._migrate(legacy_targets)), original)
        self.assertEqual(snapshot(self._migrate(current_targets)), expected)
