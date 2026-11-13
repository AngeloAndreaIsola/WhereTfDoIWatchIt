"""Tests for WT-040: Rights-reviewed public-domain archive metadata collector.

Acceptance criteria:
- Stored title provenance includes rights URL and original landing page, unknown when rights ambiguous.
- Source health and budget tracked; budget limits enforced.
- Static saved response fixtures, idempotent catalog sync, soft failures, no live APIs in tests.
- Never assert public-domain status merely because content is hosted on Internet Archive or in a collection.
- Never assert WORLDWIDE territorial scope from age or a bare Public Domain Mark alone.
- Items with UNKNOWN territorial scope emit DiscoveredContent (keeping provenance for review)
  but omit confirmed ProviderOffers for an arbitrary country.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.models import (
    Country,
    Offer,
    ProviderContent,
    Source,
    OfferEvidence,
    SyncCursor,
)
from availability.services.public_domain_catalogue import (
    RightsStatus,
    SourceBudgetTracker,
    TerritorialScope,
    build_provenance_record,
    evaluate_item_rights,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.persistence.repositories import OfferRepository
from availability.sources import SourceFetchError, source_registry
from availability.sources.base import ContentRequest
from availability.sources.public_domain_archive import (
    IA_FIELDS,
    PublicDomainArchiveClient,
    PublicDomainArchiveSource,
)
from tracker.models import Movie, Provider

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "public_domain"


def load_fixture(filename: str) -> dict:
    with open(FIXTURES_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


class RightsEvaluationServiceTests(SimpleTestCase):
    """Unit tests for evaluate_item_rights and build_provenance_record."""

    def test_verified_public_domain_mark_and_cc0(self):
        # Bare PDM without jurisdiction or territorial text -> UNKNOWN scope, not watchable in arbitrary country
        item_bare = {
            "identifier": "bare_pdm_1968",
            "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
            "rights": "Public domain mark",
        }
        res_bare = evaluate_item_rights(item_bare)
        self.assertEqual(res_bare.status, RightsStatus.PUBLIC_DOMAIN)
        self.assertEqual(res_bare.territorial_scope, TerritorialScope.UNKNOWN)
        self.assertTrue(res_bare.has_public_domain_mark)
        self.assertFalse(res_bare.is_watchable_in_country("US"))
        self.assertFalse(res_bare.is_watchable_in_country("NL"))

        # CC0 universal dedication without restrictions -> WORLDWIDE scope, watchable in US and NL
        item_cc0 = {
            "identifier": "charade_1963",
            "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/",
            "rights": "CC0 1.0 Universal",
        }
        res_cc0 = evaluate_item_rights(item_cc0)
        self.assertEqual(res_cc0.status, RightsStatus.PUBLIC_DOMAIN)
        self.assertEqual(res_cc0.territorial_scope, TerritorialScope.WORLDWIDE)
        self.assertTrue(res_cc0.has_public_domain_mark)
        self.assertTrue(res_cc0.is_watchable_in_country("US"))
        self.assertTrue(res_cc0.is_watchable_in_country("NL"))

    def test_library_of_congress_us_determination(self):
        loc_item = {
            "identifier": "hitch_hiker_1953",
            "rights": "The Library of Congress is not aware of any U.S. copyright or other restrictions in this motion picture. Absent restrictions, this material is free to use and reuse in the United States.",
            "jurisdiction": "US",
        }
        res = evaluate_item_rights(loc_item)
        self.assertEqual(res.status, RightsStatus.PUBLIC_DOMAIN)
        self.assertEqual(res.territorial_scope, TerritorialScope.US_ONLY)
        self.assertTrue(res.has_public_domain_mark)
        self.assertTrue(res.is_watchable_in_country("US"))
        self.assertFalse(res.is_watchable_in_country("NL"))

    def test_territorial_conflict_and_caveats(self):
        # Work public domain in US, but protected by foreign copyright
        conflict_item = {
            "identifier": "conflict_item",
            "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
            "rights": "Public domain in the United States, but may be protected by copyright in other countries.",
            "jurisdiction": "US",
        }
        res = evaluate_item_rights(conflict_item)
        self.assertEqual(res.status, RightsStatus.PUBLIC_DOMAIN)
        self.assertEqual(res.territorial_scope, TerritorialScope.US_ONLY)
        self.assertTrue(res.has_public_domain_mark)
        self.assertTrue(res.is_watchable_in_country("US"))
        self.assertFalse(res.is_watchable_in_country("NL"))

    def test_ambiguous_and_missing_rights_flagged_unknown(self):
        bare_item = {
            "identifier": "uploaded_movie_123",
            "title": "Unverified Movie",
        }
        res = evaluate_item_rights(bare_item)
        self.assertEqual(res.status, RightsStatus.AMBIGUOUS_UNKNOWN)
        self.assertEqual(res.territorial_scope, TerritorialScope.UNKNOWN)
        self.assertFalse(res.has_public_domain_mark)
        self.assertFalse(res.is_watchable_in_country("US"))

    def test_restricted_license_flagged_restricted(self):
        nc_item = {
            "identifier": "indie_film",
            "licenseurl": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
        }
        res = evaluate_item_rights(nc_item)
        self.assertEqual(res.status, RightsStatus.RESTRICTED)
        self.assertFalse(res.has_public_domain_mark)
        self.assertFalse(res.is_watchable_in_country("US"))

    def test_negated_and_contradictory_rights_never_authorize(self):
        for license_url in (
            "https://creativecommons.org/publicdomain/mark/1.0/",
            "https://creativecommons.org/publicdomain/zero/1.0/",
            "https://creativecommons.org/licenses/by/4.0/",
            "https://creativecommons.org/licenses/by-sa/4.0/",
        ):
            for statement, jurisdiction in (
                ("Not public domain worldwide", ""),
                ("No worldwide rights", ""),
                ("Copyrighted worldwide", ""),
                ("Worldwide rights unknown", ""),
                ("Might be public domain worldwide", ""),
                ("Not public domain in the United States", "US"),
                ("Public domain globally except the Netherlands", ""),
                ("Public domain worldwide, protected by foreign copyright in other countries", ""),
                ("Public domain worldwide", "US"),
                ("All rights reserved. Public domain worldwide", ""),
            ):
                with self.subTest(license=license_url, rights=statement, jurisdiction=jurisdiction):
                    result = evaluate_item_rights({"licenseurl": license_url, "rights": statement,
                                                   "jurisdiction": jurisdiction})
                    self.assertEqual(result.territorial_scope, TerritorialScope.UNKNOWN)
                    self.assertFalse(result.is_watchable_in_country("NL"))
                    self.assertFalse(result.is_watchable_in_country("US"))

    def test_recognized_open_licenses_and_unrecognized_urls(self):
        for name in ("by", "by-sa"):
            result = evaluate_item_rights({"licenseurl": f"https://creativecommons.org/licenses/{name}/4.0/"})
            self.assertEqual(result.status, RightsStatus.OPEN_LICENSE)
            self.assertTrue(result.is_watchable_in_country("NL"))
        for url in ("https://creativecommons.org/licenses/by/unknown/",
                    "https://creativecommons.org/publicdomain/zero/1.0/untrusted"):
            result = evaluate_item_rights({"licenseurl": url, "rights": "Public domain worldwide"})
            self.assertFalse(result.is_watchable_in_country("NL"))

    def test_multivalue_and_malformed_license_metadata_fail_closed(self):
        cc0 = "https://creativecommons.org/publicdomain/zero/1.0/"
        self.assertTrue(evaluate_item_rights({"licenseurl": [cc0]}).is_watchable_in_country("NL"))
        for value in ([cc0, "https://example.org/other-rights"], {"url": cc0}, 123):
            with self.subTest(value=value):
                result = evaluate_item_rights({"licenseurl": value})
                self.assertFalse(result.is_watchable_in_country("NL"))
                self.assertEqual(result.status, RightsStatus.AMBIGUOUS_UNKNOWN)

    def test_provenance_preserves_rights_url_and_landing_page(self):
        item = {
            "identifier": "test_provenance_film",
            "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
            "rights": "Verified US & Worldwide PD",
            "landing_page": "https://archive.org/details/test_provenance_film",
        }
        eval_res = evaluate_item_rights(item)
        prov = build_provenance_record(item, eval_res)
        self.assertEqual(prov.identifier, "test_provenance_film")
        self.assertEqual(prov.landing_page_url, "https://archive.org/details/test_provenance_film")
        self.assertEqual(prov.rights_url, "http://creativecommons.org/publicdomain/mark/1.0/")
        self.assertEqual(prov.rights_status, RightsStatus.PUBLIC_DOMAIN)


class SourceContractTests(SimpleTestCase):
    """Source registration, capabilities, query parameters, and budget metrics."""

    def test_source_is_registered(self):
        source = source_registry.get("public_domain_archive")
        self.assertIsInstance(source, PublicDomainArchiveSource)
        self.assertEqual(source.slug, "public_domain_archive")
        self.assertEqual(source.source_type, SourceType.API)

    def test_capabilities_declare_catalogue_sync(self):
        caps = PublicDomainArchiveSource.capabilities
        self.assertIn("internet_archive", caps.supported_providers)
        self.assertTrue(caps.supports_catalogue_sync)
        self.assertTrue(caps.supports_resumable_catalogue_sync)
        self.assertFalse(caps.supports_authoritative_snapshots)
        self.assertFalse(caps.supports_incremental_sync)

    def test_targeted_fetch_rejected(self):
        source = PublicDomainArchiveSource()
        with self.assertRaises(ValueError):
            source.fetch(ContentRequest(tmdb_id=1, content_type="movie", title="Some Movie"))

    def test_client_builds_bounded_ia_query_params(self):
        captured_params = {}

        def fetcher(endpoint, params):
            captured_params.update(params)
            return {"response": {"docs": [], "numFound": 0}}

        client = PublicDomainArchiveClient(fetcher=fetcher)
        client.fetch_catalogue("https://archive.org/advancedsearch.php", page=2, rows=25)

        self.assertIn("mediatype:movies", captured_params["q"])
        self.assertEqual(captured_params["fl[]"], IA_FIELDS)
        self.assertEqual(captured_params["rows"], 25)
        self.assertEqual(captured_params["page"], 2)
        self.assertEqual(captured_params["output"], "json")

    def test_budget_tracker_enforces_limit_and_health(self):
        tracker = SourceBudgetTracker(max_requests=2)
        self.assertFalse(tracker.budget_exhausted)
        tracker.record_request(success=True)
        self.assertEqual(tracker.requests_made, 1)
        self.assertTrue(tracker.health_status["healthy"])

        tracker.record_request(success=False)
        self.assertEqual(tracker.requests_made, 2)
        self.assertTrue(tracker.budget_exhausted)
        self.assertFalse(tracker.health_status["healthy"])

    def test_fetch_halts_when_budget_exhausted(self):
        calls = []

        def fetcher(endpoint, params):
            calls.append(params)
            return {"response": {"docs": [], "numFound": 0}}

        client = PublicDomainArchiveClient(fetcher=fetcher)
        source = PublicDomainArchiveSource(client=client, max_requests=1)

        source.fetch()
        self.assertEqual(len(calls), 1)

        res = source.fetch()
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.contents, [])
        self.assertEqual(res.offers, [])


class PublicDomainArchiveCollectorTests(TestCase):
    """Integration test with simulated offline fixtures through AvailabilityRefreshService."""

    @classmethod
    def setUpTestData(cls):
        Provider.objects.get_or_create(
            slug="internet_archive", defaults={"name": "Internet Archive", "is_enabled": True}
        )
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        # Pre-seed TMDb movie for match
        Movie.objects.create(
            tmdb_id=10331,
            title="Night of the Living Dead",
            year=1968,
            content_type="movie",
        )
        Movie.objects.create(
            tmdb_id=480,
            title="Charade",
            year=1963,
            content_type="movie",
        )

    def _make_source(self, fixture_payload: dict, batch_size: int = 50) -> PublicDomainArchiveSource:
        def fetcher(endpoint, params):
            return fixture_payload

        client = PublicDomainArchiveClient(fetcher=fetcher)
        return PublicDomainArchiveSource(client=client, batch_size=batch_size)

    def test_fetch_separates_discovered_content_from_territorial_offers(self):
        fixture_data = load_fixture("archive_items.json")
        source = self._make_source(fixture_data)
        res = source.fetch(country="US")

        # In archive_items.json:
        # - Night of the Living Dead (PD Mark US) -> DiscoveredContent + Offer (US)
        # - Charade (CC0 Worldwide) -> DiscoveredContent + Offer (Worldwide -> US)
        # - The Hitch-Hiker (LOC US statement) -> DiscoveredContent + Offer (US)
        # - Dracula (ambiguous/null) -> metadata retained, no offer
        # - Territorial Conflict Film (PDM US only, foreign copyright) -> DiscoveredContent + Offer (US)
        # - Bare PDM Film (PDM without jurisdiction or territory -> UNKNOWN scope) -> DiscoveredContent (kept for audit), NO Offer
        # - Open Source Adventure (CC BY-NC-ND) -> restricted metadata retained, no offer
        # - missing metadata item -> skipped
        # - Flash Gordon (PD Mark episode) -> DiscoveredContent + Offer (US)
        identifiers = {c.provider_content_id for c in res.contents}
        self.assertIn("night_of_the_living_dead_1968", identifiers)
        self.assertIn("charade_1963_pd", identifiers)
        self.assertIn("the_hitch_hiker_1953_loc", identifiers)
        self.assertIn("territorial_conflict_film", identifiers)
        self.assertIn("bare_pdm_no_jurisdiction", identifiers)
        self.assertIn("flash_gordon_s01e01", identifiers)
        self.assertIn("dracula_1931_ambiguous", identifiers)
        self.assertIn("modern_indie_cc_by", identifiers)
        self.assertEqual(len(res.contents), 8)

        # Offers emitted only for items watchable in US:
        # 5 items have confirmed US rights (NOT bare_pdm_no_jurisdiction)
        offer_ids = {o.provider_content_id for o in res.offers}
        self.assertIn("night_of_the_living_dead_1968", offer_ids)
        self.assertIn("charade_1963_pd", offer_ids)
        self.assertIn("the_hitch_hiker_1953_loc", offer_ids)
        self.assertIn("territorial_conflict_film", offer_ids)
        self.assertIn("flash_gordon_s01e01", offer_ids)
        # CRUCIAL: bare PDM with UNKNOWN scope emits NO positive offer for an arbitrary country
        self.assertNotIn("bare_pdm_no_jurisdiction", offer_ids)
        self.assertEqual(len(res.offers), 5)

        # Provenance check: deep link to original landing page
        notld = next(c for c in res.contents if c.provider_content_id == "night_of_the_living_dead_1968")
        self.assertEqual(notld.url, "https://archive.org/details/night_of_the_living_dead_1968")

        # Serial episode ordinals
        fg = next(c for c in res.contents if c.provider_content_id == "flash_gordon_s01e01")
        self.assertEqual(fg.content_type, ContentType.EPISODE)
        self.assertEqual(fg.season_number, 1)
        self.assertEqual(fg.episode_number, 1)

        # Audio language evidence check: stated language is normalized; unstated is None
        notld_offer = next(o for o in res.offers if o.provider_content_id == "night_of_the_living_dead_1968")
        self.assertEqual(notld_offer.audio_languages, ["EN"])

        charade_offer = next(o for o in res.offers if o.provider_content_id == "charade_1963_pd")
        self.assertIsNone(charade_offer.audio_languages)

    def test_unauthorized_country_receives_no_offers_for_us_only_items(self):
        """When queried for a foreign territory (e.g. NL), US-only items must NOT emit offers."""
        fixture_data = load_fixture("archive_items.json")
        source = self._make_source(fixture_data)
        res = source.fetch(country="NL")

        offer_ids = {o.provider_content_id for o in res.offers}
        # Only Charade (CC0 Worldwide) emits an offer in NL:
        self.assertEqual(offer_ids, {"charade_1963_pd"})
        self.assertNotIn("the_hitch_hiker_1953_loc", offer_ids)
        self.assertNotIn("territorial_conflict_film", offer_ids)
        self.assertNotIn("night_of_the_living_dead_1968", offer_ids)

    def test_refresh_catalogue_persists_provider_content_and_offers_idempotently(self):
        fixture_data = load_fixture("archive_items.json")
        source = self._make_source(fixture_data)
        service = AvailabilityRefreshService(source=source)

        first_run = service.refresh_catalogue()
        self.assertEqual(first_run.discovered, 5)
        # Night of the Living Dead and Charade match local catalog Movies,
        # other 3 are unresolved offer-wise (no local Movie row)
        self.assertEqual(first_run.processed, 2)
        self.assertEqual(first_run.unresolved, 3)

        # ProviderContent rows are persisted for all 8 discovered items
        self.assertEqual(ProviderContent.objects.filter(provider__slug="internet_archive").count(), 8)
        self.assertEqual(Offer.objects.filter(provider__slug="internet_archive").count(), 2)

        # Verify Offer metadata
        offer = Offer.objects.get(provider_content_id="night_of_the_living_dead_1968")
        self.assertEqual(offer.tmdb_id, 10331)
        self.assertEqual(offer.provider_url, "https://archive.org/details/night_of_the_living_dead_1968")
        self.assertEqual(offer.offer_type, OfferType.FREE)

        # Repeated run is completely idempotent (no new offers created)
        second_run = service.refresh_catalogue()
        self.assertEqual(second_run.created, 0)
        self.assertEqual(second_run.deactivated, 0)
        self.assertEqual(ProviderContent.objects.filter(provider__slug="internet_archive").count(), 8)
        self.assertEqual(Offer.objects.filter(provider__slug="internet_archive").count(), 2)

    def test_completed_refresh_preserves_rights_provenance_and_unknown_metadata(self):
        payload = load_fixture("archive_items.json")
        service = AvailabilityRefreshService(source=self._make_source(payload))
        service.refresh_catalogue()
        self.assertFalse(SyncCursor.objects.filter(source__slug="public_domain_archive").exists())
        row = ProviderContent.objects.get(provider_content_id="night_of_the_living_dead_1968")
        provenance = row.metadata["rights_provenance"]
        self.assertEqual(provenance["rights_url"], "http://creativecommons.org/publicdomain/mark/1.0/")
        self.assertEqual(provenance["landing_page_url"], row.provider_url)
        self.assertEqual(provenance["raw_rights_text"], "Public domain in the United States.")
        self.assertEqual(provenance["rights_status"], "public_domain")
        self.assertEqual(provenance["territorial_scope"], "us_only")
        self.assertTrue(provenance["observed_at"])
        for identifier in ("bare_pdm_no_jurisdiction", "dracula_1931_ambiguous"):
            row = ProviderContent.objects.get(provider_content_id=identifier)
            self.assertEqual(row.metadata["rights_provenance"]["territorial_scope"], "unknown")
            self.assertFalse(Offer.objects.filter(provider_content_id=identifier).exists())
        self.assertEqual(ProviderContent.objects.get(provider_content_id="dracula_1931_ambiguous")
                         .metadata["rights_provenance"]["raw_rights_text"], "")
        self.assertFalse(Offer.objects.filter(country__code="NL").exists())
        service.refresh_catalogue()
        row.refresh_from_db()
        self.assertIn("rights_provenance", row.metadata)

    def test_negated_worldwide_item_is_persisted_without_offer(self):
        item = {"identifier": "charade_1963_pd", "title": "Charade", "year": 1963,
                "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
                "rights": "Not public domain worldwide"}
        service = AvailabilityRefreshService(source=self._make_source({"response": {"docs": [item], "numFound": 1}}))
        service.refresh_catalogue()
        row = ProviderContent.objects.get(provider_content_id=item["identifier"])
        self.assertEqual(row.metadata["rights_provenance"]["territorial_scope"], "unknown")
        self.assertFalse(Offer.objects.filter(provider_content_id=item["identifier"]).exists())

    def test_rights_downgrade_withdraws_previous_positive_offer(self):
        item = {"identifier": "charade_1963_pd", "title": "Charade", "year": 1963,
                "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/"}
        payload = {"response": {"docs": [item], "numFound": 1}}
        service = AvailabilityRefreshService(source=self._make_source(payload))
        service.refresh_catalogue()
        offer = Offer.objects.get(provider_content_id=item["identifier"])
        self.assertTrue(offer.currently_available)
        item["rights"] = "Not public domain worldwide"
        result = service.refresh_catalogue()
        offer.refresh_from_db()
        self.assertFalse(offer.currently_available)
        self.assertEqual(result.deactivated, 1)
        item["rights"] = "CC0 worldwide dedication"
        service.refresh_catalogue()
        offer.refresh_from_db()
        self.assertTrue(offer.currently_available)
        self.assertEqual(Offer.objects.filter(provider_content_id=item["identifier"]).count(), 1)

    def test_rights_withdrawal_preserves_independent_source_evidence(self):
        item = {"identifier": "charade_1963_pd", "title": "Charade", "year": 1963,
                "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/"}
        payload = {"response": {"docs": [item], "numFound": 1}}
        service = AvailabilityRefreshService(source=self._make_source(payload))
        service.refresh_catalogue()
        offer = Offer.objects.get(provider_content_id=item["identifier"])
        other = Source.objects.create(slug="independent_archive_review", name="Independent review", type="api")
        OfferRepository().upsert(provider=offer.provider, country=offer.country, source=other,
                                 content_type=offer.content_type, tmdb_id=offer.tmdb_id,
                                 offer_type=offer.offer_type, provider_content_id=offer.provider_content_id)
        item["rights"] = "Not public domain worldwide"
        service.refresh_catalogue()
        offer.refresh_from_db()
        self.assertTrue(offer.currently_available)
        self.assertFalse(OfferEvidence.objects.get(offer_identity=offer.identity,
                         source__slug="public_domain_archive").is_present)
        self.assertTrue(OfferEvidence.objects.get(offer_identity=offer.identity, source=other).is_present)

    def test_failed_page_preserves_checkpoint_for_retry(self):
        pages = load_fixture("paginated_archive.json")
        failed_once = False
        requested_pages = []

        def fetcher(endpoint, params):
            nonlocal failed_once
            page = params["page"]
            requested_pages.append(page)
            if page == 2 and not failed_once:
                failed_once = True
                raise SourceFetchError("Temporary page failure")
            return pages[f"page_{page}"]

        service = AvailabilityRefreshService(source=PublicDomainArchiveSource(
            client=PublicDomainArchiveClient(fetcher=fetcher), batch_size=2,
        ))
        service.refresh_catalogue()
        checkpoint = SyncCursor.objects.get(source__slug="public_domain_archive").cursor
        with self.assertRaises(SourceFetchError):
            service.refresh_catalogue()
        self.assertEqual(SyncCursor.objects.get(source__slug="public_domain_archive").cursor, checkpoint)
        service.refresh_catalogue()
        self.assertEqual(requested_pages, [1, 2, 2])
        self.assertFalse(SyncCursor.objects.filter(source__slug="public_domain_archive").exists())
        self.assertEqual(ProviderContent.objects.filter(provider__slug="internet_archive").count(), 3)

    def test_paginated_walk_checkpoints_forward(self):
        paginated_data = load_fixture("paginated_archive.json")

        def fetcher(endpoint, params):
            page = params.get("page", 1)
            if page == 1:
                return paginated_data["page_1"]
            if page == 2:
                return paginated_data["page_2"]
            raise SourceFetchError(f"Unknown page {page}")

        client = PublicDomainArchiveClient(fetcher=fetcher)
        source = PublicDomainArchiveSource(client=client, batch_size=2)
        res1 = source.fetch()
        self.assertEqual(len(res1.contents), 2)
        self.assertIsNotNone(res1.cursor)

        # Resume using next cursor
        res2 = source.fetch(cursor=res1.cursor)
        self.assertEqual(len(res2.contents), 1)
        self.assertEqual(res2.contents[0].provider_content_id, "nosferatu_1922")
        self.assertIsNone(res2.cursor)

    def test_soft_failures_leave_database_intact(self):
        def failing_fetcher(endpoint, params):
            raise SourceFetchError("Archive API timeout / 503")

        client = PublicDomainArchiveClient(fetcher=failing_fetcher)
        source = PublicDomainArchiveSource(client=client)
        service = AvailabilityRefreshService(source=source)

        initial_offer_count = Offer.objects.count()
        with self.assertRaises(SourceFetchError):
            service.refresh_catalogue()

        # Database state remains untouched
        self.assertEqual(Offer.objects.count(), initial_offer_count)

    def test_cursor_checkpoint_recovery_and_completion_lifecycle(self):
        """SyncCursor row is persisted, advanced across batches, and cleared on completion."""
        paginated_data = load_fixture("paginated_archive.json")

        def fetcher(endpoint, params):
            page = params.get("page", 1)
            if page == 1:
                return paginated_data["page_1"]
            if page == 2:
                return paginated_data["page_2"]
            raise SourceFetchError(f"Unknown page {page}")

        client = PublicDomainArchiveClient(fetcher=fetcher)
        source = PublicDomainArchiveSource(client=client, batch_size=2)
        service = AvailabilityRefreshService(source=source)

        # Batch 1: walks 2 items, emits cursor page_2
        service.refresh_catalogue()
        cursor_row = SyncCursor.objects.filter(source__slug="public_domain_archive").first()
        self.assertIsNotNone(cursor_row)
        self.assertEqual(cursor_row.provider.slug, "internet_archive")
        self.assertEqual(cursor_row.country.code, "US")
        self.assertTrue(cursor_row.cursor)

        # Batch 2: completes remainder of catalogue, removes SyncCursor row
        service.refresh_catalogue()
        self.assertFalse(SyncCursor.objects.filter(source__slug="public_domain_archive").exists())
