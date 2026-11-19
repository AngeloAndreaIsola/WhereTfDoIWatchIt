"""Comprehensive test suite for Wave 2A: Verified TMDb Mapping Acceptance.

Covers:
- Verified new mapping acceptance.
- Wrong ID/type verification failure.
- Separate movie/TV namespaces for overlapping numeric TMDb IDs.
- Already mapped same target (idempotency, no duplicate audit records).
- Different existing manual mapping (HTTP 409 CORRECTION_AUTHORIZATION_REQUIRED with impact manifest).
- Concurrent acceptance / optimistic concurrency revision check.
- Repeated acceptance idempotency.
- Candidate rejection with mandatory reason.
- Candidate rediscovery preserving human rejection and acceptance.
- High confidence never auto-accepts without human decision.
- Budget and network authorization enforcement.
- Verification that no Offer, OfferEvidence, or OfferPrice rows are created.
- Capability check: recovery_supported detection based on source registry.
- Admin candidates list & detail endpoints contract.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.utils import timezone

from tracker.models import Movie, Provider
from availability.domain.enums import (
    CandidateResolutionCategory,
    CandidateReviewState,
    ContentType,
    MatchMethod,
)
from availability.models import (
    CandidateReviewAudit,
    ContentMapping,
    Country,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    ProviderContentCandidate,
    Source,
    TmdbDetailsCache,
)
from availability.persistence.repositories import MappingRepository
from availability.services.candidate_acceptance import (
    CandidateAcceptanceError,
    CandidateAcceptanceService,
    CandidateRejectionService,
    check_recovery_supported,
)
from availability.services.candidate_gateway import (
    CandidateDiscoveryBudget,
    CandidateDiscoveryBudgetExceeded,
    TMDbCandidateGateway,
)
from availability.services.candidate_scoring import persist_candidates


class CandidateAcceptanceUnitTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="raiplay", defaults={"name": "RaiPlay", "is_enabled": True}
        )
        self.source, _ = Source.objects.get_or_create(
            slug="rai_play", defaults={"name": "RaiPlay Collector", "enabled": True}
        )
        self.content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="program-101",
            content_type=ContentType.MOVIE,
            title="Dune Part Two",
            provider_url="https://www.raiplay.it/programmi/dune2",
            currently_available=True,
        )
        self.candidate = ProviderContentCandidate.objects.create(
            provider_content=self.content,
            tmdb_id=693134,
            title="Dune: Part Two",
            year=2024,
            content_type=ContentType.MOVIE,
            confidence_score=0.95,
            review_state=CandidateReviewState.PENDING,
            resolution_category=CandidateResolutionCategory.UNIQUE_CANDIDATE,
        )

    def test_high_confidence_never_auto_accepts_in_persist_candidates(self):
        scored = [
            {
                "tmdb_id": 999999,
                "title": "Super Clear Match",
                "year": 2024,
                "content_type": ContentType.MOVIE,
                "confidence_score": 0.99,
                "scoring_evidence": {"title_relation": "exact", "year_match": "exact"},
            }
        ]
        persisted = persist_candidates(
            self.content,
            scored,
            resolution_category=CandidateResolutionCategory.UNIQUE_CANDIDATE,
        )
        self.assertEqual(len(persisted), 1)
        # Even with 0.99 confidence and UNIQUE_CANDIDATE, review_state MUST remain PENDING
        self.assertEqual(persisted[0].review_state, CandidateReviewState.PENDING)

    def test_rediscovery_preserves_explicit_rejection_and_acceptance(self):
        # 1. Candidate is rejected by human
        self.candidate.review_state = CandidateReviewState.REJECTED
        self.candidate.rejection_reason = "Wrong adaptation"
        self.candidate.save()

        scored = [
            {
                "tmdb_id": self.candidate.tmdb_id,
                "title": "Dune: Part Two",
                "year": 2024,
                "content_type": ContentType.MOVIE,
                "confidence_score": 0.98,
                "scoring_evidence": {"title_relation": "exact"},
            }
        ]
        persisted = persist_candidates(
            self.content,
            scored,
            resolution_category=CandidateResolutionCategory.UNIQUE_CANDIDATE,
        )
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.review_state, CandidateReviewState.REJECTED)
        self.assertEqual(self.candidate.rejection_reason, "Wrong adaptation")
        self.assertEqual(self.candidate.confidence_score, 0.98)

        # 2. Candidate is accepted by human
        self.candidate.review_state = CandidateReviewState.ACCEPTED
        self.candidate.save()
        persisted = persist_candidates(
            self.content,
            scored,
            resolution_category=CandidateResolutionCategory.UNIQUE_CANDIDATE,
        )
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.review_state, CandidateReviewState.ACCEPTED)

    def test_tmdb_candidate_gateway_details_budget_enforcement(self):
        budget = CandidateDiscoveryBudget(limit=1)
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 693134,
            "title": "Dune: Part Two",
            "release_date": "2024-03-01",
            "overview": "Paul Atreides unites with Chani...",
        }
        mock_session.get.return_value = mock_response

        gateway = TMDbCandidateGateway(budget=budget, session=mock_session)

        # First call succeeds and consumes the 1 budget token
        details = gateway.get_details(693134, content_type=ContentType.MOVIE, authorize_network=True)
        self.assertIsNotNone(details)
        self.assertEqual(details["tmdb_id"], 693134)
        self.assertEqual(budget.attempted, 1)
        self.assertEqual(budget.remaining, 0)

        # Second call for a different ID exceeds budget
        with self.assertRaises(CandidateDiscoveryBudgetExceeded):
            gateway.get_details(12345, content_type=ContentType.MOVIE, authorize_network=True)

    def test_tmdb_candidate_gateway_requires_authorization_or_cache(self):
        gateway = TMDbCandidateGateway()
        # When authorize_network=False and cache is empty, returns None without making HTTP calls
        details = gateway.get_details(693134, content_type=ContentType.MOVIE, authorize_network=False)
        self.assertIsNone(details)

        # Seed the cache
        TmdbDetailsCache.objects.create(
            tmdb_id=693134,
            content_type=ContentType.MOVIE,
            locale="en-US",
            endpoint_version="v3",
            details={"id": 693134, "title": "Dune: Part Two", "content_type": "movie"},
        )
        details = gateway.get_details(693134, content_type=ContentType.MOVIE, authorize_network=False)
        self.assertIsNotNone(details)
        self.assertEqual(details["id"], 693134)

    def test_safe_enrichment_keeps_overlapping_media_identifiers_separate(self):
        from availability.services.candidate_acceptance import safe_enrich_or_create_movie
        series = Movie.objects.create(tmdb_id=693134, title="Dune TV", content_type="series")
        movie = safe_enrich_or_create_movie(tmdb_id=693134, verified_type="movie",
            details={"title": "Dune: Part Two", "year": 2024})
        self.assertNotEqual(movie.pk, series.pk)
        self.assertEqual(movie.content_type, "movie")
        series.refresh_from_db()
        self.assertEqual(series.title, "Dune TV")
        self.assertEqual(safe_enrich_or_create_movie(tmdb_id=693134, verified_type="movie",
            details={"title": "Keep curated metadata"}).pk, movie.pk)
        self.assertEqual(Movie.objects.filter(tmdb_id=693134).count(), 2)

    def test_verified_new_mapping_acceptance_success(self):
        mock_gateway = MagicMock()
        mock_gateway.get_details.return_value = {
            "tmdb_id": 693134,
            "title": "Dune: Part Two",
            "year": 2024,
            "content_type": ContentType.MOVIE,
            "overview": "Epic sci-fi adventure",
            "poster_path": "/dune.jpg",
            "genres": [{"id": 878, "name": "Sci-Fi"}],
            "genre_ids": [878],
            "cast": [{"id": 1, "name": "Timothée Chalamet"}],
            "director": ["Denis Villeneuve"],
            "seasons_data": [],
            "runtime_minutes": 166,
            "tmdb_rating": 8.5,
        }

        service = CandidateAcceptanceService(gateway=mock_gateway)
        result = service.accept(
            provider_content_id=self.content.id,
            tmdb_id=693134,
            reason="Verified manual review",
            expected_revision=None,
            has_expected_revision=True,
        )

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["mapping"]["revision"], 1)
        self.assertEqual(result["candidate"]["review_state"], CandidateReviewState.ACCEPTED)

        # ContentMapping created
        mapping = ContentMapping.objects.get(
            provider=self.provider, provider_content_id=self.content.provider_content_id
        )
        self.assertEqual(mapping.tmdb_id, 693134)
        self.assertEqual(mapping.match_method, MatchMethod.MANUAL)
        self.assertEqual(mapping.revision, 1)

        # Movie created
        movie = Movie.objects.get(tmdb_id=693134)
        self.assertEqual(movie.title, "Dune: Part Two")
        self.assertEqual(movie.content_type, "movie")

        # Audit record created
        audit = CandidateReviewAudit.objects.get(
            provider_content=self.content, candidate_tmdb_id=693134
        )
        self.assertEqual(audit.action, "accept")
        self.assertEqual(audit.reason, "Verified manual review")
        self.assertEqual(audit.resulting_review_state, CandidateReviewState.ACCEPTED)
        self.assertEqual(audit.expected_revision, None)
        self.assertEqual(audit.resulting_revision, 1)

        # Verify NO Offers, OfferEvidence, or OfferPrice were created
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(OfferEvidence.objects.count(), 0)
        self.assertEqual(OfferPrice.objects.count(), 0)

    def test_stale_mapping_revision_protection(self):
        # Existing mapping at revision 1
        ContentMapping.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id=self.content.provider_content_id,
            content_type=ContentType.MOVIE,
            tmdb_id=693134,
            revision=1,
            match_method=MatchMethod.TITLE_YEAR,
        )

        mock_gateway = MagicMock()
        mock_gateway.get_details.return_value = {
            "tmdb_id": 693134,
            "title": "Dune: Part Two",
            "content_type": ContentType.MOVIE,
        }
        service = CandidateAcceptanceService(gateway=mock_gateway)

        # Expecting null when revision is 1 should raise 409 STALE_MAPPING_REVISION
        with self.assertRaises(CandidateAcceptanceError) as exc_info:
            service.accept(
                provider_content_id=self.content.id,
                tmdb_id=693134,
                reason="Stale check",
                expected_revision=None,
                has_expected_revision=True,
            )
        self.assertEqual(exc_info.exception.code, "STALE_MAPPING_REVISION")
        self.assertEqual(exc_info.exception.status_code, 409)

        # Expecting wrong revision 2 should also raise 409
        with self.assertRaises(CandidateAcceptanceError) as exc_info:
            service.accept(
                provider_content_id=self.content.id,
                tmdb_id=693134,
                reason="Stale check",
                expected_revision=2,
                has_expected_revision=True,
            )
        self.assertEqual(exc_info.exception.code, "STALE_MAPPING_REVISION")

    def test_competing_mapping_conflict_generates_impact_manifest(self):
        # Content is mapped to TMDb 111111
        ContentMapping.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id=self.content.provider_content_id,
            content_type=ContentType.MOVIE,
            tmdb_id=111111,
            revision=1,
            match_method=MatchMethod.MANUAL,
        )

        mock_gateway = MagicMock()
        mock_gateway.get_details.return_value = {
            "tmdb_id": 693134,
            "title": "Dune: Part Two",
            "content_type": ContentType.MOVIE,
        }
        service = CandidateAcceptanceService(gateway=mock_gateway)

        # Attempting to accept 693134 when mapped to 111111 must fail closed with manifest
        with self.assertRaises(CandidateAcceptanceError) as exc_info:
            service.accept(
                provider_content_id=self.content.id,
                tmdb_id=693134,
                reason="Switch mapping",
                expected_revision=1,
                has_expected_revision=True,
            )
        self.assertEqual(exc_info.exception.code, "CORRECTION_AUTHORIZATION_REQUIRED")
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertIn("manifest", exc_info.exception.extra)
        self.assertIn("digest", exc_info.exception.extra)

    def test_idempotent_repeated_acceptance_does_not_duplicate_audit(self):
        mock_gateway = MagicMock()
        mock_gateway.get_details.return_value = {
            "tmdb_id": 693134,
            "title": "Dune: Part Two",
            "content_type": ContentType.MOVIE,
        }
        service = CandidateAcceptanceService(gateway=mock_gateway)

        # First acceptance
        res1 = service.accept(
            provider_content_id=self.content.id,
            tmdb_id=693134,
            reason="Verified first time",
            expected_revision=None,
            has_expected_revision=True,
        )
        self.assertEqual(res1["status"], "accepted")
        self.assertEqual(CandidateReviewAudit.objects.count(), 1)

        # Repeated acceptance with the current revision (1)
        res2 = service.accept(
            provider_content_id=self.content.id,
            tmdb_id=693134,
            reason="Repeat accept",
            expected_revision=1,
            has_expected_revision=True,
        )
        self.assertEqual(res2["status"], "already_accepted")
        # No second audit row generated
        self.assertEqual(CandidateReviewAudit.objects.count(), 1)

    def test_candidate_rejection_success_and_mandatory_reason(self):
        service = CandidateRejectionService()

        # Empty reason fails
        with self.assertRaises(CandidateAcceptanceError) as exc_info:
            service.reject(
                provider_content_id=self.content.id,
                tmdb_id=693134,
                reason="",
            )
        self.assertEqual(exc_info.exception.code, "REASON_REQUIRED")

        # Valid rejection succeeds
        res = service.reject(
            provider_content_id=self.content.id,
            tmdb_id=693134,
            reason="Incorrect title match",
        )
        self.assertEqual(res["status"], "rejected")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.review_state, CandidateReviewState.REJECTED)
        self.assertEqual(self.candidate.rejection_reason, "Incorrect title match")

        audit = CandidateReviewAudit.objects.get(
            provider_content=self.content, candidate_tmdb_id=693134
        )
        self.assertEqual(audit.action, "reject")
        self.assertEqual(audit.reason, "Incorrect title match")

    def test_acceptance_cannot_bypass_rejected_state_or_typed_identity(self):
        gateway = MagicMock()
        service = CandidateAcceptanceService(gateway=gateway)
        for state, kind, code in (
            (CandidateReviewState.REJECTED, "movie", "CANDIDATE_REJECTED"),
            (CandidateReviewState.PENDING, "series", "CANDIDATE_TYPE_CONFLICT"),
            (CandidateReviewState.PENDING, "", "CANDIDATE_TYPE_CONFLICT"),
        ):
            self.candidate.review_state, self.candidate.content_type = state, kind
            self.candidate.save()
            before = list(ProviderContentCandidate.objects.values())
            with self.assertRaises(CandidateAcceptanceError) as error:
                service.accept(provider_content_id=self.content.pk, tmdb_id=self.candidate.tmdb_id,
                               reason="Explicit fixture", expected_revision=None)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(list(ProviderContentCandidate.objects.values()), before)
            self.assertFalse(ContentMapping.objects.exists())
            self.assertFalse(CandidateReviewAudit.objects.exists())
        gateway.get_details.assert_not_called()

    def test_rejection_of_alternative_candidate_preserves_verified_mapping(self):
        mapping = ContentMapping.objects.create(provider=self.provider, source=self.source,
            provider_content_id=self.content.provider_content_id, content=self.content,
            tmdb_id=1, content_type="movie", match_method="manual", confidence=1.0)
        before = list(ContentMapping.objects.filter(pk=mapping.pk).values())
        result = CandidateRejectionService().reject(provider_content_id=self.content.pk,
            tmdb_id=self.candidate.tmdb_id, reason="Different identity")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(list(ContentMapping.objects.filter(pk=mapping.pk).values()), before)

    def test_candidate_state_is_rechecked_after_details_verification(self):
        gateway = MagicMock()
        def reject_during_verification(**kwargs):
            ProviderContentCandidate.objects.filter(pk=self.candidate.pk).update(review_state="rejected")
            return {"tmdb_id": self.candidate.tmdb_id, "content_type": "movie", "title": "Fixture"}
        gateway.get_details.side_effect = reject_during_verification
        with self.assertRaises(CandidateAcceptanceError) as error:
            CandidateAcceptanceService(gateway=gateway).accept(
                provider_content_id=self.content.pk, tmdb_id=self.candidate.tmdb_id,
                reason="Concurrent fixture", expected_revision=None)
        self.assertEqual(error.exception.code, "CANDIDATE_REJECTED")
        self.assertFalse(ContentMapping.objects.exists())
        self.assertFalse(CandidateReviewAudit.objects.exists())

    def test_recovery_supported_provenance_check(self):
        # RaiPlay source supports targeted evidence and valid provider_content_id/provider_url exists
        self.assertTrue(check_recovery_supported(self.content))

        # Blank URL -> False
        self.content.provider_url = ""
        self.assertFalse(check_recovery_supported(self.content))


class CandidateAdminApiIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.provider, _ = Provider.objects.get_or_create(
            slug="raiplay", defaults={"name": "RaiPlay", "is_enabled": True}
        )
        self.source, _ = Source.objects.get_or_create(
            slug="rai_play", defaults={"name": "RaiPlay Collector", "enabled": True}
        )
        self.content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id="program-101",
            content_type=ContentType.MOVIE,
            title="Dune Part Two",
            provider_url="https://www.raiplay.it/programmi/dune2",
            currently_available=True,
        )
        self.candidate = ProviderContentCandidate.objects.create(
            provider_content=self.content,
            tmdb_id=693134,
            title="Dune: Part Two",
            year=2024,
            content_type=ContentType.MOVIE,
            confidence_score=0.95,
            review_state=CandidateReviewState.PENDING,
            resolution_category=CandidateResolutionCategory.UNIQUE_CANDIDATE,
        )

    def test_get_candidates_list_and_detail(self):
        # List endpoint
        resp = self.client.get("/api/admin/provider-content/candidates/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_items"], 1)
        self.assertEqual(data["items"][0]["title"], "Dune Part Two")
        self.assertTrue(data["items"][0]["recovery_supported"])
        self.assertEqual(len(data["items"][0]["candidates"]), 1)

        # Detail endpoint
        resp = self.client.get(f"/api/admin/provider-content/{self.content.id}/candidates/")
        self.assertEqual(resp.status_code, 200)
        detail_data = resp.json()
        self.assertEqual(detail_data["id"], self.content.id)
        self.assertEqual(detail_data["title"], "Dune Part Two")
        self.assertIsNone(detail_data["existing_mapping"])
        self.assertEqual(detail_data["candidates"][0]["tmdb_id"], 693134)

    def test_get_detail_handles_legacy_unverified_candidate(self):
        # Legacy candidate marked ACCEPTED without human review audit
        self.candidate.review_state = CandidateReviewState.ACCEPTED
        self.candidate.save()

        resp = self.client.get(f"/api/admin/provider-content/{self.content.id}/candidates/")
        self.assertEqual(resp.status_code, 200)
        cand_data = resp.json()["candidates"][0]
        self.assertEqual(cand_data["review_state"], "legacy_unverified")
        self.assertTrue(cand_data["is_legacy_unverified"])
        self.assertEqual(cand_data["stored_review_state"], "accepted")

    @patch.object(TMDbCandidateGateway, "get_details")
    def test_post_accept_endpoint_success(self, mock_get_details):
        mock_get_details.return_value = {
            "tmdb_id": 693134,
            "title": "Dune: Part Two",
            "year": 2024,
            "content_type": ContentType.MOVIE,
            "overview": "Sci-fi epic",
        }

        payload = {
            "reason": "Verified human review",
            "expected_revision": None,
            "authorize_network": False,
        }
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_OPERATOR_ROLE="admin",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "accepted")
        self.assertEqual(data["mapping"]["revision"], 1)

    def test_post_accept_missing_expected_revision_fails(self):
        payload = {
            "reason": "Missing expected revision field",
        }
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_OPERATOR_ROLE="admin",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["code"], "EXPECTED_REVISION_REQUIRED")

    def test_post_reject_endpoint_success(self):
        payload = {
            "reason": "Wrong title entirely",
        }
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/reject/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_OPERATOR_ROLE="admin",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "rejected")
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.review_state, CandidateReviewState.REJECTED)

    def test_security_cross_origin_and_unauthorized_mutation_guards(self):
        payload = {"reason": "Test", "expected_revision": None}

        # 1. Non-loopback unauthenticated request -> 403 REMOTE_OPERATOR_AUTHENTICATION_REQUIRED
        # Even with forged operator headers, remote IP must be rejected
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="198.51.100.25",
            HTTP_X_OPERATOR_ROLE="admin",
            HTTP_X_CORRELATION_ID="forged-correlation-id",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "REMOTE_OPERATOR_AUTHENTICATION_REQUIRED")

        # 2. Browser cross-site request (Sec-Fetch-Site: cross-site) -> 403 FORBIDDEN_CROSS_ORIGIN
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "FORBIDDEN_CROSS_ORIGIN")

        # 3. Cross-port localhost origin (e.g. localhost:9999) -> 403 FORBIDDEN_ORIGIN
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_ORIGIN="http://localhost:9999",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "FORBIDDEN_ORIGIN")

        # 4. Untrusted external Origin -> 403 FORBIDDEN_ORIGIN
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_ORIGIN="https://malicious-attacker-site.com",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "FORBIDDEN_ORIGIN")

        # 5. Wildcard-host setting does NOT trust arbitrary external origin
        with self.settings(ALLOWED_HOSTS=["*"]):
            resp = self.client.post(
                f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
                data=json.dumps(payload),
                content_type="application/json",
                REMOTE_ADDR="127.0.0.1",
                HTTP_ORIGIN="https://arbitrary-site.org",
            )
            self.assertEqual(resp.status_code, 403)
            self.assertEqual(resp.json()["code"], "FORBIDDEN_ORIGIN")

        # 6. Untrusted Referer header -> 403 FORBIDDEN_REFERER
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_REFERER="https://malicious-attacker-site.com/exploit",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "FORBIDDEN_REFERER")

        # 7. Non-JSON Content-Type -> 415 INVALID_CONTENT_TYPE
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data="reason=foo",
            content_type="application/x-www-form-urlencoded",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(resp.status_code, 415)
        self.assertEqual(resp.json()["code"], "INVALID_CONTENT_TYPE")

        # 8. Same-host different-scheme Origin (e.g. https when request is http) is rejected
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_HOST="localhost:8000",
            HTTP_ORIGIN="https://localhost:8000",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "FORBIDDEN_ORIGIN")

        # 9. Legitimate frontend origin (http://localhost:3001) with Content-Type: application/json succeeds
        with patch.object(TMDbCandidateGateway, "get_details") as mock_details:
            mock_details.return_value = {
                "tmdb_id": 693134,
                "title": "Dune: Part Two",
                "content_type": ContentType.MOVIE,
            }
            resp = self.client.post(
                f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
                data=json.dumps({"reason": "Valid from frontend", "expected_revision": None}),
                content_type="application/json",
                REMOTE_ADDR="127.0.0.1",
                HTTP_ORIGIN="http://localhost:3001",
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "accepted")

    def test_docker_compose_proxy_topology_simulation(self):
        """Simulate real Compose networking: Next.js rewrite in frontend container forwarding to backend."""
        frontend_container_ip = "172.19.0.4"
        untrusted_container_ip = "172.19.0.99"
        payload = {"reason": "Compose operator acceptance", "expected_revision": None}

        # Case A: Untrusted container on the bridge network attempting mutation -> 403
        with self.settings(INTERNAL_FRONTEND_PROXY_IPS=[frontend_container_ip]):
            resp = self.client.post(
                f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
                data=json.dumps(payload),
                content_type="application/json",
                REMOTE_ADDR=untrusted_container_ip,
                HTTP_ORIGIN="http://localhost:3001",
            )
            self.assertEqual(resp.status_code, 403)
            self.assertEqual(resp.json()["code"], "REMOTE_OPERATOR_AUTHENTICATION_REQUIRED")

        # Case B: Verified frontend proxy container forwarding request from browser Origin http://localhost:3001 -> 200 OK
        with self.settings(INTERNAL_FRONTEND_PROXY_IPS=[frontend_container_ip]):
            with patch.object(TMDbCandidateGateway, "get_details") as mock_details:
                mock_details.return_value = {
                    "tmdb_id": 693134,
                    "title": "Dune: Part Two",
                    "content_type": ContentType.MOVIE,
                }
                resp = self.client.post(
                    f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
                    data=json.dumps(payload),
                    content_type="application/json",
                    REMOTE_ADDR=frontend_container_ip,
                    HTTP_HOST="backend:8000",
                    HTTP_ORIGIN="http://localhost:3001",
                    HTTP_SEC_FETCH_SITE="same-site",
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["status"], "accepted")

    def test_authenticated_staff_requires_valid_csrf(self):
        """Ensure authenticated staff sessions enforce CSRF protection despite @csrf_exempt."""
        from django.contrib.auth.models import User
        from django.test import Client

        staff_user = User.objects.create_user(username="admin_op", password="secret", is_staff=True)
        csrf_client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
        csrf_client.force_login(staff_user)

        payload = {"reason": "Staff acceptance", "expected_revision": None}
        # Attempt POST without CSRF token -> 403 CSRF_FAILED
        resp = csrf_client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_ORIGIN="http://localhost:3001",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "CSRF_FAILED")

    @patch("availability.candidate_admin_views.CandidateAcceptanceService.accept")
    def test_internal_error_does_not_disclose_server_exception(self, mock_accept):
        mock_accept.side_effect = RuntimeError("Sensitive DB password in traceback: p4ssw0rd")
        payload = {"reason": "Test", "expected_revision": None}
        resp = self.client.post(
            f"/api/admin/provider-content/{self.content.id}/candidates/693134/accept/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_OPERATOR_ROLE="admin",
        )
        self.assertEqual(resp.status_code, 500)
        data = resp.json()
        self.assertEqual(data["code"], "INTERNAL_ERROR")
        self.assertNotIn("p4ssw0rd", data["error"])
        self.assertIn("internal server error occurred", data["error"].lower())
