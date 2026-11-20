"""Bounded Tubi catalogue transport. Entry point for an approved canary only."""

from dataclasses import replace
import json
import logging
import re
from urllib.robotparser import RobotFileParser

from availability.sources.tubi import SITEMAP_INDEX_URL, TITLE_SITEMAP_RE, TubiClient, TubiSource
from tracker.services.bulk_refresh import release_source_lock, try_source_lock
from tracker.services.catalogue_only import require_collector_permission
from tracker.services.discovery_http import DiscoveryHTTP, RequestBudgetReached
from tracker.services.discovery_policy import DiscoveryPolicyError


logger = logging.getLogger(__name__)

PROVIDER = "tubi"
USER_AGENT = "WTDW-Catalogue"
ROBOTS_URL = "https://tubitv.com/robots.txt"
TITLE_PATH = re.compile(r"/(?:movies|series)/[0-9]+/[a-z0-9-]+/?")


def _resume_cursor(cursor, sitemap_url):
    if cursor is None:
        return None
    if not isinstance(cursor, str) or len(cursor) > 65536:
        raise ValueError("Invalid bounded catalogue checkpoint")
    try:
        checkpoint = json.loads(cursor)
    except ValueError:
        raise ValueError("Invalid bounded catalogue checkpoint") from None
    if (not isinstance(checkpoint, dict) or checkpoint.get("sitemap") != sitemap_url
            or not isinstance(checkpoint.get("cursor"), str)):
        raise ValueError("Checkpoint belongs to a different catalogue scope")
    return checkpoint["cursor"]


class _TubiRequester:
    def __init__(self, permission, sitemap_url, *, request_budget):
        self.permission = permission
        self.sitemap_url = sitemap_url
        self.robots = None
        self.pages_attempted = 0
        self.http = DiscoveryHTTP(permission, request_budget=request_budget, authorize=self._authorize)

    def _authorize(self, url, kind):
        # Recheck the provider toggle and reviewed grant after waiting, for
        # each dispatch including redirects. Check robots on the actual URL.
        _, current = require_collector_permission(PROVIDER)
        if current != self.permission:
            raise DiscoveryPolicyError("source_permission_changed")
        parts = current.validate_url(url)
        if parts.query:
            raise DiscoveryPolicyError("catalogue_query_out_of_scope")
        if kind == "robots":
            if url != ROBOTS_URL:
                raise DiscoveryPolicyError("catalogue_robots_out_of_scope")
            return
        if kind == "metadata" and url not in {SITEMAP_INDEX_URL, self.sitemap_url}:
            raise DiscoveryPolicyError("catalogue_metadata_out_of_scope")
        if kind == "listing" and not TITLE_PATH.fullmatch(parts.path):
            raise DiscoveryPolicyError("catalogue_url_not_a_title")
        if self.robots is None or not self.robots.can_fetch(USER_AGENT, url):
            raise DiscoveryPolicyError("catalogue_robots_disallowed")

    def _get(self, url, kind):
        response = self.http.get(url, kind=kind, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            raise DiscoveryPolicyError(f"catalogue_http_status_{response.status_code}")
        return response.text

    def load_robots(self):
        text = self._get(ROBOTS_URL, "robots")
        if not re.search(r"^\s*user-agent\s*:\s*\S", text, re.I | re.M):
            raise DiscoveryPolicyError("catalogue_robots_invalid")
        robots = RobotFileParser(ROBOTS_URL)
        robots.parse(text.splitlines())
        delay = robots.crawl_delay(USER_AGENT) or 0
        rate = robots.request_rate(USER_AGENT)
        if rate:
            if rate.requests <= 0 or rate.seconds <= 0:
                raise DiscoveryPolicyError("catalogue_robots_rate_invalid")
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 60:
            raise DiscoveryPolicyError("catalogue_robots_interval_out_of_bounds")
        self.http.interval = max(self.http.interval, delay)
        self.robots = robots

    def __call__(self, url):
        if url in {SITEMAP_INDEX_URL, self.sitemap_url}:
            return self._get(url, "metadata")
        if self.pages_attempted >= 1:
            raise RequestBudgetReached()
        self.pages_attempted += 1
        return self._get(url, "listing")


def collect_tubi_catalogue_page(sitemap_url, *, cursor=None, request_budget=5, page_limit=1):
    """Collect one titled page from one explicit index member, at most five GETs.

    Pass the result to the existing compare-and-set catalogue importer only
    after a successful fetch. Fetch errors leave its stored cursor untouched.
    The returned accounting includes robots, sitemap documents and redirects.
    """
    if type(page_limit) is not int or page_limit != 1:
        raise ValueError("The bounded catalogue requester requires page_limit=1")
    if type(request_budget) is not int or not 1 <= request_budget <= 5:
        raise ValueError("The bounded catalogue requester requires 1..5 attempts")
    if not isinstance(sitemap_url, str) or not TITLE_SITEMAP_RE.fullmatch(sitemap_url):
        raise ValueError("Select an exact Tubi title sitemap")
    source_cursor = _resume_cursor(cursor, sitemap_url)
    _, permission = require_collector_permission(PROVIDER)
    permission.validate_url(sitemap_url)
    lock = f"unofficial-catalogue:{PROVIDER}"
    if not try_source_lock(lock):
        raise RuntimeError("Catalogue discovery is already active")
    try:
        requester = _TubiRequester(permission, sitemap_url, request_budget=request_budget)
        try:
            requester.load_robots()
            collector = TubiSource(
                client=TubiClient(requester=requester, retries=0),
                max_contents=page_limit,
                max_priority=0,
                catalogue_only_sitemap=sitemap_url,
            )
            result = collector.fetch(cursor=source_cursor)
            # An empty/shell-only result cannot prove that its title was
            # visited successfully. Keep the current position for review.
            if len(result.contents) != 1:
                raise DiscoveryPolicyError("catalogue_title_not_observed")
            checkpoint = json.dumps({"sitemap": sitemap_url, "cursor": result.cursor}, sort_keys=True)
            return replace(result, cursor=checkpoint), {
                "http": requester.http.report(),
                "pages_attempted": requester.pages_attempted,
                "page_limit": page_limit,
                "sitemap": sitemap_url,
                "permission_evidence": permission.evidence,
            }
        finally:
            logger.info(
                "catalogue_only_requester provider=%s sitemap=%s pages_attempted=%d accounting=%s",
                PROVIDER, sitemap_url, requester.pages_attempted, requester.http.report(),
            )
            # Keep the lock through the cooldown so another invocation does
            # not dispatch immediately after this one releases the provider.
            requester.http.wait_for_spacing()
    finally:
        release_source_lock(lock)
