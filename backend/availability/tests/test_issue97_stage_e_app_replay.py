"""Stage E typed HTTP reads of synthetic observations persisted in isolated PostgreSQL."""
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import TestCase

from availability.models import Country, Offer, Source, SyncRun
from availability.services.issue97_collection import replay_scope
from availability.services.issue97_stage_e_adjudication import (
    ReviewedDisplayTrace, Window, adjudicate, display_rate, display_retrieval,
    source_report_retrieval, summarize,
)
from availability.tests.test_issue97_stage_e_adjudication import STAMP, digest, fixture
from config.test_database import require_disposable_database
from tracker.models import Movie, Preference, Provider, Subscription, TitleAvailabilityCheck
from tracker.tests.availability_factories import local_offer


class TypedApplicationReplay(TestCase):
    def setUp(self):
        require_disposable_database(connection)
        self.clock = self.enterContext(patch("django.utils.timezone.now", return_value=STAMP))
        self.http = self.enterContext(patch("requests.sessions.Session.request",
                                            side_effect=AssertionError("unexpected network transport")))
        country = Country.objects.get(code="NL")
        Preference.objects.create(current_country="NL", candidate_countries=["NL"])
        movie = Movie.objects.create(tmdb_id=27205, content_type="movie", title="Inception", year=2010)
        Movie.objects.create(tmdb_id=27205, content_type="series", title="Fixture series", year=2010)
        self.scope = {"tmdb_id": movie.tmdb_id, "media_type": movie.content_type, "title": movie.title,
                      "year": movie.year, "country": country.code, "scope_id": "movie-27205-nl"}
        path = Path(__file__).with_name("fixtures") / "issue97_synthetic_justwatch.json"
        self.search = json.loads(path.read_bytes())[self.scope["scope_id"]]["search"]
        encoded = json.dumps(self.search, sort_keys=True, separators=(",", ":")).encode()
        self.source_digest = sha256(encoded).hexdigest()
        # The normal adapter parses the response through its mocked HTTP transport.
        report = replay_scope(self.scope, self.search)
        self.assertEqual(report["mock_attempts"], ["GetSearchTitles"])
        self.assertEqual(report["status"], "observed_fixture_offers")
        jw, _ = Source.objects.get_or_create(slug=report["source"], defaults={"name": "JustWatch"})
        netflix = Provider.objects.get(slug="netflix")
        run = SyncRun.objects.create(provider=netflix, source=jw, country=country,
                                     mode="targeted", status="succeeded")
        for fact in report["title_offers"]:
            if fact["provider"] != "netflix":
                continue
            provider = Provider.objects.get(slug=fact["provider"])
            values = dict(source=jw, country=country, content_type=fact["media_type"],
                          tmdb_id=fact["tmdb_id"], provider=provider, offer_type=fact["offer_class"],
                          provider_content_id=fact["jw_id"], observed_at=STAMP, last_seen=STAMP)
            Offer.objects.create(**values, sync_run=run)
        # Separate app-local controls for current and purchase visibility.
        for kind in ("subscription", "rent", "buy"):
            row = local_offer(movie, country="NL", service="prime", offer_type=kind,
                              provider_content_id=f"fixture-prime-{kind}")
            Offer.objects.filter(pk=row.pk).update(observed_at=STAMP, last_seen=STAMP)
        Subscription.objects.create(user_id=1, provider=Provider.objects.get(slug="prime"),
                                    name="Fixture Prime store", access_mode="rent_buy")
        TitleAvailabilityCheck.objects.create(
            movie=movie, tmdb_id=movie.tmdb_id, content_type=movie.content_type, country=country.code,
            source=jw.slug, last_attempted_at=STAMP, last_succeeded_at=STAMP, successful_run_ids=[run.pk],
        )
        self.netflix = netflix
        self.original = Offer.objects.get(sync_run=run)
        self.original.refresh_from_db()

    def read(self, media_type, tmdb_id=27205, market="NL", **headers):
        route = f"/api/prime/{media_type}/{tmdb_id}/?current_country={market}"
        response = self.client.get(route, **headers)
        print(f"Stage E fixture read {route} HTTP {response.status_code} "
              f"sha256={sha256(response.content).hexdigest()}")
        self.http.assert_not_called()
        return response

    def reviewed_report(self):
        pair, index, native = fixture()
        pair = replace(pair, index=replace(pair.index, native_id="synthetic-MOVIE-27205",
                                           package_id="netflix"),
                       index_receipt=replace(pair.index_receipt, payload_digest=
                                             "34803b1738c95fb61a8ffcbbbc158051d500ce2b8a45f84b171655d5b7a2b856"))
        return pair, replace(index, binding=pair.index, payload_digest=self.source_digest), native

    def reviewed_abstention(self, *, tmdb_id=27205, observed_at=STAMP):
        pair, index, native = self.reviewed_report()
        search = deepcopy(self.search)
        node = search["data"]["popularTitles"]["edges"][0]["node"]
        node["offers"] = []
        request_scope = self.scope
        if tmdb_id != pair.scope.tmdb_id:
            title = f"Fixture movie {tmdb_id}"
            scope = replace(pair.scope, tmdb_id=tmdb_id, canonical_title=title)
            node["id"] = f"synthetic-MOVIE-{tmdb_id}"
            node["content"].update(title=title, externalIds={"tmdbId": str(tmdb_id)})
            request_scope = {**self.scope, "tmdb_id": tmdb_id, "title": title, "scope_id": f"movie-{tmdb_id}-nl"}
            pair = replace(pair, candidate_id=f"fixture-row-{tmdb_id}", scope=scope,
                           index=replace(pair.index, native_id=node["id"], native_title=title),
                           first_party=replace(pair.first_party, native_id=f"fixture-netflix-{tmdb_id}",
                                               native_title=title, document_ref=digest(title)),
                           native_receipt=replace(pair.native_receipt, payload_digest=digest(f"native-{tmdb_id}")))
            native = replace(native, scope=scope, binding=pair.first_party,
                             payload_digest=pair.native_receipt.payload_digest)
        report = replay_scope(request_scope, search)
        self.assertEqual((report["status"], report["title_offers"]), ("unknown", []))
        payload = digest(json.dumps(search, sort_keys=True, separators=(",", ":")))
        window = replace(index.window, start=observed_at, end=observed_at, original=observed_at)
        pair = replace(pair, index_receipt=replace(pair.index_receipt, payload_digest=payload, window=window))
        return pair, replace(index, scope=pair.scope, binding=pair.index, payload_digest=payload,
                             window=window, signal=""), native

    def score_report(self, pair, index, native, response, **overrides):
        stamp = self.clock.return_value
        options = dict(requested_scope=("movie", pair.scope.tmdb_id, "NL"),
                       read=Window(stamp, stamp, timedelta(0), stamp), status=response.status_code,
                       body=response.json(), service_label=self.netflix.name)
        return source_report_retrieval(pair, native, index, **{**options, **overrides})

    def assert_display(self, result, state, projection, numerator, denominator):
        rate = display_rate([result])
        self.assertEqual((result.state, result.projection, rate.numerator, rate.denominator, rate.value),
                         (state, projection, numerator, denominator,
                          numerator / denominator if denominator else "UNDEFINED"))

    def reviewed_display_with_abstention(self):
        saved, report_source, _ = self.reviewed_report()
        pair, index, native = self.reviewed_abstention(observed_at=STAMP + timedelta(minutes=1))
        self.clock.return_value = STAMP + timedelta(minutes=2)
        self.assertEqual((self.original.content_type, self.original.tmdb_id, self.original.country.code,
                          self.original.provider.slug, self.original.offer_type),
                         (pair.scope.media_type, pair.scope.tmdb_id, pair.scope.market,
                          pair.scope.provider, pair.scope.offer_class))
        self.assertEqual((self.original.source.slug, self.original.provider_content_id,
                          self.original.observed_at, self.original.sync_run.source.slug),
                         (saved.index.issuer, saved.index.native_id, saved.index_receipt.window.original,
                          saved.index.issuer))
        self.assertNotEqual(report_source.payload_digest, index.payload_digest)
        trace = ReviewedDisplayTrace(saved.index, saved.index_receipt)
        return replace(pair, display_trace=trace), index, native, report_source

    def test_independent_positive_with_index_abstention_and_traceable_display(self):
        pair, index, native, report_source = self.reviewed_display_with_abstention()
        response = self.read("movie")
        self.assertEqual(response.status_code, 200)
        [row] = response.json()["source_reported_offers"]
        self.assertEqual(row["reporting_source"], self.original.source.slug)
        self.assertEqual(datetime.fromisoformat(row["observed_at"]), self.original.observed_at)
        decision = adjudicate(pair, index, native)
        self.assertEqual((decision.eligibility, decision.index, decision.advertised, decision.outcome),
                         ("eligible", "unknown", "yes", "abstention"))
        counts = summarize([decision], expected_manifest=[(pair.candidate_id, pair.scope)])
        self.assertEqual(counts["outcomes"], {"abstention": 1})
        self.assertEqual((counts["agreement"].numerator, counts["agreement"].denominator,
                          counts["agreement"].value, counts["full_panel_recall"]), (0, 0, "UNDEFINED", "UNDEFINED"))
        self.assert_display(self.score_report(pair, index, native, response, report_source=report_source),
                            "displayed", "unscored", 1, 1)

    def test_abstention_excludes_display_without_reviewed_source_lineage(self):
        pair, index, native, report_source = self.reviewed_display_with_abstention()
        response = self.read("movie")
        self.assertTrue(response.json()["source_reported_offers"])
        for label, reviewed, source in (
            ("no reviewed linkage", replace(pair, display_trace=None), report_source),
            ("no original contribution", pair, None),
            ("different document", pair, replace(report_source, binding=replace(report_source.binding,
                                                                                 document_ref=digest("other")))),
            ("different payload", pair, replace(report_source, payload_digest=digest("other"))),
            ("same payload as the abstention", replace(pair, display_trace=ReviewedDisplayTrace(
                report_source.binding, replace(pair.display_trace.receipt, payload_digest=index.payload_digest))),
             replace(report_source, payload_digest=index.payload_digest)),
        ):
            with self.subTest(label=label):
                self.assert_display(self.score_report(reviewed, index, native, response, report_source=source),
                                    "unresolved", "unscored", 0, 0)

        Offer.objects.filter(pk=self.original.pk).update(observed_at=STAMP - timedelta(seconds=1))
        response = self.read("movie")
        [row] = response.json()["source_reported_offers"]
        self.assertEqual(datetime.fromisoformat(row["observed_at"]), STAMP - timedelta(seconds=1))
        self.assert_display(self.score_report(pair, index, native, response, report_source=report_source),
                            "unresolved", "unscored", 0, 0)

    def test_independent_positive_with_index_abstention_and_empty_200_is_a_miss(self):
        pair, index, native = self.reviewed_report()
        self.original.delete()
        response = self.read("movie")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source_reported_offers"], [])
        self.assertEqual(self.score_report(pair, index, native, response).state, "miss")

        pair, index, native = self.reviewed_abstention()
        decision = adjudicate(pair, index, native)
        self.assertEqual((decision.eligibility, decision.index, decision.advertised, decision.outcome),
                         ("eligible", "unknown", "yes", "abstention"))
        result = self.score_report(pair, index, native, response)
        rate = display_rate([result])
        self.assertEqual((result.state, result.projection, rate.numerator, rate.denominator, rate.value),
                         ("miss", "unscored", 0, 1, 0.0))

    def test_typed_app_404_counts_as_miss_and_provider_404_does_not(self):
        pair, index, native = self.reviewed_abstention()
        Movie.objects.get(tmdb_id=27205, content_type="movie").delete()
        response = self.read("movie")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["message"], "Movie not tracked.")
        self.assertEqual(self.read("series").status_code, 200)
        result = self.score_report(pair, index, native, response)
        rate = display_rate([result])
        self.assertEqual((result.state, result.projection, rate.numerator, rate.denominator, rate.value),
                         ("miss", "unscored", 0, 1, 0.0))
        for no_provider_proof in (replace(native, status=404), replace(native, signal="")):
            result = self.score_report(pair, index, no_provider_proof, response)
            self.assertEqual(result.state, "unscored")
            self.assertEqual(display_rate([result]).value, "UNDEFINED")

    def test_same_document_different_payloads_only_support_projection(self):
        pair, index, native = self.reviewed_report()
        pair = replace(pair, index=replace(pair.index, document_ref=native.binding.document_ref))
        index = replace(index, binding=pair.index)
        self.assertNotEqual(index.payload_digest, native.payload_digest)
        self.assertEqual(adjudicate(pair, index, native).eligibility, "same_source_echo")
        result = self.score_report(pair, index, native, self.read("movie"))
        self.assertEqual((result.state, result.projection), ("same_source_echo", "displayed"))
        self.assertEqual(display_rate([result]).denominator, 0)

    def test_abstention_does_not_admit_a_display_echo_of_the_first_party(self):
        pair, index, native, report_source = self.reviewed_display_with_abstention()
        binding = replace(index.binding, document_ref=digest("abstaining operation"))
        pair, index = replace(pair, index=binding), replace(index, binding=binding)
        response = self.read("movie")
        for changes in (
            {"binding": replace(native.binding, document_ref=report_source.binding.document_ref)},
            {"payload_digest": report_source.payload_digest},
        ):
            with self.subTest(changes=changes):
                echoed = replace(native, **changes)
                reviewed = replace(pair, first_party=echoed.binding,
                                   native_receipt=replace(pair.native_receipt, payload_digest=echoed.payload_digest))
                self.assertEqual(adjudicate(reviewed, index, echoed).outcome, "abstention")
                self.assert_display(self.score_report(reviewed, index, echoed, response, report_source=report_source),
                                    "same_source_echo", "unscored", 0, 0)

    def test_identical_payload_cannot_verify_a_typed_display_or_a_miss(self):
        pair, index, native = self.reviewed_report()
        pair = replace(pair, native_receipt=replace(pair.native_receipt, payload_digest=index.payload_digest))
        native = replace(native, payload_digest=index.payload_digest)
        for projection in ("displayed", "miss"):
            result = self.score_report(pair, index, native, self.read("movie"))
            self.assertEqual((result.state, result.projection), ("same_source_echo", projection))
            self.assertEqual(display_rate([result]).value, "UNDEFINED")
            if projection == "displayed":
                self.original.delete()

    def test_child_rows_do_not_verify_a_title_display(self):
        pair, index, native = self.reviewed_report()
        abstaining, unknown, _, report_source = self.reviewed_display_with_abstention()
        for episode in (None, 2):
            Offer.objects.filter(pk=self.original.pk).update(season_number=1, episode_number=episode)
            response = self.read("movie")
            [row] = response.json()["source_reported_offers"]
            self.assertEqual((row["season_number"], row["episode_number"]), (1, episode))
            result = self.score_report(pair, index, native, response)
            self.assertEqual((result.state, result.projection), ("unresolved", "unscored"))
            self.assertEqual(display_rate([result]).denominator, 0)
            result = self.score_report(abstaining, unknown, native, response, report_source=report_source)
            self.assert_display(result, "unresolved", "unscored", 0, 0)

    def test_abstention_does_not_admit_wrong_typed_route_market_provider_or_class(self):
        pair, index, native, report_source = self.reviewed_display_with_abstention()
        for media_type, tmdb_id, market in (("series", 27205, "NL"), ("movie", 999999, "NL"), ("movie", 27205, "BE")):
            with self.subTest(media_type=media_type, tmdb_id=tmdb_id, market=market):
                response = self.read(media_type, tmdb_id, market)
                result = self.score_report(pair, index, native, response, report_source=report_source,
                                           requested_scope=(media_type, tmdb_id, market))
                self.assert_display(result, "unresolved", "unscored", 0, 0)

        response = self.read("movie")
        for changes in ({"media_type": "series"}, {"market": "BE"}, {"provider": "prime"}, {"offer_class": "rent"}):
            with self.subTest(changes=changes):
                source = replace(report_source, scope=replace(report_source.scope, **changes))
                self.assert_display(self.score_report(pair, index, native, response, report_source=source),
                                    "unresolved", "unscored", 0, 0)

    def test_abstention_with_malformed_http_or_unreviewed_conflict_is_unresolved(self):
        pair, index, native, report_source = self.reviewed_display_with_abstention()
        invalid = self.read("movie", HTTP_ACCEPT="application/x-stage-e-unsupported")
        self.assertEqual(invalid.status_code, 406)
        self.assert_display(self.score_report(pair, index, native, invalid, report_source=report_source),
                            "unresolved", "unscored", 0, 0)
        response = self.read("movie")
        self.assert_display(self.score_report(pair, index, native, response, report_source=report_source,
                                              body={**response.json(), "source_reported_offers": None}),
                            "unresolved", "unscored", 0, 0)

        disputed = replace(index, disputed=True)
        self.assertEqual(adjudicate(pair, disputed, native).outcome, "needs_recheck")
        self.assert_display(self.score_report(pair, disputed, native, response, report_source=report_source),
                            "unresolved", "unscored", 0, 0)

    def test_abstention_does_not_admit_stale_or_ineligible_display_evidence(self):
        pair, index, native, report_source = self.reviewed_display_with_abstention()
        response = self.read("movie")
        for source in (replace(report_source, freshness="last_known"), replace(report_source, status=404)):
            self.assert_display(self.score_report(pair, index, native, response, report_source=source),
                                "unresolved", "unscored", 0, 0)
        for native_without_positive in (replace(native, signal=""), replace(native, freshness="last_known")):
            result = self.score_report(pair, index, native_without_positive, response, report_source=report_source)
            self.assert_display(result, "unscored", "unscored", 0, 0)

        for read in (replace(index.window, uncertainty=timedelta(seconds=6)), report_source.window):
            result = self.score_report(pair, index, native, response, report_source=report_source, read=read)
            self.assert_display(result, "unresolved", "unscored", 0, 0)
        self.clock.return_value = STAMP + timedelta(minutes=10)
        still_displayed = self.read("movie")
        self.assertTrue(still_displayed.json()["source_reported_offers"])
        result = self.score_report(pair, index, native, still_displayed, report_source=report_source)
        self.assert_display(result, "unresolved", "unscored", 0, 0)

        self.clock.return_value = STAMP + timedelta(days=8)
        stale = self.read("movie")
        self.assertEqual(stale.json()["source_reported_offers"], [])
        self.assertTrue(stale.json()["last_known_offers"])
        self.assert_display(self.score_report(pair, index, native, stale, report_source=report_source),
                            "unscored", "unscored", 0, 0)

    def test_mixed_displays_and_misses_do_not_establish_full_panel_accuracy(self):
        pair, index, native, report_source = self.reviewed_display_with_abstention()
        response = self.read("movie")
        decisions = [adjudicate(pair, index, native)]
        displays = [self.score_report(pair, index, native, response, report_source=report_source)]
        for tmdb_id, status in ((100001, 200), (100002, 404)):
            absent, unknown, positive = self.reviewed_abstention(tmdb_id=tmdb_id)
            if status == 200:
                Movie.objects.create(tmdb_id=tmdb_id, content_type="movie", title=absent.scope.canonical_title,
                                     year=absent.scope.year)
            missing = self.read("movie", tmdb_id)
            self.assertEqual(missing.status_code, status)
            decisions.append(adjudicate(absent, unknown, positive))
            display = self.score_report(absent, unknown, positive, missing)
            self.assert_display(display, "miss", "unscored", 0, 1)
            displays.append(display)

        self.assertEqual((display_rate(displays[:2]).numerator, display_rate(displays[:2]).denominator), (1, 2))
        rate = display_rate(displays)
        self.assertEqual((rate.numerator, rate.denominator, rate.value), (1, 3, 1 / 3))
        manifest = [(decision.candidate_id, decision.scope) for decision in decisions]
        complete = summarize(decisions, expected_manifest=manifest)
        self.assertEqual(complete["outcomes"], {"abstention": 3})
        self.assertEqual(complete["conditional_recall"].value, "UNDEFINED")
        self.assertEqual(complete["full_panel_recall"], "UNDEFINED")
        partial = summarize(decisions[:-1], expected_manifest=manifest)
        self.assertEqual((partial["panel_completeness"], partial["missing"], partial["full_panel_precision"],
                          partial["full_panel_recall"]), ("incomplete", [decisions[-1].candidate_id],
                                                          "UNDEFINED", "UNDEFINED"))

    def test_source_report_is_typed_and_retains_its_original_source_and_observation(self):
        self.clock.return_value = STAMP + timedelta(minutes=1)
        response = self.read("movie")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        title = payload["movie"]
        self.assertEqual((title["content_type"], title["tmdb_id"], title["year"], title["title"]),
                         ("movie", 27205, 2010, "Inception"))
        [row] = payload["source_reported_offers"]
        self.assertEqual((row["service"], row["offer_type"], row["country"], row["reporting_source"]),
                         (self.netflix.name, "subscription", "NL", self.original.source.slug))
        self.assertEqual(row["availability_evidence"], "source_reported")
        self.assertIsNone(row["is_entitled"])
        self.assertFalse(row["playback_verified"])
        self.assertEqual(datetime.fromisoformat(row["observed_at"]), self.original.observed_at)
        self.assertEqual(datetime.fromisoformat(row["last_seen_at"]), STAMP)
        self.assertEqual(self.original.provider_content_id, "synthetic-MOVIE-27205")
        pair, index, native = self.reviewed_report()
        observed = datetime.fromisoformat(row["observed_at"])
        source_window = replace(index.window, original=observed)
        local = Window(self.clock.return_value, self.clock.return_value, timedelta(0), self.clock.return_value)
        app = replace(index, producer="application", document_kind="source_reported_offers",
                      window=source_window, status=response.status_code,
                      binding=replace(pair.index, issuer=row["reporting_source"],
                                      native_id=self.original.provider_content_id,
                                      package_id=self.original.provider.slug),
                      scope=replace(pair.scope, media_type=title["content_type"], tmdb_id=title["tmdb_id"],
                                    canonical_title=title["title"], year=title["year"],
                                    offer_class=row["offer_type"], market=row["country"]))
        app_result = adjudicate(pair, app, native, index_name="application", app_read=local)
        self.assertEqual((app_result.index, app_result.outcome), ("unknown", "abstention"))
        self.assertEqual(adjudicate(pair, app, index, index_name="application",
                                    app_read=local).outcome, "same_source_echo")
        retrieved = source_report_retrieval(
            pair, native, replace(index, binding=app.binding, window=source_window),
            requested_scope=("movie", 27205, "NL"), read=local, status=response.status_code,
            body=payload, service_label=self.netflix.name,
        )
        self.assert_display(retrieved, "displayed", "displayed", 1, 1)
        self.assertEqual(adjudicate(pair, index, native).outcome, "TP")
        for other in (self.read("series"), self.read("movie", market="BE")):
            self.assertEqual(other.status_code, 200)
            self.assertEqual(other.json()["source_reported_offers"], [])
        self.assertEqual(self.read("movie", tmdb_id=999999).status_code, 404)

    def test_current_and_history_and_transactional_exclusions(self):
        response = self.read("movie")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("subscription", {row["offer_type"] for row in data["offers"]})
        self.assertFalse(any(row["offer_type"] in {"rent", "buy"} for row in data["offers"]))
        self.assertEqual({r["offer_type"] for r in data["purchase_offers"]}, {"rent", "buy"})
        self.assertEqual({(r["service"], r["offer_type"]) for r in data["source_reported_offers"]},
                         {(self.netflix.name, "subscription")})
        self.assertEqual(data["last_known_offers"], [])
        pair, _, fp = fixture("prime", "rent")
        retrieval = display_retrieval(pair, fp, surface="offers", status=response.status_code,
                                     read=fp.window, typed_scope=("movie", 27205, "NL"), present=False)
        self.assertEqual(retrieval.state, "excluded")
        self.assertEqual(display_rate([retrieval]).value, "UNDEFINED")
        self.clock.return_value = STAMP + timedelta(days=8)
        history = self.read("movie").json()
        self.assertEqual(history["offers"], [])
        self.assertEqual(history["source_reported_offers"], [])
        self.assertTrue(history["last_known_offers"])
        for row in history["last_known_offers"]:
            self.assertEqual(row["availability_evidence"], "stale")
            self.assertEqual(datetime.fromisoformat(row["last_seen_at"]), STAMP)
        self.original.refresh_from_db()
        self.assertEqual((self.original.observed_at, self.original.last_seen), (STAMP, STAMP))
