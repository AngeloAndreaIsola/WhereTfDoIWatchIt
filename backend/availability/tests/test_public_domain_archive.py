"""Tests for WT-040: Rights-reviewed public-domain archive metadata collector.

Acceptance criteria:
- Stored title provenance includes rights URL and original landing page, unknown when rights ambiguous.
- Source health and budget tracked.
- Static saved response fixtures, idempotent catalog sync, soft failures, no live APIs in tests.
- Never assert public-domain status merely because content is hosted on Internet Archive or in a collection.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from django.db.migrations.operations.special import RunSQL
from availability.models import (
    Country,
    Offer,
    ProviderContent,
    SyncCursor,
)
from availability.persistence.repositories import CursorRepository
from unittest.mock import patch

# SQLite test setup isolation:
# Migration tracker.0035 uses postgres-specific ALTER TABLE ADD CONSTRAINT syntax.
# For SQLite test runs, intercept table alteration without modifying the production migration.
_orig_runsql_forwards = RunSQL.database_forwards


def _sqlite_safe_database_forwards(self, app_label, schema_editor, from_state, to_state):
    if schema_editor.connection.vendor == "sqlite":
        if "tracker_externalprovidermapping" in str(self.sql):
            schema_editor.execute(
                """
                CREATE TABLE tracker_externalprovidermapping_new (
                    id integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                    integration_name varchar(50) NOT NULL,
                    external_id varchar(100) NOT NULL,
                    provider_id bigint NOT NULL REFERENCES tracker_provider (id) DEFERRABLE INITIALLY DEFERRED,
                    country varchar(2) NOT NULL,
                    CONSTRAINT uniq_external_provider_mapping UNIQUE (provider_id, integration_name, country)
                );
            """
            )
            schema_editor.execute(
                "INSERT INTO tracker_externalprovidermapping_new SELECT id, integration_name, external_id, provider_id, country FROM tracker_externalprovidermapping;"
            )
            schema_editor.execute("DROP TABLE tracker_externalprovidermapping;")
            schema_editor.execute(
                "ALTER TABLE tracker_externalprovidermapping_new RENAME TO tracker_externalprovidermapping;"
            )
            return
    return _orig_runsql_forwards(self, app_label, schema_editor, from_state, to_state)


RunSQL.database_forwards = _sqlite_safe_database_forwards
from availability.services.public_domain_catalogue import (
    RightsStatus,
    SourceBudgetTracker,
    TerritorialScope,
    build_provenance_record,
    evaluate_item_rights,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceFetchError, source_registry
from availability.sources.base import ContentRequest
from availability.sources.public_domain_archive import (
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
        item_pdm = {
            "identifier": "notld_1968",
            "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
            "rights": "Public domain mark",
        }
        res = evaluate_item_rights(item_pdm)
        self.assertEqual(res.status, RightsStatus.PUBLIC_DOMAIN)
        self.assertEqual(res.territorial_scope, TerritorialScope.WORLDWIDE)
        self.assertTrue(res.is_watchable_public_domain)

        item_cc0 = {
            "identifier": "charade_1963",
            "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/",
            "rights": "CC0 1.0 Universal",
        }
        res_cc0 = evaluate_item_rights(item_cc0)
        self.assertEqual(res_cc0.status, RightsStatus.PUBLIC_DOMAIN)
        self.assertEqual(res_cc0.territorial_scope, TerritorialScope.WORLDWIDE)
        self.assertTrue(res_cc0.is_watchable_public_domain)

    def test_library_of_congress_us_determination(self):
        loc_item = {
            "identifier": "hitch_hiker_1953",
            "rights": "The Library of Congress is not aware of any U.S. copyright or other restrictions in this motion picture. Absent restrictions, this material is free to use and reuse in the United States.",
            "jurisdiction": "US",
        }
        res = evaluate_item_rights(loc_item)
        self.assertEqual(res.status, RightsStatus.PUBLIC_DOMAIN)
        self.assertEqual(res.territorial_scope, TerritorialScope.US_ONLY)
        self.assertTrue(res.is_watchable_public_domain)

    def test_ambiguous_and_missing_rights_flagged_unknown(self):
        # Missing licenseurl and missing rights text
        bare_item = {
            "identifier": "uploaded_movie_123",
            "title": "Unverified Movie",
        }
        res = evaluate_item_rights(bare_item)
        self.assertEqual(res.status, RightsStatus.AMBIGUOUS_UNKNOWN)
        self.assertEqual(res.territorial_scope, TerritorialScope.UNKNOWN)
        self.assertFalse(res.is_watchable_public_domain)

    def test_restricted_license_flagged_restricted(self):
        nc_item = {
            "identifier": "indie_film",
            "licenseurl": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
        }
        res = evaluate_item_rights(nc_item)
        self.assertEqual(res.status, RightsStatus.RESTRICTED)
        self.assertFalse(res.is_watchable_public_domain)

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
    """Source registration, capabilities, and budget metrics."""

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

    def test_budget_tracker_records_requests_and_health(self):
        tracker = SourceBudgetTracker(max_requests=2)
        self.assertFalse(tracker.budget_exhausted)
        tracker.record_request(success=True)
        self.assertEqual(tracker.requests_made, 1)
        self.assertTrue(tracker.health_status["healthy"])
        tracker.record_request(success=True)
        self.assertTrue(tracker.budget_exhausted)


class PublicDomainArchiveCollectorTests(TestCase):
    """Integration test with simulated offline fixtures through AvailabilityRefreshService."""

    @classmethod
    def setUpTestData(cls):
        Provider.objects.get_or_create(
            slug="internet_archive", defaults={"name": "Internet Archive", "is_enabled": True}
        )
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
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

    def test_fetch_filters_ambiguous_and_restricted_items(self):
        fixture_data = load_fixture("archive_items.json")
        source = self._make_source(fixture_data)
        res = source.fetch()

        # In archive_items.json:
        # - Night of the Living Dead (PD Mark) -> watchable PD
        # - Charade (CC0) -> watchable PD
        # - The Hitch-Hiker (LOC US statement) -> watchable PD
        # - Dracula (ambiguous/null) -> skipped/flagged unknown
        # - Open Source Adventure (CC BY-NC-ND) -> restricted, skipped
        # - missing metadata item -> skipped
        # - Flash Gordon (PD Mark episode) -> watchable PD
        self.assertEqual(len(res.contents), 4)
        self.assertEqual(len(res.offers), 4)

        identifiers = {c.provider_content_id for c in res.contents}
        self.assertIn("night_of_the_living_dead_1968", identifiers)
        self.assertIn("charade_1963_pd", identifiers)
        self.assertIn("the_hitch_hiker_1953_loc", identifiers)
        self.assertIn("flash_gordon_s01e01", identifiers)
        self.assertNotIn("dracula_1931_ambiguous", identifiers)
        self.assertNotIn("modern_indie_cc_by", identifiers)

        # Provenance check: deep link to original landing page
        notld = next(c for c in res.contents if c.provider_content_id == "night_of_the_living_dead_1968")
        self.assertEqual(notld.url, "https://archive.org/details/night_of_the_living_dead_1968")

        # Serial episode ordinals
        fg = next(c for c in res.contents if c.provider_content_id == "flash_gordon_s01e01")
        self.assertEqual(fg.content_type, ContentType.EPISODE)
        self.assertEqual(fg.season_number, 1)
        self.assertEqual(fg.episode_number, 1)

    def test_refresh_catalogue_persists_provider_content_and_offers_idempotently(self):
        fixture_data = load_fixture("archive_items.json")
        source = self._make_source(fixture_data)
        service = AvailabilityRefreshService(source=source)

        first_run = service.refresh_catalogue()
        self.assertEqual(first_run.discovered, 4)
        # Night of the Living Dead and Charade match local catalog Movies,
        # Hitch-Hiker and Flash Gordon are unresolved offer-wise (no local Movie row)
        self.assertEqual(first_run.processed, 2)
        self.assertEqual(first_run.unresolved, 2)

        # ProviderContent rows are persisted for all 4 discovered items
        self.assertEqual(ProviderContent.objects.filter(provider__slug="internet_archive").count(), 4)
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
        self.assertEqual(ProviderContent.objects.filter(provider__slug="internet_archive").count(), 4)
        self.assertEqual(Offer.objects.filter(provider__slug="internet_archive").count(), 2)

    def test_paginated_walk_checkpoints_forward(self):
        paginated_data = load_fixture("paginated_archive.json")

        def fetcher(endpoint, params):
            cursor = params.get("cursor")
            if not cursor:
                return paginated_data["page_1"]
            if cursor == "page_2":
                return paginated_data["page_2"]
            raise SourceFetchError(f"Unknown cursor {cursor}")

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
            cursor = params.get("cursor")
            if not cursor:
                return paginated_data["page_1"]
            if cursor == "page_2":
                return paginated_data["page_2"]
            raise SourceFetchError(f"Unknown cursor {cursor}")

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
