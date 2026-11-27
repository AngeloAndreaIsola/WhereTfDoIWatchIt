"""#97 availability-display contract — regression coverage.

The owner's accepted contract (2026-11-23):

* a fresh advertised offer is shown as current, with country, class and an
  observation time, but never as entitlement or playback;
* a stale / uncorroborated positive stays visible with an explicit
  ``Unverified`` badge and its last-seen time, and never counts as confirmed;
* a genuinely unavailable offer disappears from current results while its
  raw row, source and timestamps remain;
* a missing or failed refresh, an expiry alone, a different country, a
  different offer class, or a different media type never produces a
  cross-title "unavailable" verdict.

All rows here are deterministic fixtures in a disposable PostgreSQL test
database. No network, no production data, no schema changes.
"""
from datetime import timedelta

from django.test import TestCase

from availability.domain.enums import AvailabilityEvidence, ContentType, OfferType
from availability.models import Offer as RawOffer
from availability.tests.freshness_helpers import declare_freshness
from availability.tests.reconciliation_fixtures import ReconciliationFixtureMixin
from tracker.models import (
    Movie,
    Preference,
    RefreshRequest,
    RefreshRequestStatus,
)
from tracker.services.access import RefreshState
from tracker.services.availability_reads import get_local_offers, get_offer_evidence
from tracker.services.watchability import (
    _needs_recheck_label,
    describe_last_known,
    evaluate_watchability,
)

FRESH = timedelta(hours=1)
STALE = timedelta(days=3)


class AvailabilityDisplayContractTests(ReconciliationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        declare_freshness(self, "recon-source-a", "recon-source-b")
        self.movie = Movie.objects.create(tmdb_id=550, title="Contract film", year=2000)

    def fresh(self):
        return self.now - FRESH

    def stale(self):
        return self.now - STALE

    def preference(self, **kwargs):
        values = dict(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            candidate_countries=["US"],
            current_country="US",
        )
        values.update(kwargs)
        return Preference.objects.create(**values)

    # 1 — fresh advertised availability appears normally
    def test_01_fresh_offer_is_current_with_country_class_and_observation_time(self):
        self.offer(last_seen=self.fresh())

        current, last_known = get_offer_evidence(self.movie)

        self.assertEqual(len(current), 1)
        self.assertEqual(last_known, [])
        self.assertEqual(current[0].availability_evidence, "verified")
        self.assertEqual(current[0].country, "US")
        self.assertEqual(current[0].offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(current[0].last_seen_at, self.fresh().isoformat())

    # 2 — stale offer stays visible, explicitly Unverified
    def test_02_stale_offer_remains_visible_with_unverified_badge(self):
        self.offer(last_seen=self.stale())

        current, last_known = get_offer_evidence(self.movie)
        self.assertEqual(current, [])
        self.assertEqual(len(last_known), 1)
        self.assertEqual(last_known[0].availability_evidence, "stale")
        self.assertEqual(last_known[0].last_seen_at, self.stale().isoformat())

        classified = describe_last_known(
            self.movie, last_known, self.preference(), current_country="US",
            user_subscriptions={"recon-netflix"},
        )
        self.assertEqual(classified.evidence, "stale")
        self.assertIn("Unverified", _needs_recheck_label(classified)[0])

    # 3 — unverified offers excluded from confirmed-availability metrics
    def test_03_unverified_offer_is_not_counted_as_confirmed_availability(self):
        self.offer(last_seen=self.stale())
        preference = self.preference()

        confirmed = get_local_offers(self.movie)
        self.assertEqual(confirmed, [])

        result = evaluate_watchability(
            self.movie, confirmed, preference,
            current_country="US", user_subscriptions={"recon-netflix"},
        )
        self.assertNotEqual(result.status, "watchable_here")
        self.assertEqual(result.status, "unknown")

    # 4 — confirmed unavailable is hidden from current results
    def test_04_confirmed_unavailable_offer_is_hidden(self):
        self.offer(
            last_seen=self.fresh(), currently_available=False,
            available_until=self.now - timedelta(hours=2),
        )

        self.assertEqual(get_offer_evidence(self.movie), ([], []))

    # 5 — historical record, source and timestamps survive being hidden
    def test_05_historical_offer_row_is_preserved(self):
        row = self.offer(
            last_seen=self.fresh(), currently_available=False,
            available_until=self.now - timedelta(hours=2),
        )

        get_offer_evidence(self.movie)  # hidden from the current presentation
        stored = RawOffer.objects.get(pk=row.pk)
        self.assertFalse(stored.currently_available)
        self.assertEqual(stored.source.slug, "recon-source-a")
        self.assertEqual(stored.provider.slug, "recon-netflix")
        self.assertEqual(stored.last_seen, self.fresh())

    # 6 — fresh evidence restores visibility after a stale observation
    def test_06_new_fresh_evidence_restores_visibility(self):
        self.offer(provider_content_id="old", last_seen=self.stale())
        self.offer(
            provider_content_id="new", source=self.source_b, last_seen=self.fresh()
        )

        [offer] = self.reconcile_movie().offers
        self.assertEqual(offer.evidence, AvailabilityEvidence.VERIFIED)

        current, last_known = get_offer_evidence(self.movie)
        self.assertEqual(len(current), 1)
        self.assertEqual(last_known, [])

    # 7 — a failed or missing refresh is not a negative verdict
    def test_07_failed_refresh_is_unknown_not_unavailable(self):
        RefreshRequest.objects.create(
            movie=self.movie, source="recon-source-a",
            status=RefreshRequestStatus.FAILED, last_error="network timeout",
        )

        self.assertEqual(get_offer_evidence(self.movie), ([], []))
        result = evaluate_watchability(
            self.movie, [], self.preference(),
            current_country="US", refresh_state=RefreshState.FAILED,
        )
        self.assertEqual(result.status, "unknown")
        self.assertIn("Unknown", result.status_label)
        self.assertNotIn("unavailable", result.status_label.lower())

    # 8 — one market's unavailability cannot remove another market's offer
    def test_08_country_scoped_unavailability_is_isolated(self):
        self.offer(country=self.us, provider_content_id="us", last_seen=self.fresh())
        self.offer(
            country=self.nl, provider_content_id="nl", currently_available=False,
            last_seen=self.fresh(), available_until=self.now - timedelta(hours=2),
        )

        us_current, us_last = get_offer_evidence(self.movie, country="US")
        nl_current, nl_last = get_offer_evidence(self.movie, country="NL")

        self.assertEqual([o.country for o in us_current], ["US"])
        self.assertEqual(us_last, [])
        self.assertEqual((nl_current, nl_last), ([], []))

    # 9 — a rental withdrawal cannot remove a subscription offer
    def test_09_offer_class_unavailability_is_isolated(self):
        self.offer(
            provider_content_id="sub", offer_type=OfferType.SUBSCRIPTION,
            last_seen=self.fresh(),
        )
        self.offer(
            provider_content_id="rent", offer_type=OfferType.RENT,
            currently_available=False, last_seen=self.fresh(),
            available_until=self.now - timedelta(hours=2),
        )

        current, last_known = get_offer_evidence(self.movie)

        self.assertEqual([o.offer_type for o in current], [OfferType.SUBSCRIPTION])
        self.assertEqual(last_known, [])

    # 10 — movie and series identities sharing a numeric TMDb id stay separate
    def test_10_movie_series_identity_collision_is_isolated(self):
        series = Movie.objects.create(
            tmdb_id=550, title="Same id series", year=2000, content_type="series"
        )
        self.offer(
            provider=self.netflix, content_type=ContentType.MOVIE,
            provider_content_id="film", last_seen=self.fresh(),
        )
        self.offer(
            provider=self.prime, content_type=ContentType.SERIES,
            provider_content_id="show", last_seen=self.fresh(),
        )

        film_current, _ = get_offer_evidence(self.movie)
        show_current, _ = get_offer_evidence(series)

        self.assertEqual([o.service for o in film_current], ["recon-netflix"])
        self.assertEqual([o.service for o in show_current], ["recon-prime"])

    # 11 — ambiguous provider content fails closed
    def test_11_ambiguous_identity_fails_closed(self):
        self.offer(provider_content_id="shared", last_seen=self.fresh())
        self.offer(tmdb_id=777, provider_content_id="shared", last_seen=self.fresh())

        [offer] = self.reconcile_movie().offers
        self.assertFalse(offer.currently_available)
        self.assertEqual(offer.evidence, AvailabilityEvidence.UNKNOWN)
        self.assertEqual(get_offer_evidence(self.movie), ([], []))

    # 12 — advertised availability is not entitlement or playback
    def test_12_advertised_offer_does_not_grant_playback_access(self):
        self.offer(last_seen=self.fresh(), audio_languages=["EN"],
                   subtitle_languages=["EN"])
        current, _ = get_offer_evidence(self.movie)

        result = evaluate_watchability(
            self.movie, current, self.preference(),
            current_country="US", user_subscriptions=set(),
        )

        self.assertEqual(result.status, "unknown")
        self.assertIsNone(result.best_offer)
        payload = result.to_dict()
        self.assertNotIn("playback", payload)
        self.assertFalse(result.access_flags.get("has_subscription", True))
