from django.test import TestCase
from tracker.models import Preference
from tracker.services.offers import Offer
from tracker.services.ranking import rank_offers, evaluate_language_rule


class LanguageRuleTests(TestCase):
    def test_evaluate_language_rule_two_viewers(self):
        # ((Audio EN OR Audio IT) AND Subtitle JA) OR (Audio JA AND (Subtitle EN OR Subtitle IT))
        two_viewer_rule = {
            "type": "group",
            "op": "OR",
            "rules": [
                {
                    "type": "group",
                    "op": "AND",
                    "rules": [
                        {"type": "condition", "field": "audio", "languages": ["EN", "IT"]},
                        {"type": "condition", "field": "subtitle", "languages": ["JA"]},
                    ],
                },
                {
                    "type": "group",
                    "op": "AND",
                    "rules": [
                        {"type": "condition", "field": "audio", "languages": ["JA"]},
                        {"type": "condition", "field": "subtitle", "languages": ["EN", "IT"]},
                    ],
                },
            ],
        }

        # Case 1: Audio IT + Sub JA -> Match
        self.assertTrue(evaluate_language_rule(two_viewer_rule, ["IT"], ["JA"]))

        # Case 2: Audio EN + Sub JA -> Match
        self.assertTrue(evaluate_language_rule(two_viewer_rule, ["EN"], ["JA"]))

        # Case 3: Audio JA + Sub EN -> Match
        self.assertTrue(evaluate_language_rule(two_viewer_rule, ["JA"], ["EN"]))

        # Case 4: Audio JA + Sub IT -> Match
        self.assertTrue(evaluate_language_rule(two_viewer_rule, ["JA"], ["IT"]))

        # Case 5: Audio EN + Sub EN -> No match (missing Japanese for friend)
        self.assertFalse(evaluate_language_rule(two_viewer_rule, ["EN"], ["EN"]))

        # Case 6: Audio JA + Sub JA -> No match (missing English/Italian for user)
        self.assertFalse(evaluate_language_rule(two_viewer_rule, ["JA"], ["JA"]))

    def test_rank_offers_with_language_rule(self):
        two_viewer_rule = {
            "type": "group",
            "op": "OR",
            "rules": [
                {
                    "type": "group",
                    "op": "AND",
                    "rules": [
                        {"type": "condition", "field": "audio", "languages": ["EN", "IT"]},
                        {"type": "condition", "field": "subtitle", "languages": ["JA"]},
                    ],
                },
                {
                    "type": "group",
                    "op": "AND",
                    "rules": [
                        {"type": "condition", "field": "audio", "languages": ["JA"]},
                        {"type": "condition", "field": "subtitle", "languages": ["EN", "IT"]},
                    ],
                },
            ],
        }

        pref = Preference(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            language_rule=two_viewer_rule,
        )

        offers = [
            Offer(country="US", service="Netflix", quality="hd", audio_languages=["EN"], subtitle_languages=["EN"]),
            Offer(country="JP", service="Prime", quality="sd", audio_languages=["JA"], subtitle_languages=["IT"]),
            Offer(country="IT", service="Disney", quality="4k", audio_languages=["IT"], subtitle_languages=["JA"]),
        ]

        ranked = rank_offers(offers, pref)

        self.assertEqual(len(ranked), 3)

        # Top offer must be 4K matching
        self.assertEqual(ranked[0].country, "IT")
        self.assertTrue(ranked[0].is_language_match)
        self.assertEqual(ranked[0].quality, "4k")

        # Second offer must be SD matching
        self.assertEqual(ranked[1].country, "JP")
        self.assertTrue(ranked[1].is_language_match)
        self.assertEqual(ranked[1].quality, "sd")

        # Last offer must be HD non-matching
        self.assertEqual(ranked[2].country, "US")
        self.assertFalse(ranked[2].is_language_match)
        self.assertEqual(ranked[2].quality, "hd")

    def test_evaluate_v2_viewers_rule(self):
        v2_rule = {
            "type": "viewers",
            "viewers": [
                {"name": "Andrea", "languages": ["EN", "IT"]},
                {"name": "Mayu", "languages": ["JA"]},
            ],
        }

        # Case 1: Audio EN + Sub JA -> Andrea speaks EN, Mayu speaks JA -> Match
        self.assertTrue(evaluate_language_rule(v2_rule, ["EN"], ["JA"]))

        # Case 2: Audio IT + Sub JA -> Andrea speaks IT, Mayu speaks JA -> Match
        self.assertTrue(evaluate_language_rule(v2_rule, ["IT"], ["JA"]))

        # Case 3: Audio JA + Sub IT -> Andrea speaks IT, Mayu speaks JA -> Match
        self.assertTrue(evaluate_language_rule(v2_rule, ["JA"], ["IT"]))

        # Case 4: Audio EN + Sub EN -> Andrea speaks EN, Mayu speaks no JA -> No match
        self.assertFalse(evaluate_language_rule(v2_rule, ["EN"], ["EN"]))

        # Case 5: Audio JA + Sub JA -> Andrea speaks no IT/EN, Mayu speaks JA -> No match
        self.assertFalse(evaluate_language_rule(v2_rule, ["JA"], ["JA"]))

        # Case 6: Audio FR + Sub FR -> Neither speaks FR -> No match
        self.assertFalse(evaluate_language_rule(v2_rule, ["FR"], ["FR"]))

    def test_preference_serializer_v2_viewers_validation(self):
        from tracker.serializers import PreferenceSerializer

        valid_data = {
            "audio_languages": ["EN"],
            "subtitle_languages": ["IT"],
            "candidate_countries": ["US"],
            "language_rule": {
                "type": "viewers",
                "viewers": [
                    {"name": "Andrea", "languages": ["en", "it"]},
                    {"name": "Mayu", "languages": ["ja"]},
                ],
            },
        }

        serializer = PreferenceSerializer(data=valid_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        validated = serializer.validated_data
        self.assertEqual(
            validated["language_rule"]["viewers"][0]["languages"], ["EN", "IT"]
        )
        self.assertEqual(
            validated["language_rule"]["viewers"][1]["languages"], ["JA"]
        )

        invalid_data = {
            "language_rule": {
                "type": "viewers",
                "viewers": [
                    {"name": "InvalidPerson", "languages": ["XX"]},
                ],
            }
        }
        invalid_serializer = PreferenceSerializer(data=invalid_data)
        self.assertFalse(invalid_serializer.is_valid())
        self.assertIn("language_rule", invalid_serializer.errors)
