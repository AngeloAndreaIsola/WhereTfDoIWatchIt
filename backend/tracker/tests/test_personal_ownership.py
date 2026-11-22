from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from tracker.models import (
    Movie,
    MovieWatchEvent,
    Preference,
    Provider,
    RefreshRequest,
    Subscription,
    UserMovieData,
    WatchlistItem,
)
from tracker.ownership import resolve_personal_owner_id
from tracker.serializers import MovieDetailSerializer, ProviderSerializer
from tracker.services.google_interests import GoogleInterestRow
from tracker.services.letterboxd import LetterboxdRow
from tracker.services.refresh_requests import process_refresh_queue, request_watchlist_refresh


class OwnerSettingTests(SimpleTestCase):
    def test_legacy_fallback_and_environment_value(self):
        with self.settings(WTDW_LEGACY_OWNER_ID=None):
            self.assertEqual(resolve_personal_owner_id(), 1)
        with self.settings(WTDW_LEGACY_OWNER_ID="97"):
            self.assertEqual(resolve_personal_owner_id(), 97)

    def test_invalid_configuration_fails_closed(self):
        for value in (0, -1, True, 2 ** 31, "", "bad", "9" * 5000, 3.5):
            with self.subTest(value=str(value)[:20]), self.settings(WTDW_LEGACY_OWNER_ID=value):
                with self.assertRaises(ImproperlyConfigured):
                    resolve_personal_owner_id()


@override_settings(WTDW_LEGACY_OWNER_ID=97)
class PersonalOwnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.movie = Movie.objects.create(tmdb_id=501, title="Installation title", year=2020)
        cls.other_movie = Movie.objects.create(tmdb_id=502, title="Historical title", year=2020)
        cls.membership = WatchlistItem.objects.create(user_id=97, movie=cls.movie, source="letterboxd")
        cls.other_membership = WatchlistItem.objects.create(user_id=1, movie=cls.movie, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=cls.other_movie, source="manual")
        UserMovieData.objects.create(user_id=1, movie=cls.movie, is_watched=True, rating=2)
        UserMovieData.objects.create(user_id=1, movie=cls.other_movie, is_watched=True)
        MovieWatchEvent.objects.create(user_id=1, movie=cls.movie, rating=2)

    def setUp(self):
        self.client = APIClient()

    def test_watchlist_reads_resolve_and_remove_follow_configured_owner(self):
        response = self.client.get("/api/watchlist/?user_id=1")
        self.assertEqual([row["id"] for row in response.json()], [self.membership.pk])

        response = self.client.post("/api/watchlist/resolve/", {
            "owner_id": 1,
            "selections": [{"tmdb_id": self.movie.tmdb_id, "user_id": 1}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["created_count"], 0)
        self.assertEqual([row["id"] for row in response.json()["items"]], [self.membership.pk])

        response = self.client.delete(f"/api/watchlist/remove/{self.movie.tmdb_id}/?owner_id=1")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(WatchlistItem.objects.filter(pk=self.membership.pk).exists())
        self.assertTrue(WatchlistItem.objects.filter(pk=self.other_membership.pk).exists())

    def test_watched_reads_writes_and_serializers_follow_configured_owner(self):
        self.assertIsNone(MovieDetailSerializer(self.movie).data["user_data"])
        self.assertEqual(MovieDetailSerializer(self.movie).data["watch_events"], [])
        response = self.client.post(
            f"/api/watched/{self.movie.tmdb_id}/?owner_id=1",
            {"user_id": 1, "rating": 4, "watched_date": "2025-01-02"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        movie_data = MovieDetailSerializer(self.movie).data
        self.assertEqual(movie_data["user_data"]["rating"], 4)
        self.assertEqual([row["rating"] for row in movie_data["watch_events"]], [4])
        response = self.client.get("/api/watched/?user_id=1")
        self.assertEqual([row["movie"]["tmdb_id"] for row in response.json()["results"]], [self.movie.tmdb_id])
        self.assertEqual(self.client.post(f"/api/unwatched/{self.movie.tmdb_id}/?user_id=1").status_code, 200)
        self.assertFalse(UserMovieData.objects.get(user_id=97, movie=self.movie).is_watched)
        self.assertTrue(UserMovieData.objects.get(user_id=1, movie=self.movie).is_watched)
        self.assertEqual(MovieWatchEvent.objects.get(user_id=1, movie=self.movie).rating, 2)

    def test_export_ignores_owner_parameter_and_excludes_historical_rows(self):
        response = self.client.get("/api/export/?owner_id=1")
        self.assertContains(response, self.movie.title)
        self.assertNotContains(response, self.other_movie.title)
        self.assertNotContains(response, ",watched")

    def test_preferences_and_provider_serialization_follow_configured_owner(self):
        Preference.objects.filter(user_id=1).update(current_country="US")
        self.assertEqual(self.client.put("/api/preferences/?user_id=1", {
            "user_id": 1, "current_country": "NL",
        }, format="json").status_code, 200)
        self.assertEqual(self.client.get("/api/preferences/?user_id=1").json()["current_country"], "NL")
        self.assertFalse(Preference.objects.filter(user_id=1, current_country="NL").exists())

        provider = Provider.objects.get(slug="prime")
        other = Subscription.objects.create(user_id=1, provider=provider, access_mode="rent_buy")
        self.assertFalse(ProviderSerializer(provider).data["is_subscribed"])
        response = self.client.post("/api/subscriptions/", {
            "user_id": 1, "provider_id": provider.pk, "access_mode": "included",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        own = Subscription.objects.get(user_id=97, provider=provider)
        self.assertEqual(ProviderSerializer(provider).data["access_mode"], "included")
        response = self.client.get("/api/subscriptions/?user_id=1")
        self.assertEqual([row["id"] for row in response.json()], [own.pk])
        self.assertEqual(self.client.delete(f"/api/subscriptions/{other.pk}/?user_id=1").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/subscriptions/{own.pk}/?user_id=1").status_code, 204)
        self.assertTrue(Subscription.objects.filter(pk=other.pk).exists())

    def test_csv_and_url_imports_ignore_supplied_owner_fields(self):
        # Known local titles and mocked HTML fetches keep every import offline.
        csv_movie = Movie.objects.create(tmdb_id=503, title="CSV title", year=2020)
        profile_movie = Movie.objects.create(tmdb_id=504, title="Profile title", year=2020)
        google_movie = Movie.objects.create(tmdb_id=505, title="Google title", year=2020)
        upload = SimpleUploadedFile("watchlist.csv", b"Title,Year\nCSV title,2020\n")
        response = self.client.post("/api/watchlist/?owner_id=1", {"file": upload, "user_id": 1})
        self.assertEqual(response.status_code, 200, response.content)
        with patch("tracker.views.fetch_profile_rows", return_value=([LetterboxdRow(profile_movie.title, 2020)], 1)):
            response = self.client.post("/api/importers/letterboxd-url/", {
                "url": "https://letterboxd.com/example/watchlist/", "user_id": 1,
            }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        with patch("tracker.services.google_interests.fetch_list_pages", return_value=[""]), patch(
            "tracker.services.google_interests.parse_page", return_value=[GoogleInterestRow(google_movie.title, 2020)],
        ):
            response = self.client.post("/api/importers/google-interests/", {
                "urls": ["https://www.google.com/interests/saved"], "user_id": {"arbitrary": 1},
            }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertCountEqual(
            WatchlistItem.objects.filter(movie__in=[csv_movie, profile_movie, google_movie])
            .values_list("movie_id", "user_id"),
            [(movie.pk, 97) for movie in (csv_movie, profile_movie, google_movie)],
        )
        self.assertFalse(UserMovieData.objects.filter(movie__in=[csv_movie, profile_movie], user_id=1).exists())

    def test_queue_only_contains_installation_watchlist_and_worker_uses_its_country(self):
        Preference.objects.create(user_id=97, current_country="NL")
        result = request_watchlist_refresh(sources=["streaming_availability"])
        self.assertEqual(result["total_watchlist"], 1)
        queue_item = RefreshRequest.objects.get()
        self.assertEqual((queue_item.movie_id, queue_item.country), (self.movie.pk, "NL"))
        # Legacy queue rows carried neither a source scope nor a market.
        RefreshRequest.objects.filter(pk=queue_item.pk).update(source="", country="")
        service = MagicMock()
        result = process_refresh_queue(service=service)
        self.assertEqual(result["requests_drained"], 1)
        service.refresh_content.assert_called_once_with(self.movie, country="NL")

    @override_settings(STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    })
    def test_admin_creation_ignores_owner_and_admin_queryset_is_scoped(self):
        self.client.force_login(get_user_model().objects.create_superuser(
            username="ownership-admin", password="temporary-test-password",
        ))
        self.assertEqual(self.client.get(
            f"/admin/tracker/watchlistitem/{self.other_membership.pk}/change/",
        ).status_code, 302)
        response = self.client.post("/admin/tracker/watchlistitem/add/?user_id=1", {
            "movie": self.other_movie.pk, "user_id": 1, "source": "letterboxd",
        })
        self.assertEqual(response.status_code, 302, response.content)
        self.assertTrue(WatchlistItem.objects.filter(user_id=97, movie=self.other_movie, source="letterboxd").exists())
