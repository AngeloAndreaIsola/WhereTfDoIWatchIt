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

    def test_presence_collapses_exact_repeats_and_keeps_distinct_variants(self):
        search = deepcopy(FIXTURES[INCEPTION["scope_id"]]["search"])
        edges = search["data"]["popularTitles"]["edges"]
        offers = edges[0]["node"]["offers"]
        rent = next(offer for offer in offers if offer["monetizationType"] == "RENT")
        rent.update(id="rent-hd", retailPriceValue=3.99, currency="EUR", presentationType="HD")
        offers.extend([
            deepcopy(rent),
            {**rent, "id": None, "retailPriceValue": 5.99},
            {**rent, "retailPriceValue": 4.99},
            {**rent, "presentationType": "SD"},
            {**rent, "monetizationType": "RENTAL"},
            {**rent, "package": {**rent["package"], "packageId": 987}},
        ])
        repeated_edge = deepcopy(edges[0])
        repeated_edge["node"]["content"]["externalIds"]["tmdbId"] = None
        repeated_edge["node"]["offers"] = [{**rent, "id": "another-provider-offer"}]
        edges.append(repeated_edge)
        partial_edge = deepcopy(edges[0])
        partial_edge["node"]["content"].pop("originalReleaseYear")
        partial_edge["node"]["offers"] = [{**rent, "id": "partial-edge-offer"}]
        edges.append(partial_edge)

        report = replay_scope(INCEPTION, search)
        self.assertEqual(len(report["title_offers"]), 6)
        fact = next(row for row in report["title_offers"] if row["offer_class"] == "rent")
        variants = fact["variants"]
        self.assertEqual(len(variants), 8)
        self.assertEqual({variant["amount"] for variant in variants}, {"3.9900", "4.9900", "5.9900"})
        self.assertEqual({variant["quality"] for variant in variants}, {"hd", "sd"})
        self.assertEqual({variant["offer_class"] for variant in variants}, {"rent"})
        edges.reverse()
        self.assertEqual(next(row for row in replay_scope(INCEPTION, search)["title_offers"]
                              if row["offer_class"] == "rent"), fact)

    def test_replay_audit_variants_exclude_signed_links_and_private_identifiers(self):
        fixture = deepcopy(FIXTURES[BREAKING_BAD["scope_id"]])
        title = fixture["search"]["data"]["popularTitles"]["edges"][0]["node"]
        season = fixture["seasons"]["data"]["node"]["seasons"][0]
        for offer in (title["offers"][0], season["offers"][0]):
            offer.update(id="fixture-private-audit-offer",
                         standardWebURL="https://example.test/audit?signature=fixture-audit-signature")
            offer["package"].update(id="fixture-private-audit-node", packageId=923456789)
        unresolved = deepcopy(title["offers"][0])
        unresolved["package"].update(technicalName="fixture-private-audit-package", shortName="", slug="")
        title["offers"].append(unresolved)

        report = replay_scope(BREAKING_BAD, fixture["search"], seasons_response=fixture["seasons"])
        for group in ("title_offers", "season_offers", "unresolved"):
            [fact] = report[group]
            [variant] = fact["variants"]
            self.assertEqual(set(variant), {"provider", "country", "offer_class", "quality", "amount", "currency"})
        serialized = json.dumps(report)
        for secret in ("https://example.test/audit", "fixture-audit-signature", "fixture-private-audit-offer",
                       "fixture-private-audit-node", "fixture-private-audit-package", "923456789"):
            self.assertNotIn(secret, serialized)

    def test_wrong_type_id_year_edition_or_conflicting_identity_stays_unknown(self):
        for field, value in (
            ("objectType", "SHOW"), ("tmdbId", "438631"), ("tmdbId", ""), ("id", None),
            ("originalReleaseYear", 2020), ("title", "Inception Redux"),
            ("alternate_native_id", "tm-other"), ("conflicting_id", "438631"),
        ):
            with self.subTest(field=field, value=value):
                search = deepcopy(FIXTURES[INCEPTION["scope_id"]]["search"])
                edges = search["data"]["popularTitles"]["edges"]
                node = edges[0]["node"]
                if field in ("alternate_native_id", "conflicting_id"):
                    conflict = deepcopy(edges[0])
                    if field == "alternate_native_id":
                        conflict["node"]["id"] = value
                    else:
                        conflict["node"]["content"]["externalIds"]["tmdbId"] = value
                    edges.append(conflict)
                elif field in ("id", "objectType"):
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

    def test_offline_reviewed_anchor_rejects_conflicting_labels_on_coalesced_edge(self):
        search = deepcopy(FIXTURES[INCEPTION["scope_id"]]["search"])
        conflicting = deepcopy(search["data"]["popularTitles"]["edges"][0])
        conflicting["node"]["content"]["externalIds"]["tmdbId"] = None
        conflicting["node"]["content"]["title"] = "Wrong Inception"
        search["data"]["popularTitles"]["edges"].append(conflicting)
        report = replay_scope(INCEPTION, search)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["title_offers"], [])
