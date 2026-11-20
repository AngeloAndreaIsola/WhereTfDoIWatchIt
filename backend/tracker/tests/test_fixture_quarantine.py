import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from availability.services.legacy_ingest import ingest_scraped_mapping
from config.test_database import require_disposable_database
from tracker.models import Movie, MovieIdentityAudit, Provider, RefreshRequest, Region, ScrapedContentMapping, UserMovieData, WatchlistItem
from tracker.services.enrichment import enrich_movie_from_tmdb, resolve_or_create_movie, sync_movie_fields
from tracker.services.letterboxd import LetterboxdRow, import_rows
from tracker.services.tmdb import TMDbItem
from tracker.services.refresh_requests import _candidate_batch, request_availability_refresh


class DisposableDatabaseGuardTests(SimpleTestCase):
    def setUp(self):
        self.target = MagicMock(
            vendor="postgresql",
            settings_dict={
                "NAME": "wtdwi_test", "USER": "wtdwi_test", "HOST": "db-test",
                "TEST": {"NAME": None, "MIRROR": None},
            },
        )

    @patch.dict("os.environ", {"WTDWI_DISPOSABLE_TEST_DB": "1"})
    def test_opt_in_does_not_allow_serving_database_or_redirected_test_name(self):
        for field, value in (("NAME", "tracker"), ("USER", "tracker"), ("HOST", "db")):
            with self.subTest(field=field), patch.dict(self.target.settings_dict, {field: value}):
                with self.assertRaisesMessage(CommandError, "disposable WTDWI"):
                    require_disposable_database(self.target, for_runner=True)
        self.target.settings_dict["TEST"]["NAME"] = "tracker"
        with self.assertRaisesMessage(CommandError, "disposable WTDWI"):
            require_disposable_database(self.target, for_runner=True)
        self.target.cursor.assert_not_called()

    @patch.dict("os.environ", {"WTDWI_DISPOSABLE_TEST_DB": "1"})
    def test_server_identity_and_isolation_are_checked(self):
        cursor = self.target.cursor.return_value.__enter__.return_value
        for actual in (
            ("tracker", "wtdwi_test", "wtdwi_test", "wtdwi_test", False),
            ("wtdwi_test", "tracker", "wtdwi_test", "wtdwi_test", False),
            ("wtdwi_test", "wtdwi_test", "wtdwi_test", "tracker", False),
            ("wtdwi_test", "wtdwi_test", "wtdwi_test", "wtdwi_test", True),
        ):
            with self.subTest(actual=actual):
                cursor.fetchone.return_value = actual
                with self.assertRaisesMessage(CommandError, "identity or cluster isolation"):
                    require_disposable_database(self.target)


class FixtureWriteGuardTests(TestCase):
    def test_prime_fixture_requires_test_opt_in(self):
        with patch.dict("os.environ", {"WTDWI_DISPOSABLE_TEST_DB": "0"}):
            with self.assertRaisesMessage(CommandError, "disposable WTDWI"):
                call_command("seed_prime_fixture", stdout=StringIO())
        self.assertFalse(Movie.objects.exists())

    def test_loaddata_guards_movie_and_rolls_back_preceding_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "movies.json"
            fixture.write_text(json.dumps([
                {"model": "tracker.region", "fields": {"provider": "fixture", "country": "NL"}},
                {"model": "tracker.movie", "pk": 60, "fields": {"tmdb_id": 61, "title": "Test"}},
            ]))
            with patch.dict("os.environ", {"WTDWI_DISPOSABLE_TEST_DB": "0"}):
                with self.assertRaisesMessage(CommandError, "disposable WTDWI"):
                    call_command("loaddata", str(fixture), verbosity=0)
            self.assertFalse(Region.objects.filter(provider="fixture").exists())
            self.assertFalse(Movie.objects.exists())
            call_command("loaddata", str(fixture), verbosity=0)
        self.assertEqual(Movie.objects.get(pk=60).title, "Test")


class QuarantineCommandTests(TransactionTestCase):
    def setUp(self):
        self.movie = Movie.objects.create(pk=818, tmdb_id=33, title="BehindA404", year=2000)
        self.refresh = RefreshRequest.objects.create(movie=self.movie, status="failed")
        config = connection.settings_dict
        self.target = f"{config['NAME']}@{config['HOST']}:{config['PORT'] or '5432'}"

    def run_command(self, *ids, apply=False, restore=False, confirm=None, out=None):
        if out is None:
            out = StringIO()
        args = [argument for pk in ids for argument in ("--movie-id", str(pk))]
        call_command(
            "quarantine_fixture_movies", *args, apply=apply,
            confirm_target=confirm if confirm is not None else self.target,
            restore=restore, stdout=out,
        )
        return json.loads(out.getvalue())

    def test_dry_run_reports_fingerprint_and_refresh_history_without_writes(self):
        report = self.run_command(818)
        [row] = report["movies"]
        self.assertEqual(row["status"], "planned")
        self.assertEqual(row["before"]["references"]["refresh_requests"]["failed"], 1)
        self.assertFalse(row["before"]["is_quarantined"])
        self.assertTrue(row["after"]["is_quarantined"])
        self.movie.refresh_from_db()
        self.assertFalse(self.movie.is_quarantined)
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_refuses_identity_drift_and_ids_outside_manifest(self):
        Movie.objects.filter(pk=818).update(tmdb_id=99)
        with self.assertRaisesMessage(CommandError, "outside the issue #113 inventory"):
            self.run_command(999, apply=True)
        self.assertEqual(self.run_command(818)["movies"][0]["reason"], "local identity does not match the manifest")

    def test_preserves_825_and_refuses_entire_mixed_batch(self):
        retained = Movie.objects.create(pk=825, tmdb_id=875002, title="Neg", year=2021, overview="Evacuation")
        report = self.run_command(825)
        self.assertIn("Evacuation", report["movies"][0]["reason"])
        with self.assertRaisesMessage(CommandError, "batch refused"):
            self.run_command(818, 825, apply=True)
        self.assertFalse(Movie.objects.get(pk=818).is_quarantined)
        retained.refresh_from_db()
        self.assertFalse(retained.is_quarantined)
        self.assertEqual(retained.overview, "Evacuation")
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_refuses_user_state_and_incorrect_target(self):
        with self.assertRaisesMessage(CommandError, "exact target token"):
            self.run_command(818, apply=True, confirm=self.target.rsplit(":", 1)[0])
        WatchlistItem.objects.create(movie=self.movie)
        report = self.run_command(818)
        self.assertEqual(report["movies"][0]["before"]["references"]["watchlist_items"], 1)
        with self.assertRaisesMessage(CommandError, "batch refused"):
            self.run_command(818, apply=True)
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_quarantine_and_reversal_are_idempotent_and_keep_references(self):
        self.assertEqual(self.run_command(818, apply=True)["movies"][0]["status"], "applied")
        self.assertEqual(self.run_command(818, apply=True)["movies"][0]["status"], "unchanged")
        self.movie.refresh_from_db()
        self.assertTrue(self.movie.is_quarantined)
        self.assertEqual(self.run_command(818, restore=True, apply=True)["movies"][0]["status"], "applied")
        self.assertEqual(self.run_command(818, restore=True, apply=True)["movies"][0]["status"], "unchanged")
        self.movie.refresh_from_db()
        self.assertFalse(self.movie.is_quarantined)
        self.assertEqual(MovieIdentityAudit.objects.filter(movie_pk=818).count(), 2)
        self.refresh.refresh_from_db()
        self.assertEqual((self.refresh.movie_id, self.refresh.status), (818, "failed"))


class QuarantineVisibilityTests(TestCase):
    def setUp(self):
        self.quarantined = Movie.objects.create(tmdb_id=875001, title="Neg", is_quarantined=True)
        self.legitimate = Movie.objects.create(tmdb_id=6, title="Neg")
        self.series = Movie.objects.create(tmdb_id=875001, content_type="series", title="Neg")

    def test_catalogue_keeps_same_title_series_and_unresolved_observation(self):
        provider = Provider.objects.create(slug="fixture-catalogue", name="Fixture Catalogue", category="scraper")
        for suffix, movie in (("resolved", self.quarantined), ("unresolved", None)):
            row = ScrapedContentMapping.objects.create(
                provider=provider, scraped_title="Neg", movie=movie, is_confirmed=bool(movie),
                scraped_url=f"https://example.invalid/{suffix}", is_available=True,
                confidence_score=1.0,
            )
            ingest_scraped_mapping(row)
        response = self.client.get("/api/catalog/unified/?q=Neg")
        self.assertEqual(response.status_code, 200)
        rows = response.json()["results"]
        identities = [(row["tmdb_id"], row["content_type"]) for row in rows if row["tmdb_id"]]
        self.assertCountEqual(identities, [(6, "movie"), (875001, "series")])
        self.assertEqual(sum(row["tmdb_id"] is None for row in rows), 1)
        response = self.client.get("/api/catalog/?q=Neg")
        self.assertCountEqual(
            [row["id"] for row in response.json()["results"]], [self.legitimate.pk, self.series.pk],
        )
        self.assertEqual(self.client.get(f"/api/movie/{self.quarantined.pk}/").status_code, 404)
        legacy = self.client.get("/api/v2/unofficial/?provider=fixture-catalogue")
        self.assertEqual(legacy.status_code, 200)
        [observation] = legacy.json()["results"]
        self.assertEqual(observation["scraped_url"], "https://example.invalid/unresolved")
        self.assertIsNone(observation["movie"])

    def test_unofficial_offers_exclude_quarantine_and_allow_the_visible_series(self):
        url = f"/api/v2/unofficial/movie/{self.quarantined.tmdb_id}/offers/"
        self.assertEqual(self.client.get(url, {"content_type": "movie"}).status_code, 404)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["movie"]["content_type"], "series")
        self.series.delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_unofficial_offers_resolve_legitimate_collisions_by_type(self):
        self.quarantined.is_quarantined = False
        self.quarantined.save(update_fields=["is_quarantined"])
        url = f"/api/v2/unofficial/movie/{self.series.tmdb_id}/offers/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url, {"content_type": "episode"}).status_code, 400)
        for kind in ("movie", "series"):
            with self.subTest(kind=kind):
                response = self.client.get(url, {"content_type": kind})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["movie"]["content_type"], kind)

    def test_watchlist_hides_quarantined_movie_and_blocks_resolution(self):
        WatchlistItem.objects.create(movie=self.quarantined)
        WatchlistItem.objects.create(movie=self.legitimate)
        self.assertEqual(len(self.client.get("/api/watchlist/").json()), 1)
        with patch("tracker.services.enrichment.TMDbClient") as tmdb:
            response = self.client.post("/api/watchlist/add/", {"tmdb_id": 875001}, content_type="application/json")
        self.assertEqual(response.status_code, 404)
        tmdb.assert_not_called()

    def test_watched_lists_hide_quarantine_without_changing_personal_data(self):
        hidden = UserMovieData.objects.create(movie=self.quarantined, is_watched=True, rating=4.0)
        UserMovieData.objects.create(movie=self.series, is_watched=True, rating=5.0)

        for url, field in (("/api/watched/", "results"), ("/api/watched/report/", "report")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            [row] = response.json()[field]
            self.assertEqual(row["movie"]["content_type"], "series")
            self.assertEqual(row["user_data"]["rating"], 5.0)
        hidden.refresh_from_db()
        self.assertTrue(hidden.is_watched)
        self.assertEqual(hidden.rating, 4.0)

    def test_import_ignores_quarantined_title_and_uri_cache_hits(self):
        self.legitimate.title = "Different title"
        self.legitimate.save(update_fields=["title"])
        uri = "https://letterboxd.com/film/neg/"
        UserMovieData.objects.create(movie=self.quarantined, letterboxd_uri=uri, is_watched=True)
        tmdb = Mock()
        tmdb.search.return_value = [
            TMDbItem(tmdb_id=self.quarantined.tmdb_id, title="Neg", year=None,
                     poster_path="/new.jpg"),
        ]

        result = import_rows([LetterboxdRow("Neg", None, uri=uri)], client=tmdb)

        tmdb.search.assert_called_once_with("Neg", year=None, content_type="movie")
        self.assertFalse(result.resolved)
        self.assertEqual([item["reason"] for item in result.skipped], ["quarantined_identity"])
        self.quarantined.refresh_from_db()
        self.assertTrue(self.quarantined.is_quarantined)
        self.assertEqual(self.quarantined.poster_path, "")
        self.assertTrue(UserMovieData.objects.get(movie=self.quarantined).is_watched)
        self.assertFalse(WatchlistItem.objects.filter(movie=self.quarantined).exists())

    def test_review_can_resolve_series_with_same_id_as_quarantined_movie(self):
        response = self.client.post(
            "/api/watchlist/resolve/",
            {"selections": [
                {"tmdb_id": self.quarantined.tmdb_id, "original_title": "Neg"},
                {"tmdb_id": self.series.tmdb_id, "content_type": "series", "original_title": "Neg"},
            ]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["resolved_count"], 1)
        item = WatchlistItem.objects.get()
        self.assertEqual(item.movie_id, self.series.pk)
        self.assertEqual(item.source, "letterboxd_resolved")

    def test_enrichment_and_pending_refresh_stay_inactive(self):
        pending = RefreshRequest.objects.create(movie=self.quarantined, status="pending")
        with patch("tracker.services.enrichment.TMDbClient") as tmdb:
            self.assertFalse(enrich_movie_from_tmdb(self.quarantined))
            with self.assertRaises(Movie.DoesNotExist):
                resolve_or_create_movie(875001)
        tmdb.assert_not_called()
        self.assertFalse(sync_movie_fields(self.quarantined, Mock()))
        self.assertIsNone(request_availability_refresh(self.quarantined))
        self.assertNotIn(pending, _candidate_batch(set(), "streaming_availability"))
        pending.refresh_from_db()
        self.assertEqual(pending.status, "pending")
