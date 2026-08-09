from django.db import IntegrityError
from django.test import TestCase

from tracker.models import (
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Preference,
    Region,
    Subscription,
    WatchlistItem,
)


class MovieTests(TestCase):
    def test_create_movie(self):
        movie = Movie.objects.create(tmdb_id=123, title="Inception", year=2010)
        self.assertEqual(str(movie), "Inception (2010)")

    def test_tmdb_id_unique(self):
        Movie.objects.create(tmdb_id=123, title="A", year=2010)
        with self.assertRaises(IntegrityError):
            Movie.objects.create(tmdb_id=123, title="B", year=2011)


class RegionTests(TestCase):
    def test_create_region(self):
        region = Region.objects.create(provider="Netflix", country="JP")
        self.assertEqual(str(region), "Netflix JP")

    def test_provider_country_unique(self):
        Region.objects.create(provider="Netflix", country="JP")
        with self.assertRaises(IntegrityError):
            Region.objects.create(provider="Netflix", country="JP")


class AvailabilityTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=1, title="M", year=2020)
        self.region = Region.objects.create(provider="Netflix", country="JP")

    def test_create_availability(self):
        a = Availability.objects.create(
            movie=self.movie, region=self.region, offer_type="subscription"
        )
        self.assertTrue(a.is_available is False)
        self.assertEqual(a.get_offer_type_display(), "Subscription")

    def test_movie_region_unique(self):
        Availability.objects.create(movie=self.movie, region=self.region, offer_type="subscription")
        with self.assertRaises(IntegrityError):
            Availability.objects.create(movie=self.movie, region=self.region, offer_type="free")


class LanguageInfoTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2, title="N", year=2019)
        self.region = Region.objects.create(provider="Prime Video", country="IT")

    def test_language_info_unique_movie_region(self):
        LanguageInfo.objects.create(movie=self.movie, region=self.region)
        with self.assertRaises(IntegrityError):
            LanguageInfo.objects.create(movie=self.movie, region=self.region)

    def test_language_entry_unique_kind_code(self):
        info = LanguageInfo.objects.create(movie=self.movie, region=self.region)
        LanguageEntry.objects.create(language_info=info, kind="audio", language_code="en")
        with self.assertRaises(IntegrityError):
            LanguageEntry.objects.create(language_info=info, kind="audio", language_code="en")

    def test_audio_and_subtitle_kinds_coexist(self):
        info = LanguageInfo.objects.create(movie=self.movie, region=self.region)
        LanguageEntry.objects.create(language_info=info, kind="audio", language_code="en")
        LanguageEntry.objects.create(language_info=info, kind="subtitle", language_code="en")
        self.assertEqual(info.entries.count(), 2)


class SubscriptionTests(TestCase):
    def test_name_unique(self):
        Subscription.objects.create(name="Netflix")
        with self.assertRaises(IntegrityError):
            Subscription.objects.create(name="Netflix")

    def test_user_id_defaults_to_one(self):
        self.assertEqual(Subscription.objects.create(name="Max").user_id, 1)


class PreferenceTests(TestCase):
    def test_default_lists_are_empty(self):
        p = Preference.objects.create()
        self.assertEqual(p.audio_languages, [])
        self.assertEqual(p.subtitle_languages, [])
        self.assertEqual(p.candidate_countries, [])

    def test_user_id_defaults_to_one(self):
        self.assertEqual(Preference.objects.create().user_id, 1)


class WatchlistItemTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=3, title="O", year=2018)

    def test_movie_source_unique(self):
        WatchlistItem.objects.create(movie=self.movie, source="letterboxd")
        with self.assertRaises(IntegrityError):
            WatchlistItem.objects.create(movie=self.movie, source="letterboxd")

    def test_user_id_defaults_to_one(self):
        self.assertEqual(WatchlistItem.objects.create(movie=self.movie).user_id, 1)
