"""Retained Tubi pages through the production guarded catalogue requester."""

from datetime import date
from unittest.mock import patch

from availability.tests.tubi_fixtures import FIXTURES, I_ROBOT, MOVIES_1, PAGE_FILES, PERMISSION
from tracker.services.catalogue_only_requester import collect_tubi_catalogue_page
from tracker.services.discovery_policy import DiscoveryPermission
from tracker.tests.discovery_fixtures import FakeClock
from tracker.tests.test_discovery_controls import response

PERMISSION_FIXTURE = DiscoveryPermission("tubi", "VERIFIED", "tubitv.com",
    (r"/robots\.txt", r"/(movies|series)/[0-9]+/[^/]+/?"),
    "offline fixture transport only; no real automation grant", date(2026, 1, 1), date(2099, 1, 1),
    request_limit=5, page_limit=1,
    metadata_paths=(r"/sitemap\.xml", r"/sitemaps/(movies|tv-shows)-[0-9]+\.xml"))

ROBOTS = "https://tubitv.com/robots.txt"
INDEX = "https://tubitv.com/sitemap.xml"
OTHER_SITEMAP = "https://tubitv.com/sitemaps/movies-2.xml"


def one_page(*, request_budget=5, page_limit=1, redirect=False, detail_url=I_ROBOT,
             robots="User-agent: *\nAllow: /\n", resolver=None, send=None, cursor=None,
             permission_check=None):
    PERMISSION_FIXTURE.validate_url(detail_url)
    index = f"<sitemapindex><sitemap><loc>{OTHER_SITEMAP}</loc></sitemap><sitemap><loc>{MOVIES_1}</loc></sitemap></sitemapindex>"
    entries = f"<urlset><url><loc>{detail_url}</loc></url><url><loc>{PERMISSION}</loc></url></urlset>"
    payloads = {
        ROBOTS: robots,
        INDEX: index,
        MOVIES_1: entries,
        detail_url: (FIXTURES / PAGE_FILES[detail_url]).read_text(),
        PERMISSION: (FIXTURES / PAGE_FILES[PERMISSION]).read_text(),
    }

    def fixture_send(url, address, headers):
        overrides = send(url, address, headers) if send else None
        if overrides is not None:
            return overrides
        if redirect and url == detail_url:
            return response(302, Location=detail_url + "/")
        if url == detail_url + "/":
            return response(body=payloads[detail_url])
        if url not in payloads:
            raise AssertionError(f"Unregistered fixture URL: {url}")
        return response(body=payloads[url])

    module = "tracker.services.catalogue_only_requester"
    with (
        patch(f"{module}.require_collector_permission", side_effect=permission_check,
              return_value=(None, PERMISSION_FIXTURE)),
        patch(f"{module}.try_source_lock", return_value=True),
        patch(f"{module}.release_source_lock"),
        patch("tracker.services.discovery_http.SystemClock", side_effect=FakeClock),
        patch("tracker.services.discovery_http.public_addresses",
              side_effect=resolver or (lambda _: ["93.184.216.34"])),
        patch("tracker.services.discovery_http.DiscoveryHTTP._send", side_effect=fixture_send),
    ):
        return collect_tubi_catalogue_page(MOVIES_1, cursor=cursor,
                                            request_budget=request_budget, page_limit=page_limit)
