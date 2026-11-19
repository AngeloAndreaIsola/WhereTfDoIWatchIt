"""P0.3 — ambiguous title matches must never become confirmed availability.

``ContentMatcher.calculate_confidence`` awarded 0.4 for a raw substring match
and another 0.2 for a missing scraped year. That is exactly 0.6, the
auto-association threshold in ``match_item``, so ``Her`` could be confirmed as
``Hereditary`` — and, through the legacy ingestion bridge, become a trusted
``ContentMapping`` on the wrong film. A title-only match must stay unresolved
for review unless the title evidence is strong (normalized equality) or an
exact shared year backs a weak containment match.

These tests exercise both layers:

- the score itself (weak evidence stays below the threshold), and
- the acceptance gate in ``match_item`` (a score alone cannot confirm a
  mapping; the matcher also demands identity-grade title evidence and a
  unique winner).

The last class pins the behaviour that must *not* regress: exact normalized
titles (including punctuation/casing variants), exact title+year, and the
established series policy where a conflicting year is treated as a catalogue
re-add date rather than disqualifying evidence.
"""
from unittest.mock import MagicMock

from django.test import TestCase

from availability.models import ContentMapping
from tracker.models import Movie, Provider, ScrapedContentMapping
from tracker.scrapers.base import ContentMatcher, ScrapedItem
from tracker.services.tmdb import TMDbItem


def item(title, year=None, content_type="movie"):
    return ScrapedItem(
        title=title,
        provider_slug="nhk_world",
        year=year,
        content_type=content_type,
        source_url="https://hydra.example/watch",
    )


def candidate(tmdb_id, title, year=None, content_type="movie"):
    return TMDbItem(
        tmdb_id=tmdb_id,
        title=title,
        year=year,
        content_type=content_type,
    )


class ConfidenceScoreTests(TestCase):
    """The score keeps weak title evidence below the 0.6 acceptance line."""

    def test_a_short_title_inside_a_longer_unrelated_title_is_not_a_match(self):
        score = ContentMatcher.calculate_confidence(
            item("Her"), candidate(530385, "Hereditary", 2018)
        )
        self.assertLess(score, 0.6)

    def test_a_shared_year_cannot_rescue_a_substring_title(self):
        score = ContentMatcher.calculate_confidence(
            item("Her", 2018), candidate(530385, "Hereditary", 2018)
        )
        self.assertLess(score, 0.6)

    def test_exact_title_and_year_is_maximum_confidence(self):
        score = ContentMatcher.calculate_confidence(
            item("Fight Club", 1999), candidate(550, "Fight Club", 1999)
        )
        self.assertEqual(score, 1.0)

    def test_punctuation_and_casing_are_normalized(self):
        score = ContentMatcher.calculate_confidence(
            item("spider-man: no way home", 2021),
            candidate(634649, "Spider-Man: No Way Home", 2021),
        )
        self.assertEqual(score, 1.0)

    def test_a_conflicting_year_is_not_evidence_for_an_exact_title(self):
        score = ContentMatcher.calculate_confidence(
            item("The Thing", 1982), candidate(60935, "The Thing", 2011)
        )
        self.assertLessEqual(score, 0.6)


# Matching-policy regressions use the existing broadcaster path; unofficial
# providers are independently blocked before this algorithm can run.
class ConfirmedMappingTests(TestCase):
    """The acceptance gate: only identity-grade evidence becomes a mapping."""

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="nhk_world",
            defaults={"name": "Hydra", "category": "scraper", "is_enabled": True},
        )

    def match(self, scraped, candidates):
        matcher = ContentMatcher(tmdb_client=MagicMock())
        matcher.tmdb_client.search.return_value = list(candidates)
        return matcher.match_item(scraped)

    # -- the reported false positives -------------------------------------

    def test_her_is_not_confirmed_as_hereditary(self):
        matched, _score, _candidates, _confirmed = self.match(
            item("Her"),
            [candidate(530385, "Hereditary", 2018)],
        )
        self.assertIsNone(matched)
        mapping = ScrapedContentMapping.objects.get(
            provider=self.provider, scraped_title="Her", scraped_year=None
        )
        self.assertIsNone(mapping.movie)
        self.assertEqual(ContentMapping.objects.count(), 0)

    def test_exact_title_with_conflicting_years_stays_unresolved(self):
        matched, score, _candidates, _confirmed = self.match(
            item("The Thing", 1982),
            [candidate(60935, "The Thing", 2011)],
        )
        self.assertIsNone(matched)
        self.assertGreaterEqual(score, 0.6, "the score alone would have accepted")
        self.assertIsNone(
            ScrapedContentMapping.objects.get(
                provider=self.provider, scraped_title="The Thing",
                scraped_year=1982,
            ).movie
        )

    def test_similar_title_without_a_year_stays_unresolved(self):
        """"Dune" is a token-wise prefix of "Dune Part Two" — still not identity."""
        matched, score, _candidates, _confirmed = self.match(
            item("Dune"),
            [candidate(693134, "Dune: Part Two", 2024)],
        )
        self.assertIsNone(matched)
        self.assertGreaterEqual(score, 0.6, "the old 0.6 threshold would have won")

    def test_overlapping_words_with_the_same_year_stay_unresolved(self):
        matched, _score, _candidates, _confirmed = self.match(
            item("The Matrix", 1999),
            [candidate(604, "The Matrix Reloaded", 1999)],
        )
        self.assertIsNone(matched)

    def test_two_equally_good_candidates_are_ambiguous(self):
        matched, _score, _candidates, _confirmed = self.match(
            item("The Thing", 1982),
            [
                candidate(1091, "The Thing", 1982),
                candidate(60935, "The Thing", 1982),
            ],
        )
        self.assertIsNone(matched, "a tie must not silently pick the first result")

    # -- behaviour that must keep working ---------------------------------

    def test_exact_title_and_year_is_confirmed(self):
        matched, score, _candidates, _confirmed = self.match(
            item("Fight Club", 1999),
            [candidate(550, "Fight Club", 1999)],
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched.tmdb_id, 550)
        self.assertEqual(score, 1.0)
        self.assertEqual(ContentMapping.objects.get(tmdb_id=550).tmdb_id, 550)

    def test_punctuation_and_casing_variants_are_confirmed(self):
        matched, score, _candidates, _confirmed = self.match(
            item("spider-man: no way home", 2021),
            [candidate(634649, "Spider-Man: No Way Home", 2021)],
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched.tmdb_id, 634649)
        self.assertEqual(score, 1.0)

    def test_exact_title_unknown_year_is_still_confirmed(self):
        matched, _score, _candidates, _confirmed = self.match(
            item("Fight Club"),
            [candidate(550, "Fight Club", 1999)],
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched.tmdb_id, 550)

    def test_series_conflicting_year_keeps_its_established_policy(self):
        """A show's provider year may be a catalogue re-add date, not first air."""
        matched, _score, _candidates, _confirmed = self.match(
            item("Dune", 2021, content_type="series"),
            [candidate(90209, "Dune", 2024, content_type="series")],
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched.tmdb_id, 90209)

    def test_an_existing_local_row_still_matches_at_full_confidence(self):
        movie = Movie.objects.create(
            tmdb_id=800, title="Local Exact", year=2011, content_type="movie"
        )

        matched, score, _candidates, _confirmed = self.match(
            item("Local Exact", 2011),
            [],
        )

        self.assertEqual(matched, movie)
        self.assertEqual(score, 1.0)
