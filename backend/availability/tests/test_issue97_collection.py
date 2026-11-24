import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from availability.models import AvailabilityExperimentAttempt, Country, Offer, Source
from availability.services.issue97_collection import collection_preflight, replay_scope
from tracker.models import Movie, Preference, Provider

FIXTURES = json.loads(
    Path(__file__).with_name("fixtures").joinpath("issue97_synthetic_justwatch.json").read_text()
)
INCEPTION = {
    "scope_id": "movie-27205-nl", "tmdb_id": 27205, "title": "Inception",
    "year": 2010, "media_type": "movie", "country": "NL",
}
BREAKING_BAD = {
    "scope_id": "series-1396-nl", "tmdb_id": 1396, "title": "Breaking Bad",
    "year": 2008, "media_type": "series", "country": "NL",
}


class Issue97ReplayTests(SimpleTestCase):
    @patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected HTTP"))
    def test_movies_preserve_six_offer_classes_and_report_mapping_gaps(self, http):
        report = replay_scope(INCEPTION, FIXTURES[INCEPTION["scope_id"]]["search"])
        self.assertEqual(
            {row["offer_class"] for row in report["title_offers"]},
            {"subscription", "addon", "rent", "buy", "free", "free_with_ads"},
        )
        self.assertEqual(len(report["unresolved"]), 2)
        self.assertEqual(report["mock_attempts"], ["GetSearchTitles"])
        self.assertEqual(report["provenance"], "SYNTHETIC_FIXTURE")
        http.assert_not_called()

    def test_repeated_offer_facts_collapse_after_normalization(self):
        search = deepcopy(FIXTURES[INCEPTION["scope_id"]]["search"])
        offers = search["data"]["popularTitles"]["edges"][0]["node"]["offers"]
        original = replay_scope(INCEPTION, search)["title_offers"]
        # An identical observation and an alternate rendition/price describe
        # the same comparable title, market, service and offer class.
        repeated = deepcopy(offers[0])
        offers.extend([repeated, {**repeated, "id": None, "presentationType": "SD",
                                  "retailPriceValue": 5.99}])

        self.assertEqual(replay_scope(INCEPTION, search)["title_offers"], original)

    def test_wrong_type_id_year_edition_or_duplicate_stays_unknown(self):
        for field, value in (
            ("objectType", "SHOW"), ("tmdbId", "438631"), ("tmdbId", ""),
            ("originalReleaseYear", 2020), ("title", "Inception Redux"), ("duplicate", True),
        ):
            with self.subTest(field=field, value=value):
                search = deepcopy(FIXTURES[INCEPTION["scope_id"]]["search"])
                edges = search["data"]["popularTitles"]["edges"]
                node = edges[0]["node"]
                if field == "duplicate":
                    edges.append(deepcopy(edges[0]))
                elif field == "objectType":
                    node[field] = value
                elif field == "tmdbId":
                    node["content"]["externalIds"][field] = value
                else:
                    node["content"][field] = value
                result = replay_scope(INCEPTION, search)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["title_offers"], [])
                self.assertFalse(result["authoritative"])

    def test_series_and_season_observations_remain_separate(self):
        fixture = FIXTURES[BREAKING_BAD["scope_id"]]
        title_only = replay_scope(BREAKING_BAD, fixture["search"])
        self.assertEqual(title_only["mock_attempts"], ["GetSearchTitles"])
        self.assertEqual(title_only["season_status"], "not_requested")
        full = replay_scope(BREAKING_BAD, fixture["search"], seasons_response=fixture["seasons"])
        self.assertEqual(full["mock_attempts"], ["GetSearchTitles", "GetTitleNode"])
        self.assertIsNone(full["title_offers"][0]["season_number"])
        self.assertEqual(full["season_offers"][0]["season_number"], 1)
        self.assertEqual(full["season_offers"][0]["tmdb_id"], 1396)

    def test_empty_or_failed_search_is_unknown_and_never_fetches_seasons(self):
        for payload in (
            {"data": {"popularTitles": {"edges": []}}},
            {"errors": [{"message": "synthetic GraphQL error"}]},
        ):
            result = replay_scope(
                BREAKING_BAD, payload, seasons_response=FIXTURES[BREAKING_BAD["scope_id"]]["seasons"],
            )
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["mock_attempts"], ["GetSearchTitles"])
            self.assertFalse(result["authoritative"])
        self.assertEqual(collection_preflight([BREAKING_BAD])["verdict"], "LIVE_COLLECTION_BLOCKED")


class Issue97ReadIsolationTests(TestCase):
    @patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected HTTP"))
    def test_replay_leaves_canonical_history_intact_and_normal_view_reads_both_buckets(self, http):
        movie = Movie.objects.create(
            tmdb_id=27205, content_type="movie", title="Inception", canonical_title="Inception", year=2010,
        )
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        source, _ = Source.objects.update_or_create(
            slug="justwatch", defaults={"name": "JustWatch", "type": "aggregator", "enabled": True},
        )
        Preference.objects.create(current_country="NL", candidate_countries=["NL"])
        now = timezone.now()
        for slug, label, stamp in (
            ("netflix", "Netflix", now - timedelta(days=90)), ("prime", "Prime Video", now),
        ):
            provider, _ = Provider.objects.get_or_create(slug=slug, defaults={"name": label, "category": "official"})
            Offer.objects.create(
                provider=provider, source=source, country=country, content_type="movie", tmdb_id=27205,
                offer_type="subscription", provider_content_id=slug, last_seen=stamp,
                observed_at=stamp, currently_available=True, identity="",
            )

        def observations():
            return list(Offer.objects.order_by("pk").values("pk", "last_seen", "observed_at", "currently_available"))

        before = observations()
        with self.assertNumQueries(0):
            for _ in range(2):
                replay_scope(INCEPTION, FIXTURES[INCEPTION["scope_id"]]["search"])
        self.assertEqual(observations(), before)
        self.assertEqual(AvailabilityExperimentAttempt.objects.count(), 0)

        response = self.client.get(reverse("movie-availability", kwargs={"tmdb_id": movie.tmdb_id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(row["service"], row["offer_type"], row["availability_evidence"]) for row in response.data["offers"]],
            [("prime", "subscription", "verified")],
        )
        self.assertEqual(
            [(row["service"], row["offer_type"], row["availability_evidence"])
             for row in response.data["last_known_offers"]],
            [("netflix", "subscription", "stale")],
        )
        http.assert_not_called()
