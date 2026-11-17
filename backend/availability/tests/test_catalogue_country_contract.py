"""The catalogue country contract: who decides a walk's market, and why.

A catalogue checkpoint is keyed ``(provider, source, country)``, so the country
a walk is attributed to *is* the checkpoint's identity. Getting it wrong is not
a cosmetic bug — it resumes one market's traversal with another market's
progress, or writes a checkpoint no later run will ever read (the defect this
suite was written after: Pluto TV declared 32 countries, resolved none of them
before fetching, and so never resumed at all).

Two ways a market is decided, and the tests below pin both:

- **configured** — the ordinary case. ``SourceCapabilities.supported_countries``
  narrows the scope, and a single declared country is the default. Every
  first-party collector works this way.
- **declared** — the source can only learn its market from the response
  (``SourceCapabilities.country_from_result``; Pluto reads the egress region).
  The result carries the observed code, and the service attributes the
  checkpoint to *that* market.

The dangerous cases are all about the second path being trusted too far: an
out-of-scope code, a code with no row, or one market's checkpoint applied to
another. Each has a test that asserts the checkpoint is *not* redirected.
"""
from __future__ import annotations

from django.test import TestCase

from availability.domain.enums import SyncMode
from availability.models import Country, Source, SyncCursor
from availability.persistence.repositories import CursorRepository
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceCapabilities, SourceFetchResult
from availability.sources.pluto_tv import PlutoTvClient, PlutoTvSource

from tracker.models import Provider

from .pluto_fixtures import FakePlutoTvApi
from .source_stubs import StubSource


def _catalogue_source(slug, *, countries, from_result=False, provider=None):
    """A minimal catalogue source declaring *countries* and *from_result*.

    ``slug`` is passed through to the stub: it is a class attribute on
    ``StubSource``, so omitting it would register every one of these under
    ``stub_source`` and the assertions below would be looking at the wrong row.
    """
    return StubSource(
        slug=slug,
        name=slug,
        response=SourceFetchResult(sync_mode=SyncMode.FULL),
        capabilities=SourceCapabilities(
            supported_providers=frozenset({provider or slug}),
            supported_countries=frozenset(countries),
            supports_catalogue_sync=True,
            supports_resumable_catalogue_sync=True,
            country_from_result=from_result,
        ),
    )


class DeclaredCountryScopeTests(TestCase):
    """An out-of-scope declaration must never redirect the checkpoint."""

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="pluto_tv", defaults={"name": "Pluto TV"}
        )
        self.source_row, _ = Source.objects.get_or_create(
            slug="declaring_source", defaults={"name": "Declaring"}
        )
        for code in ("US", "GB", "ZZ"):
            Country.objects.get_or_create(code=code, defaults={"name": code})
        # The source declares exactly one market.
        self.source = _catalogue_source("declaring_source", countries={"US"})
        # ... but its result claims another, enabled, existing market.
        self.source.response = SourceFetchResult(
            sync_mode=self.source.response.sync_mode, country="GB"
        )

    def test_a_declaration_outside_the_declared_markets_is_ignored(self):
        service = AvailabilityRefreshService(source=self.source)
        service.refresh_catalogue()
        self.assertFalse(
            SyncCursor.objects.filter(country__code="GB").exists(),
            "an out-of-scope declaration redirected checkpoint persistence",
        )

    def test_a_declaration_for_an_unknown_country_row_is_ignored(self):
        self.source.response = SourceFetchResult(
            sync_mode=self.source.response.sync_mode, country="QQ"
        )
        service = AvailabilityRefreshService(source=self.source)
        service.refresh_catalogue()
        self.assertFalse(SyncCursor.objects.exists())

    def test_a_single_country_source_keeps_its_configured_behaviour(self):
        """The common case: no declaration at all, scope from capabilities."""
        source = _catalogue_source("cbc_like", countries={"CA"})
        source.response = SourceFetchResult(
            sync_mode=source.response.sync_mode,
            cursor='{"v":1,"parts":{"shows":"abc"}}',
        )
        Country.objects.get_or_create(code="CA", defaults={"name": "Canada"})
        Provider.objects.get_or_create(slug="cbc_like", defaults={"name": "CBC"})

        AvailabilityRefreshService(source=source).refresh_catalogue()

        cursor = SyncCursor.objects.get(source__slug="cbc_like")
        self.assertEqual(cursor.country.code, "CA")
        self.assertEqual(cursor.provider.slug, "cbc_like")


class PlutoMarketSelectionTests(TestCase):
    """Pluto selects its market's checkpoint; it is never handed another's."""

    @classmethod
    def setUpTestData(cls):
        cls.provider, _ = Provider.objects.get_or_create(
            slug="pluto_tv", defaults={"name": "Pluto TV"}
        )
        # Seeded explicitly: the ``Source`` row is normally created by the
        # persistence step of a refresh, but these tests pre-write checkpoints
        # *before* any refresh runs.
        cls.source_row, _ = Source.objects.get_or_create(
            slug="pluto_tv", defaults={"name": "Pluto TV"}
        )
        for code in ("US", "GB"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def _walk(self, batch_pages=1):
        api = FakePlutoTvApi(pad_past_end=True)
        source = PlutoTvSource(
            client=PlutoTvClient(requester=api), batch_pages=batch_pages
        )
        service = AvailabilityRefreshService(source=source)
        service.refresh_catalogue()
        return api

    def test_the_checkpoint_lands_on_the_observed_market(self):
        SyncCursor.objects.all().delete()
        self._walk()
        cursor = SyncCursor.objects.get(source__slug="pluto_tv")
        # The fixture's egress region is US, so the checkpoint is US.
        self.assertEqual(cursor.country.code, "US")
        self.assertEqual(cursor.provider.slug, "pluto_tv")

    def test_a_second_run_resumes_instead_of_restarting(self):
        """The regression this suite exists for: resume, not re-walk."""
        SyncCursor.objects.all().delete()
        first = self._walk()
        second = self._walk()
        # The shows hub finished in run 1, so run 2 must not request it again.
        self.assertEqual(
            len(second.requests_matching("carouselData")), 1,
            "a resumed run re-walked a hub it had already completed",
        )

    def test_one_market_cannot_resume_with_another_markets_progress(self):
        """A GB checkpoint must not be applied to a US walk."""
        SyncCursor.objects.all().delete()
        source_row = Source.objects.get(slug="pluto_tv")
        country_gb = Country.objects.get(code="GB")
        CursorRepository().set(
            provider=self.provider, source=source_row, country=country_gb,
            cursor='{"v":1,"parts":{"movies-all-movies":'
                   '"{&quot;start&quot;:48,&quot;after&quot;:&quot;gb-only&quot;}"}}',
        )

        api = self._walk()

        # The US walk ignored the GB checkpoint entirely: it started both hubs
        # from the top rather than resuming from a foreign market's offsets.
        starts = [
            str(v["carouselParams"]["start"])
            for (op, v) in api.requests if op == "carouselData"
        ]
        self.assertEqual(starts, ["0", "0"])
        # And the GB row is untouched — no cross-market write.
        self.assertIn("gb-only", SyncCursor.objects.get(country=country_gb).cursor)

    def test_every_enabled_market_checkpoint_is_offered_to_the_source(self):
        """The service supplies all markets; the source picks one."""
        SyncCursor.objects.all().delete()
        source_row = Source.objects.get(slug="pluto_tv")
        for code in ("US", "GB"):
            CursorRepository().set(
                provider=self.provider, source=source_row,
                country=Country.objects.get(code=code),
                cursor=f'{{"v":1,"parts":{{"movies-all-movies":"{{\\"start\\":0,\\"after\\":\\"{code.lower()}-anchor\\"}}"}}}}',
            )
        api = FakePlutoTvApi(pad_past_end=True)
        source = PlutoTvSource(client=PlutoTvClient(requester=api), batch_pages=1)
        # Direct call with the mapping shape the service provides.
        mapping = {
            "US": CursorRepository().get(
                provider=self.provider, source=source_row,
                country=Country.objects.get(code="US"),
            ).cursor,
            "GB": CursorRepository().get(
                provider=self.provider, source=source_row,
                country=Country.objects.get(code="GB"),
            ).cursor,
        }
        source.fetch(cursor=mapping)
        # It resumed from the US anchor (the observed region), not GB's.
        self.assertNotIn(
            "gb-anchor",
            " ".join(
                str(v.get("carouselParams") or {}) for (_op, v) in api.requests
            ),
        )


class CountryContractCapabilityTests(TestCase):
    """The capability flag itself, and who may set it."""

    def test_only_the_egress_scoped_source_declares_country_from_result(self):
        from availability.sources import source_registry

        declaring = sorted(
            slug for slug in source_registry.slugs()
            if source_registry.get(slug).capabilities.country_from_result
        )
        # Pluto TV is the only collector whose market comes from the response.
        self.assertEqual(declaring, ["pluto_tv"])

    def test_a_country_from_result_source_declares_multiple_markets(self):
        """The flag only makes sense for a multi-market source: a single-market
        source can name its country before fetching and needs no mapping."""
        from availability.sources import source_registry

        caps = source_registry.get("pluto_tv").capabilities
        self.assertTrue(caps.country_from_result)
        self.assertGreater(len(caps.supported_countries or ()), 1)
