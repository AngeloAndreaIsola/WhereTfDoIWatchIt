"""One retained Tubi detail page through the real guarded HTTP and collector.

Only fixtures use a mock permission grant/resolver/send. Never imported by
runtime code. All unregistered fixture URLs fail rather than falling back live.
"""
from datetime import date
from urllib.robotparser import RobotFileParser

from availability.sources.tubi import TubiClient, TubiSource
from availability.tests.tubi_fixtures import FIXTURES, I_ROBOT, MOVIES_1, PAGE_FILES, PERMISSION
from tracker.services.discovery_http import DiscoveryHTTP, RequestBudgetReached
from tracker.services.discovery_policy import DiscoveryPermission, DiscoveryPolicyError
from tracker.tests.discovery_fixtures import FakeClock
from tracker.tests.test_discovery_controls import response

PERMISSION_FIXTURE = DiscoveryPermission("tubi", "VERIFIED", "tubitv.com",
    (r"/robots\.txt", r"/(movies|series)/[0-9]+/[^/]+/?"),
    "offline fixture transport only; no real automation grant", date(2026, 1, 1), date(2099, 1, 1),
    request_limit=5, page_limit=1,
    metadata_paths=(r"/sitemap\.xml", r"/sitemaps/(movies|tv-shows)-[0-9]+\.xml"))


def one_page(*, request_budget=5, page_limit=1, redirect=False, detail_url=I_ROBOT,
             robots="User-agent: *\nAllow: /\n", resolver=None, send=None, cursor=None):
    """At most one title page; XML fixtures are a bounded discovery window.

    The XML window is synthetic (two retained native URLs); the title HTML is
    the existing captured fixture, parsed by the unchanged production collector.
    """
    if type(page_limit) is not int or not 1 <= page_limit <= 1:
        raise ValueError("offline fixture requires a one-page ceiling")
    PERMISSION_FIXTURE.validate_url(detail_url)
    clock = FakeClock()
    http = DiscoveryHTTP(PERMISSION_FIXTURE, request_budget=request_budget, clock=clock,
        resolver=resolver or (lambda _: ["93.184.216.34"]))
    index = f'<sitemapindex><sitemap><loc>{MOVIES_1}</loc></sitemap></sitemapindex>'
    entries = f'<urlset><url><loc>{detail_url}</loc></url><url><loc>{PERMISSION}</loc></url></urlset>'
    payloads = {"https://tubitv.com/robots.txt": robots,
                "https://tubitv.com/sitemap.xml": index, MOVIES_1: entries,
                detail_url: (FIXTURES / PAGE_FILES[detail_url]).read_text()}

    def fixture_send(url, address, headers):
        if redirect and url == detail_url:
            return response(302, Location=detail_url+"/")
        if url == detail_url+"/":
            return response(body=payloads[detail_url])
        if url not in payloads:
            raise AssertionError("Unregistered fixture URL")
        return response(body=payloads[url])

    http._send = send or fixture_send
    robot_response = http.get("https://tubitv.com/robots.txt", kind="robots")
    policy = RobotFileParser()
    policy.parse(robot_response.text.splitlines())
    http.interval = max(http.interval, policy.crawl_delay("WTDW") or 0)
    rate = policy.request_rate("WTDW")
    if rate:
        http.interval = max(http.interval, rate.seconds / rate.requests)
    pages = 0

    def requester(url):
        nonlocal pages
        if not policy.can_fetch("WTDW", url):
            raise DiscoveryPolicyError("robots_disallows_fixture_resource")
        kind = "metadata" if url.endswith(".xml") else "listing"
        if kind == "listing":
            if pages >= page_limit:
                raise RequestBudgetReached()
            pages += 1
            http.begin_page()
        return http.get(url, kind=kind, headers={"User-Agent": "WTDW"}).text

    source = TubiSource(client=TubiClient(requester=requester, retries=0), max_contents=1, max_priority=0)
    result = source.fetch(cursor=cursor)
    return result, dict(http=http.report(), pages_attempted=pages, page_limit=page_limit)
