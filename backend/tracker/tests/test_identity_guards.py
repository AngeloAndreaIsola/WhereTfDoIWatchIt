"""Regression tests for #112 — canonical identity guards.

Reproduces the original failure pattern (wrong title persisted on a trusted
TMDb identity, PK 830 / TMDb 164) and pins the preventive semantics:
typed lookups, override labeling, display title preservation, relink conflict
validation, and durable identity audits. Offline fixtures only.
"""

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from tracker.models import Availability, Movie, MovieIdentityAudit, Region, WatchlistItem
from tracker.services.enrichment import resolve_or_create_movie, sync_movie_fields
from tracker.services.tmdb import TMDbItem
from availability.services.candidate_acceptance import safe_enrich_or_create_movie


def _details(tmdb_id, title, year, content_type="movie", overview=""):
    return TMDbItem(
        tmdb_id=tmdb_id, title=title, year=year, content_type=content_type, overview=overview
    )


class EnrichmentIdentityTests(TestCase):
    def test_enrichment_leaves_pk_830_display_title_for_guarded_repair(self):
        # The reported corruption: TMDb 164 (Breakfast at Tiffany's) carrying
        # the title "Natural Born Killers" while other metadata is correct.
        movie = Movie.objects.create(
            pk=830, tmdb_id=164, title="Natural Born Killers", year=1961, content_type="movie",
            overview="Holly Golightly is an eccentric New York City playgirl...",
        )
        changed = sync_movie_fields(
            movie, _details(164, "Breakfast at Tiffany's", 1961, overview="Holly Golightly...")
        )
        self.assertTrue(changed)
        movie.refresh_from_db()
        self.assertEqual(movie.title, "Natural Born Killers")
        self.assertEqual(movie.canonical_title, "Breakfast at Tiffany's")
        self.assertFalse(movie.title_is_override)
        self.assertFalse(MovieIdentityAudit.objects.exists())

    def test_flagged_override_survives_enrichment(self):
        movie = Movie.objects.create(
            tmdb_id=164, title="Mon petit déjeuner chez Tiffany", year=1961,
            title_is_override=True,
        )
        sync_movie_fields(movie, _details(164, "Breakfast at Tiffany's", 1961))
        movie.refresh_from_db()
        self.assertEqual(movie.title, "Mon petit déjeuner chez Tiffany")
        self.assertEqual(movie.canonical_title, "Breakfast at Tiffany's")
        self.assertTrue(movie.title_is_override)

    def test_sync_idempotent(self):
        movie = Movie.objects.create(tmdb_id=164, title="Breakfast at Tiffany's", year=1961)
        d = _details(164, "Breakfast at Tiffany's", 1961)
        self.assertTrue(sync_movie_fields(movie, d))  # canonical_title first recorded
        self.assertFalse(sync_movie_fields(movie, d))  # nothing left to change

    @patch("tracker.services.enrichment.TMDbClient")
    def test_resolve_or_create_preserves_override(self, mock_cls):
        movie = Movie.objects.create(
            tmdb_id=680, title="My Pulp Fiction", year=1994, title_is_override=True
        )
        mock_cls.return_value.details.return_value = _details(680, "Pulp Fiction", 1994)
        resolved = resolve_or_create_movie(680)
        self.assertEqual(resolved.pk, movie.pk)
        self.assertEqual(resolved.title, "My Pulp Fiction")
        self.assertEqual(resolved.canonical_title, "Pulp Fiction")

    @patch("tracker.services.enrichment.TMDbClient")
    def test_reresolution_preserves_legacy_titles_with_unknown_provenance(self, mock_cls):
        mock_cls.return_value.details.return_value = _details(164, "Breakfast at Tiffany's", 1961)
        for legacy_title in ("My Audrey Hepburn Pick", "Diamants sur canapé"):
            with self.subTest(legacy_title=legacy_title):
                movie, _ = Movie.objects.update_or_create(
                    tmdb_id=164, content_type="movie",
                    defaults={"title": legacy_title, "canonical_title": "", "year": 1961},
                )
                self.assertFalse(movie.title_is_override)
                for _ in range(2):
                    resolved = resolve_or_create_movie(164)
                    resolved.refresh_from_db()
                    self.assertEqual(resolved.pk, movie.pk)
                    self.assertEqual(resolved.title, legacy_title)
                    self.assertEqual(resolved.canonical_title, "Breakfast at Tiffany's")
                    self.assertFalse(resolved.title_is_override)


class UpdateMovieGuardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.movie = Movie.objects.create(tmdb_id=164, title="Breakfast at Tiffany's", year=1961)

    def test_title_edit_flags_override_and_audits(self):
        resp = self.client.patch(
            "/api/movie/164/", data={"title": "Diamond Breakfast"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Diamond Breakfast")
        self.assertTrue(self.movie.title_is_override)
        self.assertEqual(self.movie.canonical_title, "")  # manual edit is not canonical evidence
        audit = MovieIdentityAudit.objects.get(movie=self.movie)
        self.assertEqual(audit.operation, "update")
        self.assertEqual(audit.provenance, "manual_api")
        self.assertEqual(audit.changes["title"], {"old": "Breakfast at Tiffany's", "new": "Diamond Breakfast"})
        self.assertIsNotNone(audit.created_at)

    def test_empty_title_rejected(self):
        resp = self.client.patch("/api/movie/164/", data={"title": "   "}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(MovieIdentityAudit.objects.count(), 0)

    def test_noop_edit_creates_no_audit(self):
        resp = self.client.patch(
            "/api/movie/164/", data={"title": "Breakfast at Tiffany's"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MovieIdentityAudit.objects.count(), 0)

    def test_poster_edit_audited(self):
        resp = self.client.patch("/api/movie/164/", data={"poster_path": "/new.jpg"}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        audit = MovieIdentityAudit.objects.get()
        self.assertEqual(audit.changes["poster_path"]["new"], "/new.jpg")

    def test_identity_audit_survives_movie_deletion(self):
        response = self.client.patch("/api/movie/164/", data={"title": "Local Alias"}, format="json")
        self.assertEqual(response.status_code, 200)
        movie_pk = self.movie.pk
        audit = MovieIdentityAudit.objects.get(movie=self.movie)
        audit_id = audit.pk
        self.movie.delete()

        retained = MovieIdentityAudit.objects.get(movie_pk=movie_pk)
        self.assertEqual(retained.pk, audit_id)
        self.assertIsNone(retained.movie_id)
        self.assertEqual(retained.operation, "update")
        self.assertEqual(retained.provenance, "manual_api")
        self.assertEqual(retained.changes, audit.changes)
        self.assertEqual(retained.created_at, audit.created_at)

    def test_ambiguous_tmdb_id_requires_content_type(self):
        Movie.objects.create(tmdb_id=164, title="TV Side", content_type="series")
        resp = self.client.patch("/api/movie/164/", data={"title": "X"}, content_type="application/json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(sorted(resp.json()["content_types"]), ["movie", "series"])
        resp = self.client.patch(
            "/api/movie/164/?content_type=series", data={"title": "TV Renamed"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Movie.objects.get(content_type="series").title, "TV Renamed")
        self.assertEqual(Movie.objects.get(content_type="movie").title, "Breakfast at Tiffany's")


class LinkTmdbGuardTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_relink_cannot_clobber_existing_identity(self):
        # The genuine Natural Born Killers (TMDb 241) must survive an attempt
        # to relink the corrupted TMDb-164 row onto it.
        genuine = Movie.objects.create(tmdb_id=241, title="Natural Born Killers", year=1994)
        corrupted = Movie.objects.create(tmdb_id=164, title="Natural Born Killers", year=1961)
        with patch("tracker.views.TMDbClient") as mock_cls:
            mock_cls.return_value.details.return_value = _details(241, "Natural Born Killers", 1994)
            resp = self.client.post(
                f"/api/movie/{corrupted.pk}/link-tmdb/",
                data={"tmdb_id": 241}, content_type="application/json",
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["existing_movie_id"], genuine.pk)
        corrupted.refresh_from_db()
        genuine.refresh_from_db()
        self.assertEqual(corrupted.tmdb_id, 164)
        self.assertEqual(genuine.title, "Natural Born Killers")
        self.assertEqual(MovieIdentityAudit.objects.count(), 0)

    def test_rekey_audited_with_override_title(self):
        movie = Movie.objects.create(tmdb_id=999, title="Placeholder", year=None)
        with patch("tracker.views.TMDbClient") as mock_cls:
            mock_cls.return_value.details.return_value = _details(164, "Breakfast at Tiffany's", 1961)
            resp = self.client.post(
                f"/api/movie/{movie.pk}/link-tmdb/",
                data={"tmdb_id": 164, "title": "My Alias"}, content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        movie.refresh_from_db()
        self.assertEqual(movie.tmdb_id, 164)
        self.assertEqual(movie.title, "My Alias")
        self.assertTrue(movie.title_is_override)
        self.assertEqual(movie.canonical_title, "Breakfast at Tiffany's")
        audit = MovieIdentityAudit.objects.get(movie=movie)
        self.assertEqual(audit.operation, "link")
        self.assertEqual(audit.changes["tmdb_id"], {"old": 999, "new": 164})
        self.assertEqual(audit.changes["title"]["new"], "My Alias")

    def test_relink_without_override_uses_canonical_title_and_clears_flag(self):
        movie = Movie.objects.create(tmdb_id=999, title="Old Alias", title_is_override=True)
        with patch("tracker.views.TMDbClient") as mock_cls:
            mock_cls.return_value.details.return_value = _details(164, "Breakfast at Tiffany's", 1961)
            resp = self.client.post(
                f"/api/movie/{movie.pk}/link-tmdb/", data={"tmdb_id": 164}, content_type="application/json"
            )
        self.assertEqual(resp.status_code, 200)
        movie.refresh_from_db()
        self.assertEqual(movie.title, "Breakfast at Tiffany's")
        self.assertFalse(movie.title_is_override)

    def test_type_flip_with_availability_evidence_requires_confirmation(self):
        movie = Movie.objects.create(tmdb_id=164, title="Breakfast at Tiffany's", year=1961)
        region = Region.objects.create(provider="netflix", country="US")
        Availability.objects.create(movie=movie, region=region, offer_type="subscription")
        with patch("tracker.views.TMDbClient") as mock_cls:
            mock_cls.return_value.details.return_value = _details(164, "Some Series", 2020, content_type="series")
            resp = self.client.post(
                f"/api/movie/{movie.pk}/link-tmdb/",
                data={"tmdb_id": 164, "content_type": "series"}, content_type="application/json",
            )
            self.assertEqual(resp.status_code, 409)
            movie.refresh_from_db()
            self.assertEqual(movie.content_type, "movie")
            resp = self.client.post(
                f"/api/movie/{movie.pk}/link-tmdb/",
                data={"tmdb_id": 164, "content_type": "series", "confirm_type_change": True},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200, resp.content)
        movie.refresh_from_db()
        self.assertEqual(movie.content_type, "series")
        audit = MovieIdentityAudit.objects.get(movie=movie)
        self.assertEqual(audit.changes["content_type"], {"old": "movie", "new": "series"})


class RemoveWatchlistTypedTests(TestCase):
    def test_remove_ambiguous_requires_type(self):
        movie = Movie.objects.create(tmdb_id=100, title="Movie", content_type="movie")
        series = Movie.objects.create(tmdb_id=100, title="Series", content_type="series")
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=series, source="manual")
        client = APIClient()
        resp = client.delete("/api/watchlist/remove/100/")
        self.assertEqual(resp.status_code, 409)
        resp = client.delete("/api/watchlist/remove/100/?content_type=series")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertEqual(WatchlistItem.objects.get().movie_id, movie.pk)


class VerifiedAcceptanceCanonicalTests(TestCase):
    def test_acceptance_records_canonical_title_and_preserves_override(self):
        movie = Movie.objects.create(
            tmdb_id=500, title="My Reservoir Dogs", year=1992, title_is_override=True
        )
        result = safe_enrich_or_create_movie(
            tmdb_id=500, verified_type="movie", details={"title": "Reservoir Dogs", "year": 1992}
        )
        self.assertEqual(result.pk, movie.pk)
        self.assertEqual(result.title, "My Reservoir Dogs")
        self.assertEqual(result.canonical_title, "Reservoir Dogs")

    def test_acceptance_fills_empty_title_only(self):
        movie = Movie.objects.create(tmdb_id=500, title="", year=None)
        result = safe_enrich_or_create_movie(
            tmdb_id=500, verified_type="movie", details={"title": "Reservoir Dogs"}
        )
        self.assertEqual(result.title, "Reservoir Dogs")
        self.assertEqual(result.canonical_title, "Reservoir Dogs")
