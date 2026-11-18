"""Explicit offline-only policy, clock and public DNS fixtures for discovery."""
from dataclasses import replace
from datetime import date
from urllib.parse import urlsplit
from unittest.mock import patch

from tracker.services.discovery_policy import DiscoveryPermission


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FixturePermission(DiscoveryPermission):
    def validate_url(self, url):
        host = urlsplit(url).hostname
        assert host in {'fixture.invalid', 'hydrahd.gd', 'www.animeworld.ac', 'kisskh.do', 'kisskh.co'}, host
        return DiscoveryPermission.validate_url(replace(self, hostname=host), url)


def fixture_permission(provider):
    return FixturePermission(provider.slug, 'VERIFIED', 'fixture.invalid', (r'/.*',),
        'offline fixture only; not a live permission grant', date(2026, 1, 1), date(2099, 1, 1),
        request_limit=500, page_limit=250)


def enable_discovery_fixtures(case):
    for p in (
        patch('tracker.services.catalogue_discovery.permission_for', side_effect=fixture_permission),
        patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']),
        patch('tracker.services.discovery_http.SystemClock', side_effect=FakeClock),
    ):
        p.start()
        case.addCleanup(p.stop)


class FixtureResponse:
    status_code = 200
    headers = {}
