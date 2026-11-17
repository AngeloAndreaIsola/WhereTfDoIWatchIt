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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from django.utils import timezone as django_timezone

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
    )
    if unchanged:
        return "unchanged"
    for field, value in values.items():
        setattr(prior, field, value)
    update_fields = [*values.keys(), "updated_at"]
    if native_id and prior.provider_native_id != native_id:
        prior.provider_native_id = native_id
        update_fields.append("provider_native_id")
    prior.save(update_fields=update_fields)
    return "updated"


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
        pages = max([int(v) for v in soup_links]) if soup_links else None
        if not items and page == 1:
            return None, False, None, "unexpected_or_empty_listing_html"
        terminal = page >= pages if pages is not None else not items
        evidence = "partition_pagination_links" if pages is not None else "empty_page_terminal" if terminal else "sequential_route_unbounded"
        for item in items:
            item.raw_payload = {**(item.raw_payload or {}), "provenance_state": "SELF_ASSERTED", "access_policy_state": "PERMITTED", "catalogue_partition": part, "listing_page": page}
        return items, terminal, pages, evidence
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


def run_discovery(provider_slug, *, mode="full", request_budget=20, delay=1.0, retry_limit=1, dry_run=False, targeted_query=None):
    slug = (provider_slug or "").lower()
    if slug not in PROVIDERS: raise ValueError(f"Unsupported provider: {slug}")
    if request_budget < 1: raise ValueError("request_budget must be positive")
    if retry_limit < 0: raise ValueError("retry_limit cannot be negative")
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
        parts = _parts(slug)
        if dry_run:
            state = CatalogueDiscoveryState.objects.filter(provider=provider).first()
        else:
            state, _ = CatalogueDiscoveryState.objects.get_or_create(provider=provider)
        cursor = dict(state.cursor or {}) if mode == "full" and state else {}
        if mode == "full" and state and state.status == "COMPLETED":
            cursor = {}  # an explicit full run after completion is a fresh reconciliation
        report = {
            "status": "RUNNING", "mode": mode, "pages_discovered": 0,
            "pages_scanned": 0, "pages_remaining": None, "items_seen": 0,
            "items_unique": 0, "items_inserted": 0, "items_updated": 0,
            "items_unchanged": 0, "duplicates_skipped": 0, "errors": 0,
            "items_pending_classification": 0, "pending_inserted": 0,
            "pending_updated": 0, "pending_unchanged": 0,
            "pagination_total_changes": [],
            "duplicate_native_ids_across_partitions": 0,
            "known_site_total": None, "coverage_percent": "UNKNOWN",
            "mapped_titles": "UNKNOWN", "confirmed_offers": "UNKNOWN",
            "last_successful_checkpoint": None, "last_success_at": None,
            "last_error": "",
        }
        known_pages_by_part = {}
        started = django_timezone.now()
        if not dry_run:
            state.status, state.mode, state.started_at = "RUNNING", mode, started
            state.last_error = ""
            state.report = report
            state.save()
        completed_parts = set(cursor.get("done", []))
        seen_native_partitions = {}
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
                    try:
                        rows, terminal, total_pages, evidence = _page(scraper, slug, part, page, targeted_query)
                    except _RequestBudgetReached:
                        report["status"] = "PARTIAL"
                        report["last_error"] = "request budget exhausted before the next HTTP dispatch"
                        break
                    except _HttpFailure as exc:
                        if exc.status == 404 and page > 1:
                            rows, terminal, total_pages, evidence = [], True, page - 1, "http_404_terminal_page"
                        else:
                            report["errors"] += 1
                            report["status"] = "BLOCKED" if exc.status in (403, 429) else "FAILED"
                            report["last_error"] = f"HTTP {exc.status}"
                            raise RuntimeError(report["last_error"]) from exc
                    if rows is None:
                        report["errors"] += 1
                        report["status"] = "BLOCKED"
                        report["last_error"] = evidence
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
                    if total_pages is not None:
                        known_pages_by_part[part] = total_pages
                        cursor.setdefault("total_pages", {})[part] = total_pages
                    report["pages_discovered"] = max(report["pages_discovered"], total_pages or page)
                    fingerprint_ids = [
                        str((item.raw_payload or {}).get("id") or (item.raw_payload or {}).get("title_id") or item.source_url)
                        for item in rows
                    ] + [str(row.get("id") or "") for row in pending_rows]
                    fingerprint = hashlib.sha256(json.dumps(
                        fingerprint_ids,
                        separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
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
                                report[f"items_{outcome}"] += 1
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
                        state.cursor, state.report = cursor, report
                        state.last_successful_checkpoint = django_timezone.now()
                        state.save(update_fields=["cursor", "report", "last_successful_checkpoint", "updated_at"])
                    if not dry_run and not pagination_is_partial:
                        cursor.setdefault("parts", {})[part] = page
                        cursor.setdefault("fingerprints", {})[part] = fingerprint
                        if terminal: completed_parts.add(part)
                        cursor["done"] = sorted(completed_parts)
                        state.cursor, state.report = cursor, report
                        state.last_successful_checkpoint = django_timezone.now()
                        state.save(update_fields=["cursor", "report", "last_successful_checkpoint", "updated_at"])
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
                                state.cursor, state.report = cursor, report
                                state.last_successful_checkpoint = django_timezone.now()
                                state.save(update_fields=["cursor", "report", "last_successful_checkpoint", "updated_at"])
                        else:
                            report["last_error"] = f"{part}: {evidence}"
                        break
                    if slug == "animeworld":
                        report["status"] = "PARTIAL"
                        report["last_error"] = "official RSS is a recent-updates snapshot, not a full archive"
                        break
                    if terminal:
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
            if len(completed_parts) == len(parts) and not unresolved_count:
                report["status"] = "COMPLETED"
            elif len(completed_parts) == len(parts):
                report["status"] = "PARTIAL"
                report["last_error"] = "unresolved_title_classifications_remain"
            elif report["status"] == "RUNNING":
                report["status"] = "PARTIAL"
            known_page_counts = {**(cursor.get("total_pages") or {}), **known_pages_by_part}
            if all(part in known_page_counts for part in parts):
                report["pages_remaining"] = sum(
                    max(0, int(known_page_counts[part]) - int((cursor.get("parts") or {}).get(part, 0)))
                    for part in parts if part not in completed_parts
                )
            if report["status"] == "COMPLETED":
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
                state.report = report
                state.last_error = report.get("last_error", "")
                if report["status"] == "COMPLETED": state.last_success_at = django_timezone.now()
                state.save()
        return {**report, "provider": slug, "requests_made": requests_made, "dry_run": dry_run, "checkpoint": cursor}
    finally:
        try:
            scraper.session.get = original_get
        except (UnboundLocalError, AttributeError):
            pass
        release_source_lock(lock_name)


def discovery_status():
    return [
        {"provider": row.provider.slug, "status": row.status, "mode": row.mode, **(row.report or {})}
        for row in CatalogueDiscoveryState.objects.select_related("provider").order_by("provider__slug")
    ]


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
