"""Provider catalogue query boundary (Phase 8): local reads only.

Proves the read path

    application code -> ProviderCatalogueQueryService
        -> ProviderContentRepository -> local database

answers from the database alone:

- mapped and unresolved rows are both first-class results (``tmdb_id=None``
  is data, not an error);
- offer presence is summarized (``has_offers``/``offer_count``) and never
  inferred from — or written back to — ``currently_available``;
- no source, HTTP client, TMDb/Streaming Availability client, or refresh
  service is reachable from a query: the tests below fail if one ever is.
"""
from __future__ import annotations

import ast
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType, MatchMethod, OfferType, SourceType
from availability.domain.models import ProviderContentView, UnofficialCatalogueEntry
from availability.models import (
    LEGACY_SCRAPER_SOURCE_SLUG,
    Country,
    Offer,
    ProviderContent,
    Source,
)
from availability.persistence.repositories import (
    MappingRepository,
    OfferRepository,
    ProviderContentRepository,
)
from availability.services.catalogue_query import ProviderCatalogueQueryService
from tracker.models import Provider, ProviderCategory

BACKEND_DIR = Path(__file__).resolve().parents[2]


class CatalogueQueryTestCase(TestCase):
    def setUp(self):
        self.cbc, _ = Provider.objects.get_or_create(
            slug="cbc_gem", defaults={"name": "CBC Gem"}
        )
        self.prime, _ = Provider.objects.get_or_create(
            slug="prime-video", defaults={"name": "Prime Video"}
        )
        self.source, _ = Source.objects.get_or_create(
            slug="cbc-gem-api", defaults={"name": "CBC API", "type": SourceType.API}
        )
        self.country, _ = Country.objects.get_or_create(
            code="CA", defaults={"name": "Canada"}
        )
        self.contents = ProviderContentRepository()
        self.mappings = MappingRepository()
        self.offers = OfferRepository()
        self.service = ProviderCatalogueQueryService()

    def _content(self, provider_content_id, *, provider=None,
                 content_type=ContentType.MOVIE, title=None, **overrides):
        payload = {
            "provider": provider or self.cbc,
            "source": self.source,
            "provider_content_id": provider_content_id,
            "content_type": content_type,
            "title": provider_content_id if title is None else title,
        }
        payload.update(overrides)
        row, _created = self.contents.upsert(**payload)
        return row

    def _mapping(self, provider_content_id, tmdb_id, *, provider=None,
                 content_type=ContentType.MOVIE):
        return self.mappings.record(
            provider=provider or self.cbc,
            source=self.source,
            provider_content_id=provider_content_id,
            content_type=content_type,
            tmdb_id=tmdb_id,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

    def _offer(self, provider_content_id, *, provider=None, tmdb_id=550,
               content_type=ContentType.MOVIE, currently_available=True,
               season_number=None, episode_number=None):
        offer, _created = self.offers.upsert(
            provider=provider or self.cbc,
            country=self.country,
            source=self.source,
            content_type=content_type,
            tmdb_id=tmdb_id,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id=provider_content_id,
        )
        if not currently_available:
            Offer.objects.filter(pk=offer.pk).update(currently_available=False)
        return offer


class ProviderCatalogueQueryServiceTests(CatalogueQueryTestCase):
    # ------------------------------------------------------------------- get

    def test_get_returns_the_mapped_entry(self):
        self._content("beirut", title="Beirut")
        self._mapping("beirut", tmdb_id=550)

        view = self.service.get("cbc_gem", "beirut", ContentType.MOVIE)

        self.assertIsNotNone(view)
        self.assertEqual(view.provider, "cbc_gem")
        self.assertEqual(view.provider_content_id, "beirut")
        self.assertEqual(view.content_type, ContentType.MOVIE)
        self.assertEqual(view.title, "Beirut")
        self.assertEqual(view.tmdb_id, 550)
        self.assertTrue(view.currently_available)
        self.assertIsNotNone(view.first_seen)
        self.assertIsNotNone(view.last_seen)

    def test_get_returns_unresolved_entry_as_a_first_class_result(self):
        self._content("coronation-street", content_type=ContentType.SERIES,
                      title="Coronation Street")

        view = self.service.get("cbc_gem", "coronation-street", ContentType.SERIES)

        self.assertIsNotNone(view)
        self.assertIsNone(view.tmdb_id)
        self.assertEqual(view.title, "Coronation Street")
        self.assertFalse(view.has_offers)
        self.assertEqual(view.offer_count, 0)

    def test_get_is_none_for_unknown_identity_unknown_provider_or_wrong_slot(self):
        self._content("show", content_type=ContentType.SERIES)
        self._content("show", content_type=ContentType.SEASON, season_number=1)

        self.assertIsNone(
            self.service.get("cbc_gem", "missing", ContentType.MOVIE)
        )
        self.assertIsNone(
            self.service.get("unknown-provider", "show", ContentType.SERIES)
        )
        self.assertIsNone(
            self.service.get("cbc_gem", "show", ContentType.SEASON, season_number=2)
        )
        self.assertIsNotNone(
            self.service.get("cbc_gem", "show", ContentType.SEASON, season_number=1)
        )

    def test_get_requires_a_content_type(self):
        with self.assertRaisesMessage(ValueError, "content_type is required"):
            self.service.get("cbc_gem", "beirut", None)

    # ------------------------------------------------------------------ list

    def test_list_exposes_offer_presence_without_duplicating_offers(self):
        self._content("beirut", title="Beirut")
        self._content("float", title="Float")
        self._content("stale", title="Stale")
        self._mapping("beirut", tmdb_id=550)
        self._offer("beirut", tmdb_id=550)
        self._offer("beirut", content_type=ContentType.SEASON, season_number=1,
                    tmdb_id=550)
        self._offer("stale", tmdb_id=551, currently_available=False)

        views = {view.provider_content_id: view
                 for view in self.service.list(provider="cbc_gem")}

        self.assertEqual(set(views), {"beirut", "float", "stale"})
        # Presence summarises the offers; the view never carries Offer fields.
        self.assertEqual(views["beirut"].offer_count, 2)
        self.assertTrue(views["beirut"].has_offers)
        self.assertFalse(hasattr(views["beirut"], "offer_type"))
        # Presence counts *active* offers only — a deactivated offer is not
        # current presence, and it never rewrites the catalogue row.
        self.assertFalse(views["stale"].has_offers)
        self.assertTrue(views["stale"].currently_available)
        self.assertFalse(views["float"].has_offers)

    def test_list_filters_by_provider_slug(self):
        self._content("123", title="CBC Title")
        self._content("123", title="Prime Title", provider=self.prime)

        views = self.service.list(provider="cbc_gem", provider_content_id="123")
        self.assertEqual([view.title for view in views], ["CBC Title"])
        self.assertEqual(
            [view.title for view in self.service.list(provider="prime-video")],
            ["Prime Title"],
        )
        # Unknown slug behaves like an unknown country code: empty, not an error.
        self.assertEqual(self.service.list(provider="no-such-provider"), [])

    def test_list_filters_by_content_type_and_availability(self):
        self._content("story", content_type=ContentType.SERIES, title="Story")
        self._content("story", content_type=ContentType.SEASON, season_number=1,
                      title="Story S1")
        gone = self._content("gone", title="Gone")
        ProviderContent.objects.filter(pk=gone.pk).update(currently_available=False)

        # Identity order: cbc_gem|gone|movie|| < cbc_gem|story|season|1| <
        # cbc_gem|story|series||.
        self.assertEqual(
            [view.content_type for view in self.service.list(provider="cbc_gem")],
            [ContentType.MOVIE, ContentType.SEASON, ContentType.SERIES],
        )
        self.assertEqual(
            [view.title for view in self.service.list(
                provider="cbc_gem", content_type=ContentType.SEASON
            )],
            ["Story S1"],
        )
        self.assertEqual(
            [view.title for view in self.service.list(
                provider="cbc_gem", currently_available=False
            )],
            ["Gone"],
        )
        self.assertEqual(
            [view.title for view in self.service.list(
                provider="cbc_gem", currently_available=True
            )],
            ["Story S1", "Story"],
        )

    def test_list_returns_an_empty_result_for_an_empty_catalogue(self):
        self.assertEqual(self.service.list(), [])

    # ---------------------------------------------------------------- search

    def test_search_is_case_insensitive_partial_and_deterministic(self):
        self._content("beirut", title="Beirut")
        self._content("other", title="A Beiruti Summer", provider=self.prime)
        self._content("float", title="Float")

        views = self.service.search("BEIRU")
        self.assertEqual(
            [view.title for view in views],
            ["A Beiruti Summer", "Beirut"],
        )
        self.assertEqual(
            [view.title for view in self.service.search("beiruti", provider="cbc_gem")],
            [],
        )
        self.assertEqual(
            [view.title for view in self.service.search("float",
                                                        content_type=ContentType.MOVIE)],
            ["Float"],
        )
        self.assertEqual(self.service.search("nothing-matches"), [])

    def test_search_rejects_blank_terms(self):
        for term in ["", "   ", None]:
            with self.subTest(term=term):
                with self.assertRaisesMessage(ValueError, "must not be empty"):
                    self.service.search(term)

    # ------------------------------------------------------------ validation

    def test_invalid_content_type_raises_for_every_operation(self):
        self._content("beirut", title="Beirut")
        calls = [
            lambda: self.service.get("cbc_gem", "beirut", "trailer"),
            lambda: self.service.list(content_type="trailer"),
            lambda: self.service.search("beirut", content_type="trailer"),
        ]
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesMessage(ValueError, "Invalid content_type"):
                    call()
        # The optional filter of list()/search() stays optional.
        self.assertIsInstance(self.service.list(content_type=None), list)

    # ----------------------------------------------------------- determinism

    def test_results_are_deterministically_ordered(self):
        for provider_content_id in ["zeta", "alpha", "mid"]:
            self._content(provider_content_id, title=provider_content_id.title())

        first = [view.provider_content_id for view in self.service.list()]
        second = [view.provider_content_id for view in self.service.list()]
        self.assertEqual(first, second)
        self.assertEqual(second, ["alpha", "mid", "zeta"])
        self.assertEqual(
            [view.provider_content_id for view in self.service.search("a")],
            ["alpha", "zeta"],
        )

    def test_views_are_not_model_instances(self):
        self._content("beirut", title="Beirut")

        view = self.service.get("cbc_gem", "beirut", ContentType.MOVIE)

        self.assertIsInstance(view, ProviderContentView)
        self.assertFalse(hasattr(view, "_meta"))

    def test_list_reads_in_a_fixed_number_of_queries(self):
        for i in range(5):
            self._content(f"item-{i}", title=f"Item {i}")
            self._mapping(f"item-{i}", tmdb_id=1000 + i)
            self._offer(f"item-{i}", tmdb_id=1000 + i)

        # rows + one bulk mapping lookup + one bulk offer lookup: a fixed
        # number of queries no matter how many catalogue entries are read.
        with self.assertNumQueries(3):
            views = self.service.list()
        self.assertEqual(len(views), 5)
        self.assertEqual([view.tmdb_id for view in views],
                         [1000, 1001, 1002, 1003, 1004])

        with self.assertNumQueries(1):
            self.assertEqual(self.service.list(provider_content_id="missing"), [])


class ProviderCatalogueReadBoundaryTests(CatalogueQueryTestCase):
    """A catalogue query can never reach a source, HTTP client, or refresh."""

    def test_query_service_never_calls_sources_http_tmdb_or_refresh(self):
        self._content("beirut", title="Beirut")
        self._mapping("beirut", tmdb_id=550)
        self._offer("beirut", tmdb_id=550)
        self._content("float", title="Float")

        seams = [
            "requests.get",
            "requests.post",
            "requests.sessions.Session.request",
            "availability.services.refresh.AvailabilityRefreshService.refresh_content",
            "availability.services.refresh.AvailabilityRefreshService.refresh_catalogue",
            "tracker.services.streaming_api.StreamingAvailabilityClient.get_availability",
            "tracker.services.tmdb.TMDbClient.search",
            "tracker.services.tmdb.TMDbClient.details",
            "availability.sources.cbc_gem.CBCGemSource.fetch",
            "availability.sources.streaming_availability.StreamingAvailabilitySource.fetch",
        ]
        guards = [
            patch(seam, side_effect=AssertionError(f"query path called {seam}"))
            for seam in seams
        ]
        with ExitStack() as stack:
            spies = [stack.enter_context(guard) for guard in guards]

            self.service.get("cbc_gem", "beirut", ContentType.MOVIE)
            self.service.list(provider="cbc_gem")
            self.service.search("beiru", provider="cbc_gem")

        for seam, spy in zip(seams, spies):
            with self.subTest(seam=seam):
                spy.assert_not_called()

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        package_parts = path.relative_to(BACKEND_DIR).with_suffix("").parts[:-1]
        tree = ast.parse(path.read_text())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                modules.add(".".join((*base, node.module)))
        return modules

    def test_query_modules_import_no_network_source_or_refresh_module(self):
        """Structural guard: fails when a network-backed module is imported.

        The runtime test above only trips when a guarded seam is actually
        executed; this one trips the moment the import graph of the read path
        reaches a source, HTTP client, or refresh/cache module.
        """
        forbidden = (
            "requests",
            "httpx",
            "urllib.request",
            "availability.sources",
            "availability.services.refresh",
            "tracker.services.cache",
            "tracker.services.streaming_api",
            "tracker.services.streaming_availability",
            "tracker.services.tmdb",
        )
        query_path = [
            BACKEND_DIR / "availability" / "services" / "catalogue_query.py",
            BACKEND_DIR / "availability" / "persistence" / "repositories.py",
        ]
        for path in query_path:
            imported = self._imported_modules(path)
            offenders = [
                module
                for module in imported
                if any(
                    module == bad or module.startswith(bad + ".")
                    for bad in forbidden
                )
            ]
            with self.subTest(path=path.name):
                self.assertEqual(offenders, [], f"{path.name} imports {offenders}")


class ListUnofficialTests(CatalogueQueryTestCase):
    """``list_unofficial``: the canonical read behind the unified catalogue.

    This is the query that replaced ``ScrapedContentMapping`` reads in the
    tracker: it selects only unofficial observations (scraper-category
    providers, or anything attributed to the legacy ingestion bridge),
    applies the endpoint's filters, and enriches each row with its mapping
    provenance — all from local tables, in a fixed number of queries.
    """

    def setUp(self):
        super().setUp()
        self.hydra, _ = Provider.objects.get_or_create(
            slug="hydra",
            defaults={
                "name": "Hydra",
                "category": ProviderCategory.SCRAPER,
            },
        )
        self.legacy, _ = Source.objects.get_or_create(
            slug=LEGACY_SCRAPER_SOURCE_SLUG,
            defaults={"name": "Legacy scraper (ingested)", "type": SourceType.WEB},
        )

    def _row(self, provider_content_id, *, provider=None, source=None, **overrides):
        return self._content(
            provider_content_id,
            provider=provider or self.hydra,
            source=source or self.legacy,
            **overrides,
        )

    def _resolve(self, provider_content_id, tmdb_id, *, confidence=1.0,
                 match_method=MatchMethod.TITLE_YEAR):
        return self.mappings.record(
            provider=self.hydra,
            source=self.legacy,
            provider_content_id=provider_content_id,
            content_type=ContentType.MOVIE,
            tmdb_id=tmdb_id,
            confidence=confidence,
            match_method=match_method,
        )

    def test_predicate_selects_unofficial_observations_only(self):
        self._row("hydra-row")
        # The bridge also attributes legacy rows to non-scraper providers.
        self._row("cbc-legacy", provider=self.cbc, source=self.legacy)
        # A native source's row on an official provider is not unofficial.
        self._row("cbc-native", provider=self.cbc, source=self.source)
        # ...while a scraper-category provider counts whatever wrote the row.
        self._row("hydra-native", source=self.source)

        disabled = Provider.objects.create(
            slug="hydra-off", name="Disabled", category=ProviderCategory.SCRAPER,
            is_enabled=False,
        )
        self._row("disabled-row", provider=disabled)

        gone = self._row("gone-row")
        ProviderContent.objects.filter(pk=gone.pk).update(
            currently_available=False
        )

        entries = self.service.list_unofficial()

        self.assertEqual(
            {entry.provider_content_id for entry in entries},
            {"hydra-row", "cbc-legacy", "hydra-native"},
        )

    def test_filters_by_provider_search_country_and_year(self):
        Provider.objects.filter(pk=self.hydra.pk).update(country_code="CA")
        self._row(
            "row-a", title="Alpha Quest", release_date=date(2001, 1, 1),
            metadata={"country_code": "GB"},
        )
        self._row("row-b", title="Beta Chronicle", release_date=date(2020, 6, 1))

        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(search="ALPHA")},
            {"row-a"},
        )
        # Row-level country and provider-level country both match.
        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(country="GB")},
            {"row-a"},
        )
        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(country="CA")},
            {"row-a", "row-b"},
        )
        self.assertEqual(self.service.list_unofficial(country="US"), [])

        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(year_min=2010)},
            {"row-b"},
        )
        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(year_max=2010)},
            {"row-a"},
        )

        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(providers={"hydra"})},
            {"row-a", "row-b"},
        )
        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(providers={"cbc_gem"})},
            set(),
        )
        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial(providers=set())},
            set(),
        )

    def test_exclude_tmdb_ids_drops_resolved_but_keeps_unresolved(self):
        self._row("unresolved")
        self._row("resolved")
        self._row("other")
        self._resolve("resolved", 100)
        self._resolve("other", 200)

        self.assertEqual(
            {e.provider_content_id for e in self.service.list_unofficial()},
            {"unresolved", "resolved", "other"},
        )
        self.assertEqual(
            {
                e.provider_content_id
                for e in self.service.list_unofficial(exclude_tmdb_ids={100})
            },
            {"unresolved", "other"},
        )

    def test_entries_carry_provenance_and_normalized_metadata(self):
        self._row(
            "enriched",
            title="Enriched",
            release_date=date(1999, 3, 4),
            provider_url="https://hydra.example/enriched",
            metadata={
                "poster": "/p.jpg",
                "audio_languages": ["en"],
                "subtitle_languages": ["ES", "es"],
                "country_code": "gb",
            },
        )
        self._resolve("enriched", 42, confidence=0.65,
                      match_method=MatchMethod.MANUAL)

        [entry] = self.service.list_unofficial()

        self.assertIsInstance(entry, UnofficialCatalogueEntry)
        self.assertEqual(entry.provider, "hydra")
        self.assertEqual(entry.provider_name, "Hydra")
        self.assertEqual(entry.provider_category, ProviderCategory.SCRAPER)
        self.assertEqual(entry.source_indicator, "Scraper")
        self.assertEqual(entry.content_type, ContentType.MOVIE)
        self.assertEqual(entry.title, "Enriched")
        self.assertEqual(entry.year, 1999)
        self.assertEqual(entry.poster_path, "/p.jpg")
        self.assertEqual(entry.provider_url, "https://hydra.example/enriched")
        self.assertEqual(entry.tmdb_id, 42)
        self.assertEqual(entry.confidence, 0.65)
        self.assertEqual(entry.match_method, MatchMethod.MANUAL)
        self.assertEqual(entry.audio_languages, ["EN"])
        self.assertEqual(entry.subtitle_languages, ["ES"])

    def test_unresolved_entries_report_no_mapping_outcome(self):
        self._row("bare", release_date=None)

        [entry] = self.service.list_unofficial()

        self.assertIsNone(entry.tmdb_id)
        self.assertIsNone(entry.confidence)
        self.assertEqual(entry.match_method, "")
        self.assertIsNone(entry.year)

    def test_results_are_deterministically_ordered(self):
        row_a = self._row("row-a")
        self._row("row-b")
        row_c = self._row("row-c")

        now = timezone.now()
        ProviderContent.objects.filter(pk=row_a.pk).update(
            updated_at=now - timedelta(days=2)
        )
        ProviderContent.objects.filter(pk=row_c.pk).update(
            updated_at=now - timedelta(days=1)
        )

        # Newest observation first, stable under equal timestamps.
        self.assertEqual(
            [e.provider_content_id for e in self.service.list_unofficial()],
            ["row-b", "row-c", "row-a"],
        )

        ProviderContent.objects.filter(pk__in=[row_a.pk, row_c.pk]).update(
            updated_at=now - timedelta(days=5)
        )
        self.assertEqual(
            [e.provider_content_id for e in self.service.list_unofficial()],
            ["row-b", "row-a", "row-c"],
        )

    def test_list_unofficial_reads_in_two_queries(self):
        self._row("q-1")
        self._row("q-2")
        self._resolve("q-1", 7)

        # rows + one bulk mapping lookup; entry building issues none.
        with self.assertNumQueries(2):
            entries = self.service.list_unofficial()
        self.assertEqual(len(entries), 2)

    def test_empty_catalogue_returns_no_entries(self):
        self.assertEqual(self.service.list_unofficial(), [])

    def test_corrupt_mediaset_external_id_is_hidden_from_tmdb_catalogue(self):
        mediaset, _ = Provider.objects.update_or_create(
            slug="mediaset_infinity", defaults={
                "name": "Mediaset Infinity", "category": ProviderCategory.SCRAPER,
                "is_enabled": True,
            },
        )
        source, _ = Source.objects.get_or_create(
            slug="mediaset_infinity", defaults={"name": "Mediaset"}
        )
        row = self._row(
            "dune-guid", provider=mediaset, source=source, title="Dune",
        )
        ProviderContent.objects.filter(pk=row.pk).update(currently_available=True)
        self.mappings.record(
            provider=mediaset, source=source, provider_content_id=row.provider_content_id,
            content_type=ContentType.MOVIE, tmdb_id=438631, confidence=0.95,
            match_method=MatchMethod.EXTERNAL_ID, content=row,
        )

        self.assertEqual(self.service.list_for_tmdb(438631), [])
        self.assertEqual(self.service.list_unofficial(), [])

    def test_youtube_synthetic_external_mapping_stays_unresolved_in_legacy_reads(self):
        youtube, _ = Provider.objects.update_or_create(
            slug="youtube", defaults={
                "name": "YouTube", "category": ProviderCategory.SCRAPER,
                "is_enabled": True,
            },
        )
        youtube_source, _ = Source.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube"}
        )
        row = self._row(
            "youtube-video", provider=youtube, source=self.legacy, title="Matilda",
        )
        self.mappings.record(
            provider=youtube, source=youtube_source,
            provider_content_id=row.provider_content_id,
            content_type=ContentType.MOVIE, tmdb_id=460, confidence=0.95,
            match_method=MatchMethod.EXTERNAL_ID, content=row,
        )

        [entry] = self.service.list_unofficial()

        self.assertEqual(entry.provider_content_id, "youtube-video")
        self.assertIsNone(entry.tmdb_id)
        self.assertEqual(self.service.list_for_tmdb(460), [])

    def test_mediaset_manual_and_aggregator_external_id_mappings_remain_visible(self):
        mediaset, _ = Provider.objects.update_or_create(
            slug="mediaset_infinity", defaults={
                "name": "Mediaset Infinity", "category": ProviderCategory.SCRAPER,
                "is_enabled": True,
            },
        )
        mediaset_source, _ = Source.objects.get_or_create(
            slug="mediaset_infinity", defaults={"name": "Mediaset"}
        )
        manual = self._row(
            "manual-guid", provider=mediaset, source=mediaset_source, title="Dune",
        )
        ProviderContent.objects.filter(pk=manual.pk).update(currently_available=True)
        self.mappings.record(
                provider=mediaset, source=Source.objects.get_or_create(
                    slug="operator_review", defaults={"name": "Operator review"}
                )[0], provider_content_id=manual.provider_content_id,
            content_type=ContentType.MOVIE, tmdb_id=438631, confidence=1.0,
            match_method=MatchMethod.MANUAL, content=manual,
        )
        justwatch, _ = Provider.objects.update_or_create(
            slug="justwatch", defaults={
                "name": "JustWatch", "category": ProviderCategory.SCRAPER,
                "is_enabled": True,
            }
        )
        jw_source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"}
        )
        jw = self._row(
            "justwatch-guid", provider=justwatch, source=jw_source,
        )
        ProviderContent.objects.filter(pk=jw.pk).update(currently_available=True)
        self.mappings.record(
            provider=justwatch, source=jw_source,
            provider_content_id=jw.provider_content_id,
            content_type=ContentType.MOVIE, tmdb_id=438631, confidence=0.99,
            match_method=MatchMethod.EXTERNAL_ID, content=jw,
        )

        entries = self.service.list_for_tmdb(438631)

        self.assertEqual(
            {entry.provider_content_id for entry in entries},
            {"manual-guid", "justwatch-guid"},
        )
