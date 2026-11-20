"""Single sequential discovery egress gate; explicit redirects and DNS pinning.

DNS is resolved before each attempt; every answer must be public and the actual
TLS connection is pinned to a validated address, retaining hostname/SNI/cert
validation. No proxies, cookies, authentication, adapter retries or redirects.
"""
from collections import Counter
import ipaddress
import math
import re
import socket
import time
from urllib.parse import urljoin

import requests
import urllib3

from tracker.services.discovery_policy import DiscoveryPolicyError


class RequestBudgetReached(BaseException):
    pass


class SystemClock:
    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


def is_public_destination(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if (not ip.is_global or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or getattr(ip, "is_site_local", False)):
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped is None or is_public_destination(str(mapped))


def public_addresses(host):
    try:
        addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    except OSError:
        raise DiscoveryPolicyError("discovery_dns_failed") from None
    if not addresses:
        raise DiscoveryPolicyError("discovery_dns_empty")
    for address in addresses:
        if not is_public_destination(address):
            raise DiscoveryPolicyError("discovery_network_destination_prohibited")
    return addresses


class DiscoveryHTTP:
    def __init__(self, permission, *, request_budget, interval=2, max_redirects=3,
                 clock=None, resolver=None, timeout=10, authorize=None):
        if (type(request_budget) is not int or not 1 <= request_budget <= 500
                or type(max_redirects) is not int or not 0 <= max_redirects <= 5
                or not math.isfinite(interval) or not 0 <= interval <= 60):
            raise ValueError("invalid discovery HTTP limits")
        self.permission = permission
        self.limit = min(request_budget, permission.request_limit)
        self.interval = max(2.0, interval, permission.min_interval)
        self.max_redirects, self.timeout = max_redirects, timeout
        self.clock, self.resolver = clock or SystemClock(), resolver or public_addresses
        self.authorize = authorize
        self.attempted, self.last_started = 0, None
        self.counts, self.events = Counter(), []
        self.page_resource = None

    def begin_page(self):
        self.page_resource = None

    @staticmethod
    def resource(parts):
        return (parts.hostname, parts.path.rstrip("/"), parts.query)

    def _send(self, url, address, headers):
        """Only socket-producing seam. Never use session/environment defaults."""
        parts = self.permission.validate_url(url)
        pool = urllib3.HTTPSConnectionPool(address, port=443, assert_hostname=parts.hostname,
            server_hostname=parts.hostname, cert_reqs="CERT_REQUIRED", ca_certs=requests.certs.where())
        try:
            raw = pool.urlopen("GET", parts.path + ("?" + parts.query if parts.query else ""),
                headers={**headers, "Host": parts.hostname}, redirect=False, retries=False,
                timeout=urllib3.Timeout(connect=self.timeout, read=self.timeout))
            response = requests.Response()
            response.status_code, response.headers = raw.status, requests.structures.CaseInsensitiveDict(raw.headers)
            response._content, response.url, response.encoding = raw.data, url, "utf-8"
            return response
        except urllib3.exceptions.HTTPError:
            raise requests.ConnectionError("discovery_transport_failed") from None
        finally:
            pool.close()

    def wait_for_spacing(self):
        if self.last_started is None:
            return
        remaining = self.last_started + self.interval - self.clock.monotonic()
        while remaining > 0:
            self.clock.sleep(remaining)
            remaining = self.last_started + self.interval - self.clock.monotonic()

    def get(self, url, *, headers=None, kind="listing", retry=False, **kwargs):
        self.permission.require(self.permission.provider)
        if kind not in {"robots", "listing", "metadata"}:
            raise DiscoveryPolicyError("discovery_request_kind_rejected")
        # Never carry operator/session headers or credentials into redirects.
        headers = headers or {}
        safe_headers = {k: v for k,v in headers.items() if k.lower() in {"user-agent", "accept"}}
        if any(k not in {"timeout", "allow_redirects"} for k in kwargs):
            raise DiscoveryPolicyError("discovery_request_options_rejected")
        seen = set()
        for hop in range(self.max_redirects + 1):
            if self.attempted >= self.limit:
                raise RequestBudgetReached()
            parts = self.permission.validate_url(url)
            allowed_paths = self.permission.metadata_paths if kind == "metadata" else self.permission.paths
            if not any(re.fullmatch(p, parts.path) for p in allowed_paths):
                raise DiscoveryPolicyError("discovery_request_scope_rejected")
            if kind == "robots" and parts.path != "/robots.txt":
                raise DiscoveryPolicyError("robots_redirect_resource_changed")
            if kind == "listing":
                if self.page_resource is None:
                    self.page_resource = self.resource(parts)
                elif self.resource(parts) != self.page_resource:
                    raise DiscoveryPolicyError("listing_page_resource_changed")
            if url in seen:
                raise DiscoveryPolicyError("discovery_redirect_loop")
            seen.add(url)
            self.wait_for_spacing()
            if self.authorize is not None:
                self.authorize(url, kind)
            addresses = self.resolver(parts.hostname)
            # Validate injected resolver answers too; never trust private peers.
            if not addresses or any(not is_public_destination(a) for a in addresses):
                raise DiscoveryPolicyError("discovery_network_destination_prohibited")
            self.last_started = self.clock.monotonic()
            self.attempted += 1  # reserve before every potentially failing send
            self.counts[kind] += 1
            self.counts["redirects"] += int(hop > 0)
            self.counts["retries"] += int(retry)
            event = dict(attempt=self.attempted, kind=kind, redirect_hop=hop,
                         retry=retry, started=self.last_started)
            self.events.append(event)
            response = self._send(url, addresses[0], safe_headers)
            event["status"] = response.status_code
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            if hop >= self.max_redirects:
                raise DiscoveryPolicyError("discovery_redirect_limit_exhausted")
            location = response.headers.get("Location")
            if not location:
                raise DiscoveryPolicyError("discovery_redirect_location_missing")
            destination = urljoin(url, location)
            target = self.permission.validate_url(destination)
            if self.resource(target) != self.resource(parts):
                raise DiscoveryPolicyError("discovery_redirect_resource_changed")
            url = destination
        raise DiscoveryPolicyError("discovery_redirect_limit_exhausted")

    def report(self):
        return dict(limit=self.limit, attempted=self.attempted, counts=dict(self.counts),
                    min_interval=self.interval, events=list(self.events))

    def install(self, session):
        # Cover GET, request(), send(), POST and metadata adapter paths alike.
        # Only explicitly supported GETs can enter the guarded transport.
        def request(method, url, **kwargs):
            if method.upper() != "GET":
                raise DiscoveryPolicyError("discovery_http_method_rejected")
            return self.get(url, **kwargs)

        def send(prepared, **kwargs):
            raise DiscoveryPolicyError("discovery_direct_send_rejected")

        session.request, session.send = request, send
        session.get = lambda url, **kwargs: self.get(url, **kwargs)
