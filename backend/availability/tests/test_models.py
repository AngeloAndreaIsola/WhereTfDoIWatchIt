from django.db import IntegrityError
from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod, OfferType, SyncMode, SyncStatus
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    Source,
    SyncCursor,
    SyncRun,
    build_offer_identity,
)
from tracker.models import Movie, Provider


class BuildOfferIdentityTests(TestCase):
    def test_movie_identity(self):
        self.assertEqual(
            build_offer_identity(
                "prime-video", "NL", "movie", 550, None, None, "subscription", "ASIN123"
            ),
            "prime-video|NL|movie|550|||subscription|ASIN123",
        )

    def test_episode_identity(self):
        self.assertEqual(
            build_offer_identity(
                "bbc-iplayer", "GB", "episode", 2316, 3, 4, "free", "pid-1"
            ),
            "bbc-iplayer|GB|episode|2316|3|4|free|pid-1",
        )

    def test_missing_provider_content_id_renders_empty_slot(self):
        identity = build_offer_identity(
            "rai-play", "it", "series", 2316, None, None, "subscription", ""
        )
        self.assertEqual(identity, "rai-play|IT|series|2316|||subscription|")

    def test_country_code_is_normalized(self):
        identity = build_offer_identity(
            "tubi", "us", "movie", 550, None, None, "free", "tubi-1"
        )
        self.assertTrue(identity.startswith("tubi|US|"))


class CountryTests(TestCase):
    def test_code_uppercased_on_save(self):
        country = Country.objects.create(code="be", name="Belgium")
        country.refresh_from_db()
        self.assertEqual(country.code, "BE")
        self.assertEqual(str(country), "Belgium (BE)")

    def test_code_unique(self):
        # ``NL`` is pre-seeded (availability/migrations/0004); the duplicate is
        # written lowercase so the assertion also covers save-time upcasing.
        Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        with self.assertRaises(IntegrityError):
            Country.objects.create(code="nl", name="Duplicate")


class SourceTests(TestCase):
    def test_create_source(self):
        source = Source.objects.create(slug="justwatch", name="JustWatch", type="aggregator")
        self.assertEqual(str(source), "JustWatch")
        self.assertTrue(source.enabled)
        self.assertEqual(source.configuration, {})

    def test_slug_unique(self):
        Source.objects.create(slug="justwatch", name="JustWatch")
        with self.assertRaises(IntegrityError):
            Source.objects.create(slug="justwatch", name="Duplicate")


class OfferModelTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="prime-video", defaults={"name": "Prime Video"})
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )
        self.source = Source.objects.create(slug="justwatch", name="JustWatch", type="aggregator")

    def _offer_kwargs(self, **overrides):
        kwargs = {
            "provider": self.provider,
            "country": self.country,
            "source": self.source,
            "content_type": ContentType.MOVIE,
            "tmdb_id": 550,
            "offer_type": OfferType.SUBSCRIPTION,
            "provider_content_id": "ASIN123",
        }
        kwargs.update(overrides)
        kwargs.setdefault(
            "identity",
            build_offer_identity(
                kwargs["provider"].slug,
                kwargs["country"].code,
                kwargs["content_type"],
                kwargs["tmdb_id"],
                kwargs.get("season_number"),
                kwargs.get("episode_number"),
                kwargs["offer_type"],
                kwargs["provider_content_id"],
            ),
        )
        return kwargs

    def test_identity_unique(self):
        Offer.objects.create(**self._offer_kwargs())
        with self.assertRaises(IntegrityError):
            Offer.objects.create(**self._offer_kwargs())

    def test_language_null_versus_empty(self):
        unknown = Offer.objects.create(**self._offer_kwargs(provider_content_id="A1"))
        known_none = Offer.objects.create(
            **self._offer_kwargs(provider_content_id="A2", audio_languages=[], subtitle_languages=[])
        )
        unknown.refresh_from_db()
        known_none.refresh_from_db()
        self.assertIsNone(unknown.audio_languages)
        self.assertIsNone(unknown.subtitle_languages)
        self.assertEqual(known_none.audio_languages, [])
        self.assertIsNotNone(known_none.audio_languages)

    def test_offer_only_relates_to_provider_from_tracker(self):
        related_models = [
            f.related_model
            for f in Offer._meta.get_fields()
            if f.is_relation and f.related_model is not None
        ]
        tracker_models = {m._meta.model_name for m in related_models if m._meta.app_label == "tracker"}
        self.assertEqual(tracker_models, {"provider"})
        self.assertNotIn(Movie, related_models)

    def test_str(self):
        offer = Offer.objects.create(**self._offer_kwargs())
        self.assertEqual(str(offer), "movie:550 @ prime-video/NL")


class ContentMappingTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="bbc-iplayer", defaults={"name": "BBC iPlayer"})
        self.source = Source.objects.create(slug="bbc-api", name="BBC API", type="api")

    def test_provider_content_id_unique(self):
        ContentMapping.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="pid-1",
            content_type=ContentType.SERIES,
            tmdb_id=2316,
            match_method=MatchMethod.PROVIDER_MAPPING,
        )
        with self.assertRaises(IntegrityError):
            ContentMapping.objects.create(
                provider=self.provider,
                source=self.source,
                provider_content_id="pid-1",
                content_type=ContentType.SERIES,
                tmdb_id=9999,
                match_method=MatchMethod.TITLE_YEAR,
            )

    def test_str(self):
        mapping = ContentMapping.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="pid-1",
            content_type=ContentType.SERIES,
            tmdb_id=2316,
            match_method=MatchMethod.MANUAL,
        )
        self.assertEqual(str(mapping), "bbc-iplayer:pid-1 -> tmdb 2316")


class SyncRunTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        self.source = Source.objects.create(slug="tubi-api", name="Tubi API", type="api")

    def test_defaults(self):
        run = SyncRun.objects.create(provider=self.provider, source=self.source)
        self.assertEqual(run.status, SyncStatus.PENDING)
        self.assertEqual(run.mode, SyncMode.FULL)
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_processed, 0)
        self.assertEqual(str(run), "tubi full pending")


class SyncCursorTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="bbc-iplayer", defaults={"name": "BBC iPlayer"})
        self.source = Source.objects.create(slug="bbc-api", name="BBC API", type="api")
        self.country, _ = Country.objects.get_or_create(
            code="GB", defaults={"name": "United Kingdom"}
        )

    def test_provider_source_country_unique(self):
        SyncCursor.objects.create(
            provider=self.provider, source=self.source, country=self.country, cursor="a"
        )
        with self.assertRaises(IntegrityError):
            SyncCursor.objects.create(
                provider=self.provider, source=self.source, country=self.country, cursor="b"
            )

    def test_str(self):
        cursor = SyncCursor.objects.create(
            provider=self.provider, source=self.source, country=self.country
        )
        self.assertEqual(str(cursor), "bbc-iplayer/bbc-api/GB")
