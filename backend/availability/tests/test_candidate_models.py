"""Unit tests for ProviderContentCandidate and TmdbSearchCache models."""
from django.db import IntegrityError
from django.test import TestCase

from availability.domain.enums import (
    CandidateResolutionCategory,
    CandidateReviewState,
    ContentType,
)
from availability.models import (
    ProviderContent,
    ProviderContentCandidate,
    Source,
    TmdbSearchCache,
)
from tracker.models import Provider


class CandidateModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.filter(slug="tubi").first() or Provider.objects.create(
            name="Tubi", slug="tubi"
        )
        self.source = Source.objects.filter(slug="tubi").first() or Source.objects.create(
            slug="tubi", name="Tubi Source"
        )
        self.content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="tubi-test-cand-1234",
            content_type=ContentType.MOVIE,
            title="Inception",
        )

    def test_tmdb_search_cache_unique_constraint(self):
        TmdbSearchCache.objects.create(
            query="inception",
            year=2010,
            content_type=ContentType.MOVIE,
            results=[{"id": 27205, "title": "Inception"}],
            is_negative=False,
        )

        with self.assertRaises(IntegrityError):
            TmdbSearchCache.objects.create(
                query="inception",
                year=2010,
                content_type=ContentType.MOVIE,
                results=[],
                is_negative=True,
            )

    def test_provider_content_candidate_unique_constraint(self):
        ProviderContentCandidate.objects.create(
            provider_content=self.content,
            tmdb_id=27205,
            title="Inception",
            year=2010,
            content_type=ContentType.MOVIE,
            confidence_score=1.0,
            review_state=CandidateReviewState.PENDING,
            resolution_category=CandidateResolutionCategory.UNIQUE_CANDIDATE,
        )

        with self.assertRaises(IntegrityError):
            ProviderContentCandidate.objects.create(
                provider_content=self.content,
                tmdb_id=27205,
                title="Inception Duplicate",
                year=2010,
                content_type=ContentType.MOVIE,
            )

    def test_review_states_and_rejection_preservation(self):
        candidate = ProviderContentCandidate.objects.create(
            provider_content=self.content,
            tmdb_id=99999,
            title="Wrong Inception",
            year=2010,
            content_type=ContentType.MOVIE,
            review_state=CandidateReviewState.REJECTED,
            rejection_reason="Not the same movie; user confirmed rejection",
        )

        self.assertEqual(candidate.review_state, CandidateReviewState.REJECTED)
        self.assertIn("user confirmed rejection", candidate.rejection_reason)
