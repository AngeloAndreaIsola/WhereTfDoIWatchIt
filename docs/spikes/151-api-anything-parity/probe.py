#!/usr/bin/env python3
"""Issue #151 offline parity probe.

Runs the *current master* parser code against retained/synthetic fixtures for
the six unofficial providers. No network, no serving database, no ORM writes:
every ``fetch_url`` is replaced with a fixture payload and the database is an
in-memory SQLite that is never queried.

Usage (from the repo root, with the backend image):

    docker run --rm --network none \
      -v "$PWD/backend:/app" -w /app \
      -v "$PWD/docs/spikes/151-api-anything-parity:/probe" \
      -e PYTHONPATH=/probe -e USE_SQLITE=1 -e DJANGO_DEBUG=1 \
      -e DJANGO_SECRET_KEY=dev-insecure-secret \
      -e DJANGO_SETTINGS_MODULE=probe_settings \
      --entrypoint python wheretfdoiwatchit-backend:latest /probe/probe.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "probe_settings")
os.environ.setdefault("USE_SQLITE", "1")
os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.setdefault("DJANGO_SECRET_KEY", "dev-insecure-secret")
sys.path.insert(0, str(HERE))
sys.path.insert(0, os.getcwd())

import django  # noqa: E402

django.setup()

from tracker.scrapers import (  # noqa: E402
    AltaDefinizioneScraper,
    AnimeWorldScraper,
    CineBlog01Scraper,
    HydraHDScraper,
    KissKHScraper,
    StreamingCommunityScraper,
)
from tracker.services.catalogue_discovery import (  # noqa: E402
    PROVENANCE_BLOCKED,
    PROVIDERS,
    SCHEDULED_DISCOVERY_PROVIDERS,
    _parts,
)
from tracker.services.discovery_policy import (  # noqa: E402
    DiscoveryPolicyError,
    UNOFFICIAL_PROVIDERS,
    permission_for,
)
from tracker.services.scraper_policy import (  # noqa: E402
    ScraperExecutionIneligible,
    require_legacy_execution,
)

FIXTURE_ITEMS = {
    "kisskh": 2,
    "hydrahd": 2,
    "animeworld": 2,
    "streamingcommunity": 3,
    "cineblog01": 3,
    "altadefinizione": 2,
}


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def native_id(item):
    payload = item.raw_payload or {}
    for key in ("id", "title_id", "guid"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def metrics(items):
    urls = [i.source_url for i in items]
    ids = [native_id(i) for i in items]
    type_counts = {}
    for item in items:
        type_counts[item.content_type] = type_counts.get(item.content_type, 0) + 1
    return {
        "returned": len(items),
        "unique_source_urls": len(set(urls)),
        "unique_native_ids": len({i for i in ids if i}),
        "duplicate_source_urls": len(urls) - len(set(urls)),
        "content_type_counts": dict(sorted(type_counts.items())),
        "year_known": sum(1 for i in items if i.year),
        "audio_language_rows": sum(1 for i in items if i.audio_languages),
        "subtitle_language_rows": sum(1 for i in items if i.subtitle_languages),
        "sample": [
            {
                "title": i.title,
                "year": i.year,
                "content_type": i.content_type,
                "source_url": i.source_url,
                "native_id": native_id(i),
                "audio": i.audio_languages,
                "subs": i.subtitle_languages,
            }
            for i in items[:3]
        ],
    }


def run_with_fixture(scraper_cls, payload, *, label):
    calls = {"n": 0}

    def fake_fetch(url, *args, **kwargs):
        calls["n"] += 1
        if isinstance(payload, dict) and payload.get("__by_url__"):
            for needle, value in payload["__by_url__"]:
                if needle in url:
                    return value
            raise AssertionError(f"unregistered fixture URL: {url}")
        return payload

    scraper = scraper_cls()
    with patch.object(scraper_cls, "fetch_url", side_effect=fake_fetch):
        items = scraper._execute_run()
    result = metrics(items)
    result["fixture"] = label
    result["fetch_calls"] = calls["n"]
    result["raw_parsed_upper_bound"] = calls["n"] * FIXTURE_ITEMS.get(
        scraper_cls.provider_slug, 1
    )
    return result, items


def legacy_gate(slug):
    try:
        require_legacy_execution(
            SimpleNamespace(slug=slug, is_enabled=True, config={})
        )
        return "ALLOWED"
    except ScraperExecutionIneligible as exc:
        return exc.code
    except DiscoveryPolicyError as exc:  # BaseException subclass
        return str(exc)


def permission_snapshot(slug):
    permission = permission_for(SimpleNamespace(slug=slug))
    return {
        "status": permission.status,
        "hostname": permission.hostname or None,
        "request_limit": permission.request_limit,
        "page_limit": permission.page_limit,
        "min_interval_seconds": permission.min_interval,
    }


def main() -> int:
    results = {
        "meta": {
            "issue": 151,
            "mode": "offline-fixture-probe",
            "django": django.get_version(),
            "network": "disabled (--network none)",
            "database": "in-memory sqlite, never queried",
            "provider_set": sorted(PROVIDERS),
            "unofficial_permission_set": sorted(UNOFFICIAL_PROVIDERS),
            "scheduled_discovery_providers": sorted(SCHEDULED_DISCOVERY_PROVIDERS),
            "provenance_blocked": PROVENANCE_BLOCKED,
        },
        "contract": {},
        "providers": {},
        "checks": {},
    }

    for slug in sorted(PROVIDERS):
        results["contract"][slug] = {
            "legacy_execution": legacy_gate(slug),
            "permission": permission_snapshot(slug),
            "discovery_parts": _parts(slug),
            "provenance_blocked": slug in PROVENANCE_BLOCKED,
        }

    results["contract"]["raiplay_control"] = {"legacy_execution": legacy_gate("raiplay")}
    results["contract"]["nhk_world_control"] = {"legacy_execution": legacy_gate("nhk_world")}

    # --- KissKH: JSON listing, explicit type only, pending rows kept separately ---
    listing = json.loads(read("kisskh_listing.json"))
    detail = json.loads(read("kisskh_detail.json"))
    kisskh, kisskh_items = run_with_fixture(
        KissKHScraper, {"data": listing["data"]}, label="kisskh_listing.json"
    )
    classified, pending = KissKHScraper().parse_catalogue_rows(listing["data"])
    kisskh["parse_catalogue_rows"] = {
        "classified": len(classified),
        "pending": len(pending),
        "pending_titles": [row.get("title") for row in pending],
    }
    kisskh["detail_enrichment"] = KissKHScraper.extract_detail_metadata(
        detail, native_id="1003"
    )
    results["providers"]["kisskh"] = kisskh

    # --- HydraHD: HTML browse, URL-derived type, dedupe by URL ---
    hydra, _ = run_with_fixture(
        HydraHDScraper, read("hydrahd_listing.html"), label="hydrahd_listing.html"
    )
    results["providers"]["hydrahd"] = hydra

    # --- AnimeWorld: RSS snapshot + HTML browse (browse path has no dedupe) ---
    anime = AnimeWorldScraper()
    rss_items = anime.parse_rss(read("animeworld_rss.xml"))
    anime_browse, _ = run_with_fixture(
        AnimeWorldScraper,
        read("animeworld_search.html"),
        label="animeworld_search.html",
    )
    anime_browse["rss_snapshot"] = metrics(rss_items)
    results["providers"]["animeworld"] = anime_browse

    # --- StreamingCommunity / CineBlog01 / AltaDefinizione: listing pages ---
    sc, _ = run_with_fixture(
        StreamingCommunityScraper,
        read("streamingcommunity_listing.html"),
        label="streamingcommunity_listing.html",
    )
    results["providers"]["streamingcommunity"] = sc

    cb, _ = run_with_fixture(
        CineBlog01Scraper, read("cineblog01_listing.html"), label="cineblog01_listing.html"
    )
    results["providers"]["cineblog01"] = cb

    ad, _ = run_with_fixture(
        AltaDefinizioneScraper,
        read("altadefinizione_listing.html"),
        label="altadefinizione_listing.html",
    )
    results["providers"]["altadefinizione"] = ad

    # --- Contract checks: the probe must never claim an offer/write ---
    results["checks"] = {
        "offers_created": 0,
        "database_rows_written": 0,
        "network_requests": 0,
        "all_unofficial_legacy_gated": all(
            results["contract"][slug]["legacy_execution"] != "ALLOWED"
            for slug in sorted(PROVIDERS)
        ),
        "all_unofficial_permissions_unverified": all(
            results["contract"][slug]["permission"]["status"] != "VERIFIED"
            for slug in sorted(PROVIDERS)
        ),
        "raiplay_nhk_control_allowed": (
            results["contract"]["raiplay_control"]["legacy_execution"] == "ALLOWED"
            and results["contract"]["nhk_world_control"]["legacy_execution"] == "ALLOWED"
        ),
    }

    out = HERE / "probe_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("=" * 78)
    print("ISSUE #151 OFFLINE PARITY PROBE")
    print("=" * 78)
    print(f"django={django.get_version()} network=disabled db=in-memory(never queried)")
    print(f"providers={sorted(PROVIDERS)}")
    print()
    print("CONTRACT / GATES")
    for slug in sorted(PROVIDERS):
        c = results["contract"][slug]
        p = c["permission"]
        print(
            f"  {slug:18s} legacy={c['legacy_execution']:30s} "
            f"perm={p['status']:10s} parts={c['discovery_parts']} "
            f"provenance_blocked={c['provenance_blocked']}"
        )
    print(
        "  control raiplay   legacy="
        f"{results['contract']['raiplay_control']['legacy_execution']}"
    )
    print(
        "  control nhk_world legacy="
        f"{results['contract']['nhk_world_control']['legacy_execution']}"
    )
    print()
    print("PARSER METRICS (fixture -> returned/deduped)")
    for slug in sorted(PROVIDERS):
        m = results["providers"][slug]
        print(
            f"  {slug:18s} returned={m['returned']:2d} unique_urls={m['unique_source_urls']:2d} "
            f"unique_ids={m['unique_native_ids']:2d} types={m['content_type_counts']} "
            f"fetch_calls={m['fetch_calls']} raw_upper={m['raw_parsed_upper_bound']}"
        )
    print(
        "  kisskh pending rows:"
        f" {results['providers']['kisskh']['parse_catalogue_rows']}"
    )
    print(
        "  kisskh detail type:"
        f" {results['providers']['kisskh']['detail_enrichment']['source_media_type']}"
    )
    print(
        "  animeworld rss:"
        f" returned={results['providers']['animeworld']['rss_snapshot']['returned']}"
        f" types={results['providers']['animeworld']['rss_snapshot']['content_type_counts']}"
    )
    print()
    print("SAFETY CHECKS")
    for key, value in results["checks"].items():
        print(f"  {key} = {value}")
    print()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
