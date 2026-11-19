from django.test import TestCase

from availability.services.legacy_ingest import ingest_scraped_mapping
from tracker.models import Movie, Preference, Provider, ScrapedContentMapping, Subscription, UserMovieData
from tracker.tests.availability_factories import local_offer


class RecoveredCatalogFilterTests(TestCase):
    def setUp(self):
        self.hydra = Provider.objects.create(
            slug="hydra", name="Hydra", category="scraper", is_enabled=True
        )

    def _scrape(self, title, *, year=2024, movie=None, payload=None, provider=None, content_type="movie"):
        mapping = ScrapedContentMapping.objects.create(
            provider=provider or self.hydra,
            scraped_title=title,
            scraped_year=year,
            scraped_content_type=content_type,
            scraped_url=f"https://hydra.example/{title.lower().replace(' ', '-')}",
            movie=movie,
            raw_payload=payload or {},
            is_available=True,
        )
        ingest_scraped_mapping(mapping)
        return mapping

    def _subscribe(self, service, *, access_mode="included", is_major=False):
        provider, _ = Provider.objects.get_or_create(
            slug=service.lower(),
            defaults={
                "name": service.title(),
                "category": "official",
                "is_major": is_major,
            },
        )
        if is_major and not provider.is_major:
            Provider.objects.filter(pk=provider.pk).update(is_major=True)
        Subscription.objects.create(
            user_id=1,
            provider=provider,
            name=service.title(),
            access_mode=access_mode,
        )
        return provider

    def _results(self, query=""):
        response = self.client.get(f"/api/catalog/unified/{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"]

    def test_comma_separated_actor_ids_use_and_matching(self):
        both = Movie.objects.create(
            tmdb_id=811, title="Both Actors", cast=[
                {"id": 819, "name": "Ryan Gosling"},
                {"id": 287, "name": "Brad Pitt"},
            ]
        )
        Movie.objects.create(
            tmdb_id=812, title="Only Gosling", cast=[{"id": 819, "name": "Ryan Gosling"}]
        )
        Movie.objects.create(
            tmdb_id=813, title="Only Pitt", cast=[{"id": 287, "name": "Brad Pitt"}]
        )

        rows = self._results("?actor_id=819,287")

        self.assertEqual([row["tmdb_id"] for row in rows], [both.tmdb_id])

    def test_unofficial_actor_filter_uses_resolved_movie_cast(self):
        matching = Movie.objects.create(
            tmdb_id=821, title="Hydra Cast Match", cast=[
                {"id": 819, "name": "Ryan Gosling"},
                {"id": 287, "name": "Brad Pitt"},
            ]
        )
        self._scrape("Hydra Cast Match", movie=matching)
        self._scrape("Unresolved Cast", payload={"cast": [{"id": 819, "name": "Ryan Gosling"}]})

        rows = self._results("?providers=hydra&actor_id=819,287")

        self.assertEqual([row["tmdb_id"] for row in rows], [matching.tmdb_id])
        self.assertEqual(rows[0]["source"], "scraper")

    def test_series_and_tv_alias_filter_both_catalogue_halves(self):
        Movie.objects.create(tmdb_id=831, title="Official Film", content_type="movie")
        series = Movie.objects.create(tmdb_id=832, title="Official Series", content_type="series")
        self._scrape("Scraped Series", content_type="series", payload={"content_type": "series"})

        rows = self._results("?type=tv")

        self.assertEqual({row["content_type"] for row in rows}, {"series"})
        self.assertIn(series.tmdb_id, {row["tmdb_id"] for row in rows})
        self.assertTrue(any(row["source"] == "scraper" for row in rows))

    def test_language_filters_require_known_matching_offer_languages(self):
        matching = Movie.objects.create(tmdb_id=841, title="Known Languages")
        unknown = Movie.objects.create(tmdb_id=842, title="Unknown Languages")
        self._subscribe("netflix")
        local_offer(matching, audio_languages=["EN"], subtitle_languages=["FR"])
        local_offer(unknown, audio_languages=None, subtitle_languages=None)

        rows = self._results("?audio_languages=en&subtitle_languages=FR")

        self.assertEqual([row["tmdb_id"] for row in rows], [matching.tmdb_id])

    def test_audio_and_subtitles_from_different_providers_do_not_match(self):
        movie = Movie.objects.create(tmdb_id=843, title="Split Languages")
        self._subscribe("netflix")
        self._subscribe("disney")
        local_offer(movie, service="netflix", audio_languages=["EN"], subtitle_languages=["IT"])
        local_offer(movie, service="disney", audio_languages=["JA"], subtitle_languages=["JA"])

        rows = self._results("?audio_languages=EN&subtitle_languages=JA")

        self.assertNotIn(movie.tmdb_id, {row["tmdb_id"] for row in rows})

    def test_audio_and_subtitles_on_one_offer_match_with_or_groups(self):
        movie = Movie.objects.create(tmdb_id=844, title="One Offer Languages")
        self._subscribe("netflix")
        local_offer(
            movie,
            audio_languages=["IT"],
            subtitle_languages=["DE"],
        )

        rows = self._results(
            "?audio_languages=EN,IT&subtitle_languages=FR,DE"
        )

        self.assertEqual([row["tmdb_id"] for row in rows], [movie.tmdb_id])

    def test_both_language_groups_are_and_require_one_offer(self):
        movie = Movie.objects.create(tmdb_id=845, title="Language AND")
        self._subscribe("netflix")
        local_offer(movie, audio_languages=["EN"], subtitle_languages=["IT"])

        self.assertEqual(
            self._results("?audio_languages=EN,JA")[0]["tmdb_id"], movie.tmdb_id
        )
        self.assertEqual(
            self._results("?subtitle_languages=IT,JA")[0]["tmdb_id"], movie.tmdb_id
        )
        self.assertIn(
            movie.tmdb_id,
            {row["tmdb_id"] for row in self._results("?audio_languages=EN&subtitle_languages=IT")},
        )
        self.assertNotIn(
            movie.tmdb_id,
            {row["tmdb_id"] for row in self._results("?audio_languages=EN&subtitle_languages=JA")},
        )

    def test_language_filter_does_not_use_an_offer_excluded_by_entitlement(self):
        movie = Movie.objects.create(tmdb_id=846, title="Unowned Language Offer")
        self._subscribe("disney")
        local_offer(movie, service="netflix", audio_languages=["EN"], subtitle_languages=["JA"])
        local_offer(movie, service="disney", audio_languages=["IT"], subtitle_languages=["IT"])

        rows = self._results("?audio_languages=EN&subtitle_languages=JA")

        self.assertNotIn(movie.tmdb_id, {row["tmdb_id"] for row in rows})

    def test_language_filter_respects_the_active_provider_selection(self):
        movie = Movie.objects.create(tmdb_id=847, title="Provider Scoped Languages")
        self._subscribe("netflix")
        self._subscribe("disney")
        local_offer(movie, service="netflix", audio_languages=["EN"], subtitle_languages=["JA"])
        local_offer(movie, service="disney", audio_languages=["IT"], subtitle_languages=["IT"])

        rows = self._results(
            "?providers=disney&audio_languages=EN&subtitle_languages=JA"
        )

        self.assertNotIn(movie.tmdb_id, {row["tmdb_id"] for row in rows})

    def test_language_filter_obeys_current_territory_and_vpn_reach(self):
        movie = Movie.objects.create(tmdb_id=848, title="Territory Scoped Languages")
        self._subscribe("netflix")
        Preference.objects.update_or_create(
            user_id=1,
            defaults={"current_country": "US", "candidate_countries": ["GB"]},
        )
        local_offer(movie, country="US", audio_languages=["IT"], subtitle_languages=["IT"])
        local_offer(movie, country="GB", audio_languages=["EN"], subtitle_languages=["JA"])

        self.assertNotIn(
            movie.tmdb_id,
            {row["tmdb_id"] for row in self._results("?audio_languages=EN&subtitle_languages=JA")},
        )
        Preference.objects.filter(user_id=1).update(vpn_enabled=True)
        self.assertIn(
            movie.tmdb_id,
            {row["tmdb_id"] for row in self._results("?audio_languages=EN&subtitle_languages=JA")},
        )

    def test_language_filter_respects_active_access_type_and_rental_entitlement(self):
        movie = Movie.objects.create(tmdb_id=849, title="Access Scoped Languages")
        self._subscribe("netflix")
        self._subscribe("apple", access_mode="rent_buy")
        local_offer(movie, service="netflix", audio_languages=["IT"], subtitle_languages=["IT"])
        local_offer(movie, service="apple", offer_type="rent", audio_languages=["EN"], subtitle_languages=["JA"])

        self.assertNotIn(
            movie.tmdb_id,
            {row["tmdb_id"] for row in self._results("?access=subscription&audio_languages=EN&subtitle_languages=JA")},
        )
        self.assertIn(
            movie.tmdb_id,
            {row["tmdb_id"] for row in self._results("?access=rent&audio_languages=EN&subtitle_languages=JA")},
        )

    def test_language_filter_without_language_params_preserves_catalogue_results(self):
        first = Movie.objects.create(tmdb_id=850, title="No Language Metadata")
        second = Movie.objects.create(tmdb_id=851, title="Different Language Metadata")
        local_offer(first, audio_languages=None, subtitle_languages=None)
        local_offer(second, audio_languages=["JA"], subtitle_languages=["IT"])

        self.assertEqual(
            {row["tmdb_id"] for row in self._results()},
            {first.tmdb_id, second.tmdb_id},
        )

    def test_language_filter_composes_with_watched_actor_type_and_provider_category(self):
        movie = Movie.objects.create(
            tmdb_id=852,
            title="Watched Actor Film",
            content_type="movie",
            cast=[{"id": 619, "name": "Example Actor"}],
        )
        series = Movie.objects.create(
            tmdb_id=853,
            title="Watched Actor Series",
            content_type="series",
            cast=[{"id": 619, "name": "Example Actor"}],
        )
        provider = self._subscribe("netflix", is_major=True)
        UserMovieData.objects.create(user_id=1, movie=movie, is_watched=True)
        UserMovieData.objects.create(user_id=1, movie=series, is_watched=True)
        local_offer(movie, audio_languages=["EN"], subtitle_languages=["JA"])
        local_offer(series, audio_languages=["EN"], subtitle_languages=["JA"])

        query = "?watched=watched&actor_id=619&provider_categories=major&audio_languages=EN&subtitle_languages=JA"
        rows = self._results(query)

        self.assertEqual({row["tmdb_id"] for row in rows}, {movie.tmdb_id, series.tmdb_id})
        self.assertTrue(all(row["provider"]["slug"] == provider.slug for row in rows))
        self.assertEqual(
            {row["tmdb_id"] for row in self._results(query + "&type=movie")},
            {movie.tmdb_id},
        )
        self.assertEqual(
            {row["tmdb_id"] for row in self._results(query + "&type=series")},
            {series.tmdb_id},
        )

    def test_unofficial_language_filter_reads_canonical_catalogue_metadata(self):
        self._scrape(
            "Known Scraped Languages",
            payload={"audio_languages": ["en"], "subtitle_languages": ["fr"]},
        )
        self._scrape("Unknown Scraped Languages")

        rows = self._results("?providers=hydra&audio_languages=EN&subtitle_languages=FR")

        self.assertEqual([row["title"] for row in rows], ["Known Scraped Languages"])

    def test_unofficial_language_groups_stay_on_one_resolved_or_unresolved_entry(self):
        resolved = Movie.objects.create(tmdb_id=854, title="Resolved Scraped Languages")
        self._scrape(
            "Resolved Scraped Languages",
            movie=resolved,
            payload={"audio_languages": ["EN"], "subtitle_languages": ["IT"]},
        )
        self._scrape(
            "Unresolved Scraped Languages",
            payload={"audio_languages": ["EN"], "subtitle_languages": ["JA"]},
        )

        rows = self._results(
            "?providers=hydra&audio_languages=EN&subtitle_languages=IT"
        )

        self.assertEqual([row["title"] for row in rows], ["Resolved Scraped Languages"])

    def test_unofficial_languages_do_not_union_across_provider_entries(self):
        second_provider = Provider.objects.create(
            slug="kraken", name="Kraken", category="scraper", is_enabled=True
        )
        self._scrape(
            "Hydra Audio Only",
            payload={"audio_languages": ["EN"], "subtitle_languages": ["IT"]},
        )
        self._scrape(
            "Kraken Subtitle Only",
            payload={"audio_languages": ["JA"], "subtitle_languages": ["JA"]},
            provider=second_provider,
        )

        rows = self._results(
            "?provider_categories=scraper&audio_languages=EN&subtitle_languages=JA"
        )

        self.assertEqual(rows, [])

    def test_provider_category_selects_scraper_providers(self):
        self._scrape("Hydra Category Result")

        rows = self._results("?provider_categories=scraper")

        self.assertTrue(rows)
        self.assertTrue(all(row["source"] == "scraper" for row in rows))

