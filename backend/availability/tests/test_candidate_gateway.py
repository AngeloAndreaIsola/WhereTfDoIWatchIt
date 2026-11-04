"""Unit tests for TMDbCandidateGateway: budget enforcement, caching, bounded retries, and Retry-After."""
from unittest.mock import MagicMock, patch
import requests
from django.test import TestCase

from availability.domain.enums import ContentType
from availability.models import TmdbSearchCache
from availability.services.candidate_gateway import (
    CandidateDiscoveryBudget,
    CandidateDiscoveryBudgetExceeded,
    TMDbCandidateGateway,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


class CandidateGatewayTests(TestCase):
    def setUp(self):
        self.session = MagicMock(spec=requests.Session)
        self.sleeps = []
        self.sleep_fn = lambda s: self.sleeps.append(s)

    def test_cache_hit_positive_consumes_zero_network_budget(self):
        TmdbSearchCache.objects.create(
            query="inception",
            year=2010,
            content_type=ContentType.MOVIE,
            results=[{"id": 27205, "title": "Inception"}],
            is_negative=False,
        )
        budget = CandidateDiscoveryBudget(limit=2)
        gateway = TMDbCandidateGateway(
            budget=budget,
            session=self.session,
            sleep_fn=self.sleep_fn,
        )

        res = gateway.search("Inception", year=2010, content_type=ContentType.MOVIE, authorize_network=True)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], 27205)
        self.assertEqual(budget.attempted, 0)
        self.assertEqual(budget.cache_hits, 1)
        self.session.get.assert_not_called()

    def test_cache_hit_negative_consumes_zero_network_budget(self):
        TmdbSearchCache.objects.create(
            query="nonexistent movie",
            year=2099,
            content_type=ContentType.MOVIE,
            results=[],
            is_negative=True,
        )
        budget = CandidateDiscoveryBudget(limit=2)
        gateway = TMDbCandidateGateway(
            budget=budget,
            session=self.session,
            sleep_fn=self.sleep_fn,
        )

        res = gateway.search("nonexistent movie", year=2099, content_type=ContentType.MOVIE, authorize_network=True)
        self.assertEqual(res, [])
        self.assertEqual(budget.attempted, 0)
        self.assertEqual(budget.cache_hits, 1)
        self.session.get.assert_not_called()

    def test_unauthorized_network_search_returns_empty_and_makes_no_requests(self):
        budget = CandidateDiscoveryBudget(limit=5)
        gateway = TMDbCandidateGateway(
            budget=budget,
            session=self.session,
            sleep_fn=self.sleep_fn,
        )

        res = gateway.search("uncached movie", year=2020, content_type=ContentType.MOVIE, authorize_network=False)
        self.assertEqual(res, [])
        self.assertEqual(budget.attempted, 0)
        self.session.get.assert_not_called()

    def test_successful_search_dispatches_under_budget_and_caches_result(self):
        self.session.get.return_value = FakeResponse(
            status_code=200,
            json_data={"results": [{"id": 550, "title": "Fight Club", "release_date": "1999-10-15"}]},
        )
        budget = CandidateDiscoveryBudget(limit=2)
        gateway = TMDbCandidateGateway(
            budget=budget,
            session=self.session,
            sleep_fn=self.sleep_fn,
            allow_cache_writes=True,
        )

        res = gateway.search("Fight Club", year=1999, content_type=ContentType.MOVIE, authorize_network=True)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["tmdb_id"], 550)
        self.assertEqual(budget.attempted, 1)
        self.assertEqual(budget.succeeded, 1)

        # Check cache was created
        cached = TmdbSearchCache.objects.get(query="fight club", year=1999, content_type=ContentType.MOVIE)
        self.assertFalse(cached.is_negative)
        self.assertEqual(len(cached.results), 1)

    def test_separate_endpoints_for_movie_and_series(self):
        self.session.get.return_value = FakeResponse(status_code=200, json_data={"results": []})
        budget = CandidateDiscoveryBudget(limit=5)
        gateway = TMDbCandidateGateway(budget=budget, session=self.session, sleep_fn=self.sleep_fn)

        gateway.search("Dune", year=2021, content_type=ContentType.MOVIE, authorize_network=True)
        self.assertIn("search/movie", self.session.get.call_args[0][0])
        self.assertEqual(self.session.get.call_args[1]["params"]["year"], 2021)

        gateway.search("Chernobyl", year=2019, content_type=ContentType.SERIES, authorize_network=True)
        self.assertIn("search/tv", self.session.get.call_args[0][0])
        self.assertEqual(self.session.get.call_args[1]["params"]["first_air_date_year"], 2019)

    def test_rate_limit_429_retry_after_seconds_and_date(self):
        # 1st attempt 429 with integer Retry-After, 2nd attempt success
        self.session.get.side_effect = [
            FakeResponse(status_code=429, headers={"Retry-After": "2"}),
            FakeResponse(status_code=200, json_data={"results": [{"id": 1, "title": "Ok"}]}),
        ]
        budget = CandidateDiscoveryBudget(limit=5)
        gateway = TMDbCandidateGateway(budget=budget, session=self.session, sleep_fn=self.sleep_fn)

        res = gateway.search("Retry Test", content_type=ContentType.MOVIE, authorize_network=True)
        self.assertEqual(len(res), 1)
        self.assertEqual(budget.attempted, 2)
        self.assertEqual(budget.retried, 1)
        self.assertEqual(self.sleeps, [2.0])

    def test_budget_exhaustion_stops_before_next_dispatch(self):
        budget = CandidateDiscoveryBudget(limit=1)
        gateway = TMDbCandidateGateway(budget=budget, session=self.session, sleep_fn=self.sleep_fn)
        self.session.get.return_value = FakeResponse(status_code=200, json_data={"results": []})

        gateway.search("Movie 1", content_type=ContentType.MOVIE, authorize_network=True)
        self.assertEqual(budget.attempted, 1)

        with self.assertRaises(CandidateDiscoveryBudgetExceeded):
            gateway.search("Movie 2", content_type=ContentType.MOVIE, authorize_network=True)

        self.assertEqual(self.session.get.call_count, 1)

    def test_permanent_4xx_does_not_retry_and_never_negative_caches(self):
        resp = FakeResponse(status_code=401, json_data={"status_message": "Invalid API key"})
        self.session.get.return_value = resp
        budget = CandidateDiscoveryBudget(limit=5)
        gateway = TMDbCandidateGateway(budget=budget, session=self.session, sleep_fn=self.sleep_fn)

        with self.assertRaises(requests.HTTPError):
            gateway.search("Fail Query", content_type=ContentType.MOVIE, authorize_network=True)

        self.assertEqual(budget.attempted, 1)
        self.assertEqual(self.session.get.call_count, 1)
        self.assertFalse(TmdbSearchCache.objects.filter(query="Fail Query").exists())
