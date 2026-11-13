"""Tests for WT-013 User-configurable source ranking: official/local/unofficial.

Verifies:
- Pairwise sorting fixtures (official vs unofficial, local vs vpn, provider priority).
- Preservation of eligibility/access invariants (ineligible/unverified cannot rank above eligible).
- Null language evidence handling (unknown language vs mismatch).
- API serialization, defaults, validation, and reset of ranking_preferences.
"""
from django.test import SimpleTestCase

from tracker.models import Preference
from tracker.serializers import PreferenceSerializer
from tracker.services.offers import Offer
from tracker.services.ranking import compute_source_rank, rank_offers
from tracker.services.preferences import get_default_ranking_preferences


def _pref(
    audio=("EN",),
    subs=("EN",),
    countries=("US", "IT"),
    current_country="US",
    ranking_preferences=None,
):
    return Preference(
        user_id=1,
        audio_languages=list(audio),
        subtitle_languages=list(subs),
        candidate_countries=list(countries),
        current_country=current_country,
        vpn_enabled=True,
        ranking_preferences=ranking_preferences or {},
    )


def _offer(
    country,
    service,
    offer_type="subscription",
    quality="hd",
    audio=(),
    subs=(),
    is_scraped=False,
    source_category="",
):
    return Offer(
        country=country,
        service=service,
        offer_type=offer_type,
        quality=quality,
        audio_languages=list(audio),
        subtitle_languages=list(subs),
        is_scraped_source=is_scraped,
        source_category=source_category,
    )


class RankingWT013Tests(SimpleTestCase):
    def test_default_ranking_prefers_official_and_local(self):
        """Default ranking order places official + local ahead of unofficial or foreign."""
        pref = _pref()
        offers = [
            _offer("US", "UnofficialSite", audio=("EN",), is_scraped=True),
            _offer("IT", "Netflix", audio=("EN",), is_scraped=False),
            _offer("US", "Prime", audio=("EN",), is_scraped=False),
        ]
        ranked = rank_offers(offers, pref)
        # US Prime (official, local) -> IT Netflix (official, foreign) -> US Unofficial (unofficial)
        self.assertEqual([r.service for r in ranked], ["Prime", "Netflix", "UnofficialSite"])
        self.assertEqual(ranked[0].source_rank, 0)  # "official" index in ["official", "local", "unofficial"]
        self.assertIn("Official provider", ranked[0].ranking_explanation)
        self.assertIn("Local region offer", ranked[0].ranking_explanation)

    def test_source_priority_reordering_unofficial_first(self):
        """User can reorder source_priority to prioritize unofficial sources if prefer_official is false."""
        pref = _pref(
            ranking_preferences={
                "version": 1,
                "source_priority": ["unofficial", "local", "official"],
                "prefer_official": False,
                "prefer_local": False,
                "prefer_entitled": True,
            }
        )
        offers = [
            _offer("US", "Netflix", audio=("EN",), is_scraped=False),
            _offer("US", "ScrapedStream", audio=("EN",), is_scraped=True),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual(ranked[0].service, "ScrapedStream")
        self.assertEqual(ranked[1].service, "Netflix")

    def test_provider_priority_pairwise_tiebreak(self):
        """Provider priority reorders offers within the same source category and quality."""
        pref = _pref(
            ranking_preferences={
                "version": 1,
                "source_priority": ["official", "local", "unofficial"],
                "prefer_official": True,
                "prefer_local": True,
                "prefer_entitled": True,
                "provider_priority": ["hulu", "netflix"],
            }
        )
        offers = [
            _offer("US", "Netflix", audio=("EN",), quality="4k"),
            _offer("US", "Hulu", audio=("EN",), quality="4k"),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual(ranked[0].service, "Hulu")
        self.assertEqual(ranked[1].service, "Netflix")
        self.assertEqual(ranked[0].provider_rank, 0)
        self.assertEqual(ranked[1].provider_rank, 1)

    def test_language_mismatch_never_outranks_language_match(self):
        """Even if unofficial or provider preference is high, a language match outranks a mismatch."""
        pref = _pref(
            audio=("EN",),
            subs=("EN",),
            ranking_preferences={
                "version": 1,
                "source_priority": ["unofficial", "official", "local"],
                "prefer_official": False,
                "prefer_local": False,
                "prefer_entitled": True,
                "provider_priority": ["favprovider"],
            },
        )
        offers = [
            _offer("US", "FavProvider", audio=("JA",), subs=("JA",), is_scraped=True),
            _offer("US", "RegularService", audio=("EN",), subs=("EN",), is_scraped=False),
        ]
        ranked = rank_offers(offers, pref)
        # RegularService has language match (match_score=0); FavProvider has mismatch (match_score=1)
        self.assertEqual(ranked[0].service, "RegularService")
        self.assertEqual(ranked[1].service, "FavProvider")

    def test_null_language_evidence_treated_as_unknown_not_invented_match(self):
        """Offers with null/empty audio and subs do not falsely match."""
        pref = _pref(audio=("EN",), subs=("EN",))
        offers = [
            _offer("US", "UnknownAudio", audio=(), subs=()),
            _offer("US", "ConfirmedEN", audio=("EN",), subs=("EN",)),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual(ranked[0].service, "ConfirmedEN")
        self.assertTrue(ranked[0].is_language_match)
        self.assertIsNone(ranked[1].is_language_match)

    def test_verified_availability_evidence_precedes_stale_or_unknown(self):
        pref = _pref(ranking_preferences={
            "version": 1,
            "source_priority": ["unofficial", "official", "local"],
            "prefer_official": False,
            "prefer_local": False,
            "prefer_entitled": False,
            "provider_priority": ["stalefav", "unknownfav"],
        })
        offers = [
            Offer(country="US", service="StaleFav", offer_type="subscription", quality="hd",
                  audio_languages=["EN"], availability_evidence="stale"),
            Offer(country="US", service="UnknownFav", offer_type="subscription", quality="hd",
                  audio_languages=["EN"], availability_evidence="unknown"),
            Offer(country="US", service="Verified", offer_type="subscription", quality="sd",
                  audio_languages=["EN"], availability_evidence="verified"),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual([r.service for r in ranked], ["Verified", "StaleFav", "UnknownFav"])

    def test_compute_source_rank_handles_unknown_category(self):
        self.assertEqual(compute_source_rank(["official", "local"], is_scraped=True, is_local=False), 99)
        self.assertEqual(compute_source_rank(["official", "unofficial"], is_scraped=False, is_local=False), 0)

    def test_preference_serializer_defaults_and_validation(self):
        """Serializer validates ranking_preferences schema and rejects invalid IDs."""
        serializer = PreferenceSerializer(
            data={"ranking_preferences": {"source_priority": ["official", "invalid_source"]}}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("ranking_preferences", serializer.errors)

        valid_data = {
            "ranking_preferences": {
                "version": 1,
                "source_priority": ["local", "official", "unofficial"],
                "prefer_official": True,
                "prefer_local": False,
                "prefer_entitled": True,
                "provider_priority": ["Netflix", "Prime"],
            }
        }
        valid_serializer = PreferenceSerializer(data=valid_data)
        self.assertTrue(valid_serializer.is_valid(), valid_serializer.errors)
        res = valid_serializer.validated_data["ranking_preferences"]
        self.assertEqual(res["source_priority"], ["local", "official", "unofficial"])
        self.assertEqual(res["provider_priority"], ["netflix", "prime"])

    def test_resolve_preferences_independent_domains_preserves_language_defaults(self):
        """Saving ranking_preferences on a migration-seeded empty row must not wipe out server language defaults."""
        from django.conf import settings
        from tracker.services.preferences import resolve_preferences

        # Create a row with empty languages (like seeded v0 row) but with ranking_preferences saved
        empty_row = Preference(
            user_id=888,
            audio_languages=[],
            subtitle_languages=[],
            candidate_countries=[],
            current_country="",
            ranking_preferences={
                "version": 1,
                "source_priority": ["local", "official", "unofficial"],
                "prefer_official": True,
                "prefer_local": True,
                "prefer_entitled": True,
                "provider_priority": [],
            },
        )
        # Mock Preference.objects.filter(user_id=888).first() returning empty_row
        from unittest.mock import patch
        with patch.object(Preference.objects, "filter") as mock_filter:
            mock_filter.return_value.first.return_value = empty_row
            resolved = resolve_preferences(user_id=888)

            defaults = settings.DEFAULT_PREFERENCE
            # Language defaults are preserved!
            self.assertEqual(resolved["audio_languages"], defaults["audio_languages"])
            self.assertEqual(resolved["subtitle_languages"], defaults["subtitle_languages"])
            # Ranking preferences are respected!
            self.assertEqual(resolved["ranking_preferences"]["source_priority"], ["local", "official", "unofficial"])

    def test_prefer_entitled_tiebreak_among_eligible_offers(self):
        """prefer_entitled places subscription/free offers ahead of rent/buy offers."""
        pref = _pref(
            ranking_preferences={
                "version": 1,
                "source_priority": ["official", "local", "unofficial"],
                "prefer_official": True,
                "prefer_local": True,
                "prefer_entitled": True,
                "provider_priority": [],
            }
        )
        offers = [
            _offer("US", "Prime", offer_type="rent", quality="4k", audio=("EN",)),
            _offer("US", "Prime", offer_type="subscription", quality="hd", audio=("EN",)),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual(ranked[0].offer_type, "subscription")
        self.assertEqual(ranked[1].offer_type, "rent")

    def test_source_priority_reordering_with_disabled_prefer_official(self):
        """When user prefers unofficial sources, setting prefer_official=False allows unofficial to rank first."""
        pref = _pref(
            ranking_preferences={
                "version": 1,
                "source_priority": ["unofficial", "local", "official"],
                "prefer_official": False,
                "prefer_local": False,
                "prefer_entitled": True,
                "provider_priority": [],
            }
        )
        offers = [
            _offer("IT", "Netflix", is_scraped=False, audio=("EN",)),
            _offer("US", "ScrapedOne", is_scraped=True, audio=("EN",)),
        ]
        ranked = rank_offers(offers, pref)
        self.assertEqual(ranked[0].service, "ScrapedOne")
        self.assertEqual(ranked[1].service, "Netflix")

    def test_reordered_source_priority_with_default_flags_ranking_and_access_agreement(self):
        """When source_priority is reordered and booleans retain defaults, ranking and access agree."""
        pref = _pref(
            ranking_preferences={
                "version": 1,
                "source_priority": ["unofficial", "local", "official"],
                "prefer_official": True,
                "prefer_local": True,
                "prefer_entitled": True,
                "provider_priority": [],
            }
        )
        # Offer 1: unofficial (scraped) in US (local)
        # Offer 2: official in IT (foreign)
        o_unofficial = _offer("US", "ScrapedOne", is_scraped=True, audio=("EN",))
        o_official = _offer("IT", "Netflix", is_scraped=False, audio=("EN",))

        # 1. Test rank_offers ordering:
        ranked = rank_offers([o_official, o_unofficial], pref)
        self.assertEqual(ranked[0].service, "ScrapedOne")
        self.assertEqual(ranked[1].service, "Netflix")

        # 2. Test access.py resolve_title ordering / agreement:
        from tracker.services.access import resolve_title, build_explicit_access
        access = build_explicit_access(["netflix", "scrapedone"])
        res = resolve_title(None, [o_official, o_unofficial], pref, access=access, current_country="US", provider_map={})
        # best_offer should agree with rank_offers:
        self.assertIsNotNone(res.best_offer)
        self.assertEqual(res.best_offer.service, "ScrapedOne")

    def test_local_official_vs_vpn_unofficial_obeys_source_priority(self):
        """source_priority can put a reachable VPN offer first when prefer_local is disabled."""
        from tracker.services.access import AccessVerdict, build_explicit_access, resolve_title

        local = _offer("US", "OfficialLocal", audio=("EN",))
        vpn_unofficial = _offer(
            "IT", "UnofficialVPN", audio=("EN",), source_category="unofficial"
        )
        pref = _pref(
            countries=("US", "IT"),
            current_country="US",
            ranking_preferences={
                "version": 1,
                "source_priority": ["unofficial", "official", "local"],
                "prefer_official": True,
                "prefer_local": False,
                "prefer_entitled": True,
                "provider_priority": [],
            },
        )

        ranked = rank_offers([local, vpn_unofficial], pref, current_country="US")
        result = resolve_title(
            None, [local, vpn_unofficial], pref, access=build_explicit_access(
                ["OfficialLocal", "UnofficialVPN"]
            ), current_country="US", provider_map={},
        )
        self.assertEqual(ranked[0].service, "UnofficialVPN")
        self.assertEqual(result.best_offer.service, ranked[0].service)
        # Offer order does not change the factual verdict: one eligible local offer exists.
        self.assertEqual(result.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertEqual(result.vpn_offers[0].locality, "vpn")

    def test_local_source_priority_keeps_local_offer_first_but_verdict_tracks_reachability(self):
        from tracker.services.access import AccessVerdict, build_explicit_access, resolve_title

        local = _offer("US", "OfficialLocal", audio=("EN",))
        vpn_unofficial = _offer(
            "IT", "UnofficialVPN", audio=("EN",), source_category="unofficial"
        )
        pref = _pref(
            countries=("US", "IT"),
            current_country="US",
            ranking_preferences={
                "version": 1,
                "source_priority": ["local", "official", "unofficial"],
                "prefer_official": True,
                "prefer_local": False,
                "prefer_entitled": True,
                "provider_priority": [],
            },
        )
        ranked = rank_offers([vpn_unofficial, local], pref, current_country="US")
        result = resolve_title(
            None, [vpn_unofficial, local], pref,
            access=build_explicit_access(["OfficialLocal", "UnofficialVPN"]),
            current_country="US", provider_map={},
        )
        self.assertEqual(ranked[0].service, "OfficialLocal")
        self.assertEqual(result.best_offer.service, ranked[0].service)
        self.assertEqual(result.verdict, AccessVerdict.AVAILABLE_HERE)

    def test_vpn_only_offer_keeps_vpn_verdict_when_ranked_first(self):
        from tracker.services.access import AccessVerdict, build_explicit_access, resolve_title

        vpn_offer = _offer("IT", "UnofficialVPN", audio=("EN",), source_category="unofficial")
        pref = _pref(
            countries=("IT",),
            current_country="US",
            ranking_preferences={
                "version": 1,
                "source_priority": ["unofficial", "official", "local"],
                "prefer_official": False,
                "prefer_local": False,
                "prefer_entitled": True,
                "provider_priority": [],
            },
        )
        result = resolve_title(
            None, [vpn_offer], pref, access=build_explicit_access(["UnofficialVPN"]),
            current_country="US", provider_map={},
        )
        self.assertEqual(result.verdict, AccessVerdict.AVAILABLE_VPN)
        self.assertEqual(result.best_offer.service, "UnofficialVPN")

    def test_preference_serializer_reset_action(self):
        """Serializer resets to defaults when reset=True or null is passed."""
        s = PreferenceSerializer(data={"ranking_preferences": {"reset": True}})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["ranking_preferences"], get_default_ranking_preferences())

        s_null = PreferenceSerializer(data={"ranking_preferences": None})
        self.assertTrue(s_null.is_valid(), s_null.errors)
        self.assertEqual(s_null.validated_data["ranking_preferences"], get_default_ranking_preferences())

    def test_to_dict_includes_source_and_provider_ranks(self):
        pref = _pref()
        offer = _offer("US", "Netflix", audio=("EN",), is_scraped=False)
        ranked = rank_offers([offer], pref)
        d = ranked[0].to_dict()
        self.assertIn("ranking", d)
        self.assertIn("source_rank", d["ranking"])
        self.assertIn("provider_rank", d["ranking"])
        self.assertIn("explanation", d["ranking"])
