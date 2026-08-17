"""Tests for strict priority ranking engine."""
from django.test import TestCase

from tracker.models import Preference
from tracker.services.ranking import QUALITY_RANK, rank_offers


def _pref(audio=("en", "it"), subs=("en",), countries=("us", "it")):
    return Preference(
        user_id=1,
        audio_languages=list(audio),
        subtitle_languages=list(subs),
        candidate_countries=list(countries),
    )


def _offer(country, service, offer_type="subscription", quality="hd", audio=(), subs=()):
    return {
        "country": country,
        "service": service,
        "offer_type": offer_type,
        "quality": quality,
        "audio": list(audio),
        "subs": list(subs),
    }


class RankingTests(TestCase):
    def test_audio_wins_over_subtitle(self):
        offers = [
            _offer("us", "Netflix", audio=("es",), subs=("en",)),
            _offer("it", "Netflix", audio=("en",), subs=("it",)),
        ]
        ranked = rank_offers(offers, _pref())
        self.assertEqual(ranked[0].country, "IT")  # en matches audio; country normalized to uppercase
        self.assertEqual(ranked[0].audio_rank, 0)

    def test_audio_ties_break_by_subtitle(self):
        offers = [
            _offer("us", "Netflix", audio=("en",), subs=("fr",)),
            _offer("us", "Prime", audio=("en",), subs=("en",)),
        ]
        ranked = rank_offers(offers, _pref(audio=("en",), subs=("en",)))
        # Both audio rank 0; subtitle tie broken by en match (rank 0) vs fr (rank 99).
        self.assertEqual(ranked[0].service, "Prime")

    def test_quality_tiebreaks_after_languages(self):
        offers = [
            _offer("us", "Netflix", audio=("en",), subs=("en",), quality="hd"),
            _offer("us", "Prime", audio=("en",), subs=("en",), quality="4k"),
        ]
        ranked = rank_offers(offers, _pref())
        self.assertEqual(ranked[0].service, "Prime")  # 4k beats hd

    def test_quality_order_constant(self):
        self.assertEqual(QUALITY_RANK["4k"], 0)
        self.assertEqual(QUALITY_RANK["hdr"], 1)
        self.assertEqual(QUALITY_RANK["hd"], 2)
        self.assertEqual(QUALITY_RANK["sd"], 3)

    def test_no_match_lands_at_end(self):
        offers = [
            _offer("us", "Netflix", audio=("en",), subs=("en",)),
            _offer("us", "Prime", audio=("ja",), subs=("ja",)),
        ]
        ranked = rank_offers(offers, _pref(audio=("en",), subs=("en",)))
        self.assertEqual(ranked[0].service, "Netflix")
        self.assertEqual(ranked[-1].audio_rank, 99)

    def test_country_filter_drops_offers(self):
        offers = [
            _offer("us", "Netflix", audio=("en",), subs=("en",)),
            _offer("jp", "Netflix", audio=("en",), subs=("en",)),
        ]
        ranked = rank_offers(offers, _pref(countries=("us",)))
        self.assertEqual([r.country for r in ranked], ["US"])

    def test_empty_country_filter_includes_all(self):
        offers = [
            _offer("us", "Netflix", audio=("en",)),
            _offer("jp", "Netflix", audio=("en",)),
        ]
        ranked = rank_offers(offers, _pref(countries=()))
        self.assertEqual(len(ranked), 2)

    def test_to_dict_shape(self):
        offers = [_offer("us", "Netflix", audio=("en",), subs=("en," if False else "en",), quality="4k")]
        ranked = rank_offers(offers, _pref())
        d = ranked[0].to_dict()
        self.assertEqual(d["country"], "US")
        self.assertEqual(d["ranking"]["quality_rank"], 0)
        self.assertIn("audio_languages", d)