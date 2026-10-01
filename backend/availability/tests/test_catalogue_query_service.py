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
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod, OfferType, SourceType
from availability.domain.models import ProviderContentView
from availability.models import Country, Offer, ProviderContent, Source
from availability.persistence.repositories import (
    MappingRepository,
    OfferRepository,
    ProviderContentRepository,
)
from availability.services.catalogue_query import ProviderCatalogueQueryService
from tracker.models import Provider

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
