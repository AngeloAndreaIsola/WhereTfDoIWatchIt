from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType, MatchMethod
from availability.models import ContentMapping, Country, Offer, ProviderContent
from availability.services.refresh import AvailabilityRefreshService
from tracker.models import Movie, Provider, ProviderCategory, TitleAvailabilityCheck
from tracker.services.availability_reads import get_title_offer_evidence

from . import aggregator_fixtures as fx
from .test_justwatch_regression import RecordingPost, source_with


class JustWatchSeasonIdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        cls.netflix, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL},
        )
        cls.series = Movie.objects.create(
            tmdb_id=481991, title="Synthetic Series", year=2025, content_type=ContentType.SERIES,
        )

    def season(self, node_id="tss1", *, slot=1, child_id="481992", offers=None):
        if offers is None:
            offers = [fx.offer("season-offer", "netflix", "Netflix", "nfx")]
        return fx.title(
            node_id, f"Season {slot}", object_type="SEASON", tmdb_id=child_id,
            imdb_id=None, season_number=slot, offers=offers,
        )

    def refresh(self, *seasons, parent_id="481991"):
        show = fx.title("ts1", self.series.title, object_type="SHOW", tmdb_id=parent_id)
        post = RecordingPost([
            fx.FakeResponse(payload=fx.envelope({"popularTitles": {"edges": fx.edges(show)}})),
            fx.FakeResponse(payload=fx.envelope({"node": {"seasons": list(seasons)}})),
        ])
        started = timezone.now()
        result = AvailabilityRefreshService(source=source_with(post)).refresh_content(
            self.series, country="NL",
        )
        if result.sync_runs:
            TitleAvailabilityCheck.objects.update_or_create(
                movie=self.series, country="NL", source="justwatch",
                defaults={
                    "tmdb_id": self.series.tmdb_id, "content_type": self.series.content_type,
                    "last_attempted_at": started, "last_succeeded_at": timezone.now(),
                    "successful_run_ids": [run.pk for run in result.sync_runs],
                },
            )
        return post

    # -- core invariant: parent-keyed canonical offer, child id stays provenance --

    def test_child_tmdb_id_is_distinct_from_parent_key_used_by_season_read(self):
        post = self.refresh(self.season())
        self.assertEqual(post.payloads[1]["variables"]["nodeId"], "ts1")
        content = ProviderContent.objects.get(provider_content_id="tss1")
        # Child's own TMDb id survives as provenance in ProviderContent metadata.
        self.assertEqual(content.metadata["external_ids"]["tmdb"], "481992")
        offer = Offer.objects.get(provider_content_id="tss1")
        # Canonical offer keys off the parent series id, never the child season id.
        self.assertEqual((offer.content_type, offer.tmdb_id, offer.season_number),
                         (ContentType.SEASON, self.series.tmdb_id, 1))
        evidence = get_title_offer_evidence(self.series, season=1, country="NL")
        self.assertEqual(evidence.current, [])
        mapping = ContentMapping.objects.get(provider_content_id="tss1")
        self.assertEqual(
            ([item.season_number for item in evidence.source_reported], mapping.tmdb_id),
            ([1], self.series.tmdb_id),
        )
        self.assertEqual(mapping.content_id, content.pk)
        self.assertEqual(mapping.match_method, MatchMethod.EXTERNAL_ID)

    # -- missing native season id: child id absent but parent key still maps --

    def test_season_missing_child_tmdb_id_still_maps_to_parent(self):
        """Season node reports no child TMDb id => parent series id still used for mapping."""
        no_child = self.season(child_id=None)
        self.refresh(no_child)
        content = ProviderContent.objects.get(provider_content_id="tss1")
        self.assertIsNone(content.metadata.get("external_ids", {}).get("tmdb"))
        offer = Offer.objects.get(provider_content_id="tss1")
        self.assertEqual((offer.content_type, offer.tmdb_id, offer.season_number),
                         (ContentType.SEASON, self.series.tmdb_id, 1))
        mapping = ContentMapping.objects.get(provider_content_id="tss1")
        self.assertEqual(mapping.tmdb_id, self.series.tmdb_id)

    # -- conflicting child ids for the same season slot are skipped --

    def test_conflicting_season_ids_are_skipped_without_collapsing(self):
        """Two seasons with same slot but different child TMDb ids -> neither persists."""
        s1 = self.season(node_id="tss-a", child_id="481992")
        s2 = self.season(node_id="tss-b", child_id="481993")
        self.refresh(s1, s2)
        # Neither season's content row exists (slot was ambiguous -> skipped).
        self.assertFalse(
            ProviderContent.objects.filter(provider_content_id__in=("tss-a", "tss-b")).exists()
        )
        self.assertFalse(Offer.objects.filter(provider_content_id__in=("tss-a", "tss-b")).exists())
        self.assertFalse(
            ContentMapping.objects.filter(provider_content_id__in=("tss-a", "tss-b")).exists()
        )

    # -- specials (season_number=0) --

    def test_special_season_number_zero_maps_to_parent(self):
        """Season 0 (specials) maps to the parent series id like any other season."""
        special = self.season(node_id="tss0", slot=0, child_id="481994")
        self.refresh(special)
        content = ProviderContent.objects.get(provider_content_id="tss0")
        self.assertEqual(content.season_number, 0)
        offer = Offer.objects.get(provider_content_id="tss0")
        self.assertEqual((offer.content_type, offer.tmdb_id, offer.season_number),
                         (ContentType.SEASON, self.series.tmdb_id, 0))
        mapping = ContentMapping.objects.get(provider_content_id="tss0")
        self.assertEqual(mapping.tmdb_id, self.series.tmdb_id)
        self.assertEqual(content.metadata["external_ids"]["tmdb"], "481994")

    # -- repeated observations of the same season dedupe --

    def test_repeated_season_observations_merge_into_one_row(self):
        """Same season node observed twice in one fetch -> one content row, merged offers."""
        s1 = self.season(node_id="tss-dup", child_id="481992")
        s2 = self.season(node_id="tss-dup", child_id="481992",
                         offers=[fx.offer("season-offer-2", "netflix", "Netflix", "nfx")])
        self.refresh(s1, s2)
        # One ProviderContent row.
        contents = ProviderContent.objects.filter(provider_content_id="tss-dup")
        self.assertEqual(contents.count(), 1)
        # One Offer row (offers merged under one canonical identity).
        offers = Offer.objects.filter(provider_content_id="tss-dup")
        self.assertEqual(offers.count(), 1)
        mapping = ContentMapping.objects.get(provider_content_id="tss-dup")
        self.assertEqual(mapping.tmdb_id, self.series.tmdb_id)

    # -- stable persistence identities across repeated refreshes --

    def test_stable_persistence_identity_across_refreshes(self):
        """Repeated refreshes of the same season reuse the same ProviderContent PK."""
        self.refresh(self.season())
        first_content = ProviderContent.objects.get(provider_content_id="tss1")
        first_mapping = ContentMapping.objects.get(provider_content_id="tss1")
        first_offer = Offer.objects.get(provider_content_id="tss1")

        # Second refresh of the same season.
        self.refresh(self.season())

        second_content = ProviderContent.objects.get(provider_content_id="tss1")
        second_mapping = ContentMapping.objects.get(provider_content_id="tss1")
        second_offer = Offer.objects.get(provider_content_id="tss1")

        self.assertEqual(first_content.pk, second_content.pk)
        self.assertEqual(first_mapping.pk, second_mapping.pk)
        self.assertEqual(first_offer.pk, second_offer.pk)
        # first_seen never moves.
        self.assertEqual(first_content.first_seen, second_content.first_seen)
        # last_seen advances on re-observation.
        self.assertGreaterEqual(second_content.last_seen, first_content.last_seen)
