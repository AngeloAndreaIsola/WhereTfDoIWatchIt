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

from tracker.models import CatalogueDiscoveryState, Provider
from tracker.scrapers import resolve_scraper_class
from tracker.scrapers.adapters import KissKHScraper
from tracker.scrapers.cineblog01 import BROWSE_SECTIONS
from tracker.scrapers.streamingcommunity import BROWSE_PATHS as SC_PATHS
from tracker.scrapers.altadefinizione import BROWSE_PATHS as AD_PATHS
from tracker.services.scraped_mapping import persist_scraped_item, provider_content_id_for_item
from tracker.services.bulk_refresh import try_source_lock, release_source_lock


PROVIDERS = {"animeworld", "kisskh", "hydrahd", "cineblog01", "streamingcommunity", "altadefinizione"}


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


def _parts(slug):
    if slug == "kisskh": return [v[0] for v in KissKHScraper.BROWSE_TYPES]
    if slug == "hydrahd": return ["movies", "tv-shows"]
    if slug == "animeworld": return ["az-list"]
    if slug == "cineblog01": return [v[0] for v in BROWSE_SECTIONS]
    if slug == "streamingcommunity": return [p for p, _ in SC_PATHS]
    return [p for p, _ in AD_PATHS]


def _page(scraper, slug, part, page, query=None):
    """Return (items, terminal, known_pages, evidence). None means failure."""
    if slug == "kisskh":
        kind = dict((k, t) for k, t in KissKHScraper.BROWSE_TYPES)[part]
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
        if response_page != page or page_size < 1 or total < 0:
            return None, False, None, "inconsistent_pagination_metadata"
        if not rows and page <= (total + page_size - 1) // page_size:
            return None, False, None, "empty_page_before_reported_terminal_page"
        items = scraper.parse_items(rows, content_type=kind)
        page_total = (total + page_size - 1) // page_size
        totals = getattr(scraper, "catalogue_totals", {})
        totals[part] = total
        scraper.catalogue_totals = totals
        terminal = page >= page_total
        if len(items) != len(rows):
            evidence = "unclassified_or_malformed_anime_rows" if part == "3" else "malformed_listing_rows"
            return None, False, page_total, evidence
        return items, terminal, page_total, "api_total_count"
    if slug == "hydrahd":
        kind = "series" if part == "tv-shows" else "movie"
        last = None
        for domain in scraper.DOMAINS:
            raw = scraper.fetch_url(f"https://{domain}/{part}/date/{page}", headers=scraper.HEADERS)
            if isinstance(raw, str):
                last = raw
                break
        if last is None: return None, False, None, "unavailable_or_blocked"
        items = scraper.parse_html(last, default_content_type=kind, base_url=f"https://{domain}")
        soup_links = re.findall(r'(?:[?&]page=|/page/)(\d+)', last, re.I)
        pages = max([int(v) for v in soup_links]) if soup_links else None
        if not items and page == 1:
            return None, False, None, "unexpected_or_empty_listing_html"
        terminal = page >= pages if pages is not None else not items
        return items, terminal, pages, "pagination_links_or_empty_page"
    if slug == "animeworld":
        raw = scraper.fetch_url(f"https://www.animeworld.ac/az-list?page={page}", headers={"User-Agent": "Mozilla/5.0"})
        if not isinstance(raw, str): return None, False, None, "unavailable_or_blocked"
        items = scraper.parse_html(raw, category=None)
        if not items and page == 1:
            return None, False, None, "unexpected_or_empty_archive_html"
        page_links = [int(v) for v in re.findall(r"/az-list\?page=(\d+)", raw, re.I)]
        pages = max(page_links) if page_links else None
        # The A-Z archive owns type identity; do not infer every entry as a series.
        for item in items:
            if item.content_type not in ("movie", "series"):
                item.content_type = "movie"
            item.raw_payload.update({"unverified_discovery": True, "provenance": "animeworld_az_list"})
        terminal = page >= pages if pages is not None else not items
        return items, terminal, pages, "archive_pagination_or_empty_page"
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
            "known_site_total": None, "coverage_percent": "UNKNOWN",
            "mapped_titles": "UNKNOWN", "confirmed_offers": "UNKNOWN",
            "last_successful_checkpoint": None, "last_success_at": None,
            "last_error": "",
        }
        known_pages_by_part = {}
        scanned_by_part = {}
        started = django_timezone.now()
        if not dry_run:
            state.status, state.mode, state.started_at = "RUNNING", mode, started
            state.last_error = ""
            state.report = report
            state.save()
        completed_parts = set(cursor.get("done", []))
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
                page = int((cursor.get("parts") or {}).get(part, 0)) + 1
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
                    fingerprint = hashlib.sha256(json.dumps(
                        [str((item.raw_payload or {}).get("id") or (item.raw_payload or {}).get("title_id") or item.source_url) for item in rows],
                        separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    previous_fingerprint = (cursor.get("fingerprints") or {}).get(part)
                    if rows and not terminal and previous_fingerprint == fingerprint:
                        report["status"] = "PARTIAL"
                        report["last_error"] = f"{part}: repeated non-terminal page detected"
                        break
                    report["pages_scanned"] += 1
                    scanned_by_part[part] = scanned_by_part.get(part, 0) + 1
                    if total_pages is not None:
                        known_pages_by_part[part] = total_pages
                    totals = getattr(scraper, "catalogue_totals", {})
                    if all(value in totals for value in parts):
                        report["known_site_total"] = sum(totals[value] for value in parts)
                    report["pages_discovered"] = max(report["pages_discovered"], total_pages or page)
                    report["items_seen"] += len(rows)
                    page_seen = set()
                    for item in rows:
                        identity = provider_content_id_for_item(item) or item.source_url or (item.title, item.year, item.content_type)
                        identity = str(identity)
                        if identity in page_seen:
                            report["duplicates_skipped"] += 1
                            continue
                        page_seen.add(identity)
                        from tracker.models import ScrapedContentMapping
                        native_id = provider_content_id_for_item(item)
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
                        report["items_unique"] += 1
                        item.raw_payload = {**(item.raw_payload or {}), "unverified_discovery": True}
                        if not dry_run:
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
                            if prior is None: report["items_inserted"] += 1
                            elif _same_persisted_observation(prior, item): report["items_unchanged"] += 1
                            else: report["items_updated"] += 1
                    pagination_is_partial = evidence in {"pagination_not_demonstrated", "pagination_follow_not_verified"}
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
                        report["last_error"] = f"{part}: {evidence}"
                        break
                    if terminal:
                        break
                    page += 1
                    if delay: time.sleep(delay)
                if requests_made >= request_budget and part not in completed_parts:
                    report["status"] = "PARTIAL"
                    break
            if len(completed_parts) == len(parts):
                report["status"] = "COMPLETED"
            elif report["status"] == "RUNNING":
                report["status"] = "PARTIAL"
            if all(part in known_pages_by_part for part in parts):
                report["pages_remaining"] = sum(
                    max(0, known_pages_by_part[part] - scanned_by_part.get(part, 0))
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
            if not report.get("last_error"): report["last_error"] = "Catalogue page failed; checkpoint retained"
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
