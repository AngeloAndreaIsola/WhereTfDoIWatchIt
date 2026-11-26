"""Market declaration and entitlement API checks on disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from availability.models import Country, Source
from availability.persistence.repositories import OfferPriceRepository, OfferRepository
from availability.tests.freshness_helpers import declare_freshness
from tracker.models import (
    EntitlementChannel, EntitlementCoverage, EntitlementPlan, Movie, Preference, Provider,
    ReviewedEntitlementPackage, Subscription, ViewerMarketSelection, WatchlistItem,
)
from tracker.services.access import build_user_access, resolve_title
from tracker.services.availability_reads import (
    TitleOfferEvidence, get_title_offer_evidence, get_title_offer_evidence_bulk,
)


class ProductFixture:
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.now = timezone.now()
        self.starts = self.now - timedelta(days=1)
        self.ends = self.now + timedelta(days=10)
        self.host = Provider.objects.create(slug="synthetic-aster", name="Aster")
        self.nl, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        self.de, _ = Country.objects.get_or_create(code="DE", defaults={"name": "Germany"})
        self.plan = self.product("base", "NL")
        self.product("premium", "NL")
        self.product("paid-ads", "DE")
        self.channel("orbit", "NL")
        self.channel("comet", "NL")
        self.channel("foreign", "DE")
        self.path = "/api/subscriptions/synthetic-aster/markets/NL/"

    def product(self, key, country):
        return EntitlementPlan.objects.create(
            provider_slug=self.host.slug, billing_country=country, plan_id=key, display_name=key,
            plan_class="base", starts_at=self.starts, ends_at=self.ends,
        )

    def channel(self, key, country):
        return EntitlementChannel.objects.create(
            parent_provider=self.host.slug, country=country, package_id=key,
            display_name=key.title(), starts_at=self.starts, ends_at=self.ends,
        )

    @staticmethod
    def payload(**updates):
        return {"holding": "yes", "plan_state": "selected", "plan_id": "base", "channels": [],
                "revision": 0, **updates}

    def put(self, **updates):
        return self.client.put(self.path, self.payload(**updates), format="json")


class SelectionApiTests(ProductFixture, TestCase):
    def test_catalogue_uses_market_and_preserves_retired_selection(self):
        legacy = Subscription.objects.create(provider=self.host, name="Aster")
        result = self.client.get("/api/providers/?billing_country=NL").json()
        provider = next(row for row in result if row["slug"] == self.host.slug)
        self.assertEqual(provider["selection"]["holding"], "yes")
        self.assertEqual(provider["selection"]["revision"], 0)
        self.assertEqual({p["plan_id"] for p in provider["plan_options"]}, {"base", "premium"})
        self.assertEqual({c["package_id"] for c in provider["channel_options"]}, {"orbit", "comet"})
        self.assertIsNone(provider["unlisted_plan_option"]["plan_id"])
        response = self.put(channels=[{"package_id": "orbit", "holding": "yes"},
                                      {"package_id": "comet", "holding": "no"}])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json()["revision"], 1)
        self.assertEqual(len(response.json()["channels"]), 2)
        EntitlementPlan.objects.filter(pk=self.plan.pk).update(ends_at=self.now)
        result = self.client.get("/api/providers/?billing_country=NL").json()
        provider = next(row for row in result if row["slug"] == self.host.slug)
        self.assertEqual(provider["selection"]["plan_state"], "unsupported")
        self.assertEqual(provider["selection"]["plan_id"], "base")
        self.assertEqual(provider["selection"]["plan_display_name"], "base")
        self.assertEqual([p["plan_id"] for p in provider["plan_options"]], ["premium"])
        self.assertTrue(Subscription.objects.filter(pk=legacy.pk).exists())

    def test_validation_is_exact_and_rejects_wrong_market_host_and_duplicates(self):
        unrelated = self.channel("unrelated", "NL")
        unrelated.parent_provider = "another"
        unrelated.save(update_fields=["parent_provider"])
        for invalid in (
            {"holding": "no"}, {"plan_id": "paid-ads"}, {"plan_id": "Base"}, {"plan_id": "base "},
            {"plan_state": "unsupported"}, {"revision": "0"}, {"revision": True},
            {"channels": [{"package_id": "foreign", "holding": "yes"}]},
            {"channels": [{"package_id": "unrelated", "holding": "yes"}]},
            {"channels": [{"package_id": "orbit", "holding": "yes"}, {"package_id": "orbit", "holding": "no"}]},
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.put(**invalid).status_code, 400)
        self.assertFalse(ViewerMarketSelection.objects.exists())
        self.assertEqual(self.client.get("/api/providers/?billing_country=XYZ").status_code, 400)

    def test_replay_conflict_omission_and_legacy_revocation(self):
        Subscription.objects.create(provider=self.host, name="Aster")
        children = [{"package_id": "orbit", "holding": "yes"}, {"package_id": "comet", "holding": "no"}]
        first = self.put(channels=children)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.put(channels=children[::-1]).json(), first.json())
        conflict = self.put(plan_id="premium")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["selection"], first.json())
        self.assertEqual(self.put(revision=1, holding="no", plan_state="unspecified", plan_id=None,
                                  channels=children[:1]).json()["channels"], children[:1])
        self.assertEqual(self.put(revision=2, channels=children).json()["revision"], 3)
        other = self.client.put(self.path.replace("/NL/", "/DE/"), self.payload(plan_id="paid-ads"), format="json")
        self.assertEqual(other.json()["revision"], 1)
        self.assertEqual(self.client.post("/api/providers/synthetic-aster/toggle/", {"access_mode": "none"},
                                          format="json").status_code, 200)
        self.assertEqual(set(ViewerMarketSelection.objects.values_list("holding", "plan_id", "revision")),
                         {("no", None, 4), ("no", None, 2)})
        self.assertEqual(self.client.post("/api/providers/synthetic-aster/toggle/", {"access_mode": "included"},
                                          format="json").status_code, 200)
        self.assertEqual(self.put(revision=2).status_code, 409)
        self.assertEqual(ViewerMarketSelection.objects.get(billing_country="NL").channels.count(), 2)

    def test_viewer_resolved_from_server_and_unlisted_plan_round_trips(self):
        user_model = get_user_model()
        # Reserve the anonymous single-viewer identity.
        user_model.objects.create_user(username="legacy-viewer")
        viewer = user_model.objects.create_user(username="other-viewer")
        self.client.force_authenticate(user=viewer)
        response = self.put(plan_state="unsupported", plan_id=None, user_id=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan_state"], "unsupported")
        self.assertEqual(ViewerMarketSelection.objects.get().user_id, viewer.pk)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.put().status_code, 200)
        self.assertEqual(ViewerMarketSelection.objects.count(), 2)


class ConcurrentSelectionTests(ProductFixture, TransactionTestCase):
    def test_concurrent_first_write_distinct_and_identical_replays(self):
        def race(left, right):
            barrier = Barrier(2)

            def write(payload):
                close_old_connections()
                try:
                    barrier.wait(5)
                    return APIClient().put(self.path, payload, format="json").status_code
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=2) as pool:
                return sorted(pool.map(write, (left, right)))

        self.assertEqual(race(self.payload(), self.payload(plan_id="premium")), [200, 409])
        saved = ViewerMarketSelection.objects.get()
        self.assertEqual(saved.revision, 1)
        new = self.payload(revision=1, holding="no", plan_state="unspecified", plan_id=None)
        self.assertEqual(race(new, new), [200, 200])
        saved.refresh_from_db()
        self.assertEqual(saved.revision, 2)


class OfferProjectionTests(ProductFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.movie = Movie.objects.create(tmdb_id=910003, title="Synthetic title")
        self.source = Source.objects.create(
            slug="synthetic-rights", name="Synthetic rights",
        )
        declare_freshness(self, self.source.slug)
        self.preference, _ = Preference.objects.update_or_create(
            user_id=1, defaults={"current_country": "NL", "audio_languages": ["EN"]},
        )
        self.numbers = iter(range(100, 200))

    def offer(self, *, movie=None, provider=None, country=None, kind="subscription", channel=None,
              variant="variant-hd", starts=None, freshness=None, review=True):
        provider = provider or self.host
        country = country or self.nl
        movie = movie or self.movie
        package_id = str(next(self.numbers))
        url = f"https://example.test/{package_id}"
        last_seen = freshness or self.now
        row, _ = OfferRepository().upsert(
            provider=provider, country=country, source=self.source, content_type=movie.content_type,
            tmdb_id=movie.tmdb_id, offer_type=kind, provider_content_id=package_id,
            provider_url=url, source_record_id=variant, observed_at=last_seen, now=last_seen,
            audio_languages=["EN"],
        )
        OfferPriceRepository().record(
            offer=row, source=self.source, amount=None, currency="", now=last_seen,
            variants=[{"package_id": package_id, "package": "synthetic-package",
                       "source_offer_id": variant, "url": url, "presentation_type": "HD",
                       "available_from": starts, "available_until": None}],
        )
        mapping = ReviewedEntitlementPackage.objects.create(
            source=self.source.slug, country=country.code, source_package_id=package_id,
            provider_slug=provider.slug, offer_type=kind, starts_at=self.starts,
            source_label="Reviewed source", channel=channel, public_conditions_met=True,
        ) if review else None
        return row, mapping

    def rule(self, row, mapping, *, product=None, effect="allow", variant="variant-hd", portability="unspecified"):
        return EntitlementCoverage.objects.create(
            offer_identity=row.identity, offer_country=row.country.code, package=mapping,
            product=product or self.plan, effect=effect, variant_id=variant, portability=portability,
            starts_at=self.starts, ends_at=self.ends, observed_at=self.now, fresh_until=self.ends,
            source_label="Reviewed title rule", provenance_reference="synthetic.private.reference",
        )

    @patch("tracker.views.request_title_refresh")
    def test_detail_report_mixed_offers_and_same_row_filters(self, refresh):
        WatchlistItem.objects.create(movie=self.movie)
        self.assertEqual(self.put().status_code, 200)
        base, package = self.offer()
        self.rule(base, package)
        self.offer(kind="rent")
        self.offer(kind="buy", country=self.de)
        detail = self.client.get("/api/prime/movie/910003/?current_country=NL").json()
        verdicts = {o["offer_type"]: o["entitlement"]["verdict"] for o in detail["offers"] + detail["purchase_offers"]}
        self.assertEqual(verdicts, {"subscription": "INCLUDED", "rent": "RENT_OR_BUY", "buy": "RENT_OR_BUY"})
        self.assertEqual(detail["watchability"]["verdict"], "available_here")
        self.assertEqual([o["offer_type"] for o in detail["watchability"]["purchase_offers"]], ["rent"])
        self.assertFalse(detail["watchability"]["purchase_offers"][0]["is_entitled"])
        self.assertNotIn("synthetic.private.reference", str(detail))
        self.assertEqual(detail["offers"][0]["source_package_id"], base.provider_content_id)
        report = self.client.get("/api/watchlist/report/?current_country=NL").json()["report"]
        self.assertEqual(report[0]["watchability"]["status"], "watchable_here")
        self.assertEqual(report[0]["watchability"]["best_offer"]["entitlement"]["reason"], "PLAN_COVERS_TITLE")
        for params in ("watchable=1&access=rent", "watchable=1&country=DE",
                       "included=1&access=buy", "country=NL&access=buy"):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(f"/api/watchlist/report/?{params}").json()["report"], [])
        self.assertEqual(len(self.client.get("/api/watchlist/report/?current_country=NL&included=1").json()["report"]), 1)

    def test_exact_host_variant_portability_and_bulk_queries(self):
        self.assertEqual(self.put(channels=[{"package_id": "orbit", "holding": "yes"}]).status_code, 200)
        hosted = Provider.objects.create(slug="synthetic-hosted-channel", name="A hosted channel")
        channel = EntitlementChannel.objects.get(package_id="orbit")
        row, package = self.offer(provider=hosted, kind="addon", channel=channel)
        self.rule(row, package, variant="variant-sd")
        foreign, foreign_package = self.offer(country=self.de)
        self.rule(foreign, foreign_package, portability="allowed")
        evidence = get_title_offer_evidence(self.movie, now=self.now)
        bulk = get_title_offer_evidence_bulk([self.movie], now=self.now)
        self.assertEqual(bulk[self.movie.pk], evidence)
        access = build_user_access(1, offers=evidence.current, as_of=self.now)
        result = resolve_title(self.movie, evidence.current, self.preference, access=access)
        self.assertEqual({c.country: c.entitlement.reason for c in result.offers},
                         {"NL": "NO_APPLICABLE_RULE", "DE": "PLAN_COVERS_TITLE"})
        self.rule(row, package)
        second = Movie.objects.create(tmdb_id=910004, title="Second synthetic title")
        self.offer(movie=second)
        with CaptureQueriesContext(connection) as one:
            get_title_offer_evidence_bulk([self.movie])
            build_user_access(1, offers=evidence.current, as_of=self.now)
        with CaptureQueriesContext(connection) as two:
            pair = get_title_offer_evidence_bulk([self.movie, second])
            build_user_access(1, offers=[o for e in pair.values() for o in e.current], as_of=self.now)
        self.assertEqual(len(one), len(two))
        access = build_user_access(1, offers=evidence.current, as_of=self.now)
        result = resolve_title(self.movie, evidence.current, self.preference, access=access)
        self.assertEqual(result.best_offer.entitlement.reason, "CHANNEL_AND_BASE_COVER_TITLE")

    @patch("tracker.views.request_title_refresh")
    def test_stale_offer_stays_historical_with_entitlement_reason(self, refresh):
        self.assertEqual(self.put().status_code, 200)
        row, package = self.offer(freshness=self.now - timedelta(days=5))
        self.rule(row, package)
        detail = self.client.get("/api/prime/movie/910003/?current_country=NL").json()
        self.assertEqual(detail["offers"], [])
        self.assertEqual(detail["watchability"]["verdict"], "needs_recheck")
        [historical] = detail["last_known_offers"]
        self.assertFalse(historical["is_entitled"])
        self.assertEqual(historical["entitlement"]["reason"], "STALE_EVIDENCE")


    @patch("tracker.views.request_title_refresh")
    def test_report_only_remains_unconfirmed_even_with_matching_product(self, refresh):
        self.assertEqual(self.put().status_code, 200)
        row, package = self.offer()
        self.rule(row, package)
        [current] = get_title_offer_evidence(self.movie, now=self.now).current
        report = replace(current, availability_evidence="source_reported", reporting_source=self.source.slug)
        with patch("tracker.views.get_title_offer_evidence", return_value=TitleOfferEvidence([], [], [report])):
            data = self.client.get("/api/prime/movie/910003/?current_country=NL").json()
        self.assertEqual(data["offers"], [])
        self.assertEqual(data["watchability"]["verdict"], "unknown")
        self.assertEqual(data["purchase_offers"], [])
        [reported] = data["source_reported_offers"]
        self.assertEqual(reported["source_package_id"], row.provider_content_id)
        self.assertEqual(reported["entitlement"]["reason"], "SOURCE_REPORTED")
        self.assertFalse(reported["playback_verified"])
        self.assertIsNone(reported["is_entitled"])

    def test_expired_price_snapshot_cannot_supply_package_to_new_observation(self):
        from availability.models import OfferPrice

        self.assertEqual(self.put().status_code, 200)
        row, package = self.offer()
        self.rule(row, package)
        OfferPrice.objects.filter(offer_identity=row.identity).update(
            observed_at=self.now - timedelta(minutes=1),
        )
        evidence = get_title_offer_evidence(self.movie, now=self.now)
        result = resolve_title(self.movie, evidence.current, self.preference)
        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(result.offers[0].entitlement.reason, "PACKAGE_UNRESOLVED")


    def test_worldwide_distribution_does_not_extend_paid_plan_territory(self):
        self.assertEqual(self.put().status_code, 200)
        row, package = self.offer()
        self.rule(row, package)
        [current] = get_title_offer_evidence(self.movie, now=self.now).current
        worldwide = replace(current, territorial_scope="worldwide")
        result = resolve_title(self.movie, [worldwide], self.preference, current_country="DE")
        self.assertTrue(result.offers[0].is_entitled)
        self.assertEqual(result.offers[0].locality, "out_of_reach")
        self.assertFalse(result.offers[0].is_accessible)
        self.assertEqual(result.verdict, "available_elsewhere")
