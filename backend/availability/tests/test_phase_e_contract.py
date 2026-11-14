"""The five Phase E collectors as one family (contract + one pipeline run).

``youtube``, ``pluto_tv``, ``mediaset_infinity``, ``pbs`` and ``abc_iview``
were added by a single phase behind the existing source contract, so the
generic machinery must treat them purely as data. Two groups of invariants
are asserted here — ones no single-source test can see:

1. **Family contract**: registration, one freshness window, provider
   self-identification, seeded countries/providers, the wrong-call-shape
   rejection, and the capability-driven split between the budgeted per-title
   sweep (targeted) and the whole-catalogue run (catalogue).
2. **One pipeline run**: all five collectors executed back-to-back through
   the real ``AvailabilityRefreshService`` with their fixture APIs, proving
   provenance stays per-source, SyncRun modes match the call shape, and
   five refreshes never duplicate a Provider row.
"""
from __future__ import annotations

import datetime as dt

from django.test import TestCase

from availability.freshness import declared_freshness
from availability.domain.enums import MatchMethod
from availability.models import Country, ContentMapping, Offer, ProviderContent, Source, SyncRun
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import source_registry
from availability.sources.abc_iview import AbcIviewClient, AbcIviewSource
from availability.sources.base import ContentRequest
from availability.domain.enums import SourceType
from availability.sources.mediaset_infinity import (
    MediasetInfinityClient,
    MediasetInfinitySource,
)
from availability.sources.pbs import PbsClient, PbsSource
from availability.sources.pluto_tv import PlutoTvClient, PlutoTvSource
from availability.sources.youtube import YouTubeClient, YouTubeSource
from tracker.models import Movie, Provider
from tracker.services.bulk_refresh import refreshable_source_slugs

from .abc_iview_fixtures import FakeAbcIviewApi
from .mediaset_fixtures import FakeMediasetApi
from .pbs_fixtures import FakePbsApi
from .pluto_fixtures import FakePlutoTvApi
from .youtube_fixtures import FakeYouTubeApi

FAMILY = ("youtube", "pluto_tv", "mediaset_infinity", "pbs", "abc_iview")
#: Capability-driven split: targeted sources run per-title inside the
#: budgeted sweep, catalogue sources run as whole-catalogue refreshes.
TARGETED_FAMILY = frozenset({"youtube", "mediaset_infinity"})
CATALOGUE_FAMILY = frozenset({"pluto_tv", "pbs", "abc_iview"})

#: Titles the fixture APIs actually answer for, with their tracker rows.
KAROL_TITLE = "Karol un uomo diventato Papa"


def make_family_source(slug: str):
    """The fixture-wired instance each source's own test module uses."""
    if slug == "youtube":
        api = FakeYouTubeApi()
        return YouTubeSource(client=YouTubeClient(http_get=api.get, http_post=api.post))
    if slug == "mediaset_infinity":
        api = FakeMediasetApi()
        return MediasetInfinitySource(client=MediasetInfinityClient(http_post=api.post))
    if slug == "pluto_tv":
        return PlutoTvSource(client=PlutoTvClient(requester=FakePlutoTvApi()))
    if slug == "pbs":
        return PbsSource(client=PbsClient(http_post=FakePbsApi().post))
    if slug == "abc_iview":
        return AbcIviewSource(
            client=AbcIviewClient(requester=FakeAbcIviewApi(), delay=0.0)
        )
    raise AssertionError(f"No fixture wiring for {slug!r}")


class FamilyContractTests(TestCase):
    """Invariants every Phase E collector owes the framework."""

    def test_every_family_member_is_registered(self):
        for slug in FAMILY:
            source = source_registry.get(slug)
            self.assertEqual(source.slug, slug)
            self.assertTrue(source.name)
            # YouTube reads the innertube web surface (documented in E.1);
            # the other four speak documented JSON APIs.
            self.assertIn(
                source.source_type, (SourceType.API, SourceType.WEB), slug
            )

    def test_the_family_shares_one_freshness_window(self):
        for slug in FAMILY:
            self.assertEqual(declared_freshness(slug), dt.timedelta(hours=24))

    def test_capabilities_name_the_family_member_as_its_only_provider(self):
        for slug in FAMILY:
            caps = source_registry.get(slug).capabilities
            self.assertEqual(caps.supported_providers, frozenset({slug}))
            self.assertTrue(caps.supported_content_types)

    def test_every_declared_country_has_a_seeded_row(self):
        for slug in FAMILY:
            caps = source_registry.get(slug).capabilities
            missing = set(caps.supported_countries) - set(
                Country.objects.values_list("code", flat=True)
            )
            self.assertEqual(missing, set(), f"{slug} declares unseeded countries")

    def test_every_declared_provider_has_a_seeded_row(self):
        for slug in FAMILY:
            caps = source_registry.get(slug).capabilities
            for provider_slug in caps.supported_providers:
                self.assertTrue(
                    Provider.objects.filter(slug=provider_slug).exists(),
                    f"{slug} declares unseeded provider {provider_slug!r}",
                )

    def test_the_wrong_call_shape_is_rejected_without_any_request(self):
        for slug in sorted(TARGETED_FAMILY):
            with self.assertRaises(ValueError):
                make_family_source(slug).fetch(None)
        request = ContentRequest(
            tmdb_id=1, content_type="movie", title="Some Movie", year=2020
        )
        for slug in sorted(CATALOGUE_FAMILY):
            with self.assertRaises(ValueError):
                make_family_source(slug).fetch(request)

    def test_sweep_classification_follows_the_capability_not_the_slug(self):
        refreshable = set(refreshable_source_slugs())
        self.assertEqual(refreshable & set(FAMILY), TARGETED_FAMILY)
        for slug in CATALOGUE_FAMILY:
            self.assertNotIn(slug, refreshable)

    def test_catalogue_members_refuse_to_be_authoritative_or_incremental(self):
        for slug in CATALOGUE_FAMILY:
            caps = source_registry.get(slug).capabilities
            self.assertFalse(caps.supports_incremental_sync)
            self.assertFalse(caps.supports_authoritative_snapshots)
            self.assertFalse(caps.supports_tmdb_ids)
            self.assertTrue(caps.supports_catalogue_sync)


class OnePipelineRunTests(TestCase):
    """All five collectors through the real refresh service, back-to-back."""

    def setUp(self):
        for slug in FAMILY:
            Provider.objects.get_or_create(slug=slug, defaults={"name": slug})
        for code, name in (("US", "United States"), ("IT", "Italy"),
                           ("AU", "Australia")):
            Country.objects.get_or_create(code=code, defaults={"name": name})
        # One tracker title per fixture API that can resolve it; the rest of
        # each catalogue stays unresolved on purpose (skip path covered too).
        self.movies = [
            Movie.objects.create(  # youtube fixture
                tmdb_id=460, title="Matilda", year=1996, content_type="movie"
            ),
            Movie.objects.create(  # mediaset fixture
                tmdb_id=64997, title=KAROL_TITLE, year=2005, content_type="movie"
            ),
            Movie.objects.create(  # pluto fixtures
                tmdb_id=941, title="Friday", year=1995, content_type="movie"
            ),
            Movie.objects.create(
                tmdb_id=1403, title="Arrow", year=2012, content_type="series"
            ),
            Movie.objects.create(  # pbs fixtures
                tmdb_id=45612, title="Nature", year=2000, content_type="series"
            ),
            Movie.objects.create(  # abc fixtures
                tmdb_id=99101, title="Culture By Design", year=2026,
                content_type="series",
            ),
        ]
        mediaset_provider = Provider.objects.get(slug="mediaset_infinity")
        mediaset_source, _ = Source.objects.get_or_create(
            slug="mediaset_infinity", defaults={"name": "Mediaset Infinity"}
        )
        ContentMapping.objects.create(
            provider=mediaset_provider,
            source=mediaset_source,
            provider_content_id="F011844503000101",
            content_type="movie",
            tmdb_id=64997,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

    def _run_all_five(self):
        results = {}
        for slug in FAMILY:
            source = make_family_source(slug)
            service = AvailabilityRefreshService(source=source)
            if slug in TARGETED_FAMILY:
                movie = {
                    "youtube": self.movies[0],
                    "mediaset_infinity": self.movies[1],
                }[slug]
                results[slug] = service.refresh_content(movie)
            else:
                results[slug] = service.refresh_catalogue()
        return results

    def test_five_refreshes_keep_their_provenance_and_sync_runs(self):
        self._run_all_five()

        # One Source row per collector, all enabled.
        sources = Source.objects.filter(slug__in=FAMILY)
        self.assertEqual(sources.count(), len(FAMILY))
        self.assertTrue(all(source.enabled for source in sources))

        # Offers are distinguishable by source — provenance never merged.
        by_source: dict[str, int] = {}
        for offer in Offer.objects.all():
            by_source[offer.source.slug] = by_source.get(offer.source.slug, 0) + 1
        self.assertEqual(set(by_source), set(FAMILY))
        self.assertEqual(
            by_source,
            {
                "youtube": 1,
                "mediaset_infinity": 1,
                "pluto_tv": 2,      # Friday + Arrow (rest of the grid unmapped)
                "pbs": 6,           # Nature: series + 2 seasons + 3 episodes
                "abc_iview": 6,     # Culture By Design: series + 2 seasons + 3 eps
            },
        )

        # One SyncRun per refresh, mode by call shape, never authoritative.
        runs = SyncRun.objects.filter(source__slug__in=FAMILY)
        self.assertEqual(runs.count(), len(FAMILY))
        self.assertEqual(
            {run.source.slug for run in runs}, set(FAMILY)
        )
        modes = {run.source.slug: run.mode for run in runs}
        for slug in TARGETED_FAMILY:
            self.assertEqual(modes[slug], "targeted", slug)
        for slug in CATALOGUE_FAMILY:
            self.assertEqual(modes[slug], "full", slug)
        self.assertTrue(all(not run.authoritative for run in runs))
        self.assertTrue(all(run.status == "succeeded" for run in runs))

    def test_five_refreshes_never_duplicate_a_provider_row(self):
        self._run_all_five()
        for slug in FAMILY:
            self.assertEqual(
                Provider.objects.filter(slug=slug).count(), 1, slug
            )

    def test_unmapped_catalogue_rows_still_get_provider_content(self):
        self._run_all_five()
        # ProviderContent is per discovered series regardless of mapping:
        # PBS keeps nova/frontline, ABC keeps 7-30 alongside the mapped rows.
        content_ids = {
            row.provider_content_id
            for row in ProviderContent.objects.filter(
                provider__slug__in=CATALOGUE_FAMILY
            )
        }
        self.assertIn("nova", content_ids)
        self.assertIn("7-30", content_ids)
        self.assertIn("nature", content_ids)
        self.assertIn("culture-by-design", content_ids)

    def test_results_report_unresolved_instead_of_failing_the_run(self):
        results = self._run_all_five()
        # Catalogue runs with deliberately unseeded titles resolve partially.
        self.assertGreater(results["pbs"].unresolved, 0)
        self.assertGreater(results["abc_iview"].unresolved, 0)
        self.assertGreater(results["pluto_tv"].unresolved, 0)
        # Targeted runs resolve their one fixture title completely.
        for slug in sorted(TARGETED_FAMILY):
            self.assertEqual(results[slug].unresolved, 0, slug)
