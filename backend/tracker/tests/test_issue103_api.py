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
    def test_stale_write_precedes_catalogue_membership_validation(self):
        self.assertEqual(self.put().status_code, 200)
        saved = self.put(revision=1, plan_id="premium")
        self.assertEqual(saved.status_code, 200)
        EntitlementPlan.objects.filter(pk=self.plan.pk).update(ends_at=self.now)

        stale = self.put(revision=1)
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertEqual(stale.json()["selection"], saved.json())
        self.assertEqual(self.put(revision=2).status_code, 400)
        self.assertEqual(self.put(revision="1").status_code, 400)

    def test_channel_edit_can_preserve_only_the_saved_retired_plan(self):
        self.assertEqual(self.put().status_code, 200)
        EntitlementPlan.objects.filter(pk=self.plan.pk).update(ends_at=self.now, display_name="Aster Base")
        channels = [{"package_id": "orbit", "holding": "yes"}]
        for plan_id in ("invented", "premium", "paid-ads"):
            with self.subTest(plan_id=plan_id):
                self.assertEqual(self.put(revision=1, plan_state="unsupported", plan_id=plan_id,
                                          channels=channels).status_code, 400)
        response = self.put(revision=1, plan_state="unsupported", channels=channels)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json(), {
            "holding": "yes", "plan_state": "unsupported", "plan_id": "base",
            "plan_display_name": "Aster Base", "channels": channels, "revision": 2,
        })
        self.assertEqual(self.put(revision=1, plan_state="unsupported", channels=channels).json(), response.json())
        [selection] = [row["selection"] for row in self.client.get("/api/providers/?billing_country=NL").json()
                       if row["slug"] == self.host.slug]
        self.assertEqual(selection, response.json())
        unlisted = self.put(revision=2, plan_state="unsupported", plan_id=None)
        self.assertEqual(unlisted.status_code, 200)
        self.assertIsNone(unlisted.json()["plan_id"])

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

    def detail(self, params="current_country=NL"):
        response = self.client.get(f"/api/prime/movie/{self.movie.tmdb_id}/?{params}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def watchable_report(self):
        response = self.client.get("/api/watchlist/report/?watchable=1&audio_languages=EN")
        self.assertEqual(response.status_code, 200)
        return response.json()["report"]

    @patch("tracker.views.request_title_refresh")
    def test_missing_audio_cannot_be_borrowed_from_an_unreviewed_package(self, refresh):
        WatchlistItem.objects.create(movie=self.movie)
        self.assertEqual(self.put().status_code, 200)
        covered, package = self.offer(variant="variant-a")
        covered.audio_languages = None
        covered.save(update_fields=["audio_languages"])
        self.rule(covered, package, variant="variant-a")
        self.assertEqual(self.detail()["watchability"]["verdict"], "unknown")
        self.offer(variant="variant-b", review=False, freshness=self.now - timedelta(minutes=3))

        detail = self.detail()
        rows = {row["source_package_id"]: row for row in detail["offers"]}
        with self.subTest(surface="detail row"):
            self.assertEqual(rows[covered.provider_content_id]["audio_languages"], [])
            self.assertFalse(rows[covered.provider_content_id]["is_accessible"])
            self.assertEqual(rows[covered.provider_content_id]["entitlement"]["verdict"], "INCLUDED")
            self.assertEqual(len(rows), 2)
            [other] = [row for key, row in rows.items() if key != covered.provider_content_id]
            self.assertEqual(other["audio_languages"], ["EN"])
            self.assertFalse(other["is_accessible"])
        with self.subTest(surface="title verdict"):
            self.assertEqual(detail["watchability"]["verdict"], "unknown")
        with self.subTest(surface="filtered report"):
            self.assertEqual(len(self.watchable_report()), 0)

    @patch("tracker.views.request_title_refresh")
    def test_covered_package_survives_denied_alternative_in_either_order(self, refresh):
        WatchlistItem.objects.create(movie=self.movie)
        self.assertEqual(self.put().status_code, 200)
        older = self.now - timedelta(minutes=3)
        covered, package_a = self.offer(variant="variant-a", freshness=older)
        self.rule(covered, package_a, variant="variant-a")
        self.assertEqual(self.detail()["watchability"]["verdict"], "available_here")
        blocked, package_b = self.offer(variant="variant-b")
        self.rule(blocked, package_b, effect="deny", variant="variant-b")

        for newest in (blocked, covered):
            detail = self.detail()
            with self.subTest(newest=newest.provider_content_id, surface="detail rows"):
                self.assertEqual(
                    {row["source_package_id"]: (row["entitlement"]["verdict"], row["is_accessible"])
                     for row in detail["offers"]},
                    {covered.provider_content_id: ("INCLUDED", True), blocked.provider_content_id: ("BLOCKED", False)},
                )
            with self.subTest(newest=newest.provider_content_id, surface="title verdict"):
                self.assertEqual(detail["watchability"]["verdict"], "available_here")
            with self.subTest(newest=newest.provider_content_id, surface="filtered report"):
                reports = self.watchable_report()
                self.assertEqual(len(reports), 1)
                [report] = reports
                self.assertEqual(report["watchability"]["status"], "watchable_here")
                self.assertEqual(report["watchability"]["best_offer"]["source_package_id"], covered.provider_content_id)
            covered.last_seen, blocked.last_seen = self.now, older
            covered.save(update_fields=["last_seen"])
            blocked.save(update_fields=["last_seen"])

    @patch("tracker.views.request_title_refresh")
    def test_one_observation_keeps_each_variant_and_its_reported_audio(self, refresh):
        from availability.models import OfferPrice

        WatchlistItem.objects.create(movie=self.movie)
        self.assertEqual(self.put().status_code, 200)
        row, package = self.offer(variant="variant-a")
        self.rule(row, package, variant="variant-a", effect="deny")
        self.rule(row, package, variant="variant:b")
        snapshot = OfferPrice.objects.get(offer_identity=row.identity)
        variant_a = {**snapshot.variants[0], "audio_languages": ["en"]}
        variant_b = {**variant_a, "source_offer_id": "variant:b", "url": "https://example.test/alternate"}

        for audio, verdict in ((["de"], "available_elsewhere"), (["en"], "available_here")):
            with self.subTest(audio=audio):
                snapshot.variants = [variant_a, {**variant_b, "audio_languages": audio}]
                snapshot.save(update_fields=["variants"])
                detail = self.detail()
                rows = {offer["url"]: offer for offer in detail["offers"]}
                self.assertEqual(set(rows), {variant_a["url"], variant_b["url"]})
                self.assertEqual(rows[variant_a["url"]]["audio_languages"], ["EN"])
                self.assertEqual(rows[variant_a["url"]]["entitlement"]["verdict"], "BLOCKED")
                self.assertEqual(rows[variant_b["url"]]["audio_languages"], [audio[0].upper()])
                self.assertEqual(rows[variant_b["url"]]["entitlement"]["verdict"], "INCLUDED")
                self.assertEqual(rows[variant_b["url"]]["is_accessible"], audio == ["en"])
                self.assertEqual(detail["watchability"]["verdict"], verdict)
                self.assertEqual(len(self.watchable_report()), int(audio == ["en"]))

    @patch("tracker.views.request_title_refresh")
    def test_typed_detail_uses_current_country_for_rows_and_title(self, refresh):
        self.preference.vpn_enabled = False
        self.preference.save(update_fields=["vpn_enabled"])
        self.assertEqual(self.put().status_code, 200)
        row, package = self.offer()
        self.rule(row, package)

        for params in ("current_country=DE", "current_country=DE&country=NL", "country=DE"):
            with self.subTest(params=params):
                detail = self.detail(params)
                [offer] = detail["offers"]
                self.assertEqual(detail["watchability"]["verdict"], "available_elsewhere")
                self.assertEqual(offer["locality"], "out_of_reach")
                self.assertFalse(offer["is_accessible"])

    def test_invalid_only_language_filter_excludes_official_and_unofficial_entries(self):
        from tracker.tests.test_unofficial_api import _ingest, _make_provider

        self.offer()
        _ingest(_make_provider("synthetic-scraper"), None)
        for path, count in (("/api/catalog/", 1), ("/api/catalog/unified/", 2)):
            with self.subTest(path=path):
                self.assertEqual(len(self.client.get(f"{path}?audio_languages=EN,not-a-language").json()["results"]), count)
                for name in ("audio_languages", "subtitle_languages"):
                    response = self.client.get(f"{path}?{name}=not-a-language")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.json()["results"]), 0)

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
