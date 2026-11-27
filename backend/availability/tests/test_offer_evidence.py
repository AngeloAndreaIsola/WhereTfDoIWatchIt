"""Source-aware offer evidence: effective availability is per source.

The canonical ``Offer.identity`` deliberately excludes the source, so several
sources describing the same provider offer share one ``Offer`` row and the last
writer owns ``Offer.currently_available``. Before this table existed, an
authoritative removal from source A flipped the shared row unavailable even
though source B still reported the same offer as available, hiding an offer the
user could watch.

These tests drive the *real* write path (``AvailabilityRefreshService`` +
``OfferRepository``) and the *real* read path (reconciliation ->
``get_local_offers`` -> the availability endpoint), proving that:

* a source's authoritative removal only removes that source's evidence;
* effective availability is the union of current evidence, so a co-reporting
  source keeps the offer available;
* country scope, absence-vs-unavailable, partial/failed refreshes, idempotency,
  reappearance, legacy rows and source purge all follow the documented policy;
* a price never counts as evidence and never makes an offer available.
"""
import datetime as dt
from decimal import Decimal

from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import (
    ContentType,
    OfferType,
    SyncMode,
    SyncStatus,
)
from availability.models import Country, Offer, OfferEvidence, OfferPrice, Source
from availability.persistence.repositories import (
    InactiveSyncError,
    OfferEvidenceRepository,
    OfferPriceRepository,
    OfferRepository,
    SyncRunRepository,
)
from availability.services.refresh import AvailabilityRefreshService
from tracker.models import Movie, Preference, Provider
from tracker.services.access import (
    build_explicit_access,
    build_reach,
    classify_offer,
)
from tracker.services.availability_reads import get_local_offers, get_offer_evidence

from .freshness_helpers import declare_freshness

from .source_stubs import StubSource
from .source_stubs import fetch_result as _response
from .source_stubs import normalized_offer as _normalized


class EvidencePipelineMixin:
    """Refresh two sources over the same canonical offer."""

    def setUp(self):
        super().setUp()
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix", "is_enabled": True}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )
        self.italy, _ = Country.objects.get_or_create(
            code="IT", defaults={"name": "Italy"}
        )
        self.offers = OfferRepository()
        self.evidence = OfferEvidenceRepository()
        self.prices = OfferPriceRepository()
        self.runs = SyncRunRepository()
        declare_freshness(
            self, "source-a", "source-b", "legacy-src", "legacy-src-2",
            "legacy-only", "legacy-transition",
        )

    # -- write path -------------------------------------------------------
    def refresh(self, slug, *offers, **snapshot):
        source = StubSource(slug=slug, name=slug, response=_response(*offers, **snapshot))
        return AvailabilityRefreshService(source=source).refresh_content(self.movie)

    def observe(self, slug, pcid="shared", country="US", **overrides):
        """One non-authoritative observation of the shared offer."""
        return self.refresh(
            slug,
            _normalized(provider_content_id=pcid, country=country, **overrides),
        )

    def remove(self, slug, *, country="US"):
        """An authoritative complete snapshot that no longer lists the shared offer.

        A snapshot with *zero* offers creates no ``SyncRun`` (and therefore
        deactivates nothing), so the run anchors on an unrelated offer for the
        same provider/country group - the shared offer is then genuinely unseen.
        """
        return self.refresh(
            slug,
            _normalized(provider_content_id="anchor", tmdb_id=999, country=country),
            authoritative=True,
            complete_snapshot=True,
            sync_mode=SyncMode.FULL,
        )

    # -- read side helpers ------------------------------------------------
    def shared(self) -> Offer:
        return Offer.objects.get(tmdb_id=550)

    def shared_identity(self) -> str:
        return self.shared().identity

    def evidence_present(self, slug, identity=None):
        """Whether one source currently observes the (shared) offer."""
        identity = identity or self.shared_identity()
        row = OfferEvidence.objects.filter(
            source__slug=slug, offer_identity=identity
        ).first()
        return None if row is None else row.is_present

    def run_with_status(self, slug, status):
        source = Source.objects.get(slug=slug)
        run = self.runs.start(
            provider=self.provider, source=source, country=self.country,
            mode=SyncMode.FULL, authoritative=True,
        )
        if status == SyncStatus.PARTIAL:
            return self.runs.mark_partial(run)
        if status == SyncStatus.FAILED:
            return self.runs.mark_failed(run, "boom")
        return self.runs.mark_succeeded(run)


# ---------------------------------------------------------------------------
# Source isolation
# ---------------------------------------------------------------------------
class SourceIsolationTests(EvidencePipelineMixin, TestCase):
    def test_a_removal_keeps_b_evidence_and_effective_availability(self):
        self.observe("source-a")
        self.observe("source-b")

        self.remove("source-a")

        offer = self.shared()
        self.assertTrue(offer.currently_available)
        self.assertFalse(self.evidence_present("source-a"))
        self.assertTrue(self.evidence_present("source-b"))
        # B's observation still reaches the viewer through the real read path.
        self.assertEqual(len(get_local_offers(self.movie)), 1)

    def test_b_removal_then_makes_the_offer_unavailable(self):
        self.observe("source-a")
        self.observe("source-b")
        self.remove("source-a")
        self.remove("source-b")

        offer = self.shared()
        self.assertFalse(offer.currently_available)
        self.assertFalse(self.evidence_present("source-a"))
        self.assertFalse(self.evidence_present("source-b"))
        self.assertEqual(get_local_offers(self.movie), [])

    def test_reappearance_restores_only_the_observing_sources_evidence(self):
        self.observe("source-a")
        self.observe("source-b")
        self.remove("source-a")
        self.remove("source-b")  # both absent -> unavailable
        self.assertFalse(self.shared().currently_available)

        self.observe("source-a")  # A alone studies it again

        offer = self.shared()
        self.assertTrue(offer.currently_available)
        # A is present again; B's absence is remembered, never fabricated away
        # and never re-observed on B's behalf.
        self.assertTrue(self.evidence_present("source-a"))
        self.assertFalse(self.evidence_present("source-b"))

    def test_removal_in_one_country_does_not_touch_another(self):
        self.observe("source-a", pcid="us-shared", country="US")
        self.observe("source-a", pcid="it-shared", country="IT")

        self.remove("source-a", country="US")

        us = Offer.objects.get(tmdb_id=550, country=self.country)
        it = Offer.objects.get(tmdb_id=550, country=self.italy)
        self.assertFalse(us.currently_available)
        self.assertTrue(it.currently_available)

    def test_non_authoritative_refresh_records_no_absence(self):
        self.observe("source-a")
        self.observe("source-b")

        # A targeted (non-authoritative) snapshot that omits the shared offer:
        # absence is not evidence, so nothing may go stale or deactivate.
        self.refresh(
            "source-a", _normalized(provider_content_id="anchor", tmdb_id=999)
        )

        offer = self.shared()
        self.assertTrue(offer.currently_available)
        self.assertTrue(self.evidence_present("source-a"))
        self.assertTrue(self.evidence_present("source-b"))

    def test_partial_and_failed_runs_record_no_absence(self):
        self.observe("source-a")
        identity = self.shared_identity()

        for status in (SyncStatus.PARTIAL, SyncStatus.FAILED):
            with self.subTest(status=status):
                run = self.run_with_status("source-a", status)
                with self.assertRaises(InactiveSyncError):
                    self.offers.deactivate_unseen(run, set())
                self.assertTrue(self.shared().currently_available)
                self.assertTrue(self.evidence_present("source-a"))

    def test_repeated_refreshes_are_idempotent(self):
        self.observe("source-a")
        self.observe("source-a")
        identity = self.shared_identity()
        self.assertEqual(
            OfferEvidence.objects.filter(offer_identity=identity).count(), 1
        )

        first = self.remove("source-a")
        second = self.remove("source-a")

        self.assertEqual(
            OfferEvidence.objects.filter(offer_identity=identity).count(), 1
        )
        self.assertFalse(self.evidence_present("source-a"))
        self.assertEqual(first.deactivated, 1)
        # The offer is already unavailable, so the second run deactivates none.
        self.assertEqual(second.deactivated, 0)

    def test_failed_refresh_rolls_back_evidence_canonical_state_and_price(self):
        self.observe("source-a", price_amount=Decimal("2.9900"), price_currency="EUR")
        self.assertEqual(Offer.objects.count(), 1)

        service = AvailabilityRefreshService(source=StubSource(
            slug="source-a", name="source-a", response=_response(
                _normalized(provider_content_id="good", price_amount=Decimal("3.9900"),
                            price_currency="EUR"),
                _normalized(provider_content_id="bad",
                            content_type=ContentType.SEASON),  # no season -> raises
            ),
        ))
        with self.assertRaises(Exception):
            service.refresh_content(self.movie)

        # Everything the failing run touched is rolled back together.
        self.assertEqual(Offer.objects.count(), 1)
        self.assertEqual(OfferEvidence.objects.count(), 1)
        self.assertEqual(OfferPrice.objects.count(), 1)
        self.assertTrue(self.evidence_present("source-a"))


# ---------------------------------------------------------------------------
# Conflicting evidence and policy edges
# ---------------------------------------------------------------------------
class ConflictingEvidenceTests(EvidencePipelineMixin, TestCase):
    def test_present_evidence_wins_over_another_sources_absence(self):
        # A observes; B has never observed but authoritatively omits it. The
        # policy: an authoritative absence decides only when no source has a
        # present observation, so A keeps the offer available.
        self.observe("source-a")
        self.remove("source-b")

        offer = self.shared()
        self.assertTrue(offer.currently_available)
        self.assertTrue(self.evidence_present("source-a"))
        self.assertFalse(self.evidence_present("source-b"))
        self.assertEqual(len(get_local_offers(self.movie)), 1)

    def test_legacy_offer_without_evidence_is_not_treated_as_unavailable(self):
        # A row written before this table existed (or by a path that does not
        # record evidence) keeps its stored availability: no evidence is not
        # proof of absence.
        source = Source.objects.create(slug="legacy-src", name="Legacy")
        Offer.objects.create(
            provider=self.provider, country=self.country, source=source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.SUBSCRIPTION, provider_content_id="legacy",
            currently_available=True,
        )
        self.assertEqual(OfferEvidence.objects.count(), 0)
        self.assertEqual(len(get_local_offers(self.movie)), 1)

    def test_legacy_offer_is_deactivated_by_an_authoritative_absence(self):
        source = Source.objects.create(slug="legacy-src-2", name="Legacy")
        legacy = Offer.objects.create(
            provider=self.provider, country=self.country, source=source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.SUBSCRIPTION, provider_content_id="legacy",
            currently_available=True,
        )
        # The same source now publishes an authoritative complete snapshot that
        # omits the legacy row: that absence is genuine evidence.
        self.remove("legacy-src-2")
        legacy.refresh_from_db()
        self.assertFalse(legacy.currently_available)

    def test_no_valid_current_evidence_is_not_available(self):
        self.observe("source-a")
        self.remove("source-a")
        offer = self.shared()
        self.assertFalse(offer.currently_available)
        # Reconciliation agrees, and the read path drops it.
        result = get_local_offers(self.movie)
        self.assertEqual(result, [])

    def test_source_deletion_is_refused_while_evidence_exists(self):
        self.observe("source-a")
        source = Source.objects.get(slug="source-a")
        with self.assertRaises(ProtectedError):
            source.delete()

    def test_purge_for_source_removes_only_that_sources_evidence(self):
        self.observe("source-a")
        self.observe("source-b")
        source_a = Source.objects.get(slug="source-a")

        deleted, recomputed = self.evidence.purge_for_source(source_a)

        self.assertEqual(deleted, 1)
        self.assertEqual(recomputed, 0)  # B keeps it available
        self.assertFalse(OfferEvidence.objects.filter(source=source_a).exists())
        self.assertTrue(self.shared().currently_available)

        source_b = Source.objects.get(slug="source-b")
        self.evidence.purge_for_source(source_b)
        # The only remaining observations are gone, so it is unavailable now.
        self.assertFalse(self.shared().currently_available)

    def test_orphan_evidence_is_purged_only_when_its_offer_is_gone(self):
        self.observe("source-a", pcid="gone")
        self.observe("source-a", pcid="kept")
        gone = Offer.objects.get(provider_content_id="gone")
        Offer.objects.filter(pk=gone.pk).delete()
        self.assertEqual(OfferEvidence.objects.count(), 2)

        deleted = self.evidence.purge_orphans()

        self.assertEqual(deleted, 1)
        self.assertEqual(
            list(OfferEvidence.objects.values_list("provider_content_id", flat=True)),
            ["kept"],
        )


# ---------------------------------------------------------------------------
# Price is not evidence
# ---------------------------------------------------------------------------
class PriceIsNotEvidenceTests(EvidencePipelineMixin, TestCase):
    def test_recorded_price_creates_no_evidence_and_no_availability(self):
        source = Source.objects.create(slug="price-src", name="Price")
        offer = Offer.objects.create(
            provider=self.provider, country=self.country, source=source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.RENT, provider_content_id="priced",
            currently_available=False,
        )
        self.prices.record(
            offer=offer, source=source, amount=Decimal("2.9900"), currency="EUR",
        )
        self.assertEqual(OfferEvidence.objects.count(), 0)
        offer.refresh_from_db()
        self.assertFalse(offer.currently_available)
        self.assertEqual(get_local_offers(self.movie), [])

    def test_stale_price_does_not_reactivate_an_offer(self):
        source = Source.objects.create(slug="price-src-2", name="Price")
        offer = Offer.objects.create(
            provider=self.provider, country=self.country, source=source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.RENT, provider_content_id="priced",
            currently_available=False,
        )
        self.prices.record(
            offer=offer, source=source, amount=Decimal("2.9900"), currency="EUR",
        )
        # Price rows may be current, but availability is decided by evidence.
        self.assertTrue(OfferPrice.objects.get().is_current)
        self.assertEqual(get_local_offers(self.movie), [])


# ---------------------------------------------------------------------------
# Effective availability does not override entitlement / country / reach
# ---------------------------------------------------------------------------
class AccessRemainsIndependentTests(EvidencePipelineMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.observe("source-a")

    def _classified(self, *, access, current_country):
        preference = Preference(user_id=1, candidate_countries=[current_country])
        return classify_offer(
            get_local_offers(self.movie)[0],
            access=access,
            preference=preference,
            reach=build_reach(preference, current_country),
            provider_map={"netflix": self.provider},
        )

    def test_availability_does_not_grant_entitlement(self):
        classified = self._classified(
            access=build_explicit_access(set()), current_country="US",
        )
        self.assertFalse(classified.is_entitled)
        self.assertFalse(classified.is_accessible)

    def test_availability_does_not_override_country_reach(self):
        classified = self._classified(
            access=build_explicit_access({"netflix"}), current_country="IT",
        )
        self.assertFalse(classified.is_entitled)
        self.assertEqual(classified.locality, "out_of_reach")
        self.assertFalse(classified.is_accessible)


# ---------------------------------------------------------------------------
# End-to-end: the HTTP availability endpoint reflects effective availability
# ---------------------------------------------------------------------------
class EffectiveAvailabilityApiTests(EvidencePipelineMixin, TestCase):
    def _offers(self):
        response = self.client.get(f"/api/movie/{self.movie.tmdb_id}/availability/")
        self.assertEqual(response.status_code, 200)
        return response.json()["offers"]

    def test_offer_supported_by_b_survives_a_removal_on_the_api(self):
        self.observe("source-a")
        self.observe("source-b")
        self.remove("source-a")

        offers = self._offers()
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["service"], "netflix")

    def test_offer_with_no_current_evidence_disappears_from_the_api(self):
        self.observe("source-a")
        self.observe("source-b")
        self.remove("source-a")
        self.remove("source-b")

        self.assertEqual(self._offers(), [])


# ---------------------------------------------------------------------------
# Adversarial: precedence is order-independent, regardless of which write lands
# first (present-then-absent vs absent-then-present).
# ---------------------------------------------------------------------------
class EvidencePrecedenceTests(EvidencePipelineMixin, TestCase):
    def test_present_then_absent_order(self):
        self.observe("source-a")
        self.remove("source-b")
        self.assertTrue(self.shared().currently_available)
        self.assertTrue(self.evidence_present("source-a"))
        self.assertFalse(self.evidence_present("source-b"))

    def test_absent_then_present_order_converges_to_the_same_state(self):
        # Absence can only be recorded against an *existing* canonical row, so
        # bootstrap one with a legacy write (no evidence yet). B's authoritative
        # absence then lands first: no source is present, so the offer is
        # momentarily unavailable...
        source = Source.objects.create(slug="seed-src", name="Seed")
        Offer.objects.create(
            provider=self.provider, country=self.country, source=source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.SUBSCRIPTION, provider_content_id="shared",
            currently_available=True,
        )
        self.remove("source-b")
        self.assertFalse(self.shared().currently_available)

        # ...then A observes it. Present evidence wins regardless of order.
        self.observe("source-a")
        self.assertTrue(self.shared().currently_available)

        # A later removal by B still cannot erase A's observation.
        self.remove("source-b")
        offer = self.shared()
        self.assertTrue(offer.currently_available)
        self.assertTrue(self.evidence_present("source-a"))
        self.assertFalse(self.evidence_present("source-b"))
        self.assertEqual(len(get_local_offers(self.movie)), 1)


# ---------------------------------------------------------------------------
# Adversarial: stale evidence is surfaced but never silently auto-expired, and
# only an authoritative absence (or an explicit purge) revokes it.
# ---------------------------------------------------------------------------
class StaleEvidenceTests(EvidencePipelineMixin, TestCase):
    def test_present_evidence_does_not_auto_expire(self):
        self.observe("source-a")
        old = timezone.now() - dt.timedelta(days=400)
        OfferEvidence.objects.filter(source__slug="source-a").update(
            first_seen=old, last_seen=old, observed_at=old,
        )
        Offer.objects.update(last_seen=old, observed_at=old)

        offer = self.shared()
        # No scheduler revokes evidence: the raw lifecycle and timestamps are
        # untouched. Freshness only qualifies how it is presented: the offer
        # is "last seen", not current, and never becomes unavailable.
        self.assertTrue(offer.currently_available)
        self.assertLess(
            OfferEvidence.objects.get(source__slug="source-a").last_seen,
            timezone.now() - dt.timedelta(days=399),
        )
        self.assertEqual(get_local_offers(self.movie), [])
        [last_known] = get_offer_evidence(self.movie).last_known
        self.assertEqual(last_known.availability_evidence, "stale")

        # An authoritative absence is the one thing that revokes it.
        self.remove("source-a")
        self.assertFalse(self.shared().currently_available)

    def test_stale_absence_does_not_hide_a_fresh_observation(self):
        # A's remembered absence is old; B's observation is fresh. Freshness of
        # the *absence* must not matter: present evidence still wins.
        self.remove("source-a")
        OfferEvidence.objects.filter(source__slug="source-a").update(
            last_seen=timezone.now() - dt.timedelta(days=400),
            observed_at=timezone.now() - dt.timedelta(days=400),
        )
        self.observe("source-b")
        self.assertTrue(self.shared().currently_available)


# ---------------------------------------------------------------------------
# Adversarial: retiring a source removes only its evidence and recomputes.
# ---------------------------------------------------------------------------
class SourceRetirementTests(EvidencePipelineMixin, TestCase):
    def test_purge_removes_only_the_retired_sources_rows(self):
        self.observe("source-a")
        self.observe("source-b")
        source_a = Source.objects.get(slug="source-a")
        other_rows = set(
            OfferEvidence.objects.filter(source__slug="source-b")
            .values_list("pk", flat=True)
        )

        deleted, recomputed = self.evidence.purge_for_source(source_a)

        self.assertEqual(deleted, 1)
        self.assertEqual(recomputed, 0)  # B still observes it
        self.assertFalse(OfferEvidence.objects.filter(source=source_a).exists())
        self.assertEqual(
            set(
                OfferEvidence.objects.filter(source__slug="source-b")
                .values_list("pk", flat=True)
            ),
            other_rows,
        )
        self.assertTrue(self.shared().currently_available)

    def test_purge_of_the_sole_source_makes_the_offer_unavailable(self):
        self.observe("source-a")
        source_a = Source.objects.get(slug="source-a")
        self.evidence.purge_for_source(source_a)
        self.assertFalse(self.shared().currently_available)


# ---------------------------------------------------------------------------
# Adversarial: the legacy (no-evidence) fallback cannot win once evidence
# exists, and it is the only thing consulted when no evidence exists.
# ---------------------------------------------------------------------------
class LegacyFallbackTests(EvidencePipelineMixin, TestCase):
    def _legacy_offer(self, slug):
        source = Source.objects.create(slug=slug, name=slug)
        return Offer.objects.create(
            provider=self.provider, country=self.country, source=source,
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.SUBSCRIPTION, provider_content_id="legacy",
            currently_available=True,
        )

    def test_fallback_is_the_only_signal_with_no_evidence(self):
        self._legacy_offer("legacy-only")
        self.assertEqual(OfferEvidence.objects.count(), 0)
        self.assertTrue(self.shared().currently_available)
        self.assertEqual(len(get_local_offers(self.movie)), 1)

    def test_fallback_is_not_consulted_once_evidence_exists(self):
        offer = self._legacy_offer("legacy-transition")
        # The source now publishes an authoritative snapshot omitting the row:
        # genuine absence evidence exists, and the offer becomes unavailable.
        self.remove("legacy-transition")
        offer.refresh_from_db()
        self.assertFalse(offer.currently_available)

        # A stale/legacy writer flips the stored flag back to True. Evidence now
        # exists, so a recompute (the repair path) derives availability from it
        # and the fallback must not win.
        Offer.objects.filter(pk=offer.pk).update(currently_available=True)
        self.evidence.recompute_availability([offer.identity])
        offer.refresh_from_db()
        self.assertFalse(offer.currently_available)
        self.assertEqual(get_local_offers(self.movie), [])
