"""Normalization helpers at the source boundary (Phase 3, §5-§8)."""
from django.test import SimpleTestCase

from availability.domain.enums import ContentType, OfferType
from availability.services.normalize import (
    normalize_content_type,
    normalize_country,
    normalize_languages,
    normalize_offer_type,
)


class ContentTypeNormalizationTests(SimpleTestCase):
    def test_movie_aliases_map_to_the_movie_content_type(self):
        for value in ("movie", "film", "Movie", "  MOVIE  "):
            with self.subTest(value=value):
                self.assertEqual(normalize_content_type(value), ContentType.MOVIE)

    def test_series_aliases_map_to_the_series_content_type(self):
        for value in ("series", "show", "tv", "Series"):
            with self.subTest(value=value):
                self.assertEqual(normalize_content_type(value), ContentType.SERIES)

    def test_season_and_episode_stay_distinct(self):
        season = normalize_content_type("season")
        episode = normalize_content_type("episode")
        self.assertEqual(season, ContentType.SEASON)
        self.assertEqual(episode, ContentType.EPISODE)
        self.assertNotEqual(season, episode)

    def test_unsupported_content_type_raises(self):
        with self.assertRaises(ValueError):
            normalize_content_type("collection")


class OfferTypeNormalizationTests(SimpleTestCase):
    def test_subscription_aliases(self):
        for value in ("subscription", "svod", "SUBSCRIPTION"):
            with self.subTest(value=value):
                self.assertEqual(normalize_offer_type(value), OfferType.SUBSCRIPTION)

    def test_free_and_free_with_ads_are_distinct(self):
        self.assertEqual(normalize_offer_type("free"), OfferType.FREE)
        self.assertEqual(normalize_offer_type("avod"), OfferType.FREE_WITH_ADS)
        self.assertEqual(normalize_offer_type("free_with_ads"), OfferType.FREE_WITH_ADS)
        self.assertEqual(normalize_offer_type("free-with-ads"), OfferType.FREE_WITH_ADS)
        self.assertNotEqual(OfferType.FREE, OfferType.FREE_WITH_ADS)

    def test_tvod_family_maps_to_canonical_types(self):
        self.assertEqual(normalize_offer_type("rent"), OfferType.RENT)
        self.assertEqual(normalize_offer_type("rental"), OfferType.RENT)
        self.assertEqual(normalize_offer_type("buy"), OfferType.BUY)
        self.assertEqual(normalize_offer_type("purchase"), OfferType.BUY)
        self.assertEqual(normalize_offer_type("tvod"), OfferType.BUY)
        self.assertEqual(normalize_offer_type("addon"), OfferType.ADDON)
        self.assertEqual(normalize_offer_type("add_on"), OfferType.ADDON)

    def test_source_specific_or_missing_values_become_unknown(self):
        for value in ("mystery_type", "", None, "  "):
            with self.subTest(value=value):
                self.assertEqual(normalize_offer_type(value), OfferType.UNKNOWN)


class CountryNormalizationTests(SimpleTestCase):
    def test_country_codes_uppercase(self):
        self.assertEqual(normalize_country("us"), "US")
        self.assertEqual(normalize_country("nl"), "NL")
        self.assertEqual(normalize_country("  it  "), "IT")

    def test_malformed_country_codes_raise(self):
        for value in ("USA", "", None, "12", "U1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_country(value)


class LanguageNormalizationTests(SimpleTestCase):
    def test_unknown_languages_remain_none(self):
        self.assertIsNone(normalize_languages(None))

    def test_known_empty_list_remains_empty(self):
        self.assertEqual(normalize_languages([]), [])

    def test_audio_and_subtitle_codes_normalize_to_uppercase_canonical(self):
        self.assertEqual(normalize_languages(["eng"]), ["EN"])
        self.assertEqual(normalize_languages(["IT", "it", "eng"]), ["IT", "EN"])
        self.assertEqual(normalize_languages(["jpn", "en"]), ["JA", "EN"])

    def test_unrecognized_codes_are_preserved_uppercased_not_invented(self):
        self.assertEqual(normalize_languages(["notalang"]), ["NOTALANG"])

    def test_none_and_empty_stay_distinct(self):
        self.assertNotEqual(normalize_languages(None), normalize_languages([]))
