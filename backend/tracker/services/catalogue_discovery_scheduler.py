"""Opt-in schedules for bounded catalogue discovery and canonical reconciliation.

The existing ``refresh_worker`` catalogue lane calls this once per tick. An
empty/unset ``UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES`` value disables every
recurring catalogue run. Configuration is JSON so providers and the three modes
can be enabled independently without adding a scheduling dependency or schema.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from django.utils import timezone as django_timezone

from tracker.models import CatalogueDiscoveryState, CatalogueReconciliationRun, Provider
from tracker.services.incremental_catalogue_reconciliation import run_incremental_reconciliation
from tracker.services.catalogue_discovery import (
    CatalogueDiscoveryError,
    PROVENANCE_BLOCKED,
    SCHEDULED_DISCOVERY_PROVIDERS,
    run_discovery,
)

logger = logging.getLogger(__name__)

DEFAULT_INTERVALS = {"historical": 24 * 60 * 60, "incremental": 6 * 60 * 60, "reconciliation": 60 * 60}
SCHEDULE_MODES = set(DEFAULT_INTERVALS)


def _parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _schedule_config(config=None):
    if config is None:
        raw = os.environ.get("UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES", "").strip()
        if not raw:
            return {"enabled": False, "providers": {}}
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES must be valid JSON") from exc
    if not isinstance(config, dict):
        raise ValueError("discovery schedule config must be a JSON object")
    return config


def _validated_mode_config(provider_slug, mode, value):
    if mode not in SCHEDULE_MODES or not isinstance(value, dict):
        raise ValueError(f"invalid schedule mode for {provider_slug}: {mode}")
    interval = int(value.get("interval_seconds", DEFAULT_INTERVALS[mode]))
    request_budget = int(value.get("request_budget", 8))
    delay = float(value.get("delay_seconds", 1.0))
    retry_limit = int(value.get("retry_limit", 1))
    overlap_pages = int(value.get("overlap_pages", 3))
    max_pages = int(value.get("max_pages", 1))
    max_redirects = int(value.get("max_redirects", 3))
    if not 60 <= interval <= 31 * 24 * 60 * 60:
        raise ValueError("schedule interval_seconds must be from 60 seconds to 31 days")
    if not 1 <= request_budget <= 40:
        raise ValueError("scheduled request_budget must be between 1 and 40")
    if not 1.0 <= delay <= 60.0:
        raise ValueError("scheduled delay_seconds must be between 1 and 60")
    if not 0 <= retry_limit <= 2:
        raise ValueError("scheduled retry_limit must be between 0 and 2")
    if not 1 <= overlap_pages <= 10:
        raise ValueError("scheduled overlap_pages must be between 1 and 10")
    if not 1 <= max_pages <= 250 or not 0 <= max_redirects <= 5:
        raise ValueError("scheduled max_pages must be 1..250 and max_redirects 0..5")
    result = {
        "enabled": value.get("enabled", False) is True,
        "interval_seconds": interval,
        "request_budget": request_budget,
        "delay_seconds": delay,
        "retry_limit": retry_limit,
        "overlap_pages": overlap_pages,
        "max_pages": max_pages,
        "max_redirects": max_redirects,
    }
    if mode == "reconciliation":
        limit = int(value.get("record_limit", 25))
        if not 1 <= limit <= 250:
            raise ValueError("scheduled record_limit must be between 1 and 250")
        allow_network = value.get("allow_network", False)
        if not isinstance(allow_network, bool):
            raise ValueError("scheduled allow_network must be a JSON boolean")
        result.update(record_limit=limit, allow_network=allow_network)
    return result


def process_due_discovery_schedules(*, config=None, now=None):
    """Run only enabled, due provider/mode schedules; each failure is isolated."""
    config = _schedule_config(config)
    now = now or django_timezone.now()
    if config.get("enabled", False) is not True:
        return {"status": "DISABLED", "runs": [], "skipped": []}
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("discovery schedule providers must be a JSON object")

    result = {"status": "ENABLED", "runs": [], "skipped": []}
    for provider_slug, provider_config in providers.items():
        if not isinstance(provider_config, dict) or provider_config.get("enabled", False) is not True:
            continue
        if provider_slug not in SCHEDULED_DISCOVERY_PROVIDERS:
            result["skipped"].append({"provider": provider_slug, "reason": "provider_not_schedule_eligible"})
            continue
        if provider_slug in PROVENANCE_BLOCKED:
            result["skipped"].append({"provider": provider_slug, "reason": PROVENANCE_BLOCKED[provider_slug]})
            continue
        try:
            Provider.objects.only("id").get(slug=provider_slug)
        except Provider.DoesNotExist:
            result["skipped"].append({"provider": provider_slug, "reason": "provider_missing"})
            continue
        state = CatalogueDiscoveryState.objects.filter(provider__slug=provider_slug).first()
        cursor = (state.cursor or {}) if state else {}
        modes = cursor.get("modes", {}) if isinstance(cursor, dict) else {}
        mode_reports = (state.report or {}).get("mode_reports", {}) if state else {}
        for mode in ("incremental", "historical", "reconciliation"):
            raw_mode_config = provider_config.get(mode)
            if raw_mode_config is None:
                continue
            try:
                mode_config = _validated_mode_config(provider_slug, mode, raw_mode_config)
            except (TypeError, ValueError) as exc:
                result["skipped"].append({"provider": provider_slug, "mode": mode, "reason": str(exc)})
                continue
            if not mode_config["enabled"]:
                continue
            mode_cursor = modes.get(mode, {}) if isinstance(modes, dict) else {}
            mode_report = mode_reports.get(mode, {})
            cooldown_until = _parse_timestamp(mode_cursor.get("cooldown_until")) or _parse_timestamp(mode_report.get("cooldown_until"))
            last_started = _parse_timestamp(mode_cursor.get("last_run_started_at"))
            if last_started is None and state and state.mode == ("full" if mode == "historical" else mode):
                last_started = state.started_at
            if mode == "reconciliation":
                reconciliation = CatalogueReconciliationRun.objects.filter(provider__slug=provider_slug).first()
                last_started = reconciliation.started_at if reconciliation else None
                cooldown_until = reconciliation.cooldown_until if reconciliation else None
            if cooldown_until and cooldown_until > now:
                result["skipped"].append({"provider": provider_slug, "mode": mode, "reason": "retry_after_cooldown", "next_run_at": cooldown_until.isoformat()})
                continue
            if last_started and now < last_started + timedelta(seconds=mode_config["interval_seconds"]):
                result["skipped"].append({"provider": provider_slug, "mode": mode, "reason": "interval_not_elapsed"})
                continue
            try:
                if mode == "reconciliation":
                    report = run_incremental_reconciliation(
                        provider_slug, limit=mode_config["record_limit"],
                        request_budget=mode_config["request_budget"],
                        interval=mode_config["delay_seconds"], retries=mode_config["retry_limit"],
                        allow_network=mode_config["allow_network"], apply=True,
                    )
                else:
                    report = run_discovery(
                        provider_slug,
                        mode=mode,
                        request_budget=mode_config["request_budget"],
                        delay=mode_config["delay_seconds"],
                        retry_limit=mode_config["retry_limit"],
                        overlap_pages=mode_config["overlap_pages"],
                        max_pages=mode_config["max_pages"], max_redirects=mode_config["max_redirects"],
                    )
            except CatalogueDiscoveryError as exc:
                report = exc.report
                logger.warning("unofficial_discovery_scheduled_run_failed provider=%s mode=%s report=%s", provider_slug, mode, report)
            except Exception as exc:  # one provider/mode must not block another schedule
                report = {"provider": provider_slug, "mode": mode, "status": "FAILED", "last_error": str(exc)}
                logger.exception("unofficial_discovery_scheduler_error provider=%s mode=%s", provider_slug, mode)
            result["runs"].append(report)
    return result
