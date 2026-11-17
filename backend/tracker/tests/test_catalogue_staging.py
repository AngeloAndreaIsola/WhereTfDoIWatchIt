import json
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from tracker.models import CatalogueDiscoveryState, DiscoveredCatalogueRecord, Provider
from tracker.services.catalogue_identity import normalize_catalogue_url
from tracker.services.catalogue_staging import export_catalogue_inventory, import_catalogue_inventory


class CatalogueStagingTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="hydrahd", defaults={"name": "HydraHD"})

    def create_record(self):
        discovered = timezone.now() - timedelta(days=5)
        row = DiscoveredCatalogueRecord.objects.create(
            provider=self.provider, provider_native_id="native-7", title="Raw title",
            source_url="https://hydrahd.gd/movie/7?utm_source=listing",
            identity_url=normalize_catalogue_url("hydrahd", "https://hydrahd.gd/movie/7?utm_source=listing"),
            source_urls_seen=["https://hydrahd.gd/movie/7?utm_source=listing"],
            original_metadata={"raw": {"title": "Original", "nested": [1, 2]}},
            classification_state="PENDING", discovery_verification_state="OBSERVED",
            provenance_state="SELF_ASSERTED", access_policy_state="PERMITTED",
        )
        DiscoveredCatalogueRecord.objects.filter(pk=row.pk).update(discovered_at=discovered)
        row.refresh_from_db()
        return row

    def test_export_and_replay_preserve_raw_fields_timestamp_and_checkpoint(self):
        record = self.create_record()
        checkpoint, _ = CatalogueDiscoveryState.objects.update_or_create(
            provider=self.provider,
            defaults={"status": "PARTIAL", "mode": "full", "cursor": {"parts": {"movies": 12}},
                      "report": {"pages_scanned": 12}, "last_error": "bounded stop"},
        )
        with tempfile.TemporaryDirectory() as directory:
            exported = export_catalogue_inventory(directory)
            manifest = json.loads(Path(exported["manifest_path"]).read_text())
            self.assertEqual(manifest["record_count"], 1)
            self.assertEqual(manifest["records_by_provider"], {"hydrahd": 1})
            self.assertEqual(len(manifest["data_sha256"]), 64)

            record.delete()
            checkpoint.delete()
            first = import_catalogue_inventory(exported["data_path"])
            second = import_catalogue_inventory(exported["data_path"])

        restored = DiscoveredCatalogueRecord.objects.get(provider=self.provider)
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(restored.discovered_at, record.discovered_at)
        self.assertEqual(restored.title, "Raw title")
        self.assertEqual(restored.provider_native_id, "native-7")
        self.assertEqual(restored.original_metadata, {"raw": {"title": "Original", "nested": [1, 2]}})
        self.assertEqual(state.cursor, {"parts": {"movies": 12}})
        self.assertEqual(state.last_error, "bounded stop")

    def test_import_rejects_tampered_payload_before_writing(self):
        self.create_record()
        with tempfile.TemporaryDirectory() as directory:
            exported = export_catalogue_inventory(directory)
            Path(exported["data_path"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                import_catalogue_inventory(exported["data_path"])
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=self.provider).count(), 1)
