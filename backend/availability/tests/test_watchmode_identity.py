"""F4 regression: Watchmode content identity must be per-title, not per-provider.

The pre-fix identity was ``wm:{source_id}`` where ``source_id`` is Watchmode's
*provider* id (296 = Tubi), so every title a provider carried collapsed into a
single ``ProviderContent`` row and reconciliation hid all but the first title's
offer as ``mapping doubt``. These tests pin both halves of the repair:
the source adapter's new identity, and the split of historical rows.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from availability.domain.enums import ContentType
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    Source,
    SyncRun,
    build_offer_identity,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.services.watchmode_identity import (
    build_watchmode_content_id,
    is_collapsed_watchmode_content_id,
)
from availability.services.watchmode_identity_repair import (
    RepairError,
    apply_plan,
    build_plan,
)
from availability.sources import ContentRequest
from availability.sources.watchmode import WatchmodeSource
from availability.sources.watchmode_client import WatchmodeClient
from availability.tests import aggregator_fixtures as fx
from tracker.models import Movie, Provider


# =============================================================================
# Identity format
# =============================================================================
class WatchmodeIdentityFormatTests(SimpleTestCase):
    def test_two_titles_at_one_provider_get_different_ids(self):
        a = build_watchmode_content_id(296, ContentType.MOVIE, tmdb_id=5)
        b = build_watchmode_content_id(296, ContentType.MOVIE, tmdb_id=550)
        self.assertNotEqual(a, b)
        self.assertEqual(a, "wm:296:movie:tmdb-5")
        self.assertEqual(b, "wm:296:movie:tmdb-550")

    def test_content_type_is_part_of_the_identity(self):
        movie = build_watchmode_content_id(296, ContentType.MOVIE, tmdb_id=550)
        series = build_watchmode_content_id(296, ContentType.SERIES, tmdb_id=550)
        self.assertNotEqual(movie, series)

    def test_region_is_not_part_of_the_identity(self):
        """One provider content entry has offers in several regions."""
        source = WatchmodeSource(client=WatchmodeClient(
            http_get=lambda *a, **k: fx.FakeResponse(payload=fx.WM_SOURCES_MULTI),
            api_key="k", sleeper=lambda s: None,
            budget=lambda: None,
        ))
        row = SimpleNamespace(source_id=203, name="Netflix", type="sub",
                              region="NL", web_url="u", format="HD")
        nl = source._content_id(
            row,
            request=SimpleNamespace(tmdb_id=550, imdb_id=None, title="Fight Club"),
            content_type=ContentType.MOVIE,
        )
        ca = source._content_id(
            SimpleNamespace(**{**row.__dict__, "region": "CA"}),
            request=SimpleNamespace(tmdb_id=550, imdb_id=None, title="Fight Club"),
            content_type=ContentType.MOVIE,
        )
        self.assertEqual(nl, ca)

    def test_imdb_then_title_fallbacks_are_distinct_per_title(self):
        self.assertEqual(
            build_watchmode_content_id(12, "movie", imdb_id="tt0118528"),
            "wm:12:movie:imdb-tt0118528",
        )
        self.assertEqual(
            build_watchmode_content_id(12, "movie", title="12 Angry Men"),
            "wm:12:movie:title-12-angry-men",
        )

    def test_missing_provider_id_falls_back_to_the_provider_name(self):
        row = SimpleNamespace(source_id=None, name="Tubi TV")
        cid = WatchmodeSource._content_id(
            row,
            request=SimpleNamespace(tmdb_id=5, imdb_id=None, title="x"),
            content_type="movie",
        )
        self.assertEqual(cid, "wm:name-tubi-tv:movie:tmdb-5")

    def test_title_fallback_includes_the_year_to_separate_remakes(self):
        self.assertEqual(
            build_watchmode_content_id(12, "movie", title="12 Angry Men", year=1997),
            "wm:12:movie:title-12-angry-men-1997",
        )
        self.assertNotEqual(
            build_watchmode_content_id(12, "movie", title="12 Angry Men", year=1957),
            build_watchmode_content_id(12, "movie", title="12 Angry Men", year=1997),
        )

    def test_over_long_identity_is_folded_to_a_stable_hash(self):
        title = "A" * 400
        first = build_watchmode_content_id(12, "movie", title=title)
        second = build_watchmode_content_id(12, "movie", title=title)
        other = build_watchmode_content_id(12, "movie", title=title + "B")
        self.assertLessEqual(len(first), 100)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("wm:12:movie:"))
        self.assertFalse(is_collapsed_watchmode_content_id(first))

    def test_collapsed_detection_is_exact(self):
        self.assertTrue(is_collapsed_watchmode_content_id("wm:296"))
        self.assertTrue(is_collapsed_watchmode_content_id("wm:netflix"))
        self.assertFalse(is_collapsed_watchmode_content_id("wm:296:movie:tmdb-5"))
        self.assertFalse(is_collapsed_watchmode_content_id("608226"))
        self.assertFalse(is_collapsed_watchmode_content_id(""))

    def test_provider_id_is_not_a_content_discriminator(self):
        """The exact historical defect, stated as a property test."""
        old_a = "wm:296"
        old_b = "wm:296"
        new_a = build_watchmode_content_id(296, "movie", tmdb_id=5)
        new_b = build_watchmode_content_id(296, "movie", tmdb_id=550)
        self.assertEqual(old_a, old_b)
        self.assertNotEqual(new_a, new_b)


# =============================================================================
# Search-result verification
# =============================================================================
class WatchmodeSearchVerificationTests(SimpleTestCase):
    """``/search/`` is a search, so its first result is not an identity.

    Live defect: a ``tmdb_movie_id`` query returned a different title, the
    adapter fetched *that* title's sources, and the offers were persisted under
    the requested content. No result may be used unless an exact requested
    external id corroborates it.
    """

    def _source(self, results, sources=None):
        seen = []

        def http_get(url, headers=None, timeout=None):
            seen.append(url)
            if "/search/" in url:
                return fx.FakeResponse(payload={
                    "success": True,
                    "title_results": results,
                    "people_results": [],
                })
            return fx.FakeResponse(
                payload=fx.WM_SOURCES_NL if sources is None else sources
            )

        return WatchmodeSource(client=WatchmodeClient(
            http_get=http_get, api_key="k", sleeper=lambda s: None,
            budget=lambda: None,
        )), seen

    @staticmethod
    def _result(**overrides):
        base = dict(fx.WM_SEARCH_MOVIE["title_results"][0])
        base.update(overrides)
        return base

    def test_a_search_result_for_another_tmdb_id_is_rejected_unfetched(self):
        source, seen = self._source([self._result()])  # TMDb 550
        result = source.fetch(
            ContentRequest(tmdb_id=5, title="Four Rooms"), country="NL"
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        self.assertEqual(len(seen), 1, "sources must not be fetched from a guess")

    def test_two_results_claiming_the_requested_id_are_ambiguous(self):
        results = [
            self._result(id=1, tmdb_id=5, name="Four Rooms"),
            self._result(id=2, tmdb_id=5, name="Four Rooms"),
        ]
        source, seen = self._source(results)
        result = source.fetch(
            ContentRequest(tmdb_id=5, title="Four Rooms"), country="NL"
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(len(seen), 1)

    def test_a_result_without_a_tmdb_id_is_not_verifiable(self):
        source, seen = self._source([self._result(tmdb_id=None)])
        result = source.fetch(
            ContentRequest(tmdb_id=550, title="Fight Club"), country="NL"
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(len(seen), 1)

    def test_a_numeric_id_in_the_other_tmdb_namespace_is_rejected(self):
        source, seen = self._source([self._result(tmdb_id=5, tmdb_type="tv")])
        result = source.fetch(
            ContentRequest(tmdb_id=5, title="Four Rooms"), country="NL"
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(len(seen), 1)

    def test_an_exact_result_is_used(self):
        source, seen = self._source([self._result()])
        result = source.fetch(
            ContentRequest(tmdb_id=550, title="Fight Club"), country="NL"
        )
        self.assertTrue(result.offers)
        self.assertIn("/title/1132806/sources/", seen[1])

    def test_series_tmdb_type_aliases_are_accepted(self):
        source, seen = self._source([self._result(
            id=82, tmdb_id=1399, tmdb_type="show", type="tv",
            name="Game of Thrones",
        )])
        result = source.fetch(
            ContentRequest(tmdb_id=1399, content_type="series",
                           title="Game of Thrones"),
            country="NL",
        )
        self.assertTrue(result.offers)
        self.assertIn("/title/82/sources/", seen[1])

    def test_name_only_lookup_requires_one_exact_name(self):
        source, seen = self._source([
            self._result(id=1, tmdb_id=None),
            self._result(id=2, tmdb_id=None),
        ])
        result = source.fetch(
            SimpleNamespace(tmdb_id=None, imdb_id=None, title="Fight Club",
                            content_type="movie"),
            country="NL",
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(len(seen), 1)


# =============================================================================
# Source -> persistence
# =============================================================================
class WatchmodePersistenceIdentityTests(TestCase):
    def setUp(self):
        self.tubi, _ = Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        self.ca, _ = Country.objects.get_or_create(code="CA", defaults={"name": "Canada"})
        Movie.objects.create(tmdb_id=5, title="Four Rooms", year=1995)
        Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

    def _source(self):
        def http_get(url, headers=None, timeout=None):
            if "/search/" in url:
                tmdb = 550 if "550" in url else 5
                return fx.FakeResponse(payload={
                    "success": True,
                    "title_results": [{
                        "resultType": "title", "id": 1000 + tmdb,
                        "name": "x", "type": "movie", "year": 2000,
                        "imdb_id": None, "tmdb_id": tmdb, "tmdb_type": "movie",
                    }],
                    "people_results": [],
                })
            return fx.FakeResponse(payload=fx.WM_SOURCES_MULTI)

        return WatchmodeSource(client=WatchmodeClient(
            http_get=http_get, api_key="k", sleeper=lambda s: None,
            budget=lambda: None,
        ))

    def test_two_titles_create_two_distinct_provider_content_rows(self):
        service = AvailabilityRefreshService(source=self._source())
        for tmdb_id in (5, 550):
            movie = Movie.objects.get(tmdb_id=tmdb_id)
            service.refresh_content(movie)

        tubi_rows = ProviderContent.objects.filter(
            provider=self.tubi, provider_content_id__startswith="wm:"
        )
        self.assertEqual(tubi_rows.count(), 2)
        self.assertEqual(
            set(tubi_rows.values_list("provider_content_id", flat=True)),
            {"wm:13:movie:tmdb-5", "wm:13:movie:tmdb-550"},
        )
        for movie in Movie.objects.filter(tmdb_id__in=(5, 550)):
            offer = Offer.objects.get(provider=self.tubi, tmdb_id=movie.tmdb_id)
            self.assertIn(f"tmdb-{movie.tmdb_id}", offer.provider_content_id)

    def test_reingesting_the_same_title_is_idempotent(self):
        """A duplicate refresh must update, never duplicate, content/offers."""
        service = AvailabilityRefreshService(source=self._source())
        movie = Movie.objects.get(tmdb_id=550)
        service.refresh_content(movie)

        content = ProviderContent.objects.get(
            provider=self.tubi, provider_content_id="wm:13:movie:tmdb-550"
        )
        offer = Offer.objects.get(provider=self.tubi, tmdb_id=550)
        offer_pk = offer.pk

        service.refresh_content(movie)

        self.assertEqual(
            ProviderContent.objects.filter(
                provider=self.tubi, provider_content_id="wm:13:movie:tmdb-550"
            ).count(),
            1,
        )
        self.assertEqual(
            Offer.objects.filter(provider=self.tubi, tmdb_id=550).count(), 1
        )
        self.assertEqual(
            Offer.objects.get(provider=self.tubi, tmdb_id=550).pk, offer_pk,
            "the same offer identity must be updated in place",
        )
        self.assertTrue(
            ProviderContent.objects.filter(pk=content.pk).exists()
        )

    def test_each_title_has_its_own_mapping_and_no_mapping_doubt(self):
        service = AvailabilityRefreshService(source=self._source())
        for tmdb_id in (5, 550):
            service.refresh_content(Movie.objects.get(tmdb_id=tmdb_id))

        mappings = {
            m.provider_content_id: m.tmdb_id
            for m in ContentMapping.objects.filter(provider=self.tubi)
        }
        self.assertEqual(mappings, {
            "wm:13:movie:tmdb-5": 5,
            "wm:13:movie:tmdb-550": 550,
        })

        from availability.services.query import AvailabilityQueryService
        for tmdb_id in (5, 550):
            result = AvailabilityQueryService().get_reconciled_for_content(
                tmdb_id, "movie", "CA"
            )
            tubi = [o for o in result.offers if o.provider == "tubi"]
            self.assertEqual(len(tubi), 1)
            self.assertTrue(tubi[0].currently_available)
            self.assertFalse(tubi[0].conflicts)


# =============================================================================
# Historical repair
# =============================================================================
class WatchmodeIdentityRepairTests(TestCase):
    def setUp(self):
        self.watchmode, _ = Source.objects.get_or_create(
            slug="watchmode", defaults={"name": "Watchmode", "type": "aggregator"}
        )
        self.tubi, _ = Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        self.netflix, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"}
        )
        self.ca, _ = Country.objects.get_or_create(code="CA", defaults={"name": "Canada"})
        for tmdb_id, title, year in (
            (391, "Hannibal", 2001),
            (5, "Four Rooms", 1995),
            (550, "Fight Club", 1999),
        ):
            Movie.objects.create(tmdb_id=tmdb_id, title=title, year=year,
                                 content_type="movie")

        now = timezone.now()
        first = now - timedelta(days=10)
        self.collapsed = ProviderContent.objects.create(
            provider=self.tubi, source=self.watchmode,
            provider_content_id="wm:296", content_type="movie",
            title="Hannibal", first_seen=first, last_seen=now,
        )
        self.offers = {}
        for tmdb_id, url in ((391, "https://tubi.tv/391"), (5, "https://tubi.tv/5"),
                             (550, "https://tubi.tv/550")):
            self.offers[tmdb_id] = Offer.objects.create(
                provider=self.tubi, country=self.ca, source=self.watchmode,
                content_type="movie", tmdb_id=tmdb_id, offer_type="free",
                provider_content_id="wm:296", provider_url=url,
                first_seen=first, last_seen=now, currently_available=True,
            )
        self.mapping = ContentMapping.objects.create(
            provider=self.tubi, source=self.watchmode,
            provider_content_id="wm:296", content_type="movie", tmdb_id=391,
            content=self.collapsed, confidence=0.95, match_method="external_id",
        )
        self.evidence = OfferEvidence.objects.create(
            source=self.watchmode,
            offer_identity=self.offers[550].identity,
            provider_slug="tubi", country_code="CA", content_type="movie",
            tmdb_id=550, offer_type="free", provider_content_id="wm:296",
            is_present=True,
        )
        self.price = OfferPrice.objects.create(
            source=self.watchmode,
            offer_identity=self.offers[550].identity,
            provider_slug="tubi", country_code="CA", content_type="movie",
            tmdb_id=550, offer_type="free", provider_content_id="wm:296",
            amount="0.00", currency="CAD", observed_at=now,
        )

        # Must never be touched by the repair.
        self.native = ProviderContent.objects.create(
            provider=self.tubi, source=None, provider_content_id="608226",
            content_type="movie", title="Native Tubi entry",
        )
        self.native_offer = Offer.objects.create(
            provider=self.tubi, country=self.ca, source=self.watchmode,
            content_type="movie", tmdb_id=1247, offer_type="free",
            provider_content_id="608226",
        )
        self.other_provider_content = ProviderContent.objects.create(
            provider=self.netflix, source=self.watchmode,
            provider_content_id="tm42409", content_type="movie",
            title="Netflix native",
        )

    # -- plan --------------------------------------------------------------
    def test_plan_splits_every_observed_title(self):
        plan = build_plan()
        self.assertEqual(plan.blocked, ())
        self.assertEqual(plan.counts()["collapsed_rows"], 1)
        self.assertEqual(plan.counts()["offers_to_move"], 3)
        self.assertEqual(plan.counts()["mappings_to_move"], 1)
        self.assertEqual(plan.counts()["mappings_to_create"], 2)
        self.assertEqual(plan.counts()["evidence_rows_to_move"], 1)
        self.assertEqual(plan.counts()["price_rows_to_move"], 1)
        self.assertEqual(
            {c.new_content_id for c in plan.contents},
            {"wm:296:movie:tmdb-391", "wm:296:movie:tmdb-5",
             "wm:296:movie:tmdb-550"},
        )
        self.assertEqual(plan.digest(), build_plan().digest())

    def test_filtered_plan_moves_only_selected_offers_and_retains_the_rest(self):
        selected = {self.offers[5].id, self.offers[550].id}
        plan = build_plan(include_offer_ids=selected)

        self.assertEqual(plan.blocked, ())
        self.assertEqual(plan.counts()["offers_to_move"], 2)
        self.assertEqual(plan.counts()["offers_retained"], 1)
        self.assertEqual(plan.counts()["rows_retained"], 1)
        self.assertEqual(plan.retire, ())
        self.assertEqual(
            {content.new_content_id for content in plan.contents},
            {"wm:296:movie:tmdb-5", "wm:296:movie:tmdb-550"},
        )
        self.assertEqual(len(plan.retained), 1)
        self.assertEqual(plan.retained[0].offer_ids, (self.offers[391].id,))
        self.assertEqual(plan.retained[0].mapping_ids, (self.mapping.id,))
        self.assertNotEqual(plan.digest(), build_plan().digest())

    def test_filtered_apply_leaves_unselected_offers_and_mapping_untouched(self):
        selected = {self.offers[5].id, self.offers[550].id}
        plan = build_plan(include_offer_ids=selected)
        apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)

        self.assertTrue(
            ProviderContent.objects.filter(pk=self.collapsed.pk).exists()
        )
        self.offers[391].refresh_from_db()
        self.assertEqual(self.offers[391].provider_content_id, "wm:296")
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.provider_content_id, "wm:296")
        for tmdb_id in (5, 550):
            self.offers[tmdb_id].refresh_from_db()
            self.assertEqual(
                self.offers[tmdb_id].provider_content_id,
                f"wm:296:movie:tmdb-{tmdb_id}",
            )
            self.assertTrue(ProviderContent.objects.filter(
                provider_content_id=f"wm:296:movie:tmdb-{tmdb_id}"
            ).exists())

        rerun = build_plan(include_offer_ids=selected)
        self.assertEqual(rerun.blocked, ())
        self.assertEqual(rerun.contents, ())
        self.assertEqual(rerun.offers, ())
        self.assertEqual(rerun.retire, ())
        self.assertEqual(len(rerun.retained), 1)

    def test_filtered_plan_can_retire_a_fully_selected_row(self):
        selected = {offer.id for offer in self.offers.values()}
        plan = build_plan(include_offer_ids=selected)
        self.assertEqual(plan.blocked, ())
        self.assertEqual(len(plan.retire), 1)
        self.assertEqual(plan.retained, ())
        apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)
        self.assertFalse(
            ProviderContent.objects.filter(pk=self.collapsed.pk).exists()
        )

    def test_filtered_plan_refuses_unknown_offer_ids(self):
        plan = build_plan(
            include_offer_ids={self.offers[5].id, self.offers[550].id, 999999}
        )
        self.assertTrue(plan.blocked)
        with self.assertRaises(RepairError):
            apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)

    def test_filtered_digest_covers_the_selection(self):
        first = build_plan(include_offer_ids={self.offers[5].id})
        second = build_plan(
            include_offer_ids={self.offers[5].id, self.offers[550].id}
        )
        self.assertNotEqual(first.digest(), second.digest())

    # -- digest ------------------------------------------------------------
    def test_digest_is_stable_across_generation_times(self):
        first = build_plan()
        second = build_plan()
        self.assertEqual(first.digest(), second.digest())

    def test_digest_is_independent_of_unordered_plan_entries(self):
        plan = build_plan()
        self.assertEqual(
            plan.digest(),
            replace(
                plan,
                contents=tuple(reversed(plan.contents)),
                offers=tuple(reversed(plan.offers)),
            ).digest(),
        )

    def test_digest_covers_every_written_field(self):
        plan = build_plan()
        first = plan.contents[0]
        with_first = lambda item: (replace(first, **item),) + plan.contents[1:]
        variants = [
            replace(plan, contents=with_first({"title": "Changed"})),
            replace(plan, contents=with_first({"release_date": date(1900, 1, 1)})),
            replace(plan, contents=with_first({"provider_url": "https://example.test/other"})),
            replace(plan, contents=with_first(
                {"first_seen": first.first_seen - timedelta(days=1)}
            )),
            replace(plan, contents=with_first(
                {"last_seen": first.last_seen + timedelta(days=1)}
            )),
            replace(plan, contents=with_first({"currently_available": False})),
            replace(plan, contents=with_first({"move_mapping_method": "title"})),
            replace(plan, offers=(
                replace(plan.offers[0], old_identity="wm:rival"),
            ) + plan.offers[1:]),
            replace(plan, offers=(
                replace(plan.offers[0], new_identity="wm:296:movie:tmdb-9999"),
            ) + plan.offers[1:]),
            replace(plan, evidence=(
                replace(plan.evidence[0], merge_into_id=999),
            )),
            replace(plan, prices=(
                replace(plan.prices[0], merge_into_id=999),
            )),
            replace(plan, blocked=({"reason": "new ambiguity"},)),
        ]
        for variant in variants:
            self.assertNotEqual(
                variant.digest(), plan.digest(), "material change not hashed"
            )

    # -- guards ------------------------------------------------------------
    def test_apply_requires_digest_and_paused_workers(self):
        plan = build_plan()
        with self.assertRaises(RepairError):
            apply_plan(plan, expected_digest="")
        with self.assertRaises(RepairError):
            apply_plan(plan, expected_digest=plan.digest(), workers_paused=False)

    def test_apply_refuses_a_stale_digest(self):
        plan = build_plan()
        with self.assertRaises(RepairError):
            apply_plan(plan, expected_digest="0" * 64, workers_paused=True)
        self.assertTrue(ProviderContent.objects.filter(pk=self.collapsed.pk).exists())

    def test_apply_refuses_while_a_watchmode_run_is_running(self):
        plan = build_plan()
        SyncRun.objects.create(
            provider=self.tubi, source=self.watchmode, country=self.ca,
            status="running",
        )
        with self.assertRaises(RepairError):
            apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)

    def test_blocked_plan_is_not_applied(self):
        # A collapsed row with neither offers nor a mapping cannot be split
        # without inventing a title; the repair refuses instead.
        ProviderContent.objects.create(
            provider=self.tubi, source=self.watchmode,
            provider_content_id="wm:999", content_type="movie",
        )
        plan = build_plan()
        self.assertTrue(plan.blocked)
        with self.assertRaises(RepairError):
            apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)
        self.assertTrue(ProviderContent.objects.filter(pk=self.collapsed.pk).exists())

    def test_plan_and_apply_honour_a_planned_evidence_and_price_merge(self):
        new_pcid = "wm:296:movie:tmdb-550"
        new_offer_identity = build_offer_identity(
            "tubi", "CA", "movie", 550, None, None, "free", new_pcid
        )
        now = timezone.now()
        existing_evidence = OfferEvidence.objects.create(
            source=self.watchmode,
            offer_identity=new_offer_identity,
            provider_slug="tubi",
            country_code="CA",
            content_type="movie",
            tmdb_id=550,
            offer_type="free",
            provider_content_id=new_pcid,
            is_present=False,
        )
        existing_price = OfferPrice.objects.create(
            source=self.watchmode,
            offer_identity=new_offer_identity,
            provider_slug="tubi",
            country_code="CA",
            content_type="movie",
            tmdb_id=550,
            offer_type="free",
            provider_content_id=new_pcid,
            amount="1.00",
            currency="CAD",
            observed_at=now,
        )
        plan = build_plan()
        evidence_move = next(e for e in plan.evidence if e.evidence_id == self.evidence.pk)
        price_move = next(p for p in plan.prices if p.price_id == self.price.pk)
        self.assertEqual(evidence_move.merge_into_id, existing_evidence.pk)
        self.assertEqual(price_move.merge_into_id, existing_price.pk)

        apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)

        existing_evidence.refresh_from_db()
        self.assertTrue(existing_evidence.is_present)  # presence is OR-ed
        self.assertFalse(OfferEvidence.objects.filter(pk=self.evidence.pk).exists())
        self.assertFalse(OfferPrice.objects.filter(pk=self.price.pk).exists())
        self.assertTrue(OfferPrice.objects.filter(pk=existing_price.pk).exists())

    # -- apply -------------------------------------------------------------
    def test_apply_splits_rows_and_preserves_every_relationship(self):
        plan = build_plan()
        result = apply_plan(
            plan, expected_digest=plan.digest(), workers_paused=True
        )
        self.assertTrue(result["applied"])
        self.assertFalse(ProviderContent.objects.filter(pk=self.collapsed.pk).exists())

        rows = {
            row.provider_content_id: row
            for row in ProviderContent.objects.filter(
                provider=self.tubi, provider_content_id__startswith="wm:"
            )
        }
        self.assertEqual(set(rows), {
            "wm:296:movie:tmdb-391", "wm:296:movie:tmdb-5",
            "wm:296:movie:tmdb-550",
        })
        self.assertEqual(rows["wm:296:movie:tmdb-391"].title, "Hannibal")
        self.assertEqual(rows["wm:296:movie:tmdb-391"].release_date, date(2001, 1, 1))
        self.assertEqual(rows["wm:296:movie:tmdb-550"].title, "Fight Club")
        self.assertEqual(
            rows["wm:296:movie:tmdb-550"].metadata,
            {"external_ids": {"tmdb": "550"}},
        )

        for tmdb_id, offer in self.offers.items():
            offer.refresh_from_db()
            new_id = f"wm:296:movie:tmdb-{tmdb_id}"
            self.assertEqual(offer.provider_content_id, new_id)
            self.assertEqual(offer.identity, build_offer_identity(
                "tubi", "CA", "movie", tmdb_id, None, None, "free", new_id,
            ))

        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.provider_content_id, "wm:296:movie:tmdb-391")
        self.assertEqual(self.mapping.content_id, rows["wm:296:movie:tmdb-391"].pk)
        derived = {
            m.provider_content_id: m
            for m in ContentMapping.objects.filter(
                provider=self.tubi, provider_content_id__in=[
                    "wm:296:movie:tmdb-5", "wm:296:movie:tmdb-550",
                ],
            )
        }
        self.assertEqual({k: v.tmdb_id for k, v in derived.items()},
                         {"wm:296:movie:tmdb-5": 5, "wm:296:movie:tmdb-550": 550})
        for mapping in derived.values():
            self.assertEqual(mapping.confidence, 0.95)
            self.assertEqual(mapping.match_method, "external_id")
            self.assertEqual(mapping.source, self.watchmode)

        self.evidence.refresh_from_db()
        self.assertEqual(self.evidence.offer_identity,
                         self.offers[550].identity)
        self.price.refresh_from_db()
        self.assertEqual(self.price.offer_identity, self.offers[550].identity)

        # Unrelated rows untouched.
        self.native.refresh_from_db()
        self.assertEqual(self.native.provider_content_id, "608226")
        self.native_offer.refresh_from_db()
        self.assertEqual(self.native_offer.provider_content_id, "608226")
        self.other_provider_content.refresh_from_db()
        self.assertEqual(self.other_provider_content.provider_content_id, "tm42409")

    def test_apply_is_idempotent(self):
        plan = build_plan()
        apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)
        second = build_plan()
        self.assertTrue(second.is_empty)
        self.assertFalse(second.blocked)

    def test_split_rows_resolve_without_mapping_doubt(self):
        from availability.services.query import AvailabilityQueryService

        plan = build_plan()
        apply_plan(plan, expected_digest=plan.digest(), workers_paused=True)

        for tmdb_id in (391, 5, 550):
            result = AvailabilityQueryService().get_reconciled_for_content(
                tmdb_id, "movie", "CA"
            )
            tubi = [o for o in result.offers if o.provider == "tubi"]
            self.assertEqual(len(tubi), 1, tmdb_id)
            self.assertTrue(tubi[0].currently_available, tmdb_id)
            self.assertFalse(tubi[0].conflicts, tmdb_id)
