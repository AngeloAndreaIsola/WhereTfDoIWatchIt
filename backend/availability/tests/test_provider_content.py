"""Provider-native content persistence (Phase 7): unresolved != discarded.

End-to-end path (fixture CBC API, zero network):

    FakeCbcApi -> CBCGemSource.fetch() -> ProviderContent upsert ->
    local title/year matching -> ContentMapping (linked) -> OfferRepository

Fixture totals (see ``test_cbc_gem_source``): 5 catalogue contents, 66
offers, 3 mappings. ``float`` is trailer-only (content, zero offers) and
``coronation-street`` has no local catalog entry (content, no mapping, no
offers) — together they are the contract this phase exists for: everything
the source publishes survives a refresh with or without a TMDb identity,
and offers remain exactly as strict as before.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType, MatchMethod, SyncStatus
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncRun,
    build_provider_content_identity,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceFetchError
from availability.sources.cbc_gem import CBCGemClient, CBCGemSource
from tracker.models import Movie, Provider

from .cbc_fixtures import FakeCbcApi

TOTAL_CONTENTS = 5
TOTAL_OFFERS = 66
MAPPED_CONTENTS = 3   # north-of-north, murdoch-mysteries, beirut


class ProviderContentTestCase(TestCase):
    def setUp(self):
        self.api = FakeCbcApi()
        self.source = CBCGemSource(client=CBCGemClient(requester=self.api))
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="cbc_gem", defaults={"name": "CBC Gem"}
        )
        Country.objects.get_or_create(code="CA", defaults={"name": "Canada"})

        Movie.objects.create(
            tmdb_id=9001, title="North of North", year=2024, content_type="series"
        )
        Movie.objects.create(
            tmdb_id=9002, title="Murdoch Mysteries", year=2008, content_type="series"
        )
        Movie.objects.create(
            tmdb_id=9003, title="Beirut", year=2018, content_type="movie"
        )

    def _content(self, provider_content_id):
        return ProviderContent.objects.get(
            provider=self.provider, provider_content_id=provider_content_id
        )


class DiscoveryTests(ProviderContentTestCase):
    def test_every_catalogued_content_persists_exactly_once(self):
        self.service.refresh_catalogue()

        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(
            set(
                ProviderContent.objects.values_list("provider_content_id", flat=True)
            ),
            {"beirut", "float", "north-of-north", "murdoch-mysteries",
             "coronation-street"},
        )

    def test_unresolved_content_is_kept_without_mapping_or_offers(self):
        with self.assertLogs("availability.services.refresh", level="WARNING"):
            self.service.refresh_catalogue()

        coronation = self._content("coronation-street")
        self.assertEqual(coronation.content_type, ContentType.SERIES)
        self.assertTrue(coronation.currently_available)
        self.assertFalse(coronation.mappings.exists())
        self.assertFalse(
            Offer.objects.filter(provider_content_id="coronation-street").exists()
        )

    def test_trailer_only_content_persists_without_offers(self):
        self.service.refresh_catalogue()

        trailer_only = self._content("float")
        self.assertEqual(trailer_only.content_type, ContentType.MOVIE)
        self.assertFalse(trailer_only.mappings.exists())
        self.assertFalse(Offer.objects.filter(provider_content_id="float").exists())

    def test_rows_carry_provider_native_identity_and_observation(self):
        self.service.refresh_catalogue()

        beirut = self._content("beirut")
        self.assertEqual(
            beirut.identity,
            build_provider_content_identity("cbc_gem", "beirut", ContentType.MOVIE),
        )
        self.assertEqual(beirut.identity, "cbc_gem|beirut|movie||")
        self.assertEqual(beirut.title, "Beirut")
        self.assertEqual(beirut.provider, self.provider)
        self.assertEqual(beirut.source.slug, "cbc_gem")
        self.assertIsNone(beirut.season_number)
        self.assertIsNone(beirut.episode_number)
        self.assertTrue(beirut.currently_available)
        self.assertIsNotNone(beirut.first_seen)

    def test_mappings_link_to_the_content_row_they_resolved(self):
        self.service.refresh_catalogue()

        mappings = ContentMapping.objects.select_related("content").all()
        self.assertEqual(mappings.count(), MAPPED_CONTENTS)
        for mapping in mappings:
            self.assertIsNotNone(mapping.content)
            self.assertEqual(mapping.content.provider, self.provider)
            self.assertEqual(
                mapping.content.provider_content_id, mapping.provider_content_id
            )
        self.assertEqual(
            set(
                ProviderContent.objects.filter(mappings__isnull=True)
                .values_list("provider_content_id", flat=True)
            ),
            {"float", "coronation-street"},
        )


class LifecycleTests(ProviderContentTestCase):
    def test_repeated_refresh_converges_on_the_same_rows(self):
        self.service.refresh_catalogue()
        before = {
            row.pk: (row.identity, row.first_seen)
            for row in ProviderContent.objects.all()
        }

        self.service.refresh_catalogue()

        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(
            {row.pk: (row.identity, row.first_seen)
             for row in ProviderContent.objects.all()},
            before,
        )
        self.assertTrue(all(
            row.currently_available for row in ProviderContent.objects.all()
        ))

    def test_content_missing_from_the_catalogue_is_never_deactivated(self):
        self.service.refresh_catalogue()
        beirut_pk = self._content("beirut").pk

        self.api.categories["all-films"]["1"]["results"] = [
            card for card in self.api.categories["all-films"]["1"]["results"]
            if card["url"] != "beirut"
        ]
        result = self.service.refresh_catalogue()

        self.assertEqual(result.deactivated, 0)
        beirut = ProviderContent.objects.get(pk=beirut_pk)
        self.assertTrue(beirut.currently_available)

    def test_content_resolved_later_reuses_the_existing_row(self):
        self.service.refresh_catalogue()
        row = self._content("coronation-street")

        Movie.objects.create(
            tmdb_id=9007, title="Coronation Street", year=1960,
            content_type="series",
        )
        result = self.service.refresh_catalogue()

        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(self._content("coronation-street").pk, row.pk)
        self.assertEqual(Offer.objects.count(), TOTAL_OFFERS)
        # The 23 previously-unmapped coronation offers resolve now, and only
        # now — nothing else in the refresh changed behaviour.
        self.assertEqual(result.created, TOTAL_OFFERS - 43)
        mapping = ContentMapping.objects.get(
            provider=self.provider, provider_content_id="coronation-street"
        )
        self.assertEqual(mapping.tmdb_id, 9007)
        # Series carry no trustworthy provider year -> title-only tier.
        self.assertEqual(mapping.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(mapping.confidence, 0.7)
        self.assertEqual(mapping.content_id, row.pk)

    def test_mapping_recorded_before_discovery_is_linked_on_the_next_refresh(self):
        Source.objects.get_or_create(slug="cbc_gem", defaults={"name": "CBC Gem"})
        ContentMapping.objects.create(
            provider=self.provider,
            source=Source.objects.get(slug="cbc_gem"),
            provider_content_id="coronation-street",
            content_type=ContentType.SERIES,
            tmdb_id=777,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

        self.service.refresh_catalogue()

        mapping = ContentMapping.objects.get(
            provider=self.provider, provider_content_id="coronation-street"
        )
        self.assertEqual(mapping.tmdb_id, 777)
        self.assertEqual(mapping.match_method, MatchMethod.MANUAL)
        self.assertEqual(mapping.content_id, self._content("coronation-street").pk)


class FailureTests(ProviderContentTestCase):
    def test_fetch_failure_writes_no_provider_content(self):
        self.api.failures["/category/"] = SourceFetchError("network down")

        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()

        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

    def test_mapping_failure_rolls_back_discovery_as_well(self):
        # Discovery and mapping share one transaction, so a broken mapper
        # leaves neither provider-native rows nor mappings behind.
        with patch(
            "availability.services.refresh.match_local_catalog",
            side_effect=RuntimeError("mapper exploded"),
        ):
            with self.assertRaises(RuntimeError):
                self.service.refresh_catalogue()

        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

    def test_successful_refresh_keeps_offers_as_strict_as_before(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(Offer.objects.count(), 43)
        self.assertEqual(result.processed, 43)
        self.assertEqual(result.unresolved, 23)
        self.assertEqual(SyncRun.objects.count(), 1)
        self.assertEqual(
            SyncRun.objects.filter(status=SyncStatus.SUCCEEDED).count(), 1
        )


class ModelContractTests(TestCase):
    """ProviderContent stays catalogue metadata — never a tracker row."""

    @classmethod
    def setUpTestData(cls):
        cls.provider, _ = Provider.objects.get_or_create(
            slug="cbc_gem", defaults={"name": "CBC Gem"}
        )
        cls.source = Source.objects.create(slug="cbc-gem-api", name="CBC API")

    def test_model_has_no_tracker_content_relation(self):
        # Forward FKs only: a catalogue row references the provider (and the
        # source that observed it), never tracker.Movie/Series/TMDb ids.
        forward = {
            field.related_model._meta.label_lower
            for field in ProviderContent._meta.get_fields()
            if field.many_to_one or field.one_to_one
        }
        self.assertEqual(forward, {"tracker.provider", "availability.source"})

    def test_model_has_no_offer_or_identity_fields_it_does_not_own(self):
        names = {field.name for field in ProviderContent._meta.get_fields()}
        for absent in [
            "tmdb_id", "offer_type", "quality", "audio_languages",
            "subtitle_languages", "country", "sync_run",
        ]:
            self.assertNotIn(absent, names)

    def test_unresolved_row_has_no_required_link(self):
        row = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="float",
            content_type=ContentType.MOVIE,
            title="Float",
        )
        row.refresh_from_db()
        self.assertFalse(row.mappings.exists())
        self.assertEqual(
            row.identity, "cbc_gem|float|movie||"
        )


class ReconciliationSemanticsTests(ProviderContentTestCase):
    """Phase 8: what the catalogue observation lifecycle means — and what it does not.

    ``last_seen`` means "a successful source refresh observed this entry";
    it is not a provider availability guarantee. Absence from a
    non-authoritative refresh moves no lifecycle field at all, re-observation
    reuses the same row, and none of it is offer reconciliation — offers keep
    their Phase 3 rules in ``OfferRepository``.
    """

    CLOCK = "availability.persistence.repositories.timezone.now"

    def _refresh_at(self, when):
        # Freezes the repository clock so lifecycle assertions are exact
        # instead of racing two refreshes microseconds apart.
        with patch(self.CLOCK, return_value=when):
            return self.service.refresh_catalogue()

    def test_last_seen_advances_only_for_content_the_refresh_observed(self):
        t1 = timezone.now() - timedelta(hours=1)
        t2 = timezone.now()
        self._refresh_at(t1)
        beirut_pk = self._content("beirut").pk
        coronation_pk = self._content("coronation-street").pk

        self.api.categories["all-films"]["1"]["results"] = [
            card for card in self.api.categories["all-films"]["1"]["results"]
            if card["url"] != "beirut"
        ]
        result = self._refresh_at(t2)

        beirut = ProviderContent.objects.get(pk=beirut_pk)
        coronation = ProviderContent.objects.get(pk=coronation_pk)
        # Observed again: last_seen moves to this refresh, first_seen never does.
        self.assertEqual(coronation.first_seen, t1)
        self.assertEqual(coronation.last_seen, t2)
        # Absent from a non-authoritative refresh: nothing moves, nothing
        # deactivates — absence is not evidence.
        self.assertEqual(beirut.first_seen, t1)
        self.assertEqual(beirut.last_seen, t1)
        self.assertTrue(beirut.currently_available)
        self.assertEqual(result.deactivated, 0)
        self.assertFalse(SyncRun.objects.filter(authoritative=True).exists())

    def test_reobserved_content_flips_back_on_the_same_row(self):
        t1 = timezone.now() - timedelta(hours=1)
        t2 = timezone.now()
        self._refresh_at(t1)
        row = self._content("beirut")
        # Stands in for a future authoritative deactivation (no catalogue
        # source declares one today): re-observation must reuse the row.
        ProviderContent.objects.filter(pk=row.pk).update(currently_available=False)

        self._refresh_at(t2)

        row.refresh_from_db()
        self.assertTrue(row.currently_available)
        self.assertEqual(row.first_seen, t1)
        self.assertEqual(row.last_seen, t2)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)

    def test_catalogue_reobservation_requires_no_offer_or_mapping_reconciliation(self):
        t1 = timezone.now() - timedelta(hours=1)
        t2 = timezone.now()
        self._refresh_at(t1)
        unresolved = self._content("coronation-street")
        self.assertFalse(unresolved.mappings.exists())
        self.assertFalse(
            Offer.objects.filter(provider_content_id="coronation-street").exists()
        )

        with self.assertLogs("availability.services.refresh", level="WARNING"):
            self._refresh_at(t2)

        unresolved.refresh_from_db()
        # Discovery reconciled the catalogue row (observed again)...
        self.assertEqual(unresolved.first_seen, t1)
        self.assertEqual(unresolved.last_seen, t2)
        self.assertTrue(unresolved.currently_available)
        # ...while mapping and offer reconciliation simply never happen for
        # it: three concepts, three independent lifecycles.
        self.assertFalse(
            ContentMapping.objects.filter(provider_content_id="coronation-street").exists()
        )
        self.assertFalse(
            Offer.objects.filter(provider_content_id="coronation-street").exists()
        )
