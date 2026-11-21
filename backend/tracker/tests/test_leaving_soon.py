"""Offline acceptance tests for the watchlist-only Leaving Soon read path."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from availability.domain.enums import AvailabilityEvidence, ConflictKind
from tracker.models import Movie, WatchlistItem
from tracker.views_leaving_soon import build_leaving_soon


NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def offer(movie, *, provider="netflix", country="NL", days=7,
          source="prime", evidence=AvailabilityEvidence.VERIFIED,
          conflict=None, offer_type="subscription", season=None,
          expiry=None, observation_evidence=None, mapping_doubt=""):
    end = NOW + timedelta(days=days) if expiry is None else expiry
    observer = SimpleNamespace(
        source=source, available_until=end,
        evidence=observation_evidence or evidence,
        mapping_doubt=mapping_doubt, last_seen=NOW,
    )
    return SimpleNamespace(
        provider=provider, country=country, content_type=movie.content_type,
        tmdb_id=movie.tmdb_id, offer_type=offer_type, quality="HD",
        provider_url="https://example.invalid/title", audio_languages=("en",),
        subtitle_languages=("it",), price=None, prices=(),
        available_from=None, available_until=end,
        season_number=season, episode_number=None,
        currently_available=True, evidence=evidence,
        last_verified_at=NOW, last_seen_available_at=NOW,
        observations=(observer,),
        conflicts=() if conflict is None else (SimpleNamespace(kind=conflict),),
    )


class LeavingSoonTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        WatchlistItem.objects.create(user_id=1, movie=self.movie, source="manual")

    def _run(self, mapping, *, days=30, user_id=1, country="NL"):
        """Fixture reconciliation; mock entitlement only in these unit tests.

        The production endpoint uses the real shared classifier. Its entitlement
        and add-on rules have separate focused tests in test_access_modes_api.
        """
        def classified(raw, **_kwargs):
            qualifying = raw.offer_type not in {"rent", "buy"}
            return SimpleNamespace(
                is_accessible=raw.service != "unowned" and qualifying,
                is_qualifying_offer_type=qualifying,
                service_display=raw.service.capitalize(),
                access_type=raw.offer_type, locality="local",
            )

        with patch("tracker.views_leaving_soon.AvailabilityQueryService") as query, \
             patch("tracker.views_leaving_soon.classify_offer", side_effect=classified):
            query.return_value.get_reconciled_for_contents.return_value = mapping
            result = build_leaving_soon(
                user_id=user_id, country=country, days=days, now=NOW,
            )
            query.return_value.get_reconciled_for_contents.assert_called_once()
        return result

    def test_7_14_30_day_boundaries_and_sort(self):
        other = [Movie.objects.create(tmdb_id=551 + n, title=f"Item {n}") for n in range(3)]
        for movie in other:
            WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        mapping = {
            (self.movie.tmdb_id, "movie"): [offer(self.movie, days=7)],
            (other[0].tmdb_id, "movie"): [offer(other[0], days=14)],
            (other[1].tmdb_id, "movie"): [offer(other[1], days=30)],
            (other[2].tmdb_id, "movie"): [offer(other[2], days=31)],
        }
        self.assertEqual(len(self._run(mapping, days=7)["items"]), 1)
        self.assertEqual(len(self._run(mapping, days=14)["items"]), 2)
        result = self._run(mapping, days=30)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["coverage"]["offers_with_confirmed_expiry_data"], 4)
        dates = [x["leaving_offers"][0]["available_until"] for x in result["items"]]
        self.assertEqual(dates, sorted(dates))

    def test_justwatch_only_date_never_becomes_departure(self):
        mapping = {(550, "movie"): [offer(self.movie, source="justwatch")]}
        result = self._run(mapping)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["coverage"]["status"], "no_confirmed_expiry_data")

    def test_justwatch_date_not_confirmed_by_another_sources_presence(self):
        mixed = offer(self.movie, source="justwatch")
        verifier = SimpleNamespace(
            source="another_provider", available_until=None,
            evidence=AvailabilityEvidence.VERIFIED, mapping_doubt="", last_seen=NOW,
        )
        mixed.observations = mixed.observations + (verifier,)
        self.assertFalse(self._run({(550, "movie"): [mixed]})["items"])

    def test_conflicts_and_stale_or_unverified_observations_are_not_departures(self):
        candidates = [
            offer(self.movie, conflict=ConflictKind.WINDOW),
            offer(self.movie, conflict=ConflictKind.MAPPING),
            offer(self.movie, conflict=ConflictKind.AVAILABILITY),
            offer(self.movie, evidence=AvailabilityEvidence.STALE),
            offer(self.movie, observation_evidence=AvailabilityEvidence.STALE),
            offer(self.movie, mapping_doubt="bad mapping"),
        ]
        for candidate in candidates:
            self.assertEqual(self._run({(550, "movie"): [candidate]})["items"], [])

    def test_no_expiry_does_not_use_observation_time_as_departure(self):
        undated = offer(self.movie)
        undated.available_until = None
        undated.observations[0].available_until = None
        self.assertFalse(self._run({(550, "movie"): [undated]})["items"])

    def test_country_is_strictly_isolated_and_does_not_follow_vpn_candidates(self):
        it = offer(self.movie, country="IT")
        self.assertEqual(self._run({(550, "movie"): [it]}, country="NL")["items"], [])
        self.assertEqual(len(self._run({(550, "movie"): [it]}, country="IT")["items"]), 1)

    def test_avod_is_included_but_rent_buy_and_unowned_are_not(self):
        candidates = [
            offer(self.movie, offer_type="free_with_ads", provider="freeview"),
            offer(self.movie, offer_type="rent", provider="rental"),
            offer(self.movie, offer_type="buy", provider="store"),
            offer(self.movie, provider="unowned"),
        ]
        result = self._run({(550, "movie"): candidates})
        self.assertEqual([x["provider"] for x in result["items"][0]["leaving_offers"]], ["freeview"])

    def test_last_option_requires_all_accessible_alternatives_to_end(self):
        early = offer(self.movie, provider="early", days=7)
        later = offer(self.movie, provider="later", days=14)
        result = self._run({(550, "movie"): [early, later]})
        self.assertEqual([x["departure_kind"] for x in result["items"][0]["leaving_offers"]],
                         ["leaving_provider", "last_option"])
        unknown = offer(self.movie, provider="unknown")
        unknown.available_until = None
        unknown.observations[0].available_until = None
        result = self._run({(550, "movie"): [early, unknown]})
        self.assertEqual(result["items"][0]["leaving_offers"][0]["departure_kind"], "leaving_provider")

    def test_typed_numeric_id_collision_and_season_are_preserved(self):
        series = Movie.objects.create(tmdb_id=550, content_type="series", title="Different series")
        WatchlistItem.objects.create(user_id=1, movie=series, source="manual")
        mapping = {
            (550, "movie"): [offer(self.movie, days=7)],
            (550, "series"): [offer(series, days=14, season=2)],
        }
        result = self._run(mapping)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["movie"]["content_type"], "movie")
        self.assertEqual(result["items"][1]["leaving_offers"][0]["season_number"], 2)

    def test_empty_vs_no_departures_within_horizon(self):
        self.assertEqual(self._run({})["coverage"]["status"], "no_confirmed_expiry_data")
        future = self._run({(550, "movie"): [offer(self.movie, days=100)]})
        self.assertEqual(future["coverage"]["status"], "no_departures_within_horizon")

    def test_another_owner_is_never_returned(self):
        other = Movie.objects.create(tmdb_id=551, title="Someone else's title")
        WatchlistItem.objects.create(user_id=2, movie=other, source="manual")
        mapping = {(550, "movie"): [offer(self.movie)], (551, "movie"): [offer(other)]}
        result = self._run(mapping, user_id=1)
        self.assertEqual([x["movie"]["tmdb_id"] for x in result["items"]], [550])

    @override_settings(WTDW_LEGACY_OWNER_ID=1)
    def test_api_validates_country_horizon_and_is_read_only(self):
        for query in ["", "?country=NLD", "?country=NL&days=0", "?country=NL&days=-7",
                      "?country=NL&days=91", "?country=NL&days=nan"]:
            self.assertEqual(self.client.get("/api/leaving-soon/" + query).status_code, 400)
        with patch("tracker.views_leaving_soon.AvailabilityQueryService") as query:
            query.return_value.get_reconciled_for_contents.return_value = {}
            first = self.client.get("/api/leaving-soon/?country=nl&days=30")
            second = self.client.get("/api/leaving-soon/?country=NL")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["country"], "NL")
        self.assertEqual(first.json()["coverage"]["watchlist_titles"], 1)
        self.assertEqual(WatchlistItem.objects.filter(user_id=1).count(), 1)
