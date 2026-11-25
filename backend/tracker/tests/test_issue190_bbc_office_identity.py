"""Replay issue #190 on the real refresh and tracker read paths, offline."""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from availability.domain.enums import ContentType, MatchMethod, OfferType
from availability.domain.models import DiscoveredContent, ProviderOffer
from availability.models import ContentMapping, Country, Offer, ProviderContent, Source
from availability.services.reconciliation import AvailabilityReconciliationService
from availability.services.refresh import AvailabilityRefreshService
from availability.sources.base import SourceFetchResult
from availability.sources.bbc_iplayer import BBCIPlayerSource
from tracker.models import Movie, Provider
from tracker.services.availability_reads import get_offer_evidence, get_offer_evidence_bulk


BBC = "bbc_iplayer"
UK_PROGRAMME = "b00jd68z"
UK_URL = "https://www.bbc.co.uk/iplayer/episodes/b00jd68z/the-office"
# Synthetic control only. No BBC programme ID for the US edition was verified.
US_CONTROL_PROGRAMME = "fixture-us-office"
US_CONTROL_URL = "https://example.test/fixture-us-office"
NOW = datetime(2026, 11, 23, 15, 44, tzinfo=UTC)
REPORTED_LAST_SEEN = datetime(2026, 10, 6, 23, 11, 12, 717129, tzinfo=UTC)


class BBCOfficeIdentityTests(TestCase):
    def setUp(self):
        self.us = Movie.objects.create(
            tmdb_id=2316, content_type=ContentType.SERIES, title="The Office", year=2005,
        )
        self.provider, _ = Provider.objects.get_or_create(slug=BBC, defaults={"name": "BBC iPlayer"})
        self.source, _ = Source.objects.get_or_create(slug=BBC, defaults={"name": "BBC iPlayer"})
        self.gb, _ = Country.objects.get_or_create(code="GB", defaults={"name": "United Kingdom"})
        clock = patch("availability.services.reconciliation.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def uk_series(self):
        return Movie.objects.create(
            tmdb_id=2996, content_type=ContentType.SERIES, title="The Office", year=2001,
        )

    def mapping(self, series, *, programme=UK_PROGRAMME, method=MatchMethod.TITLE_YEAR):
        return ContentMapping.objects.create(
            provider=self.provider, source=self.source, provider_content_id=programme,
            content_type=series.content_type, tmdb_id=series.tmdb_id,
            match_method=method, confidence=0.7,
        )

    def offer(self, series, *, programme=UK_PROGRAMME, url=UK_URL, **kwargs):
        return Offer.objects.create(
            provider=self.provider, source=self.source, country=self.gb,
            provider_content_id=programme, provider_url=url, tmdb_id=series.tmdb_id,
            offer_type=OfferType.FREE, **kwargs,
        )

    def test_reported_offer_3504_is_hidden_instead_of_last_known(self):
        uk = self.uk_series()
        mapping = self.mapping(self.us)
        offer = self.offer(
            self.us, id=3504, content_type=ContentType.SERIES,
            last_seen=REPORTED_LAST_SEEN,
        )

        for series in (self.us, uk):
            response = self.client.get(reverse("movie-availability", args=[series.tmdb_id]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["offers"], [])
            self.assertEqual(response.json()["last_known_offers"], [])

        offer.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual((offer.tmdb_id, offer.provider_content_id, offer.provider_url), (
            self.us.tmdb_id, UK_PROGRAMME, UK_URL,
        ))
        self.assertEqual(offer.last_seen, REPORTED_LAST_SEEN)
        self.assertEqual(mapping.tmdb_id, self.us.tmdb_id)

    def test_fresh_wrong_work_and_descendants_stay_hidden(self):
        self.mapping(self.us)
        for content_type, season, episode in (
            (ContentType.SERIES, None, None),
            (ContentType.SEASON, 1, None),
            (ContentType.EPISODE, 1, 1),
        ):
            self.offer(
                self.us, content_type=content_type, season_number=season, episode_number=episode,
                last_seen=NOW - timedelta(hours=1),
            )

        self.assertEqual(get_offer_evidence(self.us, country="GB"), ([], []))
        self.assertEqual(get_offer_evidence(self.us, country="GB", season=1), ([], []))
        episode = AvailabilityReconciliationService().reconcile(
            content_types=(ContentType.EPISODE,), tmdb_id=self.us.tmdb_id,
            country="GB", season_number=1, episode_number=1,
        )
        self.assertEqual(episode.offers, ())
        self.assertEqual(episode.conflicts[0].offer_ids, tuple(
            Offer.objects.filter(content_type=ContentType.EPISODE).values_list("id", flat=True)
        ))

    def test_each_edition_keeps_only_its_own_programme(self):
        uk = self.uk_series()
        # Keep the wrong historical linkage in place alongside a correctly keyed
        # UK observation. It must neither surface nor hide either valid control.
        self.mapping(self.us)
        self.mapping(self.us, programme=US_CONTROL_PROGRAMME, method=MatchMethod.MANUAL)
        wrong_offer = self.offer(self.us, content_type=ContentType.SERIES, last_seen=NOW)
        uk_offer = self.offer(uk, content_type=ContentType.SERIES, last_seen=NOW)
        us_offer = self.offer(
            self.us, programme=US_CONTROL_PROGRAMME, url=US_CONTROL_URL,
            content_type=ContentType.SERIES, last_seen=NOW,
        )
        for age, expected_bucket in ((timedelta(hours=1), 0), (timedelta(days=3), 1)):
            with self.subTest(bucket=expected_bucket):
                last_seen = NOW - age
                Offer.objects.filter(pk__in=[uk_offer.pk, us_offer.pk]).update(last_seen=last_seen)
                Offer.objects.filter(pk=wrong_offer.pk).update(last_seen=last_seen + timedelta(minutes=5))

                bulk = get_offer_evidence_bulk([uk, self.us])
                for series, url in ((uk, UK_URL), (self.us, US_CONTROL_URL)):
                    for result in (get_offer_evidence(series, country="GB"), bulk[series.id]):
                        self.assertEqual([offer.url for offer in result[expected_bucket]], [url])
                        self.assertEqual(result[1 - expected_bucket], [])

    def test_us_control_programme_mapped_to_us_is_never_shown_for_uk(self):
        uk = self.uk_series()
        self.mapping(self.us, programme=US_CONTROL_PROGRAMME, method=MatchMethod.MANUAL)
        self.offer(
            uk, programme=US_CONTROL_PROGRAMME, url=US_CONTROL_URL,
            content_type=ContentType.SERIES, last_seen=NOW,
        )

        self.assertEqual(get_offer_evidence(uk, country="GB"), ([], []))

    def test_matching_numeric_id_in_movie_namespace_cannot_inherit_the_brand(self):
        uk = self.uk_series()
        movie = Movie.objects.create(tmdb_id=uk.tmdb_id, content_type=ContentType.MOVIE, title="Film")
        self.mapping(uk, method=MatchMethod.MANUAL)
        self.offer(movie, content_type=ContentType.MOVIE, last_seen=NOW)

        self.assertEqual(get_offer_evidence(movie, country="GB"), ([], []))

    def refresh_programme(self):
        source = BBCIPlayerSource()
        content = DiscoveredContent(
            provider_id=BBC, provider_content_id=UK_PROGRAMME, content_type=ContentType.SERIES,
            title="The Office", url=UK_URL,
        )
        offer = ProviderOffer(
            provider_id=BBC, provider_content_id=UK_PROGRAMME, provider_url=UK_URL,
            content_type=ContentType.SERIES, country="GB", tmdb_id=None,
            season_number=None, episode_number=None, offer_type=OfferType.FREE,
        )
        with patch.object(source, "fetch", return_value=SourceFetchResult(
            contents=[content], offers=[offer],
        )):
            return AvailabilityRefreshService(source=source).refresh_catalogue()

    def test_refresh_cannot_assign_the_programme_to_the_sole_local_homonym(self):
        result = self.refresh_programme()

        self.assertEqual(result.unresolved, 1)
        self.assertTrue(ProviderContent.objects.filter(provider_content_id=UK_PROGRAMME).exists())
        self.assertFalse(ContentMapping.objects.filter(provider_content_id=UK_PROGRAMME).exists())
        self.assertFalse(Offer.objects.filter(provider_content_id=UK_PROGRAMME).exists())

    def test_refresh_requires_review_to_replace_a_wrong_mapping(self):
        wrong = self.mapping(self.us)
        uk = self.uk_series()

        self.assertEqual(self.refresh_programme().unresolved, 1)
        wrong.refresh_from_db()
        self.assertEqual(wrong.tmdb_id, self.us.tmdb_id)
        self.assertFalse(Offer.objects.filter(provider_content_id=UK_PROGRAMME).exists())

        # Simulate a separately reviewed mapping in the disposable test DB.
        wrong.tmdb_id = uk.tmdb_id
        wrong.match_method = MatchMethod.MANUAL
        wrong.save(update_fields=["tmdb_id", "match_method"])
        self.assertEqual(self.refresh_programme().created, 1)
        self.assertEqual(
            Offer.objects.get(provider_content_id=UK_PROGRAMME).tmdb_id, uk.tmdb_id,
        )
