"""Regression tests for JustWatch positive evidence, provider identity, and content matching.

Tests cover:
1. An offer on an unsubscribed but known service remains evidence/a possible
   viewing option, never becomes an included subscription or auto-creates a
   Subscription row.
2. A completely unmapped provider is logged/skipped safely; package sync does not
   auto-subscribe or create bogus provider records.
3. A new TV series with reliable external identity can persist provider-native
   data/mapping as designed, but never enters a personal watchlist without action;
   unresolved series stays unresolved (ProviderContent kept, 0 offers, no watchlist).
4. Ambiguous same-title/year or film-vs-series matches do not create erroneous
   mapped offers. Exact title/year fallback without TMDb ID is weaker identity
   evidence and must be treated conservatively.
5. Missing offer or incomplete pagination is not proof of provider-level
   unavailability; tests for partial catalogue partitions where practical
   (deactivations=0, non-authoritative snapshot guarantees).
6. Multi-country and access type examples including NL, IT, and JP fixture data,
   without invented live provider URLs.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod, SourceType, SyncMode
from availability.models import ContentMapping, Country, Offer as ModelOffer, OfferPrice, ProviderContent, Source
from availability.services.refresh import AvailabilityRefreshService
from availability.sources.base import ContentRequest
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import (
    JustWatchClient,
    JustWatchPackage,
)
from tracker.models import (
    ExternalProviderMapping,
    Movie,
    Preference,
    Provider,
    ProviderCategory,
    Subscription,
    WatchlistItem,
)
from tracker.services.access import (
    AccessVerdict,
    Locality,
    SubscriptionAccessMode,
    build_user_access,
    resolve_title,
)
from tracker.services.availability_reads import get_offer_evidence_bulk
from tracker.services.offers import Offer as DomainOffer
from tracker.services.provider_catalogue import (
    JUSTWATCH_INTEGRATION,
    sync_provider_catalogue,
)

from . import aggregator_fixtures as fx


def sleeper(_attempt):
    """Fast sleeper for tests."""


class RecordingPost:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        if not self.responses:
            return fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY)
        return self.responses.pop(0)


def source_with(post, **kwargs):
    return JustWatchSource(
        client=JustWatchClient(http_post=post, sleeper=sleeper), **kwargs
    )


class JustWatchRegressionCoverageTests(TestCase):
    def setUp(self):
        # Ensure base countries exist
        for code, name in (("NL", "Netherlands"), ("IT", "Italy"), ("JP", "Japan"), ("US", "United States")):
            Country.objects.get_or_create(code=code, defaults={"name": name, "enabled": True})

        # Ensure providers exist
        self.netflix, _ = Provider.objects.get_or_create(
            slug="netflix",
            defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )
        self.prime, _ = Provider.objects.get_or_create(
            slug="prime",
            defaults={"name": "Prime Video", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )
        self.disney, _ = Provider.objects.get_or_create(
            slug="disney",
            defaults={"name": "Disney+", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )
        self.raiplay, _ = Provider.objects.get_or_create(
            slug="raiplay",
            defaults={"name": "RaiPlay", "category": ProviderCategory.BVOD, "is_enabled": True},
        )
        self.apple_store, _ = Provider.objects.get_or_create(
            slug="apple_tv_store",
            defaults={"name": "Apple TV Store", "category": ProviderCategory.OFFICIAL, "is_enabled": False},
        )

        # Clear subscriptions and watchlists for a clean state
        Subscription.objects.all().delete()
        WatchlistItem.objects.all().delete()

    @patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected HTTP"))
    def test_equal_prices_and_repeated_catalogue_observations_keep_typed_offers(self, http):
        def priced_offer(technical_name, name, package, offer_class, *, offer_id=None):
            offer = fx.offer(offer_id, technical_name, name, package, monetization=offer_class)
            return {**offer, "retailPriceValue": 4.99}

        rent = priced_offer("netflix", "Netflix", "nfx", "RENT", offer_id="shared")
        buy = priced_offer("netflix", "Netflix", "nfx", "BUY", offer_id="shared")
        prime = priced_offer("amazonprimevideo", "Prime Video", "prv", "RENT")
        second_rent = {**rent, "standardWebURL": "https://example.test/second-movie"}
        series_rent = {**rent, "standardWebURL": "https://example.test/first-series"}
        nodes = [
            fx.title("tm98001", "First Movie", tmdb_id="98001", offers=[rent, rent, buy, prime, prime]),
            fx.title("tm98002", "Second Movie", tmdb_id="98002", offers=[second_rent]),
            fx.title("ts98001", "First Series", object_type="SHOW", tmdb_id="98001", offers=[series_rent]),
        ]
        titles = [
            Movie.objects.create(tmdb_id=int(node["content"]["externalIds"]["tmdbId"]),
                                 content_type=kind, title=node["content"]["title"])
            for node, kind in zip(nodes, ("movie", "movie", "series"))
        ]
        payload = fx.envelope({"popularTitles": {"edges": fx.edges(*nodes)}})
        post = RecordingPost([fx.FakeResponse(payload=payload) for _ in range(3)])
        service = AvailabilityRefreshService(source=source_with(post))
        service.refresh_catalogue(country="NL")
        service.refresh_catalogue(country="IT")
        offer_pks = set(ModelOffer.objects.values_list("pk", flat=True))
        price_pks = set(OfferPrice.objects.values_list("pk", flat=True))
        service.refresh_catalogue(country="NL")

        self.assertEqual(set(ModelOffer.objects.values_list("pk", flat=True)), offer_pks)
        self.assertEqual(set(OfferPrice.objects.values_list("pk", flat=True)), price_pks)
        self.assertEqual(len(offer_pks), 10)
        self.assertEqual(len(price_pks), 10)
        self.assertEqual(set(OfferPrice.objects.values_list("amount", "currency")), {(Decimal("4.99"), "EUR")})

        expected = [
            {("netflix", "rent"), ("netflix", "buy"), ("prime", "rent")},
            {("netflix", "rent")},
            {("netflix", "rent")},
        ]
        displayed = get_offer_evidence_bulk(titles)
        for title, pairs, expected_rent in zip(titles, expected, (rent, second_rent, series_rent)):
            with self.subTest(title=title.title):
                evidence = displayed[title.pk]
                offers = evidence.current + evidence.last_known
                keys = {(offer.country, offer.service, offer.offer_type) for offer in offers}
                self.assertEqual(keys, {(country, service, kind)
                                        for country in ("NL", "IT") for service, kind in pairs})
                self.assertEqual(len(offers), len(keys))
                self.assertEqual({(offer.price["amount"], offer.price["currency"]) for offer in offers},
                                 {("4.9900", "EUR")})
                for offer in offers:
                    if (offer.service, offer.offer_type) == ("netflix", "rent"):
                        variants = offer.prices[0]["variants"]
                        self.assertEqual(len(variants), 1)
                        self.assertEqual(variants[0]["url"], expected_rent["standardWebURL"])
        http.assert_not_called()

    def test_catalogue_skips_rows_without_a_native_title_id(self):
        offer = fx.offer("o1", "netflix", "Netflix", "nfx")
        nodes = [
            fx.title(None, "Missing First", tmdb_id="98001", offers=[offer]),
            fx.title("", "Missing Second", tmdb_id="98002", offers=[offer]),
            fx.title("tm98003", "Identified", tmdb_id="98003", offers=[offer]),
        ]
        payload = fx.envelope({"popularTitles": {"edges": fx.edges(*nodes)}})
        service = AvailabilityRefreshService(source=source_with(RecordingPost([fx.FakeResponse(payload=payload)])))
        service.refresh_catalogue(provider="netflix", country="NL")

        self.assertEqual(list(ModelOffer.objects.values_list("tmdb_id", "provider_content_id")),
                         [(98003, "tm98003")])
        self.assertEqual(list(ProviderContent.objects.values_list("provider_content_id", flat=True)),
                         ["tm98003"])

    @patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected HTTP"))
    def test_copresent_variants_survive_refresh_on_stable_offer_and_price_rows(self, http):
        title = Movie.objects.create(tmdb_id=98004, content_type="movie", title="Variants", year=2025)
        hd = fx.offer("shared", "netflix", "Netflix", "nfx", monetization="RENT",
                      url="https://example.test/rent/hd")
        hd["retailPriceValue"] = 3.99
        uhd = {**hd, "presentationType": "_4K", "retailPriceValue": 5.99,
               "standardWebURL": "https://example.test/rent/4k"}
        distinct_package = {**hd, "package": {**hd["package"], "packageId": 99004}}
        unpriced = {**hd, "id": None, "retailPriceValue": None}
        past_window = {**hd, "id": "past", "retailPriceValue": 1.99,
                       "availableTo": "2000-01-01T00:00:00Z"}
        variants = [hd, hd, uhd, distinct_package, past_window, unpriced]

        def response(offers):
            node = fx.title("tm98004", title.title, tmdb_id="98004", offers=offers)
            return fx.FakeResponse(payload=fx.envelope({"popularTitles": {"edges": fx.edges(node)}}))

        post = RecordingPost([response(variants), response(variants[::-1])])
        service = AvailabilityRefreshService(source=source_with(post))
        service.refresh_catalogue(country="NL")
        price = OfferPrice.objects.get(tmdb_id=title.tmdb_id)
        self.assertTrue(price.is_current)
        identities = (ModelOffer.objects.get(tmdb_id=title.tmdb_id).pk, price.pk)
        snapshot = price.variants
        service.refresh_catalogue(country="NL")

        price = OfferPrice.objects.get(tmdb_id=title.tmdb_id)
        offer = ModelOffer.objects.get(tmdb_id=title.tmdb_id)
        self.assertEqual((offer.pk, price.pk), identities)
        self.assertEqual(price.amount, Decimal("3.99"))
        self.assertTrue(price.is_current)
        self.assertEqual(offer.quality, "hd")
        self.assertEqual(price.variants, snapshot)
        self.assertEqual(
            {(variant["source_offer_id"], variant["presentation_type"],
              variant["amount"], variant["package_id"]) for variant in price.variants},
            {("shared", "HD", "3.9900", 1), ("shared", "_4K", "5.9900", 1),
             ("shared", "HD", "3.9900", 99004), ("", "HD", None, 1),
             ("past", "HD", "1.9900", 1)},
        )
        self.assertEqual(len(price.variants), 5)
        self.assertFalse(next(variant for variant in price.variants
                              if variant["source_offer_id"] == "past")["window_open_at_observation"])
        evidence = get_offer_evidence_bulk([title])[title.pk]
        displayed, = evidence.current + evidence.last_known
        self.assertEqual(displayed.price["amount"], "3.9900")
        self.assertTrue(displayed.price["is_from"])
        self.assertEqual(displayed.prices[0]["variants"], snapshot)
        http.assert_not_called()

    def test_conflicting_native_title_across_catalogue_pages_stays_unresolved(self):
        offer = fx.offer("o1", "netflix", "Netflix", "nfx")

        def response(*nodes):
            return fx.FakeResponse(payload=fx.envelope({"popularTitles": {"edges": fx.edges(*nodes)}}))

        post = RecordingPost([
            response(fx.title("ambiguous", "First", tmdb_id="98001", offers=[offer])),
            response(fx.title("ambiguous", "Second", tmdb_id="98002", offers=[offer]),
                     fx.title("confirmed", "Third", tmdb_id="98003", offers=[offer])),
        ])
        service = AvailabilityRefreshService(source=source_with(post))
        service.refresh_catalogue(country="NL", provider="netflix", pages=2)

        self.assertEqual(list(ModelOffer.objects.values_list("tmdb_id", "provider_content_id")),
                         [(98003, "confirmed")])
        self.assertEqual(list(ContentMapping.objects.values_list("tmdb_id", "provider_content_id")),
                         [(98003, "confirmed")])

    # -------------------------------------------------------------------------
    # 1. An offer on an unsubscribed but known service remains evidence/a possible
    #    viewing option, never becomes an included subscription.
    # -------------------------------------------------------------------------
    def test_unsubscribed_known_provider_offer_persists_evidence_without_autosubscribe(self):
        """Offers on known but unsubscribed services are recorded as valid Offer rows,
        but never generate a Subscription row and resolve as unowned/available elsewhere."""
        movie = Movie.objects.create(
            tmdb_id=80001, title="Global Cinema Showcase", year=2024, content_type="movie"
        )
        # User only has Disney+ subscribed; Netflix and Prime Video are known but unsubscribed
        Subscription.objects.create(
            user_id=1, provider=self.disney, name="Disney+", access_mode=SubscriptionAccessMode.INCLUDED
        )

        post = RecordingPost([
            fx.FakeResponse(payload=fx.JW_SEARCH_MULTI_COUNTRY_ACCESS)
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        res = service.refresh_content(movie, country="NL")
        self.assertGreater(res.processed, 0)

        # Verify Offer row for netflix in NL exists as immutable observation evidence
        netflix_offer = ModelOffer.objects.filter(tmdb_id=80001, provider=self.netflix, country__code="NL").first()
        self.assertIsNotNone(netflix_offer, "Netflix offer should be persisted as evidence")
        self.assertEqual(netflix_offer.offer_type, "subscription")

        # Crucial: Subscription rows must NOT be auto-created for netflix
        self.assertFalse(
            Subscription.objects.filter(user_id=1, provider=self.netflix).exists(),
            "JustWatch refresh must NEVER auto-subscribe the user to an unsubscribed provider"
        )
        self.assertEqual(
            Subscription.objects.filter(user_id=1).count(),
            1,
            "Only the pre-existing Disney+ subscription must exist"
        )

        # User access resolution check: viewing option remains an unowned option
        pref = Preference.objects.create(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["NL"],
            candidate_countries=["NL"],
            current_country="NL",
            vpn_enabled=False,
        )
        # Construct tracker-domain Offer with correct fields
        domain_offer = DomainOffer(
            country="NL",
            service=netflix_offer.provider.slug,
            offer_type=netflix_offer.offer_type,
            url=netflix_offer.provider_url,
            quality=netflix_offer.quality or "hd",
            audio_languages=netflix_offer.audio_languages,
            subtitle_languages=netflix_offer.subtitle_languages,
        )
        resolved = resolve_title(
            movie,
            [domain_offer],
            pref,
        )
        # Since Netflix is not owned by user, the offer verdict must be AVAILABLE_ELSEWHERE (not AVAILABLE_HERE)
        self.assertEqual(resolved.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertNotEqual(resolved.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertIn("No active subscription", " ".join(resolved.explanation))

    # -------------------------------------------------------------------------
    # 2. A completely unmapped provider is logged/skipped safely; package sync
    #    does not auto-subscribe.
    # -------------------------------------------------------------------------
    def test_unmapped_provider_logged_and_skipped_safely(self):
        """Unknown JustWatch package on a resolvable title is logged at WARNING and skipped without corrupting state."""
        movie = Movie.objects.create(
            tmdb_id=999, title="Obscure Title", year=2025, content_type="movie"
        )
        # Title that matches TMDb id, containing an unmapped package 'wug' alongside netflix
        resolvable_with_unmapped = fx.envelope({
            "popularTitles": {
                "edges": fx.edges(
                    fx.title(
                        "tm999", "Obscure Title", year=2025, tmdb_id="999",
                        offers=[
                            fx.offer("o9", "wug", "Weird Unknown Service", "wug", "FLATRATE"),
                            fx.offer("o10", "netflix", "Netflix", "nfx", "FLATRATE"),
                        ]
                    )
                )
            }
        })
        post = RecordingPost([
            fx.FakeResponse(payload=resolvable_with_unmapped)
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        with self.assertLogs("availability.sources.justwatch", level="WARNING") as cm:
            res = service.refresh_content(movie, country="NL")

        # Must log justwatch_provider_unmapped
        self.assertTrue(
            any("justwatch_provider_unmapped" in msg and "wug" in msg for msg in cm.output),
            "Unmapped package 'wug' must be logged as a warning during offer processing"
        )
        # Exactly one offer (netflix) persisted, and zero for the unmapped provider
        self.assertEqual(res.processed, 1)
        self.assertEqual(ModelOffer.objects.filter(tmdb_id=999).count(), 1)
        self.assertEqual(ModelOffer.objects.filter(tmdb_id=999, provider=self.netflix).count(), 1)

    def test_provider_catalogue_sync_never_auto_subscribes_and_skips_unmapped(self):
        """Package sync reports unmapped packages safely and does not auto-subscribe."""
        class _FakeClient:
            def providers(self, country):
                return [
                    JustWatchPackage(
                        clear_name="Mystery Stream",
                        technical_name="mysterystream_unmapped",
                        short_name="mys",
                        monetization_types=["FLATRATE"],
                    ),
                    JustWatchPackage(
                        clear_name="Netflix",
                        technical_name="netflix",
                        short_name="nfx",
                        monetization_types=["FLATRATE"],
                    )
                ]

        report = sync_provider_catalogue(["NL"], client=_FakeClient())

        # Unmapped package is reported, not guessed or subscribed
        unmapped_names = [u["technical_name"] for u in report["unmapped"]]
        self.assertIn("mysterystream_unmapped", unmapped_names)
        self.assertFalse(Provider.objects.filter(slug="mysterystream_unmapped").exists())

        # Package sync must never create any Subscription row
        self.assertEqual(
            Subscription.objects.count(),
            0,
            "Provider catalogue sync must never auto-subscribe the user"
        )

    # -------------------------------------------------------------------------
    # 3. A new TV series with reliable external identity can persist
    #    provider-native data/mapping as designed, but never enters a personal
    #    watchlist without action; unresolved series stays unresolved.
    # -------------------------------------------------------------------------
    def test_new_tv_series_persists_native_content_and_mapping_without_touching_watchlist(self):
        """TV series with TMDb ID persists ProviderContent and ContentMapping, but never enters WatchlistItem."""
        post = RecordingPost([
            fx.FakeResponse(payload=fx.JW_SEARCH_SERIES_JP),
            fx.FakeResponse(payload=fx.envelope(fx.JW_SEASONS)),
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        req = ContentRequest(tmdb_id=90001, title="Tokyo Midnight Stories", year=2024, content_type="series")
        result = source.fetch(req, country="JP")

        # Discoveries should have content and offers
        self.assertTrue(result.contents)
        self.assertTrue(result.offers)

        # Persist through AvailabilityRefreshService
        refresh_res = service._persist(result, tmdb_id=90001)
        self.assertGreater(refresh_res.processed, 0)

        # Check ProviderContent persisted
        series_content = ProviderContent.objects.filter(
            provider_content_id="ts90001", content_type=ContentType.SERIES
        ).first()
        self.assertIsNotNone(series_content, "ProviderContent for series should be persisted")
        self.assertEqual(series_content.title, "Tokyo Midnight Stories")

        # Check ContentMapping persisted
        mapping = ContentMapping.objects.filter(
            provider_content_id="ts90001", tmdb_id=90001
        ).first()
        self.assertIsNotNone(mapping, "ContentMapping should be created with TMDb external ID")
        self.assertEqual(mapping.match_method, MatchMethod.EXTERNAL_ID)

        # Crucial check: Personal watchlist MUST remain empty!
        self.assertEqual(
            WatchlistItem.objects.count(),
            0,
            "New TV series must never enter a personal watchlist without explicit user action"
        )

    def test_unresolved_series_persists_provider_content_but_zero_offers_and_no_watchlist(self):
        """An unresolved TV series stays unresolved: ProviderContent kept, zero offers, no WatchlistItem."""
        post = RecordingPost([
            fx.FakeResponse(payload=fx.JW_SEARCH_SERIES_UNRESOLVED),
            fx.FakeResponse(payload=fx.envelope({"node": {"seasons": []}})),
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        # Search without known TMDb ID, title not in local catalog
        req = ContentRequest(tmdb_id=None, title="Unmapped Enigmatic Chronicles", year=2024, content_type="series")
        result = source.fetch(req, country="JP")

        with self.assertLogs("availability.services.refresh", level="WARNING") as cm:
            refresh_res = service._persist(result, tmdb_id=None)

        # ProviderContent row is preserved
        unres_content = ProviderContent.objects.filter(provider_content_id="ts90002").first()
        self.assertIsNotNone(unres_content, "Unresolved series ProviderContent must still be persisted")

        # Offers without TMDb ID cannot be mapped to canonical offers
        self.assertEqual(refresh_res.processed, 0)
        self.assertGreater(refresh_res.unresolved, 0)
        self.assertEqual(ModelOffer.objects.filter(provider_content_id="ts90002").count(), 0)

        # ContentMapping must not exist
        self.assertFalse(ContentMapping.objects.filter(provider_content_id="ts90002").exists())

        # Watchlist must not be touched
        self.assertEqual(WatchlistItem.objects.count(), 0)

    # -------------------------------------------------------------------------
    # 4. Ambiguous same-title/year or film-vs-series matches do not create
    #    erroneous mapped offers. Exact title/year fallback without TMDb ID is
    #    weaker identity evidence and must be treated conservatively.
    # -------------------------------------------------------------------------
    def test_ambiguous_same_title_and_year_does_not_create_mapped_offers(self):
        """Two distinct JustWatch nodes with same title and year resolve to empty/unresolved, not first hit."""
        ambiguous_payload = fx.envelope({
            "popularTitles": {
                "edges": fx.edges(
                    fx.title("tm1001", "Identical Twins", year=2020, tmdb_id="1111",
                             offers=[fx.offer("o1", "netflix", "Netflix", "nfx")]),
                    fx.title("tm1002", "Identical Twins", year=2020, tmdb_id="2222",
                             offers=[fx.offer("o2", "netflix", "Netflix", "nfx")]),
                )
            }
        })
        post = RecordingPost([fx.FakeResponse(payload=ambiguous_payload)])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        # Query without TMDb ID
        req = ContentRequest(tmdb_id=None, title="Identical Twins", year=2020, content_type="movie")
        res = source.fetch(req, country="NL")

        # Should be empty because identity is ambiguous
        self.assertEqual(res.contents, [])
        self.assertEqual(res.offers, [])

    def test_film_vs_series_cross_namespace_collision_rejected(self):
        """A Movie request must never match a TV SHOW node even if numeric TMDb ID or title matches."""
        tv_show_payload = fx.envelope({
            "popularTitles": {
                "edges": fx.edges(
                    fx.title("ts555", "Twin Peaks", object_type="SHOW", year=1990, tmdb_id="192",
                             offers=[fx.offer("o1", "netflix", "Netflix", "nfx")]),
                )
            }
        })
        post = RecordingPost([
            fx.FakeResponse(payload=tv_show_payload),
            fx.FakeResponse(payload=fx.envelope({"node": {"seasons": []}})),
        ])
        source = source_with(post)

        # Request movie with same TMDb ID (which belongs to a TV series on JW)
        req = ContentRequest(tmdb_id=192, title="Twin Peaks", year=1990, content_type="movie")
        res = source.fetch(req, country="NL")

        # Must reject candidate because content_type SHOW != MOVIE
        self.assertEqual(res.contents, [])
        self.assertEqual(res.offers, [])

    def test_exact_title_year_fallback_conservative_rejection_on_conflicting_id(self):
        """Fallback with exact title and year is rejected if candidate has a conflicting TMDb ID."""
        candidate_with_different_id = fx.envelope({
            "popularTitles": {
                "edges": fx.edges(
                    fx.title("tm777", "Solaris", year=1972, tmdb_id="9999",
                             offers=[fx.offer("o1", "netflix", "Netflix", "nfx")]),
                )
            }
        })
        post = RecordingPost([fx.FakeResponse(payload=candidate_with_different_id)])
        source = source_with(post)

        # User wants Solaris (1972) with TMDb ID 593 (Tarkovsky)
        req = ContentRequest(tmdb_id=593, title="Solaris", year=1972, content_type="movie")
        res = source.fetch(req, country="NL")

        self.assertEqual(res.contents, [])
        self.assertEqual(res.offers, [])

    # -------------------------------------------------------------------------
    # 5. Missing offer or incomplete pagination is not proof of provider-level
    #    unavailability; tests for partial catalogue partitions.
    # -------------------------------------------------------------------------
    def test_missing_offer_or_incomplete_pagination_never_deactivates_unseen_offers(self):
        """JustWatch catalogue runs must never deactivate unseen offers (supports_authoritative_snapshots=False)."""
        # Create an existing offer from another source (e.g. collector) or previous run
        source_model, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR}
        )
        existing_offer = ModelOffer.objects.create(
            provider=self.netflix,
            country=Country.objects.get(code="NL"),
            source=source_model,
            content_type=ContentType.MOVIE,
            tmdb_id=550,
            offer_type="subscription",
            currently_available=True,
            provider_content_id="existing_node",
        )

        # Now simulate a partial/incomplete JustWatch catalogue run that returns empty titles or different titles
        post = RecordingPost([
            fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY)
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        # Refresh catalogue partition
        refresh_res = service.refresh_catalogue(provider="netflix", country="NL", cursor={"offset": 0})

        # Crucial assertions:
        self.assertEqual(refresh_res.deactivated, 0, "Aggregator run must NEVER deactivate unseen offers")
        existing_offer.refresh_from_db()
        self.assertTrue(
            existing_offer.currently_available,
            "Existing offer must remain currently_available=True despite absence in JustWatch page"
        )

    def test_partial_partition_walk_with_missing_titles_preserves_immutable_observations(self):
        """Even when JustWatch pagination stops early, previously recorded offers remain active."""
        source_model, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR}
        )
        prior_offer = ModelOffer.objects.create(
            provider=self.prime,
            country=Country.objects.get(code="IT"),
            source=source_model,
            content_type=ContentType.MOVIE,
            tmdb_id=12345,
            offer_type="subscription",
            currently_available=True,
            provider_content_id="prime_node_1",
        )

        # Next catalogue walk for IT prime only finds another title
        single_title_page = fx.envelope({
            "popularTitles": {
                "edges": fx.edges(
                    fx.title("tm9999", "Other Movie", year=2023, tmdb_id="99999",
                             offers=[fx.offer("o_prime_1", "amazonprimevideo", "Amazon Prime Video", "prv")])
                )
            }
        })
        post = RecordingPost([
            fx.FakeResponse(payload=single_title_page),
            fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY),
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        refresh_res = service.refresh_catalogue(provider="prime", country="IT", cursor={"offset": 0}, pages=2)

        self.assertEqual(refresh_res.deactivated, 0)
        prior_offer.refresh_from_db()
        self.assertTrue(prior_offer.currently_available)

    # -------------------------------------------------------------------------
    # 6. Relevant country and access type examples (NL, IT, JP) fixture data.
    # -------------------------------------------------------------------------
    def test_disabled_provider_offers_are_skipped(self):
        """When a provider is disabled (such as apple_tv_store by default), its offers are skipped."""
        self.assertFalse(self.apple_store.is_enabled)
        movie = Movie.objects.create(
            tmdb_id=80001, title="Global Cinema Showcase", year=2024, content_type="movie"
        )
        post = RecordingPost([
            fx.FakeResponse(payload=fx.JW_SEARCH_MULTI_COUNTRY_ACCESS)
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        service.refresh_content(movie, country="NL")
        self.assertFalse(
            ModelOffer.objects.filter(tmdb_id=80001, provider=self.apple_store).exists(),
            "Offers for disabled provider must be skipped"
        )

    def test_multi_country_and_access_type_normalization_with_enabled_providers(self):
        """Verify offer parsing, normalization, and persistence across NL, IT, and JP for various access types."""
        # Enable apple_tv_store locally for this test to verify RENT and BUY offer persistence
        self.apple_store.is_enabled = True
        self.apple_store.save(update_fields=["is_enabled"])

        movie = Movie.objects.create(
            tmdb_id=80001, title="Global Cinema Showcase", year=2024, content_type="movie"
        )
        post = RecordingPost([
            fx.FakeResponse(payload=fx.JW_SEARCH_MULTI_COUNTRY_ACCESS)
        ])
        source = source_with(post)
        service = AvailabilityRefreshService(source=source)

        # Refresh for NL
        service.refresh_content(movie, country="NL")
        nl_offers = ModelOffer.objects.filter(tmdb_id=80001, country__code="NL").order_by("offer_type")
        self.assertTrue(nl_offers.filter(offer_type="subscription", provider=self.netflix).exists())
        self.assertTrue(nl_offers.filter(offer_type="rent", provider=self.apple_store).exists())

        # Refresh for IT
        post.responses.append(fx.FakeResponse(payload=fx.JW_SEARCH_MULTI_COUNTRY_ACCESS))
        service.refresh_content(movie, country="IT")
        it_offers = ModelOffer.objects.filter(tmdb_id=80001, country__code="IT")
        self.assertTrue(it_offers.filter(offer_type="subscription", provider=self.prime).exists())
        self.assertTrue(it_offers.filter(offer_type="free_with_ads", provider=self.raiplay).exists())

        # Refresh for JP
        post.responses.append(fx.FakeResponse(payload=fx.JW_SEARCH_MULTI_COUNTRY_ACCESS))
        service.refresh_content(movie, country="JP")
        jp_offers = ModelOffer.objects.filter(tmdb_id=80001, country__code="JP")
        self.assertTrue(jp_offers.filter(offer_type="subscription", provider=self.netflix).exists())
        self.assertTrue(jp_offers.filter(offer_type="buy", provider=self.apple_store).exists())
        # Unmapped stream offer in JP must not exist
        self.assertFalse(jp_offers.filter(provider__slug="unmappedstreamjp").exists())
