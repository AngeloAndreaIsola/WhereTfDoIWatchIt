"""Stale positive evidence is "last seen", never "watchable now" — nor "unavailable".

Reconciliation keeps the raw lifecycle (``currently_available``) and timestamps
intact; freshness is applied as an additive ``evidence`` state. Only
``verified`` (fresh, positive, window open, unambiguous) evidence reaches the
current offer list, watchability, filters and counts. Stale or uncorroborated
positives stay discoverable as last-known offers with a qualified 🕓 status.
Clock: reconciliation tests pass a frozen ``now``; read-path tests use
offsets far from every window edge. No network.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import AvailabilityEvidence, ConflictKind
from availability.models import Offer as RawOffer, Source
from availability.tests.freshness_helpers import declare_freshness
from availability.tests.reconciliation_fixtures import ReconciliationFixtureMixin
from tracker.models import Movie, Preference, Subscription, WatchlistItem
from tracker.services.availability_reads import get_local_offers, get_offer_evidence
from tracker.services.watchability import (
    _needs_recheck_label,
    describe_last_known,
    evaluate_watchability,
    qualify_watchability,
)

FRESH = timedelta(hours=1)
STALE = timedelta(days=3)


class _Base(ReconciliationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        declare_freshness(self, "recon-source-a", "recon-source-b")
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

    def fresh(self):
        return self.now - FRESH

    def stale(self):
        return self.now - STALE


class ReconciledEvidenceTests(_Base):
    def test_fresh_positive_is_verified(self):
        row = self.offer(last_seen=self.fresh())

        [offer] = self.reconcile_movie().offers

        self.assertTrue(offer.currently_available)
        self.assertEqual(offer.evidence, AvailabilityEvidence.VERIFIED)
        self.assertTrue(offer.is_verified_available)
        self.assertEqual(offer.last_verified_at, RawOffer.objects.get(pk=row.pk).last_seen)
        self.assertEqual(len(get_local_offers(self.movie)), 1)

    def test_stale_only_positive_is_last_seen_and_raw_row_untouched(self):
        row = self.offer(last_seen=self.stale())

        [offer] = self.reconcile_movie().offers

        # Lifecycle is preserved; only the presentation state is qualified.
        self.assertTrue(offer.currently_available)
        self.assertEqual(offer.evidence, AvailabilityEvidence.STALE)
        self.assertFalse(offer.is_verified_available)
        self.assertIsNone(offer.last_verified_at)
        self.assertEqual(offer.last_seen_available_at, self.stale())
        stored = RawOffer.objects.get(pk=row.pk)
        self.assertTrue(stored.currently_available)
        self.assertEqual(stored.last_seen, self.stale())

        current, last_known = get_offer_evidence(self.movie)
        self.assertEqual(current, [])
        self.assertEqual([o.availability_evidence for o in last_known], ["stale"])
        self.assertEqual(last_known[0].last_seen_at, self.stale().isoformat())

    def test_fresh_and_stale_sources_are_evaluated_independently(self):
        self.offer(provider_content_id="a", last_seen=self.fresh())
        self.offer(provider_content_id="b", source=self.source_b, last_seen=self.stale())

        result = self.reconcile_movie()
        [offer] = result.offers

        self.assertEqual(offer.evidence, AvailabilityEvidence.VERIFIED)
        self.assertEqual(
            {obs.source: obs.evidence for obs in offer.observations},
            {
                "recon-source-a": AvailabilityEvidence.VERIFIED,
                "recon-source-b": AvailabilityEvidence.STALE,
            },
        )
        self.assertIn(ConflictKind.FRESHNESS, [c.kind for c in result.conflicts])
        self.assertEqual(len(get_local_offers(self.movie)), 1)

    def test_unknown_freshness_is_unverified_not_unavailable(self):
        undeclared = Source.objects.create(slug="recon-undeclared", name="Undeclared")
        self.offer(source=undeclared, last_seen=self.fresh())

        [offer] = self.reconcile_movie().offers

        self.assertTrue(offer.currently_available)
        self.assertEqual(offer.evidence, AvailabilityEvidence.UNKNOWN)
        current, last_known = get_offer_evidence(self.movie)
        self.assertEqual(current, [])
        self.assertEqual([o.availability_evidence for o in last_known], ["unknown"])

    def test_expired_window_is_genuinely_unavailable(self):
        self.offer(last_seen=self.fresh(), available_until=self.now - timedelta(hours=2))

        [offer] = self.reconcile_movie().offers

        self.assertFalse(offer.currently_available)
        self.assertEqual(offer.evidence, AvailabilityEvidence.UNAVAILABLE)
        self.assertEqual(get_offer_evidence(self.movie), ([], []))

    def test_stale_positive_beside_a_negative_stays_last_seen(self):
        self.offer(provider_content_id="a", last_seen=self.stale())
        self.offer(
            provider_content_id="b", source=self.source_b,
            currently_available=False, last_seen=self.fresh(),
        )

        result = self.reconcile_movie()
        [offer] = result.offers

        self.assertEqual(offer.evidence, AvailabilityEvidence.STALE)
        self.assertIn(ConflictKind.AVAILABILITY, [c.kind for c in result.conflicts])

    def test_fresh_positive_beside_a_negative_stays_verified(self):
        self.offer(provider_content_id="a", last_seen=self.fresh())
        self.offer(provider_content_id="b", source=self.source_b, currently_available=False)

        [offer] = self.reconcile_movie().offers

        self.assertEqual(offer.evidence, AvailabilityEvidence.VERIFIED)

    def test_mapping_ambiguity_is_never_verified_nor_shown_under_this_title(self):
        self.offer(provider_content_id="shared", last_seen=self.fresh())
        self.offer(tmdb_id=777, provider_content_id="shared", last_seen=self.fresh())

        [offer] = self.reconcile_movie().offers

        self.assertFalse(offer.currently_available)
        self.assertEqual(offer.evidence, AvailabilityEvidence.UNKNOWN)
        # The provider content may belong to tmdb 777: not attributed here.
        self.assertEqual(get_offer_evidence(self.movie), ([], []))


@patch("tracker.services.enrichment.enrich_movie_from_tmdb")
class ApiAgreementTests(_Base):
    """Catalog, homepage, watchlist report and detail agree on one state."""

    def setUp(self):
        super().setUp()
        Preference.objects.create(
            user_id=1, audio_languages=["EN"], subtitle_languages=["EN"],
            candidate_countries=["US"], current_country="US",
        )
        Subscription.objects.create(user_id=1, provider=self.netflix, name="Netflix")
        self.stale_movie = Movie.objects.create(tmdb_id=551, title="Old News", year=2000)
        self.offer(tmdb_id=550, provider_content_id="fresh", last_seen=self.fresh(),
                   audio_languages=["EN"], subtitle_languages=["EN"])
        self.offer(tmdb_id=551, provider_content_id="stale", last_seen=self.stale(),
                   audio_languages=["EN"], subtitle_languages=["EN"])

    def catalog(self, query=""):
        resp = self.client.get(f"/api/catalog/{query}")
        self.assertEqual(resp.status_code, 200)
        return {item["tmdb_id"]: item["watchability"] for item in resp.json()["results"]}

    def test_available_now_filter_excludes_stale_and_status_is_qualified(self, _enrich):
        everything = self.catalog()
        self.assertEqual(everything[550]["status"], "watchable_here")
        self.assertEqual(everything[550]["last_known"], None)
        stale = everything[551]
        self.assertEqual(stale["status"], "needs_recheck")
        self.assertEqual(stale["status_label"], "Unverified availability — last seen, needs recheck")
        self.assertEqual(stale["availability_evidence"], "stale")
        self.assertEqual(stale["last_seen_at"], self.stale().isoformat())
        self.assertIsNone(stale["best_offer"])
        # What the evidence meant when last seen is kept apart from the status.
        self.assertEqual(stale["last_known"]["status"], "watchable_here")

        self.assertEqual(set(self.catalog("?status=watchable")), {550})
        self.assertEqual(set(self.catalog("?watchable=true")), {550})
        self.assertEqual(set(self.catalog("?status=needs_recheck")), {551})
        self.assertEqual(set(self.catalog("?status=not_watchable")), set())
        # Where it was seen keeps it discoverable.
        self.assertEqual(set(self.catalog("?country=US")), {550, 551})

    def test_homepage_lists_only_verified_titles(self, _enrich):
        resp = self.client.get("/api/homepage/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({item["tmdb_id"] for item in resp.json()["results"]}, {550})

    def test_watchlist_report_counts_stale_as_needs_recheck(self, _enrich):
        WatchlistItem.objects.create(user_id=1, movie=self.movie)
        WatchlistItem.objects.create(user_id=1, movie=self.stale_movie)

        summary = self.client.get("/api/watchlist/report/").json()["summary"]

        self.assertEqual(summary["available_movies"], 1)
        self.assertEqual(summary["needs_recheck_movies"], 1)
        self.assertEqual(summary["unavailable_movies"], 0)
        self.assertEqual(summary["countries"], [{"country": "US", "count": 1}])

    def test_detail_matches_catalog(self, _enrich):
        detail = self.client.get("/api/prime/movie/551/").json()

        self.assertEqual(detail["offers"], [])
        self.assertEqual(detail["watchability"]["status"], "needs_recheck")
        self.assertEqual(
            detail["watchability"]["status_label"], self.catalog()[551]["status_label"]
        )
        [last_known] = detail["last_known_offers"]
        self.assertEqual(last_known["service"], "recon-netflix")
        self.assertEqual(last_known["availability_evidence"], "stale")
        self.assertEqual(last_known["last_seen_at"], self.stale().isoformat())
        self.assertTrue(last_known["is_entitled"])

        fresh = self.client.get("/api/prime/movie/550/").json()
        self.assertEqual(len(fresh["offers"]), 1)
        self.assertEqual(fresh["last_known_offers"], [])
        self.assertNotEqual(fresh["watchability"]["status"], "needs_recheck")
        self.assertIsNone(fresh["watchability"]["last_known"])

    def test_legacy_availability_endpoint_separates_last_known(self, _enrich):
        payload = self.client.get("/api/movie/551/availability/").json()

        self.assertEqual(payload["offers"], [])
        [last_known] = payload["last_known_offers"]
        self.assertEqual(last_known["availability_evidence"], "stale")
        fresh = self.client.get("/api/movie/550/availability/").json()
        self.assertEqual(fresh["offers"][0]["availability_evidence"], "verified")


class EntitlementAndLanguageStayIndependentTests(_Base):
    def setUp(self):
        super().setUp()
        Subscription.objects.create(user_id=1, provider=self.netflix, name="Netflix")

    def catalog(self):
        resp = self.client.get("/api/catalog/")
        return {item["tmdb_id"]: item["watchability"] for item in resp.json()["results"]}

    def test_unsubscribed_provider_stale_offer(self):
        Preference.objects.create(user_id=1, candidate_countries=["US"], current_country="US")
        self.offer(provider=self.prime, provider_content_id="p", last_seen=self.stale())
        fresh_movie = Movie.objects.create(tmdb_id=552, title="Fresh", year=2001)
        self.offer(tmdb_id=552, provider=self.prime, provider_content_id="q",
                   last_seen=self.fresh())

        watch = self.catalog()

        # Fresh but unowned: unchanged verdict.
        self.assertEqual(watch[fresh_movie.tmdb_id]["status"], "available_elsewhere")
        # Stale and unowned: needs recheck, and still flagged as not entitled.
        stale = watch[550]
        self.assertEqual(stale["status"], "needs_recheck")
        self.assertEqual(stale["last_known"]["status"], "available_elsewhere")
        [offer] = stale["last_known"]["offers"]
        self.assertFalse(offer["is_entitled"])
        self.assertNotIn("When last seen, it was included", " ".join(stale["explanation"]))

    def test_multi_viewer_language_match_on_stale_and_unknown_offers(self):
        # Classified with an explicit multi-viewer rule: the language verdict
        # of a last-known offer is the shared classifier's, unchanged.
        preference = Preference(
            user_id=1, candidate_countries=["US"], current_country="US",
            language_rule={"type": "viewers", "viewers": [
                {"name": "A", "languages": ["EN"]},
                {"name": "B", "languages": ["JA"]},
            ]},
        )
        undeclared = Source.objects.create(slug="recon-undeclared", name="Undeclared")
        self.offer(provider_content_id="ok", last_seen=self.stale(),
                   audio_languages=["EN"], subtitle_languages=["JA"])
        other = Movie.objects.create(tmdb_id=553, title="EN only", year=2002)
        self.offer(tmdb_id=553, provider_content_id="en", source=undeclared,
                   last_seen=self.fresh(), audio_languages=["EN"], subtitle_languages=["EN"])
        subs = {"recon-netflix"}

        both = describe_last_known(
            self.movie, get_offer_evidence(self.movie).last_known, preference,
            current_country="US", user_subscriptions=subs,
        )
        self.assertEqual(both.evidence, "stale")
        self.assertTrue(both.offers[0]["is_language_match"])
        self.assertEqual(both.status, "watchable_here")

        en_only = describe_last_known(
            other, get_offer_evidence(other).last_known, preference,
            current_country="US", user_subscriptions=subs,
        )
        self.assertEqual(en_only.evidence, "unknown")
        self.assertFalse(en_only.offers[0]["is_language_match"])
        self.assertEqual(en_only.status, "available_elsewhere")

        # Qualified status for both; never watchable, never unavailable.
        for movie, last_known in ((self.movie, both), (other, en_only)):
            result = qualify_watchability(
                evaluate_watchability(movie, [], preference, user_subscriptions=subs),
                last_known,
            )
            self.assertEqual(result.status, "needs_recheck")
        self.assertEqual(
            _needs_recheck_label(en_only)[0], "Unverified availability — needs recheck"
        )
