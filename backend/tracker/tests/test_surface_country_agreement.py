"""The watchlist badge and the detail page must agree about *where* a title is.

Regression for movie 615457 ("Nobody"): the detail page read the user's saved
country (NL) and showed 🟢 "Watchable here", while every list surface
(homepage, catalog, watchlist) hard-coded ``"US"`` and so classified the very
same local offer as foreign — 🟡 "Watchable with VPN". Two different countries,
two different verdicts, one resolver.

These tests pin that all surfaces default to the user's saved country when no
explicit ``country``/``current_country`` parameter is passed.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import Client, TestCase

from tracker.models import Movie, Preference, Provider, Subscription, WatchlistItem
from tracker.tests.availability_factories import local_offer


class SurfaceCountryAgreementTests(TestCase):
    def setUp(self):
        # Saved country is NL; the user subscribes to Prime. update_or_create
        # because a migration seeds a Preference row for user_id=1.
        Preference.objects.update_or_create(
            user_id=1,
            defaults={
                "audio_languages": ["EN"],
                "subtitle_languages": ["EN"],
                "current_country": "NL",
                "vpn_enabled": True,
            },
        )
        prime = Provider.objects.get(slug="prime")
        Subscription.objects.create(user_id=1, provider=prime, name="Prime Video")

        self.movie = Movie.objects.create(tmdb_id=615457, title="Nobody", year=2021)
        # A local (NL) subscription offer the user is entitled to.
        local_offer(self.movie, country="NL", service="prime", offer_type="subscription",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        WatchlistItem.objects.create(user_id=1, movie=self.movie)

    def test_detail_and_watchlist_agree_on_saved_country(self):
        detail = Client().get("/api/prime/movie/615457/").json()
        self.assertEqual(detail["watchability"]["verdict"], "available_here")
        self.assertEqual(detail["watchability"]["status_symbol"], "🟢")

        report = Client().get("/api/watchlist/report/").json()
        row = next(
            r for r in report["report"]
            if (r.get("movie") or {}).get("tmdb_id") == 615457
        )
        watch = row["watchability"]
        # Same country (NL) ⇒ same verdict ⇒ same symbol. The bug was a 🟡 here.
        self.assertEqual(watch["status"], "watchable_here")
        self.assertEqual(watch["status_symbol"], detail["watchability"]["status_symbol"])

    def test_list_surfaces_honour_explicit_country_override(self):
        # An explicit current_country still wins over the saved one. (Not the
        # ``country`` param: that one is also a *filter* over offer countries.)
        report = Client().get("/api/watchlist/report/?current_country=US").json()
        row = next(
            r for r in report["report"]
            if (r.get("movie") or {}).get("tmdb_id") == 615457
        )
        # The NL offer is reachable via VPN from the US, not local.
        self.assertEqual(row["watchability"]["status"], "watchable_vpn")
