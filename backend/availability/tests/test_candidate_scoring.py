"""Adversarial and correctness unit tests for deterministic scoring and candidate categorization."""
import datetime
from django.test import TestCase

from availability.domain.enums import (
    CandidateResolutionCategory,
    CandidateReviewState,
    ContentType,
)
from availability.models import ProviderContent, ProviderContentCandidate, Source
from availability.services.candidate_scoring import (
    categorize_candidates,
    persist_candidates,
    score_candidate,
)
from tracker.models import Provider


class CandidateScoringTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.filter(slug="tubi").first() or Provider.objects.create(
            name="Tubi", slug="tubi"
        )
        self.source = Source.objects.filter(slug="tubi").first() or Source.objects.create(
            slug="tubi", name="Tubi Source"
        )

    def test_same_title_different_release_years(self):
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="thing-1982",
            content_type=ContentType.MOVIE,
            title="The Thing",
            release_date=datetime.date(1982, 1, 1),
        )

        cand_1982 = {"tmdb_id": 1091, "title": "The Thing", "year": 1982}
        cand_2011 = {"tmdb_id": 60935, "title": "The Thing", "year": 2011}

        score_82, ev_82 = score_candidate(content, cand_1982)
        score_11, ev_11 = score_candidate(content, cand_2011)

        self.assertGreater(score_82, score_11)
        self.assertEqual(ev_82["year_match"], "exact")
        self.assertEqual(ev_11["year_match"], "conflict")

    def test_localized_vs_original_title_matching(self):
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="spirited-away-it",
            content_type=ContentType.MOVIE,
            title="La città incantata",
            metadata={"original_title": "Sen to Chihiro no Kamikakushi"},
            release_date=datetime.date(2001, 1, 1),
        )

        cand = {
            "tmdb_id": 129,
            "title": "Spirited Away",
            "original_title": "Sen to Chihiro no Kamikakushi",
            "year": 2001,
        }
        score, ev = score_candidate(content, cand)
        self.assertGreaterEqual(score, 0.8)
        self.assertEqual(ev["title_relation"], "exact")
        self.assertEqual(ev["matched_target"], "original_title")

    def test_ambiguous_candidates_with_tied_scores(self):
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="pinocchio-amb",
            content_type=ContentType.MOVIE,
            title="Pinocchio",
            release_date=datetime.date(2022, 1, 1),
        )

        cands = [
            {"tmdb_id": 532639, "title": "Pinocchio", "year": 2022, "confidence_score": 1.0},
            {"tmdb_id": 511817, "title": "Guillermo del Toro's Pinocchio", "year": 2022, "confidence_score": 0.8},
            {"tmdb_id": 789100, "title": "Pinocchio", "year": 2022, "confidence_score": 1.0},
        ]

        category = categorize_candidates(content, cands)
        self.assertEqual(category, CandidateResolutionCategory.AMBIGUOUS)

    def test_year_conflict_and_missing_year(self):
        content_no_year = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="matrix-no-yr",
            content_type=ContentType.MOVIE,
            title="The Matrix",
        )
        cand = {"tmdb_id": 603, "title": "The Matrix", "year": 1999}
        score, ev = score_candidate(content_no_year, cand)
        self.assertEqual(ev["year_match"], "unknown_content_year")
        self.assertGreaterEqual(score, 0.6)

    def test_previous_candidate_rejection_survives_rediscovery(self):
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="reject-test",
            content_type=ContentType.MOVIE,
            title="Dune",
            release_date=datetime.date(2021, 1, 1),
        )

        # Pre-create candidate that human explicitly rejected
        rejected_cand = ProviderContentCandidate.objects.create(
            provider_content=content,
            tmdb_id=12345,
            title="Dune 1984",
            year=1984,
            content_type=ContentType.MOVIE,
            confidence_score=0.4,
            review_state=CandidateReviewState.REJECTED,
            rejection_reason="User explicitly chose not this remake",
        )

        scored = [
            {
                "tmdb_id": 12345,
                "title": "Dune 1984",
                "year": 1984,
                "content_type": ContentType.MOVIE,
                "confidence_score": 0.45,
                "scoring_evidence": {"note": "rediscovered"},
            }
        ]

        persisted = persist_candidates(content, scored, CandidateResolutionCategory.CONFLICT)
        self.assertEqual(len(persisted), 1)

        refreshed = ProviderContentCandidate.objects.get(pk=rejected_cand.pk)
        self.assertEqual(refreshed.review_state, CandidateReviewState.REJECTED)
        self.assertEqual(refreshed.rejection_reason, "User explicitly chose not this remake")
