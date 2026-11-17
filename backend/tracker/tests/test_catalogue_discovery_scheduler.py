from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from tracker.models import CatalogueDiscoveryState, Provider
from tracker.services.catalogue_discovery import CatalogueDiscoveryError
from tracker.services.catalogue_discovery_scheduler import process_due_discovery_schedules


class CatalogueDiscoverySchedulerTests(TestCase):
    def setUp(self):
        self.hydra = Provider.objects.get_or_create(
            slug="hydrahd", defaults={"name": "HydraHD", "category": "scraper"}
        )[0]
        self.animeworld = Provider.objects.get_or_create(
            slug="animeworld", defaults={"name": "AnimeWorld", "category": "scraper"}
        )[0]

    def test_scheduling_is_disabled_when_unconfigured(self):
        with patch.dict("os.environ", {"UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES": ""}), \
             patch("tracker.services.catalogue_discovery_scheduler.run_discovery") as run:
            result = process_due_discovery_schedules()

        self.assertEqual(result, {"status": "DISABLED", "runs": [], "skipped": []})
        run.assert_not_called()

    def test_modes_have_independent_due_times_budgets_and_enable_switches(self):
        now = timezone.now()
        CatalogueDiscoveryState.objects.create(
            provider=self.hydra,
            status="PARTIAL",
            mode="full",
            cursor={"modes": {"historical": {"last_run_started_at": (now - timedelta(minutes=5)).isoformat()}}},
        )
        config = {
            "enabled": True,
            "providers": {
                "hydrahd": {
                    "enabled": True,
                    "historical": {"enabled": True, "interval_seconds": 600, "request_budget": 12},
                    "incremental": {"enabled": True, "interval_seconds": 60, "request_budget": 7, "overlap_pages": 2},
                },
                "animeworld": {"enabled": False, "incremental": {"enabled": True}},
            },
        }
        with patch("tracker.services.catalogue_discovery_scheduler.run_discovery", return_value={"status": "PARTIAL"}) as run:
            result = process_due_discovery_schedules(config=config, now=now)

        self.assertEqual(len(result["runs"]), 1)
        run.assert_called_once_with(
            "hydrahd", mode="incremental", request_budget=7, delay=1.0,
            retry_limit=1, overlap_pages=2,
        )
        self.assertEqual(result["skipped"], [{"provider": "hydrahd", "mode": "historical", "reason": "interval_not_elapsed"}])

    def test_retry_after_cooldown_suppresses_due_mode(self):
        now = timezone.now()
        CatalogueDiscoveryState.objects.create(
            provider=self.hydra,
            status="BLOCKED",
            mode="incremental",
            cursor={"modes": {"incremental": {"cooldown_until": (now + timedelta(minutes=10)).isoformat()}}},
        )
        config = {"enabled": True, "providers": {"hydrahd": {
            "enabled": True,
            "incremental": {"enabled": True, "interval_seconds": 60},
        }}}
        with patch("tracker.services.catalogue_discovery_scheduler.run_discovery") as run:
            result = process_due_discovery_schedules(config=config, now=now)

        run.assert_not_called()
        self.assertEqual(result["skipped"][0]["reason"], "retry_after_cooldown")

    def test_bad_provider_schedule_is_isolated_and_other_provider_runs(self):
        config = {"enabled": True, "providers": {
            "hydrahd": {"enabled": True, "historical": {"enabled": True, "request_budget": 500}},
            "animeworld": {"enabled": True, "incremental": {"enabled": True, "request_budget": 2}},
        }}
        with patch("tracker.services.catalogue_discovery_scheduler.run_discovery", return_value={"provider": "animeworld", "status": "PARTIAL"}) as run:
            result = process_due_discovery_schedules(config=config)

        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["runs"][0]["provider"], "animeworld")
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["provider"], "hydrahd")

    def test_run_failure_does_not_prevent_other_provider_schedule(self):
        config = {"enabled": True, "providers": {
            "hydrahd": {"enabled": True, "historical": {"enabled": True}},
            "animeworld": {"enabled": True, "incremental": {"enabled": True}},
        }}

        def run(provider_slug, **kwargs):
            if provider_slug == "hydrahd":
                raise CatalogueDiscoveryError({"provider": provider_slug, "mode": kwargs["mode"], "status": "FAILED"})
            return {"provider": provider_slug, "mode": kwargs["mode"], "status": "PARTIAL"}

        with patch("tracker.services.catalogue_discovery_scheduler.run_discovery", side_effect=run):
            result = process_due_discovery_schedules(config=config)

        self.assertEqual([r["provider"] for r in result["runs"]], ["hydrahd", "animeworld"])
        self.assertEqual(result["runs"][0]["status"], "FAILED")
        self.assertEqual(result["runs"][1]["status"], "PARTIAL")
