"""Stage E typed HTTP reads of synthetic observations persisted in isolated PostgreSQL."""
import json
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
    Window, adjudicate, display_rate, display_retrieval, source_report_retrieval,
)
from availability.tests.test_issue97_stage_e_adjudication import STAMP, fixture
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
        scope = {"tmdb_id": movie.tmdb_id, "media_type": movie.content_type, "title": movie.title,
                 "year": movie.year, "country": country.code, "scope_id": "movie-27205-nl"}
        path = Path(__file__).with_name("fixtures") / "issue97_synthetic_justwatch.json"
        search = json.loads(path.read_bytes())[scope["scope_id"]]["search"]
        encoded = json.dumps(search, sort_keys=True, separators=(",", ":")).encode()
        self.source_digest = sha256(encoded).hexdigest()
        # The normal adapter parses the response through its mocked HTTP transport.
        report = replay_scope(scope, search)
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

    def read(self, media_type, tmdb_id=27205, market="NL"):
        route = f"/api/prime/{media_type}/{tmdb_id}/?current_country={market}"
        response = self.client.get(route)
        print(f"Stage E fixture read {route} HTTP {response.status_code} "
              f"sha256={sha256(response.content).hexdigest()}")
        self.http.assert_not_called()
        return response

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
        pair, index, native = fixture()
        pair = replace(pair, index=replace(pair.index, native_id="synthetic-MOVIE-27205",
                                           package_id="netflix"),
                       index_receipt=replace(pair.index_receipt, payload_digest=
                                             "34803b1738c95fb61a8ffcbbbc158051d500ce2b8a45f84b171655d5b7a2b856"))
        index = replace(index, binding=pair.index, payload_digest=self.source_digest)
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
        self.assertEqual(display_rate([retrieved]).value, 1.0)
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
