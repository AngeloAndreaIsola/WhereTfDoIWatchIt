"""Price persistence, provenance and conflict semantics (offer price + currency).

Covers the whole path the redesign adds:

- normalization of a structured source price (never a display string);
- ``OfferPriceRepository`` refresh/update behaviour (idempotent, changed,
  missing, malformed, removed, re-observed);
- the refresh service writing a price beside its offer in one transaction;
- reconciliation exposing source-specific prices and selecting a display price
  deterministically without overwriting one source with another;
- the read/API shape (amount as a decimal string, explicit currency);
- the invariant that a price never changes availability or entitlement.
"""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from availability.domain.enums import ConflictKind, ContentType, OfferType, SyncMode
from availability.models import Country, Offer, OfferPrice, Source, SyncRun
from availability.persistence.repositories import (
    InactiveSyncError,
    OfferPriceRepository,
    OfferRepository,
    SyncRunRepository,
)
from availability.services.normalize import (
    normalize_currency,
    normalize_price,
    normalize_price_amount,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import JustWatchOffer
from tracker.models import Movie, Preference, Provider, Subscription
from tracker.services.access import (
    build_reach,
    build_explicit_access,
    classify_offer,
    resolve_title,
)
from tracker.services.availability_reads import get_local_offers
from tracker.services.offers import Offer as TrackerOffer
from tracker.services.ranking import rank_offers

from .freshness_helpers import declare_freshness
from .reconciliation_fixtures import ReconciliationFixtureMixin
from .source_stubs import StubSource
from .source_stubs import fetch_result as _response
from .source_stubs import normalized_offer as _normalized


# ---------------------------------------------------------------------------
# Normalization (pure) — the source boundary, not a display-string guess
# ---------------------------------------------------------------------------
class PriceNormalizationTests(TestCase):
    def test_structured_amount_and_currency_are_preserved(self):
        price = normalize_price(2.99, "EUR")
        self.assertEqual(price.amount, Decimal("2.9900"))
        self.assertEqual(price.currency, "EUR")
        self.assertEqual(price.error, "")
        self.assertTrue(price.is_valid)

    def test_float_amount_does_not_carry_binary_drift(self):
        price = normalize_price(2.9900000000000002, "EUR")
        self.assertEqual(price.amount, Decimal("2.9900"))

    def test_string_numeric_amount_is_accepted(self):
        self.assertEqual(normalize_price_amount("3.99"), (Decimal("3.9900"), ""))

    def test_currency_is_upper_cased_but_never_invented(self):
        self.assertEqual(normalize_currency("eur"), ("EUR", ""))
        self.assertEqual(normalize_currency(""), ("", ""))
        self.assertEqual(normalize_currency("Euro"), ("", "invalid currency"))
        self.assertEqual(normalize_currency("EU"), ("", "invalid currency"))

    def test_missing_amount_is_not_zero(self):
        price = normalize_price(None, "EUR")
        self.assertIsNone(price.amount)
        self.assertEqual(price.error, "missing amount")
        self.assertFalse(price.is_valid)

    def test_missing_currency_is_reported(self):
        price = normalize_price(2.99, "")
        self.assertEqual(price.amount, Decimal("2.9900"))
        self.assertEqual(price.error, "missing currency")
        self.assertFalse(price.is_valid)

    def test_display_only_price_is_ambiguous_not_parsed(self):
        # JustWatch can send ``retailPrice`` ("€2,99", localized) without the
        # structured value. That must never be parsed into an authoritative
        # amount — it is recorded as an ambiguity instead.
        price = normalize_price(None, "", "€2,99")
        self.assertIsNone(price.amount)
        self.assertEqual(price.error, "display-only price")

    def test_invalid_and_negative_amounts_are_rejected(self):
        for value in (-1, "abc", True, float("nan"), float("inf")):
            amount, error = normalize_price_amount(value)
            self.assertIsNone(amount, value)
            self.assertEqual(error, "invalid amount", value)

    def test_amount_larger_than_the_column_is_rejected(self):
        amount, error = normalize_price_amount("100000000")
        self.assertIsNone(amount)
        self.assertEqual(error, "amount out of range")

    def test_precision_is_preserved_to_four_places(self):
        self.assertEqual(normalize_price_amount("1.2345")[0], Decimal("1.2345"))


# ---------------------------------------------------------------------------
# Repository refresh/update semantics
# ---------------------------------------------------------------------------
class OfferPriceRepositoryTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="price-netflix", defaults={"name": "Netflix"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )
        self.source, _ = Source.objects.get_or_create(
            slug="price-src", defaults={"name": "Source"}
        )
        self.other_source, _ = Source.objects.get_or_create(
            slug="price-src-b", defaults={"name": "Source B"}
        )
        self.offer = Offer.objects.create(
            provider=self.provider,
            country=self.country,
            source=self.source,
            content_type=ContentType.MOVIE,
            tmdb_id=550,
            offer_type=OfferType.RENT,
            provider_content_id="pcid",
        )
        self.repo = OfferPriceRepository()

    def _record(self, amount=None, currency="", source=None, error="", **kwargs):
        return self.repo.record(
            offer=self.offer,
            source=source or self.source,
            amount=amount,
            currency=currency,
            error=error,
            **kwargs,
        )

    def test_valid_price_is_created_and_current(self):
        row = self._record(Decimal("2.9900"), "EUR", source_offer_id="o1")
        self.assertEqual(OfferPrice.objects.count(), 1)
        self.assertEqual(row.amount, Decimal("2.9900"))
        self.assertEqual(row.currency, "EUR")
        self.assertTrue(row.is_current)
        self.assertEqual(row.offer_identity, self.offer.identity)

    def test_repeated_observation_is_idempotent(self):
        first = self._record(Decimal("2.9900"), "EUR", now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
        second = self._record(Decimal("2.9900"), "EUR", now=dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))
        self.assertEqual(OfferPrice.objects.count(), 1)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.amount, Decimal("2.9900"))
        self.assertGreater(second.last_seen, first.last_seen)
        # first_seen never moves.
        self.assertEqual(second.first_seen, first.first_seen)

    def test_changed_price_updates_the_row(self):
        self._record(Decimal("2.9900"), "EUR")
        row = self._record(Decimal("3.9900"), "EUR")
        self.assertEqual(OfferPrice.objects.count(), 1)
        self.assertEqual(row.amount, Decimal("3.9900"))
        self.assertTrue(row.is_current)

    def test_missing_price_marks_stale_without_zeroing(self):
        self._record(Decimal("2.9900"), "EUR")
        row = self._record(None, "", error="")
        self.assertEqual(row.amount, Decimal("2.9900"))  # preserved, not zero
        self.assertFalse(row.is_current)
        self.assertEqual(row.validation_error, "price not reported")

    def test_malformed_price_marks_stale_and_keeps_last_known(self):
        self._record(Decimal("2.9900"), "EUR")
        row = self._record(None, "EU", error="invalid currency")
        self.assertEqual(row.amount, Decimal("2.9900"))
        self.assertFalse(row.is_current)
        self.assertEqual(row.validation_error, "invalid currency")

    def test_ambiguous_observation_without_prior_value_still_records_reason(self):
        row = self._record(None, "", error="display-only price", source_offer_id="o1")
        self.assertIsNotNone(row)
        self.assertIsNone(row.amount)
        self.assertFalse(row.is_current)
        self.assertEqual(row.validation_error, "display-only price")
        self.assertEqual(row.source_offer_id, "o1")

    def test_no_price_and_no_prior_value_records_nothing(self):
        self.assertIsNone(self._record(None, "", error=""))
        self.assertEqual(OfferPrice.objects.count(), 0)

    def test_reobservation_after_stale_restores_current(self):
        self._record(Decimal("2.9900"), "EUR")
        self._record(None, "", error="")
        row = self._record(Decimal("2.9900"), "EUR")
        self.assertTrue(row.is_current)
        self.assertEqual(row.validation_error, "")
        self.assertEqual(OfferPrice.objects.count(), 1)

    def test_two_sources_keep_independent_prices(self):
        self._record(Decimal("2.9900"), "EUR", source=self.source)
        self._record(Decimal("4.9900"), "EUR", source=self.other_source)
        rows = {
            row.source.slug: row.amount for row in OfferPrice.objects.all()
        }
        self.assertEqual(rows, {"price-src": Decimal("2.9900"),
                                "price-src-b": Decimal("4.9900")})

    def test_source_without_a_price_never_erases_another_source(self):
        self._record(Decimal("2.9900"), "EUR", source=self.source)
        self._record(None, "", error="", source=self.other_source)
        a = OfferPrice.objects.get(source=self.source)
        self.assertTrue(a.is_current)
        self.assertEqual(a.amount, Decimal("2.9900"))
        # The other source observed no price and has never had one: no row.
        self.assertFalse(OfferPrice.objects.filter(source=self.other_source).exists())


# ---------------------------------------------------------------------------
# Authoritative deactivation is per source: one source's removal must never
# stale another source's still-valid price for the same canonical offer.
# ---------------------------------------------------------------------------
class PriceDeactivationSourceIsolationTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="iso-netflix", defaults={"name": "Netflix"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )
        self.it, _ = Country.objects.get_or_create(
            code="IT", defaults={"name": "Italy"}
        )
        self.source_a, _ = Source.objects.get_or_create(
            slug="iso-src-a", defaults={"name": "Authoritative A"}
        )
        self.source_b, _ = Source.objects.get_or_create(
            slug="iso-src-b", defaults={"name": "Co-reporting B"}
        )
        # One shared canonical row: identity deliberately excludes the source,
        # so both sources observe the *same* offer identity.
        self.offer = Offer.objects.create(
            provider=self.provider,
            country=self.country,
            source=self.source_a,
            content_type=ContentType.MOVIE,
            tmdb_id=550,
            offer_type=OfferType.RENT,
            provider_content_id="shared",
        )
        self.prices = OfferPriceRepository()
        self.runs = SyncRunRepository()
        self.offers = OfferRepository()

    def _record(self, source, amount="2.9900"):
        return self.prices.record(
            offer=self.offer, source=source, amount=Decimal(amount),
            currency="EUR",
        )

    def _authoritative_run(self, *, source=None, country=None):
        run = self.runs.start(
            provider=self.provider,
            source=source or self.source_a,
            country=country or self.country,
            mode=SyncMode.FULL,
            authoritative=True,
        )
        return self.runs.mark_succeeded(run)

    def test_removal_by_one_source_leaves_the_other_sources_price_current(self):
        a = self._record(self.source_a)
        b = self._record(self.source_b)

        run = self._authoritative_run(source=self.source_a)
        self.offers.deactivate_unseen(run, set())

        a.refresh_from_db()
        b.refresh_from_db()
        # Only the run's own source's price goes stale.
        self.assertFalse(a.is_current)
        self.assertTrue(b.is_current)
        self.assertEqual(b.amount, Decimal("2.9900"))
        # The offer row here was created directly, without per-source evidence
        # rows. Availability is now derived from evidence - not prices - so with
        # no source holding a *present observation* A's authoritative absence
        # leaves the offer unavailable. B's price survives untouched, and B
        # restores availability by re-observing the offer (see
        # availability.tests.test_offer_evidence for the evidence model).
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.currently_available)

    def test_removal_does_not_zero_or_delete_the_other_sources_price(self):
        a = self._record(self.source_a)
        b = self._record(self.source_b)
        run = self._authoritative_run(source=self.source_a)
        self.offers.deactivate_unseen(run, set())

        self.assertEqual(OfferPrice.objects.count(), 2)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.amount, Decimal("2.9900"))
        self.assertEqual(b.amount, Decimal("2.9900"))

    def test_reappearance_reactivates_only_the_reobserving_sources_price(self):
        a = self._record(self.source_a)
        self._record(self.source_b)
        run = self._authoritative_run(source=self.source_a)
        self.offers.deactivate_unseen(run, set())

        # Source A studies the offer again with a valid price on the same row.
        reopened = self.prices.record(
            offer=self.offer, source=self.source_a,
            amount=Decimal("3.9900"), currency="EUR",
        )
        self.assertTrue(reopened.is_current)
        self.assertEqual(reopened.amount, Decimal("3.9900"))
        self.assertTrue(OfferPrice.objects.get(source=self.source_b).is_current)

    def test_deactivation_is_scoped_to_the_runs_own_source_not_just_identity(self):
        # A price row for an unrelated country/identity must never be touched,
        # and the other source in the same country stays current.
        other_country_offer = Offer.objects.create(
            provider=self.provider,
            country=self.it,
            source=self.source_a,
            content_type=ContentType.MOVIE,
            tmdb_id=551,
            offer_type=OfferType.RENT,
            provider_content_id="it-only",
        )
        untouched = self.prices.record(
            offer=other_country_offer, source=self.source_a,
            amount=Decimal("1.9900"), currency="EUR",
        )
        own = self._record(self.source_a)
        peer = self._record(self.source_b)

        run = self._authoritative_run(source=self.source_a)
        self.offers.deactivate_unseen(run, set())

        own.refresh_from_db()
        peer.refresh_from_db()
        untouched.refresh_from_db()
        self.assertFalse(own.is_current)
        self.assertTrue(peer.is_current)
        self.assertTrue(untouched.is_current)

    def test_failed_run_cannot_stale_any_price(self):
        a = self._record(self.source_a)
        b = self._record(self.source_b)
        run = self.runs.start(
            provider=self.provider, source=self.source_a,
            country=self.country, mode=SyncMode.FULL, authoritative=True,
        )
        run = self.runs.mark_failed(run, "boom")

        with self.assertRaises(InactiveSyncError):
            self.offers.deactivate_unseen(run, set())

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertTrue(a.is_current)
        self.assertTrue(b.is_current)

    def test_manual_offer_deletion_is_reapable_and_only_reaps_orphans(self):
        self._record(self.source_a)
        self._record(self.source_b)
        # A second offer with its own price that must survive.
        kept = Offer.objects.create(
            provider=self.provider, country=self.country,
            source=self.source_a, content_type=ContentType.MOVIE,
            tmdb_id=777, offer_type=OfferType.RENT,
            provider_content_id="kept",
        )
        kept_price = self.prices.record(
            offer=kept, source=self.source_a,
            amount=Decimal("5.9900"), currency="EUR",
        )
        orphan_identity = self.offer.identity
        # Manual canonical deletion: no FK, so the price rows remain.
        Offer.objects.filter(pk=self.offer.pk).delete()
        self.assertEqual(
            OfferPrice.objects.filter(offer_identity=orphan_identity).count(), 2
        )

        deleted = self.prices.purge_orphans()

        self.assertEqual(deleted, 2)
        self.assertFalse(
            OfferPrice.objects.filter(offer_identity=orphan_identity).exists()
        )
        kept_price.refresh_from_db()
        self.assertEqual(kept_price.amount, Decimal("5.9900"))


# ---------------------------------------------------------------------------
# Refresh writes the price beside the offer, in the same transaction
# ---------------------------------------------------------------------------
class RefreshPricePersistenceTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )

    def _service(self, response):
        return AvailabilityRefreshService(source=StubSource(response=response))

    def test_refresh_persists_the_price(self):
        service = self._service(_response(_normalized(
            offer_type=OfferType.RENT,
            price_amount=Decimal("2.9900"),
            price_currency="EUR",
            source_record_id="justwatch:1",
        )))
        service.refresh_content(self.movie)

        price = OfferPrice.objects.get()
        offer = Offer.objects.get()
        self.assertEqual(price.offer_identity, offer.identity)
        self.assertEqual(price.amount, Decimal("2.9900"))
        self.assertEqual(price.currency, "EUR")
        self.assertEqual(price.source_offer_id, "justwatch:1")
        self.assertTrue(price.is_current)

    def test_repeated_refresh_is_idempotent(self):
        response = _response(_normalized(
            offer_type=OfferType.RENT,
            price_amount=Decimal("2.9900"),
            price_currency="EUR",
        ))
        self._service(response).refresh_content(self.movie)
        self._service(response).refresh_content(self.movie)
        self.assertEqual(OfferPrice.objects.count(), 1)

    def test_changed_price_across_refreshes_updates_the_row(self):
        self._service(_response(_normalized(
            offer_type=OfferType.RENT, price_amount=Decimal("2.9900"),
            price_currency="EUR",
        ))).refresh_content(self.movie)
        self._service(_response(_normalized(
            offer_type=OfferType.RENT, price_amount=Decimal("3.9900"),
            price_currency="EUR",
        ))).refresh_content(self.movie)

        self.assertEqual(OfferPrice.objects.count(), 1)
        self.assertEqual(OfferPrice.objects.get().amount, Decimal("3.9900"))

    def test_authoritative_removal_marks_the_price_stale(self):
        self._service(_response(_normalized(
            provider_content_id="A", offer_type=OfferType.RENT,
            price_amount=Decimal("2.9900"), price_currency="EUR",
        ))).refresh_content(self.movie)

        # A later authoritative, complete snapshot that no longer lists offer A
        # but still lists another offer for the same provider/country group.
        self._service(_response(
            _normalized(provider_content_id="B", offer_type=OfferType.RENT),
            authoritative=True,
            complete_snapshot=True,
            sync_mode=SyncMode.FULL,
        )).refresh_content(self.movie)

        stale = OfferPrice.objects.get(provider_content_id="A")
        self.assertFalse(stale.is_current)
        self.assertEqual(stale.amount, Decimal("2.9900"))  # kept, not zeroed

    def test_price_is_not_written_when_the_refresh_fails(self):
        service = AvailabilityRefreshService(
            source=StubSource(response=_response(_normalized(
                offer_type=OfferType.RENT,
                price_amount=Decimal("2.9900"),
                price_currency="EUR",
                content_type=ContentType.SEASON,  # requires a season number -> rolls back
            )))
        )
        with self.assertRaises(Exception):
            service.refresh_content(self.movie)
        self.assertEqual(OfferPrice.objects.count(), 0)
        self.assertEqual(Offer.objects.count(), 0)

    def test_a_later_failure_rolls_back_an_already_written_offer_and_price(self):
        # One valid, priced offer is persisted first, then a malformed offer in
        # the same run raises: the shared transaction must roll back _both_ the
        # offer and its price, leaving no half-written state.
        service = self._service(_response(
            _normalized(
                provider_content_id="good", offer_type=OfferType.RENT,
                price_amount=Decimal("2.9900"), price_currency="EUR",
            ),
            _normalized(
                provider_content_id="bad", offer_type=OfferType.RENT,
                content_type=ContentType.SEASON,  # no season number -> raises
            ),
        ))
        with self.assertRaises(Exception):
            service.refresh_content(self.movie)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(OfferPrice.objects.count(), 0)


# ---------------------------------------------------------------------------
# Reconciliation: source-specific prices, deterministic selection, conflicts
# ---------------------------------------------------------------------------
class ReconciliationPriceTests(ReconciliationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.prices = OfferPriceRepository()

    def _price(self, row, amount, currency="EUR", error=""):
        return self.prices.record(
            offer=row, source=row.source, amount=amount, currency=currency,
            error=error, source_offer_id=f"offer-{row.pk}",
        )

    def test_single_price_is_selected(self):
        row = self.offer(provider_content_id="a", offer_type=OfferType.RENT)
        self._price(row, Decimal("2.9900"))

        result = self.reconcile_movie()
        offer = result.offers[0]
        self.assertIsNotNone(offer.price)
        self.assertEqual(offer.price.amount, Decimal("2.9900"))
        self.assertEqual(offer.price.currency, "EUR")
        self.assertFalse(offer.price_is_from)
        self.assertEqual(offer.conflicts, ())

    def test_missing_price_selects_nothing(self):
        self.offer(provider_content_id="a", offer_type=OfferType.RENT)
        result = self.reconcile_movie()
        self.assertIsNone(result.offers[0].price)

    def test_two_sources_prices_are_both_kept_and_lowest_selected(self):
        a = self.offer(provider_content_id="a", offer_type=OfferType.RENT,
                       source=self.source_a)
        b = self.offer(provider_content_id="b", offer_type=OfferType.RENT,
                       source=self.source_b)
        self._price(a, Decimal("3.9900"))
        self._price(b, Decimal("2.9900"))

        result = self.reconcile_movie()
        offer = result.offers[0]
        self.assertEqual({p.source for p in offer.prices},
                         {"recon-source-a", "recon-source-b"})
        self.assertEqual(offer.price.amount, Decimal("2.9900"))
        self.assertTrue(offer.price_is_from)
        self.assertIn(ConflictKind.PRICE, [c.kind for c in result.conflicts])

    def test_agreeing_sources_do_not_conflict(self):
        a = self.offer(provider_content_id="a", offer_type=OfferType.RENT,
                       source=self.source_a)
        b = self.offer(provider_content_id="b", offer_type=OfferType.RENT,
                       source=self.source_b)
        self._price(a, Decimal("2.9900"))
        self._price(b, Decimal("2.9900"))

        result = self.reconcile_movie()
        self.assertEqual(result.offers[0].price.amount, Decimal("2.9900"))
        self.assertFalse(result.offers[0].price_is_from)
        self.assertNotIn(ConflictKind.PRICE, [c.kind for c in result.conflicts])

    def test_stale_price_is_never_selected(self):
        a = self.offer(provider_content_id="a", offer_type=OfferType.RENT,
                       source=self.source_a)
        b = self.offer(provider_content_id="b", offer_type=OfferType.RENT,
                       source=self.source_b)
        self._price(a, Decimal("2.9900"))
        self._price(b, Decimal("2.9900"))
        # Source B re-observes with no price: remembered, not confirmed.
        self.prices.record(offer=b, source=self.source_b, amount=None,
                           currency="", error="")

        result = self.reconcile_movie()
        offer = result.offers[0]
        self.assertEqual(offer.price.source, "recon-source-a")
        self.assertTrue(any(p.is_current is False for p in offer.prices))

    def test_rent_and_buy_prices_are_never_combined(self):
        rent = self.offer(provider_content_id="rent", offer_type=OfferType.RENT)
        buy = self.offer(provider_content_id="buy", offer_type=OfferType.BUY)
        self._price(rent, Decimal("2.9900"))
        self._price(buy, Decimal("9.9900"))

        result = self.reconcile_movie()
        by_type = {offer.offer_type: offer for offer in result.offers}
        self.assertEqual(by_type[OfferType.RENT].price.amount, Decimal("2.9900"))
        self.assertEqual(by_type[OfferType.BUY].price.amount, Decimal("9.9900"))
        # One price per monetization type: no merged "from" and no false
        # disagreement between a rental and a purchase.
        self.assertFalse(by_type[OfferType.RENT].price_is_from)
        self.assertFalse(by_type[OfferType.BUY].price_is_from)
        self.assertNotIn(ConflictKind.PRICE, [c.kind for c in result.conflicts])

    def test_addon_price_is_not_a_subscription_price(self):
        self.offer(provider_content_id="sub", offer_type=OfferType.SUBSCRIPTION)
        addon = self.offer(provider_content_id="addon", offer_type=OfferType.ADDON)
        self._price(addon, Decimal("4.9900"))

        result = self.reconcile_movie()
        by_type = {offer.offer_type: offer for offer in result.offers}
        # A subscription offer carries no per-title price, and the add-on's
        # channel price never leaks onto it.
        self.assertIsNone(by_type[OfferType.SUBSCRIPTION].price)
        self.assertEqual(by_type[OfferType.ADDON].price.amount, Decimal("4.9900"))

    def test_cross_currency_prices_are_not_silently_combined(self):
        eur_a = self.offer(provider_content_id="eur-a", offer_type=OfferType.RENT,
                           source=self.source_a)
        eur_b = self.offer(provider_content_id="eur-b", offer_type=OfferType.RENT,
                           source=self.source_a)
        usd = self.offer(provider_content_id="usd", offer_type=OfferType.RENT,
                         source=self.source_b)
        self._price(eur_a, Decimal("2.9900"), currency="EUR")
        self._price(eur_b, Decimal("3.9900"), currency="EUR")
        self._price(usd, Decimal("1.0000"), currency="USD")

        result = self.reconcile_movie()
        offer = result.offers[0]
        # The majority currency wins even though a USD amount is numerically
        # lower: no FX conversion and no cross-currency "cheapest" is implied.
        self.assertEqual(offer.price.currency, "EUR")
        self.assertEqual(offer.price.amount, Decimal("2.9900"))
        self.assertTrue(offer.price_is_from)
        self.assertIn(ConflictKind.PRICE, [c.kind for c in result.conflicts])
        self.assertEqual(
            {p.currency for p in offer.prices if p.is_current}, {"EUR", "USD"}
        )

    def test_price_provenance_follows_the_offer_across_deactivation(self):
        row = self.offer(provider_content_id="a", offer_type=OfferType.RENT)
        self._price(row, Decimal("2.9900"))
        Offer.objects.filter(pk=row.pk).update(currently_available=False)

        result = self.reconcile_movie()
        offer = result.offers[0]
        self.assertFalse(offer.currently_available)
        # The price row is still attached (inspectable) but the read path
        # filters the deactivated offer out below.
        self.assertEqual(offer.price.amount, Decimal("2.9900"))


# ---------------------------------------------------------------------------
# Read + API shape
# ---------------------------------------------------------------------------
class PriceReadPathTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )
        self.source, _ = Source.objects.get_or_create(
            slug="read-src", defaults={"name": "Source"}
        )
        declare_freshness(self, "read-src")
        self.offer = Offer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.RENT, provider_content_id="pcid",
        )
        OfferPriceRepository().record(
            offer=self.offer, source=self.source, amount=Decimal("2.9900"),
            currency="EUR", source_offer_id="o1",
        )

    def test_read_path_exposes_structured_price(self):
        offers = get_local_offers(self.movie)
        self.assertEqual(len(offers), 1)
        price = offers[0].price
        self.assertIsNotNone(price)
        # Amount is a decimal string, never a float; currency is explicit.
        self.assertEqual(Decimal(price["amount"]), Decimal("2.99"))
        self.assertEqual(price["currency"], "EUR")
        self.assertEqual(price["source"], "read-src")
        self.assertTrue(price["is_current"])

    def test_serialized_offer_carries_the_price(self):
        offer = get_local_offers(self.movie)[0]
        payload = offer.to_dict()
        self.assertEqual(Decimal(payload["price"]["amount"]), Decimal("2.99"))
        self.assertEqual(payload["prices"][0]["currency"], "EUR")

    def test_ranked_serialization_carries_the_price(self):
        preference = Preference(user_id=1, candidate_countries=["NL"])
        ranked = rank_offers(get_local_offers(self.movie), preference,
                             current_country="NL")
        self.assertEqual(Decimal(ranked[0].to_dict()["price"]["amount"]),
                         Decimal("2.99"))

    def test_missing_price_reads_as_none_not_free(self):
        OfferPrice.objects.all().delete()
        offers = get_local_offers(self.movie)
        self.assertIsNone(offers[0].price)


# ---------------------------------------------------------------------------
# Price must not influence availability or entitlement
# ---------------------------------------------------------------------------
class PriceDoesNotAffectAccessTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )
        self.source, _ = Source.objects.get_or_create(
            slug="access-src", defaults={"name": "Source"}
        )
        declare_freshness(self, "access-src")
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

    def _rent_offer(self, with_price):
        offer = Offer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.RENT, provider_content_id=f"pcid-{with_price}",
        )
        if with_price:
            OfferPriceRepository().record(
                offer=offer, source=self.source, amount=Decimal("2.9900"),
                currency="EUR",
            )
        return get_local_offers(self.movie)[0]

    def test_price_does_not_make_an_unowned_offer_accessible(self):
        preference = Preference(user_id=1, candidate_countries=["NL"])
        classified = classify_offer(
            self._rent_offer(with_price=True),
            access=build_explicit_access(set()),
            preference=preference,
            reach=build_reach(preference, "NL"),
            provider_map={"netflix": self.provider},
        )
        self.assertEqual(classified.access_type, "rent")
        self.assertFalse(classified.is_entitled)
        self.assertFalse(classified.is_accessible)
        self.assertIsNotNone(classified.price)

    def test_price_presence_does_not_change_the_title_verdict(self):
        # Declare a rent/buy account so the offer is a *purchasable* option; the
        # verdict must be identical with and without a price.
        Subscription.objects.create(
            user_id=1, provider=self.provider, access_mode="rent_buy",
        )
        preference = Preference(user_id=1, candidate_countries=["NL"])
        with_price = resolve_title(
            self.movie, [self._rent_offer(with_price=True)], preference,
            current_country="NL",
        )
        OfferPrice.objects.all().delete()
        without_price = resolve_title(
            self.movie, [self._rent_offer(with_price=False)], preference,
            current_country="NL",
        )
        self.assertEqual(with_price.verdict, without_price.verdict)
        self.assertEqual(
            with_price.access_flags["has_rent"],
            without_price.access_flags["has_rent"],
        )
        # The purchase list is the detail page's `purchase_offers` payload.
        self.assertEqual(
            Decimal(with_price.purchase_offers[0].price["amount"]),
            Decimal("2.99"),
        )
        self.assertIsNone(without_price.purchase_offers[0].price)


# ---------------------------------------------------------------------------
# Source parsing: JustWatch structured price reaches ProviderOffer
# ---------------------------------------------------------------------------
class JustWatchPriceParsingTests(TestCase):
    def setUp(self):
        self.source = JustWatchSource(client=object())

    def _node(self):
        return SimpleNamespace(id="tm1", object_type="MOVIE",
                               season_number=None, episode_number=None)

    def _build(self, **offer_kwargs):
        offer = JustWatchOffer(
            id=99, monetization_type="RENT", presentation_type="HD",
            package=SimpleNamespace(), url="https://example.test/x",
            **offer_kwargs,
        )
        return self.source._offer(
            offer, provider_slug="netflix", country="NL",
            content_type=ContentType.MOVIE, tmdb_id=550, content=None,
            node=self._node(),
        )

    def test_structured_price_is_normalized_onto_the_offer(self):
        result = self._build(retail_price="€2.99", retail_price_value=2.99,
                             currency="EUR")
        self.assertEqual(result.price_amount, Decimal("2.9900"))
        self.assertEqual(result.price_currency, "EUR")
        self.assertEqual(result.price_error, "")

    def test_subscription_offer_without_a_price_is_not_free_of_charge(self):
        result = self._build(retail_price=None, retail_price_value=None,
                             currency="")
        self.assertIsNone(result.price_amount)
        self.assertEqual(result.price_currency, "")
        self.assertEqual(result.price_error, "")

    def test_display_only_price_is_flagged_not_parsed(self):
        result = self._build(retail_price="€2,99", retail_price_value=None,
                             currency="")
        self.assertIsNone(result.price_amount)
        self.assertEqual(result.price_error, "display-only price")
