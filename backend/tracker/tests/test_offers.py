from django.test import TestCase

from tracker.services.offers import Offer


class OfferFromDictTests(TestCase):
    def test_audio_key_variants(self):
        o1 = Offer.from_dict({"audio": ["EN", "JA"]})
        o2 = Offer.from_dict({"audio_languages": ["EN", "JA"]})
        o3 = Offer.from_dict({"audio": ["EN"], "audio_languages": ["JA"]})
        self.assertEqual(o1.audio_languages, ["EN", "JA"])
        self.assertEqual(o2.audio_languages, ["EN", "JA"])
        self.assertEqual(o3.audio_languages, ["EN"])

    def test_subtitle_key_variants(self):
        o1 = Offer.from_dict({"subs": ["EN", "FR"]})
        o2 = Offer.from_dict({"subtitle_languages": ["EN", "FR"]})
        o3 = Offer.from_dict({"subs": ["EN"], "subtitle_languages": ["FR"]})
        self.assertEqual(o1.subtitle_languages, ["EN", "FR"])
        self.assertEqual(o2.subtitle_languages, ["EN", "FR"])
        self.assertEqual(o3.subtitle_languages, ["EN"])

    def test_service_key_variants(self):
        o1 = Offer.from_dict({"service": "netflix"})
        o2 = Offer.from_dict({"provider": "prime"})
        both = Offer.from_dict({"service": "netflix", "provider": "prime"})
        self.assertEqual(o1.service, "netflix")
        self.assertEqual(o2.service, "prime")
        self.assertEqual(both.service, "netflix")

    def test_country_uppercased(self):
        o = Offer.from_dict({"country": "us"})
        self.assertEqual(o.country, "US")

    def test_offer_type_lowered(self):
        o = Offer.from_dict({"offer_type": "SUBSCRIPTION"})
        self.assertEqual(o.offer_type, "subscription")

    def test_quality_lowered(self):
        o = Offer.from_dict({"quality": "4K"})
        self.assertEqual(o.quality, "4k")

    def test_is_scraped_source_default_false(self):
        o = Offer.from_dict({"service": "x"})
        self.assertFalse(o.is_scraped_source)

    def test_is_scraped_source_true(self):
        o = Offer.from_dict({"service": "x", "is_scraped_source": True})
        self.assertTrue(o.is_scraped_source)

    def test_empty_dict(self):
        o = Offer.from_dict({})
        self.assertEqual(o.country, "")
        self.assertEqual(o.service, "")
        self.assertEqual(o.offer_type, "")
        self.assertEqual(o.quality, "")
        self.assertEqual(o.url, "")
        self.assertEqual(o.audio_languages, [])
        self.assertEqual(o.subtitle_languages, [])
        self.assertFalse(o.is_scraped_source)
        self.assertIsNone(o.available_from)
        self.assertIsNone(o.available_until)

    def test_to_dict_roundtrip(self):
        original = Offer(
            country="US",
            service="netflix",
            offer_type="subscription",
            quality="4k",
            url="https://netflix.com/watch/1",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            is_scraped_source=False,
            available_from="2026-11-20T12:00:00+00:00",
            available_until=None,
        )
        d = original.to_dict()
        restored = Offer.from_dict(d)
        self.assertEqual(restored.country, original.country)
        self.assertEqual(restored.service, original.service)
        self.assertEqual(restored.offer_type, original.offer_type)
        self.assertEqual(restored.quality, original.quality)
        self.assertEqual(restored.url, original.url)
        self.assertEqual(restored.audio_languages, original.audio_languages)
        self.assertEqual(restored.subtitle_languages, original.subtitle_languages)
        self.assertEqual(restored.is_scraped_source, original.is_scraped_source)
        self.assertEqual(restored.available_from, original.available_from)
        self.assertIsNone(restored.available_until)

    def test_inconsistent_source_keys_audio_first(self):
        o = Offer.from_dict({"audio": ["EN"], "audio_languages": ["FR"]})
        self.assertEqual(o.audio_languages, ["EN"])

    def test_inconsistent_source_keys_subs_first(self):
        o = Offer.from_dict({"subs": ["EN"], "subtitle_languages": ["FR"]})
        self.assertEqual(o.subtitle_languages, ["EN"])

    def test_dict_like_access_backward_compat(self):
        o = Offer.from_dict({"service": "netflix", "country": "US"})
        self.assertEqual(o.get("service"), "netflix")
        self.assertEqual(o["country"], "US")
        self.assertIn("service", o)


class OfferRankingRegressionTests(TestCase):
    def test_ranking_with_audio_languages_key(self):
        from tracker.models import Preference
        from tracker.services.ranking import rank_offers

        pref = Preference(
            user_id=1, audio_languages=["EN"], subtitle_languages=["EN"],
            candidate_countries=["US"],
        )
        offers = [
            Offer.from_dict({"country": "us", "service": "Netflix", "offer_type": "subscription", "quality": "4k", "audio_languages": ["EN"], "subtitle_languages": ["EN"]}),
            Offer.from_dict({"country": "us", "service": "Prime", "offer_type": "subscription", "quality": "hd", "audio_languages": ["FR"], "subtitle_languages": ["FR"]}),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual(ranked[0].service, "Netflix")

    def test_ranking_with_provider_key(self):
        from tracker.models import Preference
        from tracker.services.ranking import rank_offers

        pref = Preference(
            user_id=1, audio_languages=["EN"], subtitle_languages=["EN"],
            candidate_countries=["US"],
        )
        offers = [
            Offer.from_dict({"country": "us", "provider": "netflix", "offer_type": "subscription", "quality": "4k", "audio": ["EN"], "subs": ["EN"]}),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual(ranked[0].service, "netflix")
