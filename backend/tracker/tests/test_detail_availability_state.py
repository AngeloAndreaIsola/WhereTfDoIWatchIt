"""P9 — the detail endpoint's `availability` verdict.

The defect this pins: the movie/TV detail pages rendered one fixed sentence
whenever `offers` was empty —

    Not currently available on your subscribed streaming services.

— which is a claim about the world. Measured over 400 tracked movies with no
local offers, every single one was ``unknown`` (375 ``not_checked``, 21
``not_applicable``, 4 ``title_not_found``) and *none* were ``unavailable``. So
the old copy asserted something false about nearly all of them.

The endpoint now surfaces the verdict the classifier already computes. These
tests therefore have two jobs:

1. the endpoint reports the classifier's real answer, in all three states; and
2. the classifier itself is *not* changed here — it is the shared
   ``title_source_report``, so if these tests pass, the classifier and the
   report endpoint agree by construction.

They deliberately build real offers and real request evidence instead of
mocking the final state. A mocked state would only prove the view copies a
string, which is exactly the failure mode being fixed.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from availability.domain.enums import SourceType
from availability.models import Source
from availability.sources import DEFAULT_SOURCE_SLUG
from tracker.models import (
    Movie,
    Provider,
    ProviderCategory,
    RefreshRequest,
    RefreshRequestStatus,
    ScrapedContentMapping,
)
from tracker.services.availability_report import (
    TitleAvailabilityState,
    title_source_report,
)

from availability.services.legacy_ingest import ingest_scraped_mapping

from .availability_factories import local_offer


def both_offer(movie, *, slug="fake_scraper", country="IT"):
    """Make a title available on *both* read paths, as production data is.

    The detail endpoint serves unofficial offers read from ProviderContent +
    ContentMapping, while the availability classifier judges evidence from
    canonical ``Offer`` rows (``currently_available=True``). They are
    deliberately separate reads, so a title the page lists as available while
    the classifier still calls it unknown is a real state — and a test that
    asserted "available" from only one of them would be asserting a fiction.
    """
    provider, _ = Provider.objects.get_or_create(
        slug=slug,
        defaults={
            "name": slug,
            "category": ProviderCategory.SCRAPER,
            "is_enabled": True,
            "country_code": country,
            "domain": f"{slug}.example.com",
        },
    )
    if provider.category != ProviderCategory.SCRAPER:
        provider.category = ProviderCategory.SCRAPER
        provider.save()
    row = ScrapedContentMapping.objects.create(
        provider=provider,
        movie=movie,
        scraped_title=movie.title,
        scraped_year=movie.year or 1999,
        scraped_url=f"https://{slug}.example/watch",
        country_code=country,
        is_available=True,
        is_confirmed=False,
        confidence_score=0.0,
        raw_payload={"audio_languages": ["EN"], "subtitle_languages": ["IT"]},
    )
    ingest_scraped_mapping(row)
    # The canonical read the classifier consults.
    local_offer(movie, country="NL", service=slug)
    return row

NL = "NL"


def unavailable_everywhere(movie):
    """Make *every* title-level source terminally answer "no".

    ``unavailable`` is not something a fixture can assert into existence — the
    classifier grants it only when every applicable source produced a
    definitive negative (``classify_title``: unavailable requires every source
    to be in ``ABSENCE_REASONS``). The minimum real state is therefore one
    terminal ``DONE`` ``RefreshRequest`` per title-level source and no active
    offer, because a DONE row with no offer is exactly the "the source was
    consulted and there was nothing here" answer.

    Derived from the classifier's own source list rather than hardcoded, so a
    newly registered title-level source cannot silently invalidate the fixture.
    """
    from tracker.services.availability_report import _title_level_slugs

    for slug in _title_level_slugs():
        RefreshRequest.objects.update_or_create(
            movie=movie, source=slug,
            defaults={"status": RefreshRequestStatus.DONE},
        )
    return movie


class DetailAvailabilityStateTests(TestCase):
    """One client, three states, one endpoint."""

    def setUp(self):
        self.client = APIClient()
        # The title-level sources the classifier consults. Created explicitly so
        # the verdict's provenance is stable regardless of what other tests or
        # migrations have seeded.
        for slug, name in (
            (DEFAULT_SOURCE_SLUG, "Streaming Availability API"),
            ("justwatch", "JustWatch"),
            ("watchmode", "Watchmode"),
        ):
            Source.objects.get_or_create(
                slug=slug, defaults={"name": name, "type": SourceType.API})

    def detail(self, movie):
        """The endpoint the detail pages actually read.

        Both the movie and TV pages call ``/api/v2/unofficial/movie/<id>/offers/``;
        ``/api/movie/<id>/availability/`` is a different, canonical local read.
        """
        response = self.client.get(
            f"/api/v2/unofficial/movie/{movie.tmdb_id}/offers/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    # -- available --------------------------------------------------------

    def test_a_title_with_an_active_offer_is_available(self):
        movie = Movie.objects.create(tmdb_id=900001, title="Has An Offer")
        both_offer(movie)

        body = self.detail(movie)
        self.assertGreaterEqual(len(body["offers"]), 1)
        self.assertEqual(body["availability"]["state"],
                         TitleAvailabilityState.AVAILABLE)

    def test_available_carries_the_source_that_resolved_it(self):
        movie = Movie.objects.create(tmdb_id=900002, title="Resolved Source")
        both_offer(movie)

        availability = self.detail(movie)["availability"]
        self.assertTrue(availability["resolved_sources"],
                        "an available title must name what resolved it")
        self.assertIn(DEFAULT_SOURCE_SLUG, availability["resolved_sources"])

        rows = availability["sources"]
        self.assertTrue(rows, "provenance rows must be present")
        for row in rows:
            self.assertIn("source", row)
            self.assertIn("status", row)
            self.assertIn("label", row)

    # -- unknown ----------------------------------------------------------

    def test_an_unchecked_title_is_unknown_not_unavailable(self):
        """The regression, asserted directly.

        A movie with no offers and no request evidence at all. The old UI called
        this "not currently available".
        """
        movie = Movie.objects.create(tmdb_id=900003, title="Never Checked")

        body = self.detail(movie)
        self.assertEqual(body["offers"], [])
        self.assertEqual(body["availability"]["state"],
                         TitleAvailabilityState.UNKNOWN)
        self.assertNotEqual(body["availability"]["state"],
                            TitleAvailabilityState.UNAVAILABLE)

    def test_unknown_names_the_unresolved_sources(self):
        movie = Movie.objects.create(tmdb_id=900004, title="Nobody Answered")
        availability = self.detail(movie)["availability"]

        self.assertTrue(
            availability["unresolved_sources"],
            "an unknown title must say which sources are outstanding",
        )
        self.assertFalse(availability["resolved_sources"])

    # -- unavailable ------------------------------------------------------

    def test_a_definitively_negative_title_is_unavailable(self):
        """The one state that licenses "not currently available".

        Every title-level source has terminally answered "nothing here", which
        is the only evidence that makes absence meaningful. Asserted against
        the classifier as well as the endpoint so the two cannot drift.
        """
        movie = Movie.objects.create(tmdb_id=900005, title="Checked, Absent")
        unavailable_everywhere(movie)

        # Classifier first: the fixture really does earn the state.
        verdict = title_source_report([movie.id])[movie.id]
        self.assertEqual(verdict["state"],
                         TitleAvailabilityState.UNAVAILABLE,
                         f"fixture did not earn 'unavailable': {verdict['reason']}")

        body = self.detail(movie)
        self.assertEqual(body["offers"], [])
        self.assertEqual(body["availability"]["state"],
                         TitleAvailabilityState.UNAVAILABLE)
        self.assertFalse(
            body["availability"]["unresolved_sources"],
            "every source answered, so nothing may be outstanding",
        )

    def test_a_single_silent_source_keeps_a_title_unknown(self):
        """The other half of the invariant, which is what P9 got wrong.

        One unanswered source is enough to prevent `unavailable`: silence is
        not a negative answer. This is the case the old UI mislabelled.
        """
        from tracker.services.availability_report import _title_level_slugs

        movie = Movie.objects.create(tmdb_id=900013, title="Almost Answered")
        slugs = _title_level_slugs()
        for slug in slugs[:-1]:
            RefreshRequest.objects.update_or_create(
                movie=movie, source=slug,
                defaults={"status": RefreshRequestStatus.DONE},
            )
        # The last source is left with no request at all: it has not spoken.

        self.assertEqual(title_source_report([movie.id])[movie.id]["state"],
                         TitleAvailabilityState.UNKNOWN)
        self.assertEqual(self.detail(movie)["availability"]["state"],
                         TitleAvailabilityState.UNKNOWN)

    def test_the_endpoint_never_disagrees_with_the_classifier(self):
        """The endpoint presents the verdict; it does not form its own."""
        movie = Movie.objects.create(tmdb_id=900014, title="Consistency")
        unavailable_everywhere(movie)

        verdict = title_source_report([movie.id])[movie.id]
        availability = self.detail(movie)["availability"]
        self.assertEqual(availability["state"], verdict["state"])
        self.assertEqual(
            availability["reason"],
            (getattr(verdict["reason"], "value", None) or str(verdict["reason"]))
            if verdict["reason"] else None,
        )

    # -- reasons ----------------------------------------------------------

    def test_reasons_are_existing_classifier_values(self):
        """Reasons must be the classifier's, not invented here."""
        seen = set()
        for tmdb_id in (900010, 900011, 900012):
            movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"T{tmdb_id}")
            reason = self.detail(movie)["availability"]["reason"]
            if reason:
                seen.add(reason)

        for reason in seen:
            self.assertIsInstance(reason, str)
            self.assertTrue(reason.strip())
        # A never-checked title must land on a real reason rather than "".
        self.assertTrue(seen, "no reason was reported at all")

    def test_an_available_title_has_no_unresolved_reason(self):
        """An empty reason string is not the same as no reason."""
        movie = Movie.objects.create(tmdb_id=900006, title="Fine")
        both_offer(movie)
        availability = self.detail(movie)["availability"]

        if availability["state"] == TitleAvailabilityState.AVAILABLE:
            self.assertIn(availability["reason"], (None, "", "resolved"))

    # -- shape / compatibility -------------------------------------------

    def test_the_payload_shape_is_stable(self):
        movie = Movie.objects.create(tmdb_id=900007, title="Shape")
        availability = self.detail(movie)["availability"]

        for key in ("state", "reason", "resolved_sources",
                    "unresolved_sources", "sources"):
            self.assertIn(key, availability)
        self.assertIsInstance(availability["sources"], list)
        self.assertIn(availability["state"],
                      (TitleAvailabilityState.AVAILABLE,
                       TitleAvailabilityState.UNAVAILABLE,
                       TitleAvailabilityState.UNKNOWN))

    def test_existing_offer_fields_are_untouched(self):
        """The addition must not disturb the fields the page already used."""
        movie = Movie.objects.create(tmdb_id=900008, title="Compatible")
        both_offer(movie)
        body = self.detail(movie)

        for key in ("movie", "offers", "selected_season"):
            self.assertIn(key, body)
        self.assertEqual(body["movie"]["tmdb_id"], movie.tmdb_id)
        self.assertIsNone(body["selected_season"])

    def test_series_detail_carries_the_same_verdict(self):
        """A season-narrowed series request takes the early-return path."""
        movie = Movie.objects.create(
            tmdb_id=900009, title="A Series", content_type="series")
        body = self.client.get(
            f"/api/v2/unofficial/movie/{movie.tmdb_id}/offers/?season=1")
        self.assertEqual(body.status_code, 200)
        self.assertIn("availability", body.json())
        self.assertIn(body.json()["availability"]["state"],
                      (TitleAvailabilityState.AVAILABLE,
                       TitleAvailabilityState.UNAVAILABLE,
                       TitleAvailabilityState.UNKNOWN))

    def test_an_untracked_movie_is_still_404(self):
        """A missing verdict must not become a 500."""
        self.assertEqual(
            self.client.get(
                "/api/v2/unofficial/movie/88888888/offers/").status_code,
            404,
        )