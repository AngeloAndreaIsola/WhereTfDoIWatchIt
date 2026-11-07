"""Trust boundaries for legacy title signals and canonical offers.

These tests pin mapping claims as display evidence only: a mapping can describe
where a title was seen, but it cannot manufacture a canonical offer or resolve
the title for scheduling. Only confirmed, fresh, positive mappings in the
requested market are trusted.
"""
from __future__ import annotations

import datetime as dt
import os
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType
from availability.freshness import freshness_for
from availability.models import Country, Offer, Source
from tracker.models import Availability, Movie, Provider, Region, ScrapedContentMapping
from tracker.services import availability_report as ar, source_policy
from tracker.services.availability_reads import get_local_offers


class MappingEvidenceTrustTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=95001, title="Mapping Evidence", year=2024,
            content_type=ContentType.MOVIE,
        )
        self.provider = Provider.objects.create(
            slug="mapping-streamer", name="Mapping Streamer", category="stream",
        )
        for code in ("NL", "JP", "IT"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    def _mapping(
        self, *, country="NL", confirmed=True, available=True, checked_at=None,
        title="Mapping Evidence",
    ):
        return ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title=title,
            scraped_year=2024,
            country_code=country,
            movie=self.movie,
            is_confirmed=confirmed,
            confidence_score=0.95 if confirmed else 0.2,
            is_available=available,
            last_scraped_at=checked_at,
        )

    def _signals(self, country="NL"):
        return ar.title_source_report([self.movie.pk], country=country)[self.movie.pk]["title_signals"]

    def test_fresh_confirmed_positive_mapping_is_trusted_for_its_country(self):
        self._mapping(checked_at=timezone.now())

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        [signal] = entry["title_signals"]

        self.assertEqual(signal["trust"], "confirmed_mapping")
        self.assertTrue(signal["trusted"])
        self.assertTrue(signal["fresh"])
        self.assertFalse(signal["playable"])
        self.assertIn("mapping-streamer", entry["available_on"])
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)

        api_verdict = self.client.get(
            f"/api/v2/unofficial/movie/{self.movie.tmdb_id}/offers/"
        ).json()["availability"]
        [api_signal] = api_verdict["title_signals"]
        self.assertEqual(api_signal["trust"], "confirmed_mapping")
        self.assertTrue(api_signal["trusted"])
        self.assertFalse(api_signal["playable"])

    def test_stale_confirmed_mapping_is_historical_even_when_row_was_updated(self):
        mapping = self._mapping(
            checked_at=timezone.now() - freshness_for("legacy_scraper") - dt.timedelta(seconds=1)
        )
        # A metadata edit advances updated_at; only last_scraped_at represents
        # a fresh observation of availability.
        ScrapedContentMapping.objects.filter(pk=mapping.pk).update(
            updated_at=timezone.now(), confidence_score=0.99,
        )

        [signal] = self._signals()

        self.assertEqual(signal["trust"], "confirmed_mapping")
        self.assertFalse(signal["trusted"])
        self.assertFalse(signal["fresh"])
        self.assertNotIn("mapping-streamer", ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]["available_on"])

    def test_unconfirmed_mapping_is_never_trusted_even_when_fresh(self):
        self._mapping(confirmed=False, checked_at=timezone.now())

        [signal] = self._signals()

        self.assertEqual(signal["trust"], "unconfirmed_mapping")
        self.assertFalse(signal["trusted"])
        self.assertTrue(signal["fresh"])
        self.assertFalse(signal["playable"])

    def test_stale_unconfirmed_mapping_is_retained_as_untrusted_history(self):
        self._mapping(
            confirmed=False,
            checked_at=timezone.now() - freshness_for("legacy_scraper") - dt.timedelta(seconds=1),
        )

        [signal] = self._signals()

        self.assertEqual(signal["trust"], "unconfirmed_mapping")
        self.assertFalse(signal["trusted"])
        self.assertFalse(signal["fresh"])
        self.assertFalse(signal["playable"])

    def test_unconfirmed_fresh_mapping_is_not_added_to_available_on(self):
        self._mapping(confirmed=False, checked_at=timezone.now())

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]

        self.assertNotIn("mapping-streamer", entry["available_on"])
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)

    def test_missing_observation_timestamp_is_not_made_fresh_by_updated_at(self):
        mapping = self._mapping(checked_at=None)
        ScrapedContentMapping.objects.filter(pk=mapping.pk).update(updated_at=timezone.now())

        [signal] = self._signals()

        self.assertFalse(signal["trusted"])
        self.assertFalse(signal["fresh"])
        self.assertIsNone(signal["checked_at"])

    def test_negative_mapping_does_not_emit_positive_or_trusted_signal(self):
        self._mapping(available=False, checked_at=timezone.now())

        self.assertEqual(self._signals(), [])
        self.assertNotIn("mapping-streamer", ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]["available_on"])

    def test_explicit_country_filters_nl_and_jp_mapping_evidence(self):
        self._mapping(country="NL", checked_at=timezone.now())
        self._mapping(country="JP", checked_at=timezone.now(), title="Mapping Evidence JP")
        self._mapping(country="IT", checked_at=timezone.now(), title="Mapping Evidence IT")

        self.assertEqual([s["country"] for s in self._signals("NL")], ["NL"])
        self.assertEqual([s["country"] for s in self._signals("JP")], ["JP"])
        self.assertEqual([s["country"] for s in self._signals("IT")], ["IT"])

    def test_mapping_associated_with_another_movie_does_not_leak(self):
        other = Movie.objects.create(
            tmdb_id=95002, title="Different Movie", year=2023,
            content_type=ContentType.MOVIE,
        )
        ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Different Movie",
            scraped_year=2023,
            country_code="NL",
            movie=other,
            is_confirmed=True,
            confidence_score=0.95,
            is_available=True,
            last_scraped_at=timezone.now(),
        )

        self.assertEqual(self._signals(), [])

    def test_unscoped_mapping_does_not_satisfy_explicit_market(self):
        self._mapping(country="", checked_at=timezone.now())

        self.assertEqual(self._signals("NL"), [])

    def test_legacy_availability_is_display_only_unknown_trust(self):
        region = Region.objects.create(provider="mapping-streamer", country="NL")
        Availability.objects.create(
            movie=self.movie, region=region, is_available=True,
            offer_type="subscription", last_checked_at=timezone.now(),
        )

        [signal] = self._signals()

        self.assertEqual(signal["source"], "legacy_availability")
        self.assertEqual(signal["trust"], "legacy_unknown")
        self.assertFalse(signal["trusted"])
        self.assertFalse(signal["playable"])
        self.assertEqual(get_local_offers(self.movie), [])

    def test_legacy_availability_obeys_explicit_country_scope(self):
        for code in ("NL", "JP"):
            region = Region.objects.create(provider="mapping-streamer", country=code)
            Availability.objects.create(
                movie=self.movie, region=region, is_available=True,
                offer_type="subscription", last_checked_at=timezone.now(),
            )

        self.assertEqual([s["country"] for s in self._signals("NL")], ["NL"])
        self.assertEqual([s["country"] for s in self._signals("JP")], ["JP"])
        self.assertEqual(self._signals("IT"), [])

    def test_stale_legacy_availability_is_not_verified(self):
        region = Region.objects.create(provider="mapping-streamer", country="NL")
        Availability.objects.create(
            movie=self.movie, region=region, is_available=True,
            offer_type="subscription",
            last_checked_at=timezone.now() - dt.timedelta(days=30),
        )

        [signal] = self._signals()

        self.assertFalse(signal["trusted"])
        self.assertFalse(signal["fresh"])
        self.assertFalse(signal["playable"])

    def test_legacy_availability_does_not_resolve_the_title(self):
        region = Region.objects.create(provider="mapping-streamer", country="NL")
        Availability.objects.create(
            movie=self.movie, region=region, is_available=True,
            offer_type="subscription", last_checked_at=timezone.now(),
        )

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]

        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)
        self.assertEqual(entry["evidence"], "title_signal")
        self.assertNotIn("mapping-streamer", entry["available_on"])

    def test_mapping_evidence_never_manufactures_canonical_offers(self):
        self._mapping(checked_at=timezone.now())

        self.assertEqual(get_local_offers(self.movie), [])
        self.assertEqual(Offer.objects.filter(tmdb_id=self.movie.tmdb_id).count(), 0)

    def test_canonical_offer_wins_while_mapping_claim_remains_non_playable(self):
        self._mapping(confirmed=False, checked_at=timezone.now())
        provider = Provider.objects.create(
            slug="canonical-netflix", name="Netflix", category="stream",
        )
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"},
        )
        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=provider,
            country=Country.objects.get(code="NL"),
            source=source,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
            last_seen=timezone.now(),
        )

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]
        [signal] = entry["title_signals"]

        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)
        self.assertEqual(entry["evidence"], "offers")
        self.assertFalse(signal["trusted"])
        self.assertFalse(signal["playable"])

    def test_canonical_offer_for_another_content_type_does_not_resolve_movie(self):
        provider = Provider.objects.create(
            slug="canonical-series", name="Series Service", category="stream",
        )
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"},
        )
        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=provider,
            country=Country.objects.get(code="NL"),
            source=source,
            content_type=ContentType.SERIES,
            offer_type="subscription",
            currently_available=True,
            last_seen=timezone.now(),
        )

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]

        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)
        self.assertEqual(entry["offer_count"], 0)
        self.assertNotIn("canonical-series", entry["available_on"])

    def test_canonical_offer_from_another_country_does_not_resolve_explicit_market(self):
        provider = Provider.objects.create(
            slug="canonical-jp", name="Japan Service", category="stream",
        )
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"},
        )
        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=provider,
            country=Country.objects.get(code="JP"),
            source=source,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
            last_seen=timezone.now(),
        )

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]

        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)
        self.assertEqual(entry["offer_count"], 0)
        self.assertNotIn("canonical-jp", entry["available_on"])

    def test_future_confirmed_mapping_timestamp_uses_canonical_freshness_policy(self):
        self._mapping(checked_at=timezone.now() + dt.timedelta(days=1))

        [signal] = self._signals()

        self.assertTrue(signal["fresh"])
        self.assertTrue(signal["trusted"])

    def test_expired_canonical_offer_does_not_resolve_title(self):
        provider = Provider.objects.create(
            slug="expired-provider", name="Expired Service", category="stream",
        )
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"},
        )
        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=provider,
            country=Country.objects.get(code="NL"),
            source=source,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
            available_until=timezone.now() - dt.timedelta(hours=1),
            last_seen=timezone.now(),
        )

        entry = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]

        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)
        self.assertEqual(entry["offer_count"], 0)
        self.assertNotIn("expired-provider", entry["available_on"])

    def test_untrusted_mapping_does_not_suppress_metered_source(self):
        self._mapping(confirmed=False, checked_at=timezone.now())
        report = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision = source_policy.decide_sources_for_title(
                report, applicable_slugs=["justwatch", "streaming_availability"],
            )

        self.assertIn("streaming_availability", decision["allowed_sources"])

    def test_fresh_canonical_cheaper_offer_can_suppress_metered_source(self):
        provider = Provider.objects.create(
            slug="canonical-netflix", name="Netflix", category="stream",
        )
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"},
        )
        Offer.objects.create(
            tmdb_id=self.movie.tmdb_id,
            provider=provider,
            country=Country.objects.get(code="NL"),
            source=source,
            content_type=ContentType.MOVIE,
            offer_type="subscription",
            currently_available=True,
            last_seen=timezone.now(),
        )
        report = ar.title_source_report([self.movie.pk], country="NL")[self.movie.pk]

        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            decision = source_policy.decide_sources_for_title(
                report, applicable_slugs=["justwatch", "streaming_availability"],
            )

        self.assertEqual(report["state"], ar.TitleAvailabilityState.AVAILABLE)
        self.assertNotIn("streaming_availability", decision["allowed_sources"])
