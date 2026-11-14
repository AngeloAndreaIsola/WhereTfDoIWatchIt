from django.test import TestCase

from availability.services.legacy_ingest import ingest_scraped_mapping
from tracker.models import Movie, Provider, ScrapedContentMapping
from tracker.tests.availability_factories import local_offer


class RecoveredCatalogFilterTests(TestCase):
    def setUp(self):
        self.hydra = Provider.objects.create(
            slug="hydra", name="Hydra", category="scraper", is_enabled=True
        )

    def _scrape(self, title, *, year=2024, movie=None, payload=None):
        mapping = ScrapedContentMapping.objects.create(
            provider=self.hydra,
            scraped_title=title,
            scraped_year=year,
            scraped_url=f"https://hydra.example/{title.lower().replace(' ', '-')}",
            movie=movie,
            raw_payload=payload or {},
            is_available=True,
        )
        ingest_scraped_mapping(mapping)
        return mapping

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
        self._scrape("Scraped Series", payload={"content_type": "series"})

        rows = self._results("?type=tv")

        self.assertEqual({row["content_type"] for row in rows}, {"series"})
        self.assertIn(series.tmdb_id, {row["tmdb_id"] for row in rows})
        self.assertTrue(any(row["source"] == "scraper" for row in rows))

    def test_language_filters_require_known_matching_offer_languages(self):
        matching = Movie.objects.create(tmdb_id=841, title="Known Languages")
        unknown = Movie.objects.create(tmdb_id=842, title="Unknown Languages")
        local_offer(matching, audio_languages=["EN"], subtitle_languages=["FR"])
        local_offer(unknown, audio_languages=None, subtitle_languages=None)

        rows = self._results("?audio_languages=en&subtitle_languages=FR")

        self.assertEqual([row["tmdb_id"] for row in rows], [matching.tmdb_id])

    def test_unofficial_language_filter_reads_canonical_catalogue_metadata(self):
        self._scrape(
            "Known Scraped Languages",
            payload={"audio_languages": ["en"], "subtitle_languages": ["fr"]},
        )
        self._scrape("Unknown Scraped Languages")

        rows = self._results("?providers=hydra&audio_languages=EN&subtitle_languages=FR")

        self.assertEqual([row["title"] for row in rows], ["Known Scraped Languages"])

    def test_provider_category_selects_scraper_providers(self):
        self._scrape("Hydra Category Result")

        rows = self._results("?provider_categories=scraper")

        self.assertTrue(rows)
        self.assertTrue(all(row["source"] == "scraper" for row in rows))

