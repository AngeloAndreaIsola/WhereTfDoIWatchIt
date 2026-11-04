"""Regression coverage for the PR #47 candidate-discovery safety contract."""
from datetime import timedelta
from unittest.mock import MagicMock

import requests
from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType
from availability.models import TmdbSearchCache
from availability.services.candidate_gateway import CandidateDiscoveryBudget, TMDbCandidateGateway
from availability.services.candidate_scoring import normalize_title


class Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload or {"results": []}
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(str(self.status_code))
            error.response = self
            raise error

    def json(self):
        return self.payload


class CandidateDiscoveryRegressionTests(TestCase):
    def gateway(self, session, *, budget=3, **kwargs):
        return TMDbCandidateGateway(
            session=session,
            budget=CandidateDiscoveryBudget(budget),
            sleep_fn=kwargs.pop("sleep_fn", lambda _seconds: None),
            **kwargs,
        )

    def test_network_authorization_requires_finite_positive_budget_at_gateway_boundary(self):
        session = MagicMock()
        with self.assertRaises(ValueError):
            TMDbCandidateGateway(session=session).search("Dune", authorize_network=True)
        session.get.assert_not_called()

    def test_redirect_is_rejected_explicitly_without_retrying_or_decoding(self):
        session = MagicMock()
        session.get.return_value = Response(302, {"not": "a search payload"})
        with self.assertRaises(requests.HTTPError):
            self.gateway(session).search("Dune", authorize_network=True)
        self.assertEqual(session.get.call_count, 1)

    def test_unicode_normalization_casefolds_but_preserves_unicode_word_characters(self):
        self.assertEqual(normalize_title("Amélie"), "amélie")
        self.assertEqual(normalize_title("Straße"), "strasse")
        self.assertEqual(normalize_title("Spider-Man: No Way Home"), "spider man no way home")

    def test_expired_positive_cache_is_refreshed_and_uses_full_identity(self):
        TmdbSearchCache.objects.create(
            query="dune", year=2021, year_fingerprint="year:2021",
            content_type=ContentType.MOVIE, locale="en-US", endpoint_version="v3",
            results=[{"tmdb_id": 1}], is_negative=False,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        session = MagicMock()
        session.get.return_value = Response(200, {"results": [{"id": 438631, "title": "Dune"}]})
        results = self.gateway(session).search("Dune", year=2021, authorize_network=True)
        self.assertEqual(results[0]["tmdb_id"], 438631)
        cache = TmdbSearchCache.objects.get(query="dune", year_fingerprint="year:2021")
        self.assertFalse(cache.is_negative)
        self.assertGreater(cache.expires_at, timezone.now() + timedelta(days=29))

    def test_successful_empty_result_is_negative_cached_for_seven_days_only(self):
        session = MagicMock()
        session.get.return_value = Response(200, {"results": []})
        self.assertEqual(self.gateway(session).search("New title", authorize_network=True), [])
        cache = TmdbSearchCache.objects.get(query="new title", year_fingerprint="year:none")
        self.assertTrue(cache.is_negative)
        self.assertGreater(cache.expires_at, timezone.now() + timedelta(days=6))
        self.assertLess(cache.expires_at, timezone.now() + timedelta(days=8))

    def test_cache_identity_distinguishes_locale_and_null_year(self):
        session = MagicMock()
        session.get.return_value = Response(200, {"results": []})
        self.gateway(session, locale="en-US").search("Amélie", authorize_network=True)
        self.gateway(session, locale="fr-FR").search("Amélie", authorize_network=True)
        self.assertEqual(TmdbSearchCache.objects.filter(year_fingerprint="year:none").count(), 2)

    def test_failed_request_never_creates_negative_cache(self):
        session = MagicMock()
        session.get.return_value = Response(401)
        with self.assertRaises(requests.HTTPError):
            self.gateway(session).search("Fail Query", authorize_network=True)
        self.assertFalse(TmdbSearchCache.objects.filter(query="fail query").exists())

    def test_request_pacing_applies_between_attempts_and_not_to_cache_hit(self):
        sleeps = []
        session = MagicMock()
        session.get.return_value = Response(200, {"results": []})
        gateway = self.gateway(session, min_request_interval_seconds=2.5, sleep_fn=sleeps.append)
        gateway.search("first", authorize_network=True)
        gateway.search("second", authorize_network=True)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 2.5, places=2)
        gateway.search("second", authorize_network=True)
        self.assertEqual(len(sleeps), 1)
