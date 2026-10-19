"""Executable NL catalogue acceptance list (task 7).

The task fixes a minimum set of 31 services that must be covered for the
Netherlands. This turns that list into a guard over the static JustWatch
identity tables, so a future rename/removal that silently drops one of them
fails here instead of quietly shrinking what the app can cover.

It asserts the *static* mapping only (no network, no DB seeding), which is what
makes it stable: the tables are the single source of truth the catalogue sync
walks, and every entry's canonical slug must also be walkable in NL (have a
measured short name) — otherwise it would be discovered but never collected.
"""
from django.test import SimpleTestCase

from availability.sources.justwatch_providers import (
    PACKAGE_SHORT_NAMES,
    PACKAGE_TO_PROVIDER_SLUG,
    provider_slug_for_package,
)

#: (label, technical name(s) on JustWatch, canonical slug). Every NL service on
#: the acceptance list that JustWatch actually carries.
NL_ACCEPTANCE: list[tuple[str, tuple[str, ...], str]] = [
    ("Netflix", ("netflix", "netflixkids"), "netflix"),
    ("Netflix Kids", ("netflixkids",), "netflix"),
    ("Amazon Prime Video", ("amazonprimevideo",), "prime"),
    ("Disney+", ("disneyplus",), "disney"),
    ("Apple TV+", ("appletvplus",), "apple"),
    ("Videoland", ("videoland",), "videoland"),
    ("Viaplay", ("viaplay", "viaplaysports"), "viaplay"),
    ("SkyShowtime", ("skyshowtime",), "skyshowtime"),
    ("Canal+", ("canalplus",), "canal_plus"),
    ("NLZIET", ("nlziet",), "nlziet"),
    ("Film1", ("film1nl",), "film1"),
    ("Hayu", ("hayu",), "hayu"),
    ("Ziggo TV", ("ziggo",), "ziggo"),
    ("NPO Start", ("npostart",), "npo_start"),
    ("Picl", ("picl",), "picl"),
    ("Sooner", ("sooner",), "sooner"),
    ("IFFR Unleashed", ("iffrunleashed",), "iffr_unleashed"),
    ("Pathé Thuis", ("pathe",), "pathe_thuis"),
    ("Apple TV Store", ("itunes",), "apple_tv_store"),
    ("Rakuten TV", ("wuaki",), "rakuten_tv"),
    ("Crunchyroll", ("crunchyroll",), "crunchyroll"),
    ("Curiosity Stream", ("curiositystream",), "curiosity_stream"),
    ("GuideDoc", ("guidedoc",), "guidedoc"),
    ("DOCSVILLE", ("docsville",), "docsville"),
    ("Magellan TV", ("magellantv",), "magellan_tv"),
    ("BroadwayHD", ("broadwayhd",), "broadwayhd"),
    ("WOW Presents Plus", ("wowpresentsplus",), "wow_presents_plus"),
    ("JustWatch TV", ("justwatchtv",), "justwatch_tv"),
    ("Filmzie", ("filmzie",), "filmzie"),
    ("South Park", ("southpark",), "south_park"),
]

#: On the requested list but *not carried by JustWatch in NL* — recorded here so
#: the omission is a decision, not an oversight. Google Play Movies has no NL
#: package on the measured endpoint.
NL_ACCEPTANCE_ABSENT: list[tuple[str, tuple[str, ...]]] = [
    ("Google Play Movies", ("googleplay", "googleplaymovies")),
]


class NLCatalogueAcceptanceTests(SimpleTestCase):
    def test_every_acceptance_service_maps_to_its_canonical_slug(self):
        for label, technicals, slug in NL_ACCEPTANCE:
            for technical in technicals:
                self.assertEqual(
                    provider_slug_for_package(technical),
                    slug,
                    f"{label}: {technical!r} must map to {slug!r}",
                )

    def test_every_acceptance_slug_is_walkable_in_nl(self):
        for label, _technicals, slug in NL_ACCEPTANCE:
            self.assertIn(
                slug,
                PACKAGE_SHORT_NAMES,
                f"{label} ({slug}) has no measured short-name table",
            )
            self.assertIn(
                "NL",
                PACKAGE_SHORT_NAMES[slug],
                f"{label} ({slug}) is not walkable in NL",
            )

    def test_netflix_kids_is_an_alias_not_a_second_entitlement(self):
        # Netflix Kids is a curated Netflix profile: its titles belong to the
        # Netflix subscription, so it must not become a separate provider.
        self.assertEqual(provider_slug_for_package("netflixkids"), "netflix")
        self.assertNotIn("netflix_kids", PACKAGE_TO_PROVIDER_SLUG.values())

    def test_apple_tv_store_is_distinct_from_apple_tv_plus(self):
        # A rent/buy storefront must never resolve to the subscription service.
        self.assertEqual(provider_slug_for_package("itunes"), "apple_tv_store")
        self.assertEqual(provider_slug_for_package("appletvplus"), "apple")

    def test_absent_services_are_not_silently_mapped(self):
        for label, technicals in NL_ACCEPTANCE_ABSENT:
            for technical in technicals:
                self.assertEqual(
                    provider_slug_for_package(technical),
                    "",
                    f"{label}: {technical!r} must stay unmapped (not offered in NL)",
                )
