"""Multiple sources feeding the same refresh pipeline (Phase 4 §7, §13).

Source = where the information came from; Provider = where the user can watch.
Two sources reporting the same provider/content must both survive as
distinguishable ``availability.Offer`` rows — provenance is never deduplicated
away — and no duplicate ``Provider`` row may appear per source.
"""
from django.test import TestCase

from availability.domain.enums import OfferType, SyncStatus
from availability.models import Country, Offer, Source, SyncRun
from availability.services.refresh import AvailabilityRefreshService
from tracker.models import Movie, Provider

from .source_stubs import StubSource, fetch_result, normalized_offer


class MultiSourceProvenanceTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        self.provider, _ = Provider.objects.get_or_create(
            slug="prime", defaults={"name": "Prime Video"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )

    def _refresh(self, slug, provider_content_id):
        """One refresh unit from one source, reporting the same content."""
        source = StubSource(
            slug=slug,
            name=slug.replace("_", " ").title(),
            response=fetch_result(
                normalized_offer(
                    provider_id="prime",
                    provider_content_id=provider_content_id,
                    tmdb_id=550,
                    offer_type=OfferType.SUBSCRIPTION,
                )
            ),
        )
        return AvailabilityRefreshService(source=source).refresh_content(self.movie)

    def test_two_sources_for_one_provider_stay_distinguishable_by_source(self):
        first = self._refresh("stub_aggregator", "jw-81651789")
        second = self._refresh("prime_video", "prime-catalog-42")

        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 1)
        self.assertEqual(Offer.objects.count(), 2)

        by_source = {
            offer.source.slug: offer
            for offer in Offer.objects.select_related("source", "provider", "country")
        }
        self.assertEqual(set(by_source), {"stub_aggregator", "prime_video"})
        for offer in by_source.values():
            self.assertEqual(offer.provider, self.provider)
            self.assertEqual(offer.country, self.country)
            self.assertEqual(offer.tmdb_id, 550)
            self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)
            self.assertTrue(offer.currently_available)
        self.assertNotEqual(
            by_source["stub_aggregator"].provider_content_id,
            by_source["prime_video"].provider_content_id,
        )
        self.assertEqual(
            {run.source.slug for run in SyncRun.objects.select_related("source")},
            {"stub_aggregator", "prime_video"},
        )

    def test_one_provider_row_is_shared_across_sources(self):
        self._refresh("stub_aggregator", "jw-1")
        self._refresh("prime_video", "pw-1")

        # §7: do not create duplicate Provider records for every source.
        self.assertEqual(Provider.objects.filter(slug="prime").count(), 1)
        self.assertEqual(
            set(Offer.objects.values_list("provider_id", flat=True)),
            {self.provider.pk},
        )

    def test_each_source_gets_its_own_source_row_and_sync_run(self):
        self._refresh("stub_aggregator", "jw-1")
        self._refresh("prime_video", "pw-1")

        # Membership, not equality: the aggregator sources are seeded by
        # migration 0006, so the table legitimately contains more than the
        # two rows this test writes.
        written = set(Source.objects.values_list("slug", flat=True))
        self.assertTrue({"stub_aggregator", "prime_video"}.issubset(written))
        runs = SyncRun.objects.select_related("source")
        self.assertEqual(runs.count(), 2)
        self.assertEqual({run.status for run in runs}, {SyncStatus.SUCCEEDED})
        self.assertEqual(
            {run.source.slug for run in runs}, {"stub_aggregator", "prime_video"}
        )

    def test_identical_provider_content_ids_converge_on_one_canonical_row(self):
        # Identity (Phase 3) has no source slot: two sources reporting the SAME
        # provider content id describe the SAME provider content, so both
        # refreshes upsert one canonical row and its provenance follows the
        # last writer. Sources using their own id namespaces (above) stay
        # fully distinguishable — this is the deliberate boundary.
        self._refresh("stub_aggregator", "same-id")
        self._refresh("prime_video", "same-id")

        self.assertEqual(Offer.objects.count(), 1)
        self.assertEqual(Offer.objects.get().source.slug, "prime_video")
