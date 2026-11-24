"""Offline acceptance tests for the watchlist-only Leaving Soon read path."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from availability.domain.enums import AvailabilityEvidence, ConflictKind
from availability.domain.models import ProviderOfferWithdrawal
from availability.models import Country, Offer, OfferEvidence, Source
from availability.persistence.repositories import OfferRepository
from availability.sources.pbs import PbsSource
from tracker.models import Movie, Preference, Provider, Subscription, WatchlistItem
from tracker.services.access import UserAccess
from tracker.views_leaving_soon import _confirmed_expiry, build_leaving_soon


NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def offer(movie, *, provider="netflix", country="NL", days=7,
          source="prime", evidence=AvailabilityEvidence.VERIFIED,
          conflict=None, offer_type="subscription", season=None,
          expiry=None, observation_evidence=None, mapping_doubt=""):
    end = NOW + timedelta(days=days) if expiry is None else expiry
    observer = SimpleNamespace(
        source=source, available_until=end, provider_content_id=str(movie.tmdb_id),
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

    @override_settings(WTDW_LEGACY_OWNER_ID=17)
    def test_repeated_episode_gets_only_select_and_keep_query_count_flat(self):
        country, _ = Country.objects.get_or_create(code="GB", defaults={"name": "United Kingdom"})
        source, _ = Source.objects.get_or_create(slug="bbc_iplayer", defaults={"name": "BBC iPlayer"})
        provider = Provider.objects.create(slug="fixture-iplayer", name="iPlayer", category="bvod")

        def add_series(tmdb_id):
            series = Movie.objects.create(tmdb_id=tmdb_id, content_type="series", title="Show")
            WatchlistItem.objects.create(user_id=17, movie=series, source="manual")
            Offer.objects.create(
                provider=provider, source=source, country=country,
                tmdb_id=tmdb_id, content_type="episode", season_number=1, episode_number=3,
                provider_content_id=str(tmdb_id), offer_type="free", audio_languages=["EN"],
                last_seen=NOW, available_until=NOW + timedelta(days=7),
            )

        def read():
            with CaptureQueriesContext(connection) as queries, \
                 patch("requests.sessions.Session.send", side_effect=AssertionError("unexpected HTTP")), \
                 patch("tracker.views_leaving_soon.timezone.now", return_value=NOW):
                response = self.client.get("/api/leaving-soon/?country=GB")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(q["sql"].lstrip().startswith("SELECT") for q in queries))
            return response.json(), len(queries)

        add_series(550)
        first, count = read()
        self.assertEqual(first["items"][0]["leaving_offers"][0]["episode_number"], 3)
        self.assertEqual(read(), (first, count))
        add_series(551)
        larger, larger_count = read()
        self.assertEqual(len(larger["items"]), 2)
        self.assertEqual(larger_count, count)


class LeavingSoonScopeTests(SimpleTestCase):
    """Exercise the real classifier with fixed read results and no database."""

    def setUp(self):
        self.series = Movie(id=5, tmdb_id=550, content_type="series", title="Show")
        rows = self.enterContext(patch.object(WatchlistItem.objects, "filter"))
        rows.return_value.select_related.return_value.order_by.return_value = [
            WatchlistItem(movie=self.series, user_id=17),
        ]
        self.preference = Preference(current_country="NL")
        self.enterContext(patch("tracker.views_leaving_soon.build_preference",
                                return_value=self.preference))
        self.access = UserAccess(included={"netflix"})
        self.enterContext(patch("tracker.views_leaving_soon.build_user_access", return_value=self.access))
        self.providers = []
        self.enterContext(patch.object(Provider.objects, "all", return_value=self.providers))
        self.query = self.enterContext(patch(
            "tracker.views_leaving_soon.AvailabilityQueryService.get_reconciled_for_contents"
        ))
        self.enterContext(patch("tracker.views_leaving_soon._recorded_presence", return_value={}))

    def departures(self, *offers, country="NL"):
        def lookup(pairs, *, now):
            return {key: [o for o in offers if (o.tmdb_id, o.content_type) == key]
                    for key in pairs}
        self.query.side_effect = lookup
        result = build_leaving_soon(user_id=17, country=country, now=NOW)
        self.query.assert_called_once()
        self.query.reset_mock()
        return [row for item in result["items"] for row in item["leaving_offers"]]

    def test_native_seasons_and_episodes_are_read_without_movie_id_collisions(self):
        season = offer(self.series, season=2, days=8)
        season.content_type = "season"
        episode = offer(self.series, season=1, source="bbc_iplayer")
        episode.content_type, episode.episode_number = "episode", 3
        unrelated = offer(Movie(tmdb_id=550, content_type="movie"))
        rows = self.departures(season, episode, unrelated)
        self.assertEqual([(r["season_number"], r["episode_number"], r["departure_kind"])
                          for r in rows], [(1, 3, "last_option"), (2, None, "last_option")])

    def test_a_sibling_episode_cannot_replace_the_expiring_episode(self):
        early = offer(self.series, season=1, days=7)
        later = offer(self.series, season=1, days=14)
        early.episode_number, later.episode_number = 1, 2
        self.assertEqual([r["departure_kind"] for r in self.departures(early, later)],
                         ["last_option", "last_option"])

    def test_pbs_aggregate_and_tubi_inherited_windows_cannot_confirm_departures(self):
        for source, kind in [("pbs", "series"), ("pbs", "season"), ("tubi", "season")]:
            with self.subTest(source=source, kind=kind):
                raw = offer(self.series, source=source, season=1 if kind == "season" else None)
                raw.content_type = kind
                self.assertEqual(_confirmed_expiry(raw, NOW), (None, ()))
        episode = offer(self.series, source="pbs", season=1)
        episode.content_type, episode.episode_number = "episode", 2
        self.assertEqual(_confirmed_expiry(episode, NOW), (episode.available_until, episode.observations))

    def test_foreign_offer_requires_reach_even_when_its_country_is_requested(self):
        foreign = offer(self.series, country="IT")
        self.assertEqual(self.departures(foreign, country="IT"), [])
        self.preference.vpn_enabled = True
        self.preference.candidate_countries = ["IT"]
        self.assertEqual([r["locality"] for r in self.departures(foreign, country="IT")], ["vpn"])

    def test_only_entitled_streams_and_addons_with_their_base_count(self):
        self.providers.append(Provider(slug="channel", name="Channel", config={"addon_parent": "prime"}))
        self.access.addon.add("channel")
        self.access.free.add("tubi")
        self.access.rent_buy.add("store")
        offers = [offer(self.series, provider=service, offer_type=kind) for service, kind in [
            ("netflix", "subscription"), ("channel", "subscription"), ("tubi", "free"),
            ("tubi", "free_with_ads"), ("store", "rent"), ("store", "buy"),
        ]]
        expected = {"subscription", "free", "free_with_ads"}
        self.assertEqual({r["access_type"] for r in self.departures(*offers)}, expected)
        self.access.included.add("prime")
        self.assertEqual({r["access_type"] for r in self.departures(*offers)}, expected | {"addon"})

    def test_invalid_or_ambiguous_countries_are_rejected_before_reading(self):
        for query in ["country=ZZ", "country=NL&country=IT", "country=ß"]:
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/api/leaving-soon/?" + query).status_code, 400)
        self.query.assert_not_called()

    @override_settings(WTDW_LEGACY_OWNER_ID=17)
    def test_anonymous_owner_follows_the_server_setting(self):
        with patch("tracker.views_leaving_soon.build_leaving_soon", return_value={}) as build:
            self.assertEqual(self.client.get("/api/leaving-soon/?country=NL&user_id=99").status_code, 200)
        self.assertEqual(build.call_args.kwargs["user_id"], 17)


@override_settings(WTDW_LEGACY_OWNER_ID=37)
class LeavingSoonDatabaseTests(TestCase):
    """Exercise the routed GET with stored observations and real entitlements."""

    @classmethod
    def setUpTestData(cls):
        cls.us, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        cls.nl, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        cls.direct, _ = Source.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        cls.jw, _ = Source.objects.get_or_create(slug="justwatch", defaults={"name": "JustWatch"})
        cls.avod = Provider.objects.create(slug="qa-avod", name="QA AVOD", category="avod")
        cls.other = Provider.objects.create(slug="qa-bvod", name="QA BVOD", category="bvod")
        cls.pref = Preference.objects.create(user_id=37, current_country="US", audio_languages=["en"])

    def title(self, tmdb_id, kind="movie", *, owner=37):
        movie = Movie.objects.create(tmdb_id=tmdb_id, content_type=kind, title=f"{kind} {tmdb_id}")
        WatchlistItem.objects.create(user_id=owner, movie=movie, source="manual")
        return movie

    def observation(self, title, *, provider=None, source=None, days=7, kind=None,
                    season=None, episode=None, offer_type="free_with_ads", country=None,
                    age=timedelta(0), native_id=None):
        source = source or self.direct
        return Offer.objects.create(
            tmdb_id=title.tmdb_id, content_type=kind or title.content_type,
            provider=provider or self.avod, source=source, country=country or self.us,
            season_number=season, episode_number=episode, offer_type=offer_type,
            provider_content_id=native_id or f"{source.slug}-{title.content_type}-{title.tmdb_id}",
            audio_languages=["en"], last_seen=NOW - age,
            available_until=None if days is None else NOW + timedelta(days=days),
        )

    def get(self, query="country=US", *, status=200, client=None):
        # Leave SQL, reconciliation, scoring and response rendering real.
        boundaries = (
            "availability.sources.registry.SourceRegistry.get",
            "availability.services.refresh.AvailabilityRefreshService.refresh_content",
            "availability.services.refresh.AvailabilityRefreshService.refresh_catalogue",
            "tracker.services.refresh_requests.request_availability_refresh",
            "requests.sessions.Session.send",
            "socket.socket.connect",
        )
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(path, side_effect=AssertionError(path)))
                      for path in boundaries]
            stack.enter_context(patch("tracker.views_leaving_soon.timezone.now", return_value=NOW))
            callbacks = stack.enter_context(self.captureOnCommitCallbacks())
            queries = stack.enter_context(CaptureQueriesContext(connection))
            response = (client or self.client).get("/api/leaving-soon/?" + query)
        for guard in guards:
            guard.assert_not_called()
        self.assertEqual(callbacks, [])
        statements = [query["sql"] for query in queries]
        self.assertTrue(all(sql.lstrip().startswith("SELECT ") for sql in statements), statements)
        self.assertEqual(response.status_code, status)
        return response.json(), len(statements)

    def test_id_collision_and_overlapping_scope_in_both_directions(self):
        movie = self.title(801)
        series = self.title(801, "series")
        source, _ = Source.objects.get_or_create(slug="bbc_iplayer", defaults={"name": "iPlayer"})
        self.observation(movie, days=2)
        self.observation(series, kind="series", days=3)
        self.observation(series, provider=self.other, source=source, kind="season", season=2, days=6)
        self.observation(series, kind="episode", season=2, episode=4, days=4)
        self.observation(series, kind="episode", season=1, episode=4, days=5)
        data, _ = self.get()
        summaries = {
            (item["movie"]["tmdb_id"], item["movie"]["content_type"]): [
                (row["provider"], row["season_number"], row["episode_number"], row["departure_kind"])
                for row in item["leaving_offers"]
            ] for item in data["items"]
        }
        self.assertEqual(summaries, {
            (801, "movie"): [("qa-avod", None, None, "last_option")],
            (801, "series"): [
                ("qa-avod", None, None, "leaving_provider"),
                ("qa-avod", 2, 4, "leaving_provider"),
                ("qa-avod", 1, 4, "last_option"),
                ("qa-bvod", 2, None, "last_option"),
            ],
        })
        self.observation(series, provider=self.other, kind="series", days=None)
        self.assertEqual([row["departure_kind"] for row in self.get()[0]["items"][1]["leaving_offers"]],
                         ["leaving_provider"] * 4)

    def test_pbs_parent_coverage_prevents_a_last_option_claim(self):
        series = self.title(802, "series")
        pbs, _ = Source.objects.get_or_create(slug="pbs", defaults={"name": "PBS"})
        provider, _ = Provider.objects.get_or_create(slug="pbs", defaults={"name": "PBS", "category": "bvod"})
        Subscription.objects.create(user_id=37, provider=provider, access_mode="free")
        _, rows = PbsSource()._show_rows({"slug": "qa-show", "title": "Show", "language": "en"}, [{
            "slug": "only-asset", "seasonOrdinal": 1, "episodeOrdinal": 2, "language": "en",
            "videoType": "FULL_LENGTH", "availability": "available", "expireDate": "2026-11-28",
        }])
        self.assertEqual([row.content_type for row in rows], ["series", "season", "episode"])
        for row in rows:
            Offer.objects.create(
                tmdb_id=series.tmdb_id, content_type=row.content_type, provider=provider,
                source=pbs, country=self.us, season_number=row.season_number,
                episode_number=row.episode_number, provider_content_id=row.provider_content_id,
                offer_type=row.offer_type, audio_languages=row.audio_languages, last_seen=NOW,
                available_until=row.available_until,
            )
        data, _ = self.get()
        self.assertEqual(data["coverage"]["offers_with_confirmed_expiry_data"], 1)
        self.assertEqual([(r["sources"], r["season_number"], r["episode_number"], r["departure_kind"])
                          for r in data["items"][0]["leaving_offers"]],
                         [(["pbs"], 1, 2, "leaving_provider")])

    def test_only_fresh_independent_matching_expiry_counts(self):
        for index, (direct_days, age, jw_days) in enumerate([
            (None, 0, None), (None, 0, 7), (7, 48, None), (7, 0, 9), (7, 0, 7),
        ]):
            movie = self.title(810 + index)
            self.observation(movie, days=direct_days, age=timedelta(hours=age))
            self.observation(movie, source=self.jw, days=jw_days)
        data, _ = self.get()
        self.assertEqual(data["coverage"]["watchlist_titles"], 5)
        self.assertEqual(data["coverage"]["offers_with_confirmed_expiry_data"], 1)
        self.assertEqual([(item["movie"]["tmdb_id"], item["leaving_offers"][0]["sources"])
                          for item in data["items"]], [(814, ["tubi"])])

    def shared_identity(self):
        title = self.title(820)
        repo = OfferRepository()
        identity = dict(
            provider=self.avod, country=self.us, tmdb_id=title.tmdb_id, content_type="movie",
            offer_type="free_with_ads", provider_content_id="shared-native-id", audio_languages=["en"],
        )
        previous, _ = repo.upsert(source=self.jw, now=NOW - timedelta(minutes=1),
                                  available_until=None, **identity)
        latest, created = repo.upsert(source=self.direct, available_until=NOW + timedelta(days=7),
                                      now=NOW, **identity)
        self.assertFalse(created)
        self.assertEqual(previous.pk, latest.pk)
        return repo, latest

    def test_reconstructible_end_date_conflict_and_resolution(self):
        movie = self.title(821)
        direct = self.observation(movie, days=7)
        aggregator = self.observation(movie, source=self.jw, days=9)
        self.assertNotEqual(direct.identity, aggregator.identity)
        data, _ = self.get()
        self.assertEqual(data["items"], [])
        self.assertEqual(data["coverage"]["offers_with_confirmed_expiry_data"], 0)
        aggregator.available_until = direct.available_until
        aggregator.save(update_fields=["available_until"])
        resolved, _ = self.get()
        self.assertEqual(resolved["coverage"]["offers_with_confirmed_expiry_data"], 1)
        self.assertEqual(resolved["items"][0]["leaving_offers"][0]["sources"], ["tubi"])

    def test_withdrawn_expiry_source_cannot_borrow_another_sources_presence(self):
        repo, latest = self.shared_identity()
        self.assertEqual(self.get()[0]["coverage"]["offers_with_confirmed_expiry_data"], 1)
        repo.withdraw_observed(
            source=self.direct, provider=self.avod, country=self.us,
            content=ProviderOfferWithdrawal(provider_id=self.avod.slug, country="US",
                                            provider_content_id=latest.provider_content_id, content_type="movie"),
        )
        self.assertEqual(dict(OfferEvidence.objects.filter(offer_identity=latest.identity)
                              .values_list("source__slug", "is_present")), {"tubi": False, "justwatch": True})
        data, _ = self.get()
        self.assertEqual(data["coverage"]["offers_with_confirmed_expiry_data"], 0)
        self.assertEqual(data["items"], [])

    def test_retired_source_cannot_supply_expiry_from_purged_evidence(self):
        repo, latest = self.shared_identity()
        repo.evidence.purge_for_source(self.direct)
        self.assertEqual(list(OfferEvidence.objects.filter(offer_identity=latest.identity)
                              .values_list("source__slug", "is_present")), [("justwatch", True)])
        data, _ = self.get()
        self.assertEqual(data["coverage"]["offers_with_confirmed_expiry_data"], 0)
        self.assertEqual(data["items"], [])

    def test_tubi_inherited_season_is_excluded_with_justwatch_agreement(self):
        series = self.title(825, "series")
        self.observation(series, kind="season", season=4)
        self.observation(series, source=self.jw, kind="season", season=4)
        self.observation(series, kind="episode", season=4, episode=1)
        data, _ = self.get()
        self.assertEqual([(r["season_number"], r["episode_number"], r["sources"])
                          for r in data["items"][0]["leaving_offers"]], [(4, 1, ["tubi"])])

    def test_request_country_filters_offers_and_saved_country_controls_reach(self):
        movie = self.title(830)
        self.observation(movie, days=2)
        self.observation(movie, country=self.nl, days=5)
        self.assertEqual(self.get("country=NL")[0]["items"], [])
        self.pref.candidate_countries = ["NL"]
        self.pref.vpn_enabled = True
        self.pref.save()
        for country, locality in [("NL", "vpn"), ("US", "local")]:
            rows = self.get("country=" + country)[0]["items"][0]["leaving_offers"]
            self.assertEqual([(r["country"], r["locality"], r["departure_kind"]) for r in rows],
                             [(country, locality, "last_option")])

    def test_ambiguous_country_never_queries_database(self):
        for query in ("country=US&country=US", "country=&country=US", "country=US&country=",
                      "country=US%2CNL", "country=QQ", "country=%C5%BFg"):
            with self.subTest(query=query):
                _, count = self.get(query, status=400)
                self.assertEqual(count, 0)

    def test_duplicate_days_returns_bad_request(self):
        for query in (
            "country=NL&days=7&days=7",
            "country=NL&days=7&days=14",
            "country=NL&days=14&days=7",
            "country=NL&days=&days=7",
            "country=NL&days=7&days=",
        ):
            with self.subTest(query=query):
                self.get(query, status=400)

    @override_settings()
    def test_anonymous_without_configured_owner_uses_legacy_owner_one(self):
        del settings.WTDW_LEGACY_OWNER_ID
        with patch("tracker.views_leaving_soon.build_leaving_soon", return_value={}) as build:
            self.get()
        build.assert_called_once_with(user_id=1, country="US", days=30)

    def test_session_principal_does_not_switch_the_installation_watchlist(self):
        user = get_user_model().objects.create_user(pk=71, username="fixture-owner")
        self.observation(self.title(832, owner=user.pk))
        self.observation(self.title(833))
        client = APIClient()
        client.force_authenticate(user=user)
        data, _ = self.get("country=US&user_id=71", client=client)
        self.assertEqual([item["movie"]["tmdb_id"] for item in data["items"]], [833])
        self.assertEqual(data["coverage"]["watchlist_titles"], 1)

    def test_persisted_entitlement_modes_and_qualifying_alternatives(self):
        movie = self.title(840)
        base = Provider.objects.create(slug="qa-base", name="Base")
        channel = Provider.objects.create(slug="qa-channel", name="Channel", config={"addon_parent": base.slug})
        store = Provider.objects.create(slug="qa-store", name="Store")
        unknown = Provider.objects.create(slug="qa-unknown", name="Unknown")
        for provider, mode in [(store, "rent_buy"), (unknown, "unrecognized"), (channel, "addon")]:
            Subscription.objects.create(provider=provider, user_id=37, access_mode=mode)
        for provider, kind in [(channel, "subscription"), (store, "rent"), (store, "buy"),
                               (unknown, "subscription"), (self.avod, "unknown")]:
            self.observation(movie, provider=provider, offer_type=kind, days=14)
        self.observation(movie, days=7)

        def summary():
            rows = self.get()[0]["items"][0]["leaving_offers"]
            return [(r["provider"], r["access_type"], r["departure_kind"]) for r in rows]

        self.assertEqual(summary(), [("qa-avod", "free_with_ads", "last_option")])
        Subscription.objects.create(provider=base, user_id=37, access_mode="included")
        self.assertEqual(summary(), [("qa-avod", "free_with_ads", "leaving_provider"),
                                     ("qa-channel", "addon", "last_option")])

    def test_bulk_reads_are_bounded_on_many_seasons_and_offers(self):
        bbc, _ = Source.objects.get_or_create(slug="bbc_iplayer", defaults={"name": "BBC iPlayer"})
        repo = OfferRepository()
        empty, empty_queries = self.get()
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["coverage"]["watchlist_titles"], 0)
        self.assertEqual(empty["coverage"]["status"], "no_confirmed_expiry_data")
        self.assertLessEqual(empty_queries, 6)
        previous = 0
        counts = []
        for size in [1, 12, 48]:
            for index in range(previous, size):
                series = self.title(900 + index, "series")
                for season in [1, 2, 3]:
                    for provider, source, kind, episode, days in [
                        (self.avod, bbc, "season", None, 8), (self.other, self.direct, "episode", 2, 7),
                    ]:
                        repo.upsert(
                            provider=provider, country=self.us, source=source, content_type=kind,
                            tmdb_id=series.tmdb_id, season_number=season, episode_number=episode,
                            offer_type="free_with_ads", provider_content_id=str(series.tmdb_id),
                            audio_languages=["en"], now=NOW, available_until=NOW + timedelta(days=days),
                        )
            previous = size
            data, count = self.get()
            self.assertEqual(len(data["items"]), size)
            self.assertEqual(data["coverage"]["offers_with_confirmed_expiry_data"], 6 * size)
            self.assertTrue(all(len(item["leaving_offers"]) == 6 for item in data["items"]))
            counts.append(count)
        self.assertEqual(counts, [counts[0]] * 3)
        self.assertLessEqual(counts[0], 12)
        self.assertEqual(self.get(), (data, counts[-1]))
        print(f"Leaving Soon SELECT counts for 0/1/12/48 titles: {[empty_queries, *counts]}")
