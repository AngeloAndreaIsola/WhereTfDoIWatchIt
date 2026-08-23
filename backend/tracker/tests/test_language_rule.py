from django.test import TestCase
from tracker.models import Preference
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
            {"country": "US", "service": "Netflix", "quality": "hd", "audio": ["EN"], "subs": ["EN"]},  # Non-matching
            {"country": "JP", "service": "Prime", "quality": "sd", "audio": ["JA"], "subs": ["IT"]},   # Matching SD
            {"country": "IT", "service": "Disney", "quality": "4k", "audio": ["IT"], "subs": ["JA"]},  # Matching 4K
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
