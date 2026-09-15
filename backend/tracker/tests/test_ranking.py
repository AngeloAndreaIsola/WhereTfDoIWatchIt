"""Tests for strict priority ranking engine."""
from django.test import TestCase

from tracker.models import Preference
from tracker.services.offers import Offer
from tracker.services.ranking import QUALITY_RANK, rank_offers


def _pref(audio=("EN", "IT"), subs=("EN",), countries=("US", "IT")):
    return Preference(
        user_id=1,
        audio_languages=list(audio),
        subtitle_languages=list(subs),
        candidate_countries=list(countries),
    )


def _offer(country, service, offer_type="subscription", quality="hd", audio=(), subs=()):
    return Offer(
        country=country,
        service=service,
        offer_type=offer_type,
        quality=quality,
        audio_languages=list(audio),
        subtitle_languages=list(subs),
    )


class RankingTests(TestCase):
    def test_audio_wins_over_subtitle(self):
        offers = [
            _offer("us", "Netflix", audio=("ES",), subs=("EN",)),
            _offer("it", "Netflix", audio=("EN",), subs=("IT",)),
        ]
        ranked = rank_offers(offers, _pref())
        self.assertEqual(ranked[0].country, "IT")  # EN matches audio; country normalized to uppercase
        self.assertEqual(ranked[0].audio_rank, 0)

    def test_audio_ties_break_by_subtitle(self):
        offers = [
            _offer("us", "Netflix", audio=("EN",), subs=("FR",)),
            _offer("us", "Prime", audio=("EN",), subs=("EN",)),
        ]
        ranked = rank_offers(offers, _pref(audio=("EN",), subs=("EN",)))
        # Both audio rank 0; subtitle tie broken by EN match (rank 0) vs FR (rank 99).
        self.assertEqual(ranked[0].service, "Prime")

    def test_quality_tiebreaks_after_languages(self):
        offers = [
            _offer("us", "Netflix", audio=("EN",), subs=("EN",), quality="hd"),
            _offer("us", "Prime", audio=("EN",), subs=("EN",), quality="4k"),
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
            _offer("us", "Netflix", audio=("EN",), subs=("EN",)),
            _offer("us", "Prime", audio=("JA",), subs=("JA",)),
        ]
        ranked = rank_offers(offers, _pref(audio=("EN",), subs=("EN",)))
        self.assertEqual(ranked[0].service, "Netflix")
        self.assertEqual(ranked[-1].audio_rank, 99)

    def test_country_filter_drops_offers(self):
        offers = [
            _offer("us", "Netflix", audio=("EN",), subs=("EN",)),
            _offer("jp", "Netflix", audio=("EN",), subs=("EN",)),
        ]
        ranked = rank_offers(offers, _pref(countries=("US",)))
        self.assertEqual([r.country for r in ranked], ["US"])

    def test_current_country_ranks_first(self):
        offers = [
            _offer("in", "Prime"),
            _offer("it", "Prime"),
            _offer("ca", "Prime"),
        ]
        ranked = rank_offers(offers, _pref(countries=()), current_country="IT")
        self.assertEqual([r.country for r in ranked], ["IT", "IN", "CA"])

    def test_current_country_does_not_affect_existing_ranking_without_local(self):
        offers = [
            _offer("in", "Prime"),
            _offer("it", "Prime"),
            _offer("ca", "Prime"),
        ]
        ranked = rank_offers(offers, _pref(countries=()), current_country="JP")
        self.assertEqual([r.country for r in ranked], ["IN", "IT", "CA"])

    def test_local_tie_preserves_existing_logic(self):
        offers = [
            _offer("it", "Prime", quality="hd"),
            _offer("it", "Netflix", quality="4k"),
        ]
        ranked = rank_offers(offers, _pref(countries=()), current_country="IT")
        self.assertEqual([r.service for r in ranked], ["Netflix", "Prime"])

    def test_empty_country_filter_includes_all(self):
        offers = [
            _offer("us", "Netflix", audio=("EN",)),
            _offer("jp", "Netflix", audio=("EN",)),
        ]
        ranked = rank_offers(offers, _pref(countries=()))
        self.assertEqual(len(ranked), 2)

    def test_to_dict_shape(self):
        offers = [_offer("us", "Netflix", audio=("EN",), subs=("EN",), quality="4k")]
        ranked = rank_offers(offers, _pref())
        d = ranked[0].to_dict()
        self.assertEqual(d["country"], "US")
        self.assertEqual(d["ranking"]["quality_rank"], 0)
        self.assertIn("audio_languages", d)

    # --- Edge case tests per user requirement ---

    def test_en_preference_matching_external_representations(self):
        offers = [
            _offer("us", "Service1", audio=("eng",)),
            _offer("us", "Service2", audio=("en",)),
            _offer("us", "Service3", audio=("EN",)),
        ]
        ranked = rank_offers(offers, _pref(audio=("EN",)))
        for r in ranked:
            self.assertEqual(r.audio_rank, 0)
            self.assertEqual(r.audio, ["EN"])

    def test_de_matching_deutsch(self):
        offers = [_offer("de", "Service", audio=("Deutsch",))]
        ranked = rank_offers(offers, _pref(audio=("DE",), countries=()))
        self.assertEqual(ranked[0].audio_rank, 0)
        self.assertEqual(ranked[0].audio, ["DE"])

    def test_ja_matching_japanese_provider_metadata(self):
        offers = [_offer("jp", "Service", audio=("jpn",), subs=("Japanese",))]
        ranked = rank_offers(offers, _pref(audio=("JA",), subs=("JA",), countries=()))
        self.assertEqual(ranked[0].audio_rank, 0)
        self.assertEqual(ranked[0].sub_rank, 0)
        self.assertEqual(ranked[0].audio, ["JA"])
        self.assertEqual(ranked[0].subs, ["JA"])

    def test_norwegian_mappings(self):
        offers = [
            _offer("no", "S1", audio=("no",)),
            _offer("no", "S2", audio=("nor",)),
            _offer("no", "S3", audio=("nob",)),
            _offer("no", "S4", audio=("nb",)),
        ]
        ranked = rank_offers(offers, _pref(audio=("NB",), countries=()))
        for r in ranked:
            self.assertEqual(r.audio_rank, 0)
            self.assertEqual(r.audio, ["NB"])

    def test_unsupported_external_language_does_not_match(self):
        offers = [_offer("us", "Service", audio=("Klingon", "xx"))]
        ranked = rank_offers(offers, _pref(audio=("EN",)))
        self.assertEqual(ranked[0].audio_rank, 99)
        self.assertEqual(ranked[0].audio, [])

    def test_audio_match_beats_subtitle_regardless_of_quality(self):
        offers = [
            _offer("us", "SubOnly4K", audio=("FR",), subs=("EN",), quality="4k"),
            _offer("us", "AudioSD", audio=("EN",), subs=("FR",), quality="sd"),
        ]
        ranked = rank_offers(offers, _pref(audio=("EN",), subs=("EN",)))
        self.assertEqual(ranked[0].service, "AudioSD")

    def test_subtitle_match_beats_higher_quality_non_matching_offer(self):
        offers = [
            _offer("us", "NoMatch4K", audio=("FR",), subs=("FR",), quality="4k"),
            _offer("us", "SubSD", audio=("FR",), subs=("EN",), quality="sd"),
        ]
        ranked = rank_offers(offers, _pref(audio=("EN",), subs=("EN",)))
        self.assertEqual(ranked[0].service, "SubSD")

    def test_deterministic_multiple_preferred_languages_order(self):
        offers = [
            _offer("us", "SecondPref", audio=("IT",)),
            _offer("us", "FirstPref", audio=("EN",)),
        ]
        ranked = rank_offers(offers, _pref(audio=("EN", "IT")))
        self.assertEqual(ranked[0].service, "FirstPref")
        self.assertEqual(ranked[0].audio_rank, 0)
        self.assertEqual(ranked[1].service, "SecondPref")
        self.assertEqual(ranked[1].audio_rank, 1)

    def test_missing_empty_language_metadata_no_false_match(self):
        offers = [_offer("us", "Empty", audio=(), subs=())]
        ranked = rank_offers(offers, _pref(audio=("EN",), subs=("EN",)))
        self.assertEqual(ranked[0].audio_rank, 99)
        self.assertEqual(ranked[0].sub_rank, 99)
