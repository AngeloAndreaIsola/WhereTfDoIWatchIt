"""Bounded, resumable catalogue discovery for the six unofficial providers.

This is deliberately command-driven. Each page is committed before the next
request, through the established legacy ingestion bridge; discoveries remain
unverified and never create playback offers.
"""
from __future__ import annotations

import re
import time
import hashlib
import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from django.utils import timezone as django_timezone
from django.db import transaction
from django.db.models import Count

from tracker.models import CatalogueDiscoveryState, DiscoveredCatalogueRecord, KissKHPendingClassification, Provider
from tracker.scrapers import resolve_scraper_class
from tracker.scrapers.adapters import KissKHScraper
from tracker.scrapers.cineblog01 import BROWSE_SECTIONS
from tracker.scrapers.streamingcommunity import BROWSE_PATHS as SC_PATHS
from tracker.scrapers.altadefinizione import BROWSE_PATHS as AD_PATHS
from tracker.services.scraped_mapping import persist_scraped_item, provider_content_id_for_item
from tracker.services.bulk_refresh import try_source_lock, release_source_lock
from tracker.services.catalogue_identity import normalize_catalogue_url


PROVIDERS = {"animeworld", "kisskh", "hydrahd", "cineblog01", "streamingcommunity", "altadefinizione"}
PROVENANCE_BLOCKED = {
    "cineblog01": "PROVENANCE_UNVERIFIED: current .run candidate is not linked to the historical AGCOM domains",
    "altadefinizione": "PROVENANCE_UNVERIFIED: current .ink candidate is not linked to the provider; the cited AGCOM host is distinct",
    "streamingcommunity": "PROVENANCE_UNVERIFIED: current .army candidate has no corroborated provider-domain linkage",
}
DISCOVERY_MODE_KEYS = {"full": "historical", "historical": "historical", "incremental": "incremental", "targeted": "targeted"}
SCHEDULED_DISCOVERY_PROVIDERS = {"hydrahd", "animeworld"}
DEFAULT_INCREMENTAL_OVERLAP_PAGES = 3
MAX_INLINE_RETRY_AFTER_SECONDS = 30.0
DISCOVERY_LEASE_SECONDS = int(os.environ.get("UNOFFICIAL_CATALOGUE_LEASE_SECONDS", "3600"))


class _RequestBudgetReached(BaseException):
    pass


class _HttpFailure(Exception):
    def __init__(self, status, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}")


class CatalogueDiscoveryError(RuntimeError):
    """A failed bounded run with its request and checkpoint evidence attached."""

    def __init__(self, report):
        self.report = report
        super().__init__(report.get("last_error") or "Catalogue discovery failed")


class CataloguePageRows(list):
    """Classified rows plus valid observations awaiting stronger type evidence."""
    def __init__(self, items=(), pending=(), *, total_count=None, page=None, page_size=None):
        super().__init__(items)
        self.pending = list(pending)
        self.total_count = total_count
        self.page = page
        self.page_size = page_size


def resolve_kisskh_pending_classification(pending, detail):
    """Promote a pending row only when detail type explicitly says Movie/TVSeries.

    Calling this again with a non-resolving payload records another attempt and
    leaves the row pending, making retries safe and idempotent.
    """
    from tracker.scrapers.base import ScrapedItem

    scraper = KissKHScraper()
    metadata = scraper.extract_detail_metadata(detail, native_id=pending.native_id)
    pending.detail_attempts += 1
    if not metadata:
        pending.last_detail_error = "missing_or_mismatched_detail_metadata"
        pending.save(update_fields=["detail_attempts", "last_detail_error", "last_seen"])
        return False
    normalized_type = metadata["source_media_type"].strip().casefold().replace(" ", "")
    content_type = {
        "movie": "movie", "tvseries": "series", "tv_series": "series",
        "tv-series": "series", "series": "series",
    }.get(normalized_type)
    pending.detail_enrichment = metadata
    pending.last_detail_error = ""
    if content_type is None:
        pending.classification_reason = f"unresolved provider type: {metadata['source_media_type'] or 'missing'}"
        pending.save(update_fields=["detail_attempts", "detail_enrichment", "last_detail_error", "classification_reason", "last_seen"])
        return False

    raw_payload = {
        "id": pending.native_id,
        "listing_observation": pending.listing_observation,
        "detail_enrichment": metadata,
        "unverified_discovery": True,
    }
    item = ScrapedItem(
        title=metadata["title"] or pending.title,
        original_title=metadata["original_title"] or None,
        year=metadata["release_year"] or pending.release_year,
        content_type=content_type,
        provider_slug="kisskh",
        source_url=metadata["detail_url"] or pending.detail_url,
        raw_payload=raw_payload,
    )
    from tracker.services.scraped_mapping import persist_scraped_item
    persist_scraped_item(provider=pending.provider, item=item)
    pending.status = "RESOLVED"
    pending.resolved_content_type = content_type
    pending.classification_reason = f"explicit detail type: {metadata['source_media_type']}"
    pending.save(update_fields=["status", "resolved_content_type", "detail_attempts", "detail_enrichment", "last_detail_error", "classification_reason", "last_seen"])
    return True


def _same_persisted_observation(mapping, item):
    """Compare against the payload normalized by persist_scraped_item()."""
    expected_payload = dict(item.raw_payload) if isinstance(item.raw_payload, dict) else {}
    expected_payload.setdefault("content_type", item.content_type)
    return (
        mapping.scraped_title == item.title
        and mapping.scraped_year == item.year
        and mapping.scraped_content_type == item.content_type
        and mapping.scraped_url == item.source_url
        and mapping.raw_payload == expected_payload
        and mapping.is_available == item.availability
    )


_SEMANTIC_METADATA_KEYS = {
    "year", "release_year", "original_title", "feed_title", "anime_type",
    "source_media_type", "external_ids", "tmdb_id", "listing_content_type_hint",
}


def _semantic_metadata(metadata):
    if not isinstance(metadata, dict):
        return {}
    return {key: metadata[key] for key in _SEMANTIC_METADATA_KEYS if key in metadata}


@transaction.atomic
def _persist_unclassified_observation(*, provider, item, partition):
    """Persist provider evidence without projecting an inferred media type."""
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    native_id = str(payload.get("guid") or provider_content_id_for_item(item) or "").strip()
    identity_url = normalize_catalogue_url(provider.slug, item.source_url)
    prior = DiscoveredCatalogueRecord.objects.filter(provider=provider, provider_native_id=native_id).first() if native_id else None
    by_url = DiscoveredCatalogueRecord.objects.filter(provider=provider, identity_url=identity_url).first() if identity_url else None
    if prior is None and (by_url is None or not by_url.provider_native_id or not native_id):
        prior = by_url
    metadata = dict(payload)
    metadata["observed_source_url"] = item.source_url
    metadata.update({
        "unverified_discovery": True,
        "catalogue_partition": partition,
        "listing_content_type_hint": item.content_type,
    })
    observation_history = []
    if prior is not None and isinstance(prior.original_metadata, dict):
        observation_history = list(prior.original_metadata.get("listing_observations") or [])
    page_number = payload.get("listing_page")
    if page_number is not None:
        observation = {"partition": partition, "page": int(page_number), "source_url": item.source_url}
        if observation not in observation_history:
            observation_history.append(observation)
    if observation_history:
        metadata["listing_observations"] = observation_history
    provenance = payload.get("provenance_state", "SELF_ASSERTED" if provider.slug == "hydrahd" else "PROVENANCE_UNVERIFIED")
    access = payload.get("access_policy_state", "PERMITTED" if provider.slug == "hydrahd" else "UNKNOWN")
    if prior is None:
        metadata["source_urls_seen"] = [item.source_url]
        DiscoveredCatalogueRecord.objects.create(
            provider=provider,
            provider_native_id=native_id,
            source_url=item.source_url,
            identity_url=identity_url,
            source_urls_seen=[item.source_url],
            provenance_state=provenance,
            access_policy_state=access,
            classification_state="PENDING",
            classified_content_type=None,
            discovery_verification_state="VERIFIED" if provenance == "VERIFIED" else "OBSERVED",
            title=item.title,
            original_metadata=metadata,
        )
        return "inserted"
    seen_urls = list(dict.fromkeys((prior.source_urls_seen or []) + [item.source_url]))
    metadata["source_urls_seen"] = seen_urls
    values = {"title": item.title, "original_metadata": metadata, "identity_url": identity_url,
              "source_urls_seen": seen_urls, "provenance_state": provenance, "access_policy_state": access}
    unchanged = (
        prior.title == item.title
        and prior.identity_url == identity_url
        and prior.original_metadata == metadata
        and prior.source_urls_seen == seen_urls
        and prior.provenance_state == provenance
        and prior.access_policy_state == access
        and (not native_id or prior.provider_native_id == native_id)
    )
    if unchanged:
        DiscoveredCatalogueRecord.objects.filter(pk=prior.pk).update(last_seen_at=django_timezone.now())
        return "unchanged"
    semantic_changed = (
        prior.title != item.title
        or (bool(native_id) and prior.provider_native_id != native_id)
        or _semantic_metadata(prior.original_metadata) != _semantic_metadata(metadata)
    )
    for field, value in values.items():
        setattr(prior, field, value)
    prior.last_seen_at = django_timezone.now()
    update_fields = [*values.keys(), "last_seen_at", "updated_at"]
    if native_id and prior.provider_native_id != native_id:
        prior.provider_native_id = native_id
        update_fields.append("provider_native_id")
    prior.save(update_fields=update_fields)
    return "updated" if semantic_changed else "source_metadata_updated"


def _mode_cursors(state):
    """Return the root cursor and mode cursors, migrating legacy state in memory.

    Before separate modes existed, ``cursor`` itself held the historical walk.
    Preserve that cursor under the mode the provider last used; the caller
    writes the versioned structure only when it commits discovery progress.
    """
    root = deepcopy(state.cursor or {}) if state else {}
    modes = root.get("modes")
    if isinstance(modes, dict):
        return root, modes
    legacy = {key: value for key, value in root.items() if key != "modes"}
    modes = {}
    if legacy:
        prior_mode = getattr(state, "mode", "full")
        mode_key = "incremental" if prior_mode == "incremental" else "historical"
        modes[mode_key] = legacy
    root["modes"] = modes
    return root, modes


def _checkpoint_root(root, modes, mode_key, cursor):
    modes[mode_key] = cursor
    root["modes"] = modes
    # Keep the legacy top-level view aligned with historical progress for
    # existing API clients and operational scripts.
    if mode_key == "historical":
        for key in ("done", "parts", "fingerprints", "navigation_max", "reported_totals", "total_pages"):
            if key in cursor:
                root[key] = deepcopy(cursor[key])
            else:
                root.pop(key, None)
    return root


def _parts(slug):
    if slug == "kisskh": return [v[0] for v in KissKHScraper.BROWSE_TYPES]
    if slug == "hydrahd": return ["movies", "tv-shows"]
    if slug == "animeworld": return ["rss-animes"]
    if slug == "cineblog01": return [v[0] for v in BROWSE_SECTIONS]
    if slug == "streamingcommunity": return [p for p, _ in SC_PATHS]
    return [p for p, _ in AD_PATHS]


def _page(scraper, slug, part, page, query=None):
    """Return (items, terminal, known_pages, evidence). None means failure."""
    if slug == "kisskh":
        path = f"/api/DramaList/List?page={page}&type={part}&sub=0&country=0&status=0&order=1&pageSize={scraper.DISCOVERY_PAGE_SIZE}"
        raw = scraper._fetch_api(path)
        if not isinstance(raw, dict): return None, False, None, "missing_or_unexpected_api_response"
        if raw.get("error") or raw.get("success") is False:
            return None, False, None, "api_error_response"
        rows = raw.get("data")
        if not isinstance(rows, list): return None, False, None, "missing_data_array"
        try:
            response_page = int(raw.get("page"))
            page_size = int(raw.get("pageSize"))
            total = int(raw.get("totalCount"))
        except (TypeError, ValueError):
            return None, False, None, "missing_or_invalid_pagination_metadata"
        if page_size < 1 or total < 0 or response_page < 1 or response_page > page:
            return None, False, None, "inconsistent_pagination_metadata"
        if not rows and page <= (total + page_size - 1) // page_size:
            return None, False, None, "empty_page_before_reported_terminal_page"
        items, pending = scraper.parse_catalogue_rows(rows)
        page_total = (total + page_size - 1) // page_size
        totals = getattr(scraper, "catalogue_totals", {})
        totals[part] = total
        scraper.catalogue_totals = totals
        clamped_to_terminal = (
            response_page == page_total and page == page_total + 1 and page_total > 0
        )
        if response_page != page and not clamped_to_terminal:
            return None, False, page_total, "inconsistent_pagination_metadata"
        terminal = page >= page_total or clamped_to_terminal
        if len(items) + len(pending) != len(rows):
            evidence = "malformed_listing_rows"
            return None, False, page_total, evidence
        return CataloguePageRows(
            items, pending, total_count=total, page=response_page, page_size=page_size
        ), terminal, page_total, "api_clamped_terminal_page" if clamped_to_terminal else "api_total_count"
    if slug == "hydrahd":
        kind = "series" if part == "tv-shows" else "movie"
        domain = "hydrahd.gd"
        last = scraper.fetch_url(f"https://{domain}/{part}/date/{page}", headers=scraper.HEADERS)
        if last is None: return None, False, None, "unavailable_or_blocked"
        items = scraper.parse_html(last, default_content_type=kind, base_url=f"https://{domain}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(last, "html.parser")
        soup_links = []
        for anchor in soup.find_all("a", href=True):
            match = re.search(rf"/{re.escape(part)}/date/(\d+)(?:/|$|[?#])", anchor["href"], re.I)
            if match: soup_links.append(int(match.group(1)))
        navigation_max = max(soup_links) if soup_links else None
        if not items and page == 1:
            return None, False, None, "unexpected_or_empty_listing_html"
        terminal = not items
        evidence = f"pagination_links_navigation_max={navigation_max}" if navigation_max is not None else "empty_page_terminal" if terminal else "sequential_route_unbounded"
        for item in items:
            item.raw_payload = {**(item.raw_payload or {}), "provenance_state": "SELF_ASSERTED", "access_policy_state": "PERMITTED", "catalogue_partition": part, "listing_page": page}
        return items, terminal, None, evidence
    if slug == "animeworld":
        if part != "rss-animes" or page > 1: return None, False, None, "official_rss_is_single_snapshot"
        raw = scraper.fetch_url("https://www.animeworld.ac/rss/animes", headers={"Accept": "application/rss+xml, application/xml"})
        if not isinstance(raw, str): return None, False, None, "unavailable_or_blocked"
        items = scraper.parse_rss(raw)
        return items, False, None, "official_rss_snapshot_not_full_archive"
    if slug == "cineblog01":
        section = next((x for x in BROWSE_SECTIONS if x[0] == part), None)
        if not section: return None, False, None, "unknown_section"
        path = part if page == 1 else f"{part}page/{page}/"
        raw, domain = scraper._try_fetch(path)
        if raw is None: return None, False, None, "unavailable_or_blocked"
        items = __import__("tracker.scrapers.listing", fromlist=["items_from_listing"]).items_from_listing(raw, href_regex=__import__("tracker.scrapers.cineblog01", fromlist=["HREF_PATTERN"]).HREF_PATTERN, provider_slug=slug, base_url=f"https://{domain}", default_content_type=section[1])
        if not items and page == 1:
            return None, False, None, "unexpected_or_empty_archive_html"
        links = [int(v) for v in re.findall(r"/page/(\d+)/", raw, re.I)]
        pages = max(links) if links else None
        terminal = page >= pages if pages is not None else not items
        return items, terminal, pages, "archive_pagination_or_empty_page"
    # The current public paths are one-page observations. Absence of a
    # verified continuation is unknown, never proof that the catalogue is complete.
    if slug in ("streamingcommunity", "altadefinizione"):
        paths = SC_PATHS if slug == "streamingcommunity" else AD_PATHS
        kind = dict(paths).get(part, "movie")
        if page > 1:
            return None, False, None, "pagination_follow_not_verified"
        page_path = part
        if slug == "streamingcommunity": raw, domain = scraper._try_domains(page_path)
        else: raw, domain = scraper._try_fetch(page_path)
        if raw is None: return None, False, None, "unavailable_or_blocked"
        listing = __import__("tracker.scrapers.listing", fromlist=["items_from_listing"]).items_from_listing
        href_re = r"/titles/\d+-" if slug == "streamingcommunity" else __import__("tracker.scrapers.altadefinizione", fromlist=["HREF_PATTERN"]).HREF_PATTERN
        items = listing(raw, href_regex=href_re, provider_slug=slug, base_url=f"https://{domain}", default_content_type=kind)
        links = [int(v) for v in re.findall(r"/page/(\d+)/", raw, re.I)]
        # Do not manufacture a /page/N URL. Until a verified per-page link
        # follower is available, report the observed tile set as partial.
        evidence = "pagination_follow_not_verified" if links else "pagination_not_demonstrated"
        return items, False, max(links) if links else None, evidence
    return None, False, None, "unsupported_provider"


def run_discovery(provider_slug, *, mode="full", request_budget=20, delay=1.0, retry_limit=1, dry_run=False, targeted_query=None, partition=None, overlap_pages=DEFAULT_INCREMENTAL_OVERLAP_PAGES):
    slug = (provider_slug or "").lower()
    if slug not in PROVIDERS: raise ValueError(f"Unsupported provider: {slug}")
    if mode not in DISCOVERY_MODE_KEYS: raise ValueError(f"Unsupported discovery mode: {mode}")
    if request_budget < 1: raise ValueError("request_budget must be positive")
    if retry_limit < 0: raise ValueError("retry_limit cannot be negative")
    if overlap_pages < 1: raise ValueError("overlap_pages must be positive")
    if DISCOVERY_LEASE_SECONDS <= 0: raise ValueError("UNOFFICIAL_CATALOGUE_LEASE_SECONDS must be positive")
    mode_key = DISCOVERY_MODE_KEYS[mode]
    if slug == "animeworld" and mode == "full":
        # Compatibility for older callers: a former `full` RSS invocation is
        # now explicitly an incremental snapshot and remains PARTIAL.
        mode = "incremental"
        mode_key = "incremental"
    if slug == "animeworld" and mode_key == "historical":
        raise ValueError("AnimeWorld exposes a recent RSS snapshot only; use incremental mode")
    if mode_key == "incremental" and slug != "animeworld" and slug != "hydrahd":
        raise ValueError(f"Incremental discovery is not enabled for {slug}")
    lock_name = f"unofficial-catalogue:{slug}"
    if not try_source_lock(lock_name):
        raise RuntimeError(f"A discovery run is already active for {slug}")
    try:
        provider = Provider.objects.get(slug=slug)
        scraper = resolve_scraper_class(slug)()
        if slug in PROVENANCE_BLOCKED:
            report = {
                "status": "BLOCKED", "mode": mode, "pages_scanned": 0,
                "items_seen": 0, "items_unique": 0, "items_inserted": 0,
                "items_pending_classification": 0, "requests_made": 0,
                "last_error": PROVENANCE_BLOCKED[slug],
            }
            if not dry_run:
                state, _ = CatalogueDiscoveryState.objects.get_or_create(provider=provider)
                state.status, state.mode, state.report, state.last_error = "BLOCKED", mode, report, report["last_error"]
                state.save()
            return {**report, "provider": slug, "dry_run": dry_run}
        # Count every dispatch, including domain fallback, and retry only
        # transient network/5xx/429 failures with an explicit bounded limit.
        requests_made = 0
        original_get = scraper.session.get
        def budgeted_get(*args, **kwargs):
            nonlocal requests_made
            if requests_made >= request_budget:
                raise _RequestBudgetReached()
            requests_made += 1
            return original_get(*args, **kwargs)
        scraper.session.get = budgeted_get
        user_agent = "Mozilla/5.0 (compatible; WTDW catalogue discovery)"
        robots_by_origin = {}
        def request_with_retries(url, headers):
            for attempt in range(retry_limit + 1):
                try:
                    response = scraper.session.get(url, timeout=scraper.timeout, headers=headers)
                except requests.RequestException:
                    if attempt >= retry_limit: raise
                    if delay: time.sleep(delay * (2 ** attempt))
                    continue
                status = int(response.status_code)
                if status == 429 or status >= 500:
                    retry_after = response.headers.get("Retry-After") if status == 429 else None
                    if attempt >= retry_limit:
                        raise _HttpFailure(status, retry_after)
                    wait = _retry_after_seconds(retry_after)
                    if wait is not None and wait > MAX_INLINE_RETRY_AFTER_SECONDS:
                        # Defer long cooldowns to the next scheduled tick rather
                        # than holding a worker process asleep for minutes.
                        raise _HttpFailure(status, retry_after)
                    if wait is None: wait = delay * (2 ** attempt)
                    if wait: time.sleep(wait)
                    continue
                return response
            return None

        def fetch_discovery_url(url, headers=None):
            request_headers = {"User-Agent": user_agent}
            if headers: request_headers.update(headers)
            parts = urlsplit(url)
            origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
            if origin not in robots_by_origin:
                robots_url = f"{origin}/robots.txt"
                robots_response = request_with_retries(robots_url, {"User-Agent": user_agent})
                if robots_response is None:
                    raise _HttpFailure(503)
                if int(robots_response.status_code) in (404, 410):
                    robots_by_origin[origin] = None
                elif int(robots_response.status_code) >= 400:
                    raise _HttpFailure(int(robots_response.status_code))
                else:
                    robots = RobotFileParser(robots_url)
                    robots.parse(robots_response.text.splitlines())
                    robots_by_origin[origin] = robots
            robots = robots_by_origin[origin]
            if robots and not robots.can_fetch(user_agent, url):
                raise _HttpFailure(403)
            crawl_delay = robots.crawl_delay(user_agent) if robots else None
            if crawl_delay and delay < crawl_delay:
                time.sleep(crawl_delay - delay)
            response = request_with_retries(url, request_headers)
            if response is None: return None
            if int(response.status_code) >= 400:
                raise _HttpFailure(int(response.status_code), response.headers.get("Retry-After"))
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "/api/dramalist/" in url.lower() and "json" not in content_type:
                return response.text
            try:
                return response.json()
            except ValueError:
                return response.text
        scraper.fetch_url = fetch_discovery_url
        all_parts = _parts(slug)
        if partition is not None and partition not in all_parts:
            raise ValueError(f"Unsupported {slug} partition: {partition}")
        if partition is not None and mode == "targeted":
            raise ValueError("--partition is only available in full or incremental mode")
        parts = [partition] if partition is not None else all_parts
        if dry_run:
            state = CatalogueDiscoveryState.objects.filter(provider=provider).first()
        else:
            state, _ = CatalogueDiscoveryState.objects.get_or_create(provider=provider)
            if state.status == "RUNNING":
                lease_reference = state.heartbeat_at or state.started_at or state.updated_at
                lease_age = django_timezone.now() - lease_reference if lease_reference else None
                if lease_age is not None and lease_age.total_seconds() <= DISCOVERY_LEASE_SECONDS:
                    raise RuntimeError(
                        f"Discovery for {slug} is marked RUNNING with a live lease "
                        f"(owner={state.lease_owner or 'unknown'}, heartbeat={lease_reference.isoformat()}); "
                        "refusing to reclaim it"
                    )
                previous_report = deepcopy(state.report or {})
                state.status = "PARTIAL"
                state.last_error = (
                    f"Recovered stale RUNNING lease after "
                    f"{int(lease_age.total_seconds()) if lease_age else 'unknown'} seconds; "
                    "resuming from last committed checkpoint"
                )
                previous_report["recovery"] = {
                    "recovered_at": django_timezone.now().isoformat(),
                    "previous_lease_owner": state.lease_owner,
                    "previous_heartbeat_at": state.heartbeat_at.isoformat() if state.heartbeat_at else None,
                    "previous_last_error": state.last_error or previous_report.get("last_error", ""),
                    "last_committed_checkpoint": deepcopy(state.cursor or {}),
                }
                previous_report["status"] = "PARTIAL"
                previous_report["last_error"] = state.last_error
                state.report = previous_report
                state.lease_owner = ""
                state.lease_expires_at = None
                state.save(update_fields=["status", "last_error", "report", "lease_owner",
                                          "lease_expires_at", "updated_at"])
        root_cursor, mode_cursors = _mode_cursors(state)
        cursor = deepcopy(mode_cursors.get(mode_key) or {})
        if mode_key == "historical" and state and state.status == "COMPLETED":
            cursor = {}  # an explicit historical run after completion is a fresh reconciliation
        if slug == "hydrahd" and cursor.get("total_pages"):
            # Earlier runs treated the largest visible next-page link as a
            # stable total. Preserve it as navigation evidence, not a total.
            cursor.setdefault("navigation_max", {}).update(cursor.pop("total_pages"))
        if mode_key == "incremental" and slug == "hydrahd" and cursor.get("window_complete"):
            windows_completed = int(cursor.get("windows_completed", 0))
            last_started = cursor.get("last_run_started_at")
            page_fingerprints = cursor.get("page_fingerprints", {})
            page_identities = cursor.get("page_identities", {})
            cursor = {
                "parts": {}, "done": [], "window_complete": False,
                "windows_completed": windows_completed,
                "last_run_started_at": last_started,
                "page_fingerprints": page_fingerprints,
                "page_identities": page_identities,
            }
        baseline_page_identities = deepcopy(cursor.get("page_identities", {}))
        report = {
            "status": "RUNNING", "mode": mode_key, "pages_discovered": 0,
            "pages_scanned": 0, "pages_remaining": None, "items_seen": 0,
            "items_unique": 0, "items_inserted": 0, "items_updated": 0,
            "items_unchanged": 0, "items_source_metadata_updated": 0,
            "duplicates_skipped": 0, "errors": 0,
            "duplicate_identities": 0, "new_unique_titles": 0,
            "failed_pages": [], "navigation_anomalies": [],
            "page_gaps": [], "terminal_page_evidence": [],
            "pagination_shifts": [], "items_shifted_between_pages": 0,
            "pages_by_partition": {}, "page_ranges": {},
            "items_pending_classification": 0, "pending_inserted": 0,
            "pending_updated": 0, "pending_unchanged": 0,
            "pagination_total_changes": [],
            "duplicate_native_ids_across_partitions": 0,
            "known_site_total": None, "coverage_percent": "UNKNOWN",
            "mapped_titles": "UNKNOWN", "confirmed_offers": "UNKNOWN",
            "last_successful_checkpoint": None, "last_success_at": None,
            "last_error": "",
        }
        if mode_key == "incremental" and slug == "hydrahd":
            report["overlap_pages"] = overlap_pages
        known_pages_by_part = {}
        started = django_timezone.now()
        if not dry_run:
            lease_owner = uuid.uuid4().hex
            cursor["last_run_started_at"] = started.isoformat()
            root_cursor = _checkpoint_root(root_cursor, mode_cursors, mode_key, cursor)
            prior_report = deepcopy(state.report or {})
            previous_mode_reports = deepcopy(prior_report.get("mode_reports", {}))
            if prior_report.get("recovery"):
                report["recovery"] = prior_report["recovery"]
            state.status, state.mode, state.started_at = "RUNNING", ("full" if mode_key == "historical" else mode_key), started
            state.heartbeat_at = started
            state.lease_expires_at = started + timedelta(seconds=DISCOVERY_LEASE_SECONDS)
            state.lease_owner = lease_owner
            state.last_error = ""
            state.report = {**report, "mode_reports": previous_mode_reports}
            state.cursor = root_cursor
            state.save()
        completed_parts = set(cursor.get("done", []))
        seen_native_partitions = {}
        active_page = None

        def save_cursor_checkpoint():
            nonlocal root_cursor
            root_cursor = _checkpoint_root(root_cursor, mode_cursors, mode_key, cursor)
            mode_reports = deepcopy((state.report or {}).get("mode_reports", {}))
            mode_reports[mode_key] = deepcopy(report)
            state.cursor = root_cursor
            state.report = {**report, "mode_reports": mode_reports}
            state.last_successful_checkpoint = django_timezone.now()
            state.heartbeat_at = django_timezone.now()
            state.lease_expires_at = state.heartbeat_at + timedelta(seconds=DISCOVERY_LEASE_SECONDS)
            state.lease_owner = lease_owner
            state.save(update_fields=["cursor", "report", "last_successful_checkpoint", "heartbeat_at",
                                      "lease_expires_at", "lease_owner", "updated_at"])

        try:
            if mode == "targeted":
                # Existing provider search adapters are real search routes for
                # KissKH, HydraHD and AnimeWorld. Others currently offer only
                # partial catalogue filtering, which remains explicitly partial.
                try:
                    rows = scraper.run(query=targeted_query)
                except _RequestBudgetReached:
                    rows = []
                    report["last_error"] = "request budget exhausted during targeted search"
                report["items_seen"] = len(rows)
                for item in rows:
                    report["items_unique"] += 1
                    item.raw_payload = {**(item.raw_payload or {}), "unverified_discovery": True}
                    if not dry_run:
                        if slug != "kisskh":
                            outcome = _persist_unclassified_observation(
                                provider=provider, item=item, partition="targeted_search"
                            )
                            report[f"items_{outcome}"] += 1
                            report["items_pending_classification"] += 1
                            continue
                        from tracker.models import ScrapedContentMapping
                        native_id = provider_content_id_for_item(item)
                        prior = ScrapedContentMapping.objects.filter(
                            provider=provider, provider_content_id=native_id,
                            scraped_content_type=item.content_type,
                        ).first() if native_id else None
                        if prior is None:
                            prior = ScrapedContentMapping.objects.filter(
                                provider=provider, provider_content_id="",
                                scraped_title=item.title, scraped_year=item.year,
                                scraped_content_type=item.content_type,
                            ).first()
                        persist_scraped_item(provider=provider, item=item)
                        if prior is None: report["items_inserted"] += 1
                        elif _same_persisted_observation(prior, item): report["items_unchanged"] += 1
                        else: report["items_updated"] += 1
                report["pages_scanned"] = 1 if rows else 0
                report["status"] = "PARTIAL"  # provider search is never a completeness traversal
                return {**report, "provider": slug, "requests_made": requests_made, "dry_run": dry_run, "query": targeted_query}
            for part in parts:
                if part in completed_parts: continue
                page = 1 if slug == "animeworld" else int((cursor.get("parts") or {}).get(part, 0)) + 1
                while requests_made < request_budget:
                    active_page = (part, page)
                    try:
                        rows, terminal, total_pages, evidence = _page(scraper, slug, part, page, targeted_query)
                    except _RequestBudgetReached:
                        report["status"] = "PARTIAL"
                        report["last_error"] = "request budget exhausted before the next HTTP dispatch"
                        break
                    except _HttpFailure as exc:
                        if slug == "hydrahd" and exc.status == 404 and page > 1:
                            report["errors"] += 1
                            report["status"] = "PARTIAL"
                            report["last_error"] = f"{part}: HTTP 404 is not verified as a terminal page"
                            report["failed_pages"].append({"partition": part, "page": page, "status": exc.status})
                            break
                        if exc.status == 404 and page > 1:
                            rows, terminal = [], True
                            total_pages = None if slug == "hydrahd" else page - 1
                            evidence = "http_404_terminal_page"
                        else:
                            report["errors"] += 1
                            report["status"] = "BLOCKED" if exc.status in (403, 429) else "FAILED"
                            report["last_error"] = f"HTTP {exc.status}"
                            report["failed_pages"].append({"partition": part, "page": page, "status": exc.status})
                            if exc.retry_after is not None:
                                report["retry_after"] = exc.retry_after
                                retry_seconds = _retry_after_seconds(exc.retry_after)
                                if retry_seconds is not None:
                                    report["cooldown_until"] = (django_timezone.now() + timedelta(seconds=retry_seconds)).isoformat()
                            raise RuntimeError(report["last_error"]) from exc
                    if rows is None:
                        report["errors"] += 1
                        report["status"] = "BLOCKED"
                        report["last_error"] = evidence
                        report["failed_pages"].append({"partition": part, "page": page, "evidence": evidence})
                        raise RuntimeError(evidence)
                    pending_rows = list(getattr(rows, "pending", []))
                    response_total = getattr(rows, "total_count", None)
                    stored_totals = cursor.setdefault("reported_totals", {})
                    old_total = stored_totals.get(part)
                    total_changed = response_total is not None and old_total is not None and int(old_total) != int(response_total)
                    if response_total is not None:
                        stored_totals[part] = int(response_total)
                    if total_changed:
                        report["pagination_total_changes"].append({
                            "partition": part, "previous": int(old_total), "current": int(response_total),
                        })
                    observed_count = len(rows) + len(pending_rows)
                    report["pages_scanned"] += 1
                    report["items_seen"] += observed_count
                    visited_pages = report["pages_by_partition"].setdefault(part, [])
                    if visited_pages and page != visited_pages[-1] + 1:
                        report["page_gaps"].append({"partition": part, "after": visited_pages[-1], "before": page})
                    visited_pages.append(page)
                    prior_range = report["page_ranges"].get(part)
                    report["page_ranges"][part] = [page, page] if prior_range is None else [min(prior_range[0], page), max(prior_range[1], page)]
                    if total_pages is not None:
                        known_pages_by_part[part] = total_pages
                        cursor.setdefault("total_pages", {})[part] = total_pages
                    if slug == "hydrahd":
                        nav_match = re.search(r"navigation_max=(\d+)", evidence)
                        if nav_match:
                            navigation_max = int(nav_match.group(1))
                            report.setdefault("navigation_max_observed", {})[part] = navigation_max
                            if navigation_max < page + 1:
                                report["navigation_anomalies"].append({"partition": part, "page": page, "navigation_max": navigation_max, "evidence": "no_forward_page_link"})
                            if observed_count:
                                cursor.setdefault("navigation_max", {})[part] = navigation_max
                        if observed_count == 0:
                            report["status"] = "PARTIAL"
                            report["pages_discovered"] = max(report["pages_discovered"], page)
                            report["last_error"] = f"{part}: empty listing is not verified as a terminal page"
                            report["terminal_page_candidate"] = {
                                "partition": part,
                                "page": page,
                                "evidence": evidence,
                            }
                            break
                    report["pages_discovered"] = max(report["pages_discovered"], total_pages or page)
                    fingerprint_ids = [
                        str((item.raw_payload or {}).get("id") or (item.raw_payload or {}).get("title_id") or item.source_url)
                        for item in rows
                    ] + [str(row.get("id") or "") for row in pending_rows]
                    fingerprint = hashlib.sha256(json.dumps(
                        fingerprint_ids,
                        separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    if mode_key == "incremental" and slug == "hydrahd":
                        page_key = str(page)
                        page_fingerprints = cursor.setdefault("page_fingerprints", {}).setdefault(part, {})
                        prior_page_fingerprint = page_fingerprints.get(page_key)
                        identity_values = [
                            normalize_catalogue_url(slug, item.source_url) or str((item.raw_payload or {}).get("id") or item.title)
                            for item in rows
                        ]
                        prior_page_identities = cursor.setdefault("page_identities", {}).setdefault(part, {})
                        for other_page, other_ids in baseline_page_identities.get(part, {}).items():
                            if other_page == page_key:
                                continue
                            shifted = set(identity_values) & set(other_ids)
                            if shifted:
                                report["items_shifted_between_pages"] += len(shifted)
                                if len(report.setdefault("pagination_shifts", [])) < 50:
                                    report["pagination_shifts"].append({"partition": part, "from_page": int(other_page), "to_page": page, "items": len(shifted)})
                        if prior_page_fingerprint and prior_page_fingerprint != fingerprint:
                            report["pagination_shifts"].append({"partition": part, "page": page, "evidence": "same_page_fingerprint_changed"})
                        page_fingerprints[page_key] = fingerprint
                        prior_page_identities[page_key] = identity_values
                    previous_fingerprint = (cursor.get("fingerprints") or {}).get(part)
                    if observed_count and not terminal and previous_fingerprint == fingerprint:
                        report["duplicates_skipped"] += observed_count
                        report["status"] = "PARTIAL"
                        report["last_error"] = f"{part}: repeated non-terminal page detected"
                        break
                    totals = getattr(scraper, "catalogue_totals", {})
                    if all(value in totals for value in parts):
                        report["known_site_total"] = sum(totals[value] for value in parts)
                    page_seen = set()
                    for observed in pending_rows:
                        native_id = str(observed.get("id") or "").strip()
                        identity = (native_id, "pending", part)
                        if identity in page_seen:
                            report["duplicates_skipped"] += 1
                            continue
                        page_seen.add(identity)
                        prior_partitions = seen_native_partitions.setdefault(native_id, set())
                        if not prior_partitions:
                            report["items_unique"] += 1
                        elif part not in prior_partitions:
                            report["duplicate_native_ids_across_partitions"] += 1
                        prior_partitions.add(part)
                        report["items_pending_classification"] += 1
                        if dry_run:
                            continue
                        metadata = scraper.extract_detail_metadata(observed, native_id=native_id)
                        pending, created = KissKHPendingClassification.objects.get_or_create(
                            provider=provider,
                            native_id=native_id,
                            partition=part,
                            defaults={
                                "title": metadata["title"] or "",
                                "release_year": metadata["release_year"],
                                "detail_url": metadata["detail_url"],
                                "listing_observation": observed,
                            },
                        )
                        if created:
                            report["items_inserted"] += 1
                            report["pending_inserted"] += 1
                        else:
                            unchanged = (
                                pending.title == (metadata["title"] or "")
                                and pending.release_year == metadata["release_year"]
                                and pending.detail_url == metadata["detail_url"]
                                and pending.listing_observation == observed
                            )
                            if unchanged:
                                report["items_unchanged"] += 1
                                report["pending_unchanged"] += 1
                                pending.save(update_fields=["last_seen"])
                            else:
                                pending.title = metadata["title"] or pending.title
                                pending.release_year = metadata["release_year"] or pending.release_year
                                pending.detail_url = metadata["detail_url"] or pending.detail_url
                                pending.listing_observation = observed
                                pending.save(update_fields=["title", "release_year", "detail_url", "listing_observation", "last_seen"])
                                report["items_updated"] += 1
                                report["pending_updated"] += 1
                    for item in rows:
                        native_id = provider_content_id_for_item(item)
                        identity = (native_id, item.content_type) if native_id else (item.source_url or item.title, item.content_type)
                        if identity in page_seen:
                            report["duplicates_skipped"] += 1
                            continue
                        page_seen.add(identity)
                        from tracker.models import ScrapedContentMapping
                        repeated_url = (
                            not native_id
                            and ScrapedContentMapping.objects.filter(provider=provider, scraped_url=item.source_url)
                            .exclude(scraped_title=item.title, scraped_year=item.year, scraped_content_type=item.content_type)
                            .exists()
                            if item.source_url else False
                        )
                        if repeated_url:
                            report["duplicates_skipped"] += 1
                            continue
                        if native_id:
                            prior_partitions = seen_native_partitions.setdefault(native_id, set())
                            if not prior_partitions:
                                report["items_unique"] += 1
                            elif part not in prior_partitions:
                                report["duplicate_native_ids_across_partitions"] += 1
                            prior_partitions.add(part)
                        else:
                            report["items_unique"] += 1
                        item.raw_payload = {**(item.raw_payload or {}), "unverified_discovery": True}
                        if slug == "kisskh":
                            item.raw_payload["catalogue_partition"] = part
                        if not dry_run:
                            if slug != "kisskh":
                                outcome = _persist_unclassified_observation(
                                    provider=provider, item=item, partition=part
                                )
                                if outcome == "source_metadata_updated":
                                    report["items_source_metadata_updated"] += 1
                                    report["items_unchanged"] += 1
                                else:
                                    report[f"items_{outcome}"] += 1
                                if outcome != "inserted":
                                    report["duplicate_identities"] += 1
                                report["items_pending_classification"] += 1
                                continue
                            prior = None
                            if native_id:
                                prior = ScrapedContentMapping.objects.filter(
                                    provider=provider,
                                    provider_content_id=native_id,
                                    scraped_content_type=item.content_type,
                                ).first()
                            if prior is None:
                                prior = ScrapedContentMapping.objects.filter(
                                    provider=provider,
                                    provider_content_id="",
                                    scraped_title=item.title,
                                    scraped_year=item.year,
                                    scraped_content_type=item.content_type,
                                ).first()
                            persist_scraped_item(provider=provider, item=item)
                            if slug == "kisskh" and native_id:
                                pending_type = item.content_type
                                KissKHPendingClassification.objects.filter(
                                    provider=provider, native_id=native_id, status="PENDING"
                                ).update(
                                    status="RESOLVED",
                                    resolved_content_type=pending_type,
                                    classification_reason="explicit listing type",
                                )
                            if prior is None: report["items_inserted"] += 1
                            elif _same_persisted_observation(prior, item): report["items_unchanged"] += 1
                            else: report["items_updated"] += 1
                    pagination_is_partial = evidence in {"pagination_not_demonstrated", "pagination_follow_not_verified", "official_rss_snapshot_not_full_archive"} or total_changed
                    if slug == "animeworld" and evidence == "official_rss_snapshot_not_full_archive" and not dry_run:
                        cursor.setdefault("parts", {})[part] = 1
                        cursor.setdefault("snapshots", {})[part] = {
                            "feed_last_build_date": getattr(scraper, "feed_last_build_date", None),
                            "records_observed": observed_count,
                        }
                        cursor.setdefault("snapshots", {})[part]["last_synced_at"] = django_timezone.now().isoformat()
                        save_cursor_checkpoint()
                    if not dry_run and not pagination_is_partial:
                        cursor.setdefault("parts", {})[part] = page
                        cursor.setdefault("fingerprints", {})[part] = fingerprint
                        if terminal:
                            report["terminal_page_evidence"].append({"partition": part, "page": page, "evidence": evidence})
                        if terminal and mode_key == "historical": completed_parts.add(part)
                        if mode_key == "incremental" and slug == "hydrahd" and page >= overlap_pages:
                            completed_parts.add(part)
                        cursor["done"] = sorted(completed_parts)
                        save_cursor_checkpoint()
                    if pagination_is_partial:
                        report["status"] = "PARTIAL"
                        if total_changed:
                            report["last_error"] = f"{part}: pagination_total_changed_restart_partition"
                            if not dry_run:
                                # The index may have shifted. Preserve inserted
                                # records, but restart this partition next time.
                                cursor.setdefault("parts", {})[part] = 0
                                cursor.setdefault("fingerprints", {}).pop(part, None)
                                cursor["done"] = sorted(completed_parts - {part})
                                save_cursor_checkpoint()
                        else:
                            report["last_error"] = f"{part}: {evidence}"
                        break
                    if slug == "animeworld":
                        report["status"] = "PARTIAL"
                        report["last_error"] = "official RSS is a recent-updates snapshot, not a full archive"
                        break
                    if terminal:
                        break
                    if mode_key == "incremental" and slug == "hydrahd" and page >= overlap_pages:
                        break
                    page += 1
                    if delay: time.sleep(delay)
                if requests_made >= request_budget and part not in completed_parts:
                    report["status"] = "PARTIAL"
                    break
            unresolved_count = KissKHPendingClassification.objects.filter(
                provider=provider, status="PENDING"
            ).count() if slug == "kisskh" else 0
            report["unresolved_classifications"] = unresolved_count
            if mode_key == "historical" and all(part in completed_parts for part in all_parts) and not unresolved_count:
                report["status"] = "COMPLETED"
            elif mode_key == "historical" and all(part in completed_parts for part in all_parts):
                report["status"] = "PARTIAL"
                report["last_error"] = "unresolved_title_classifications_remain"
            elif report["status"] == "RUNNING":
                report["status"] = "PARTIAL"
            if mode_key == "incremental" and slug == "hydrahd":
                cursor["window_complete"] = all(part in completed_parts for part in all_parts)
                report["window_complete"] = cursor["window_complete"]
                if cursor["window_complete"]:
                    cursor["windows_completed"] = int(cursor.get("windows_completed", 0)) + 1
                    cursor["last_window_completed_at"] = django_timezone.now().isoformat()
                    save_cursor_checkpoint()
                report["status"] = "PARTIAL"  # a bounded overlap is not historical completeness
            known_page_counts = {**(cursor.get("total_pages") or {}), **known_pages_by_part}
            if all(part in known_page_counts for part in all_parts):
                report["pages_remaining"] = sum(
                    max(0, int(known_page_counts[part]) - int((cursor.get("parts") or {}).get(part, 0)))
                    for part in all_parts if part not in completed_parts
                )
            report["new_unique_titles"] = report["items_inserted"]
            if report["pages_scanned"] and report["errors"] == 0:
                report["last_success_at"] = django_timezone.now().isoformat()
            report["last_successful_checkpoint"] = (
                state.last_successful_checkpoint.isoformat()
                if state and state.last_successful_checkpoint else None
            )
        except KeyboardInterrupt:
            report["status"] = "PARTIAL"
            report["last_error"] = "Stopped by operator; committed page checkpoint retained"
        except Exception as exc:
            if report["status"] != "BLOCKED": report["status"] = "FAILED"
            if not report.get("last_error"):
                report["last_error"] = f"Catalogue page failed ({type(exc).__name__}); checkpoint retained"
            if active_page and not any(x.get("partition") == active_page[0] and x.get("page") == active_page[1] for x in report["failed_pages"]):
                report["failed_pages"].append({"partition": active_page[0], "page": active_page[1], "error": report["last_error"]})
            report["requests_dispatched"] = requests_made
            raise CatalogueDiscoveryError({
                **report,
                "provider": slug,
                "requests_made": requests_made,
                "dry_run": dry_run,
                "checkpoint": cursor,
            }) from exc
        finally:
            if not dry_run:
                state.status = report["status"]
                mode_reports = deepcopy((state.report or {}).get("mode_reports", {}))
                report["new_unique_titles"] = report["items_inserted"]
                mode_reports[mode_key] = deepcopy(report)
                state.report = {**report, "mode_reports": mode_reports}
                state.last_error = report.get("last_error", "")
                if report.get("last_success_at"):
                    state.last_success_at = django_timezone.now()
                if report.get("cooldown_until"):
                    cursor["cooldown_until"] = report["cooldown_until"]
                elif report.get("pages_scanned"):
                    cursor.pop("cooldown_until", None)
                root_cursor = _checkpoint_root(root_cursor, mode_cursors, mode_key, cursor)
                state.cursor = root_cursor
                state.heartbeat_at = django_timezone.now()
                state.lease_owner = ""
                state.lease_expires_at = None
                state.save(update_fields=["status", "report", "last_error", "cursor", "last_success_at",
                                          "heartbeat_at", "lease_owner", "lease_expires_at", "updated_at"])
        report["new_unique_titles"] = report["items_inserted"]
        return {**report, "provider": slug, "requests_made": requests_made, "dry_run": dry_run, "checkpoint": cursor, "checkpoints_by_mode": deepcopy(mode_cursors), "partition": partition}
    finally:
        try:
            scraper.session.get = original_get
        except (UnboundLocalError, AttributeError):
            pass
        release_source_lock(lock_name)


def discovery_status():
    counts = {}
    for item in DiscoveredCatalogueRecord.objects.values("provider_id", "classification_state").annotate(total=Count("id")):
        counts.setdefault(item["provider_id"], {})[item["classification_state"]] = item["total"]
    result = []
    for row in CatalogueDiscoveryState.objects.select_related("provider").order_by("provider__slug"):
        cursor = row.cursor or {}
        modes = cursor.get("modes", {}) if isinstance(cursor, dict) else {}
        provider_counts = counts.get(row.provider_id, {})
        current = row.report or {}
        result.append({
            "provider": row.provider.slug,
            "status": row.status,
            "heartbeat_at": row.heartbeat_at,
            "lease_expires_at": row.lease_expires_at,
            "mode": current.get("mode") or row.mode,
            "inventory_count": sum(provider_counts.values()),
            "pending_classification_count": provider_counts.get("PENDING", 0),
            "last_success_at": row.last_success_at,
            "historical_checkpoint": (modes.get("historical") or {}).get("parts", cursor.get("parts", {})),
            "incremental_checkpoint": (modes.get("incremental") or {}).get("parts", {}),
            "mode_reports": current.get("mode_reports", {}),
            **{key: value for key, value in current.items() if key != "mode_reports"},
        })
    return result


def _retry_after_seconds(value):
    if not value: return None
    try: return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None: target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
