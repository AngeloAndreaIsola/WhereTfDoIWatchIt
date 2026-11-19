"""Reviewed discovery permissions. Reachability/robots are not permission grants.

No current unofficial provider has independently verified automation permission.
Approval requires a reviewed code change with retained primary evidence, scope,
expiry and operator rate limits; provider JSON/CLI flags cannot self-approve.
"""
from dataclasses import dataclass
from datetime import date
import math
import re
from urllib.parse import urlsplit


class DiscoveryPolicyError(BaseException):
    """Fatal guard, like budget exhaustion: adapter catch-all handlers must not
    swallow it and continue crawling. Public messages are stable reason codes.
    """


@dataclass(frozen=True)
class DiscoveryPermission:
    provider: str
    status: str = "UNVERIFIED"
    hostname: str = ""
    paths: tuple[str, ...] = ()
    evidence: str = ""
    reviewed_on: date | None = None
    expires_on: date | None = None
    min_interval: float = 2.0
    request_limit: int = 40
    page_limit: int = 10
    metadata_paths: tuple[str, ...] = ()

    def require(self, provider, *, today=None):
        today = today or date.today()
        if self.status == "DENIED":
            raise DiscoveryPolicyError("source_permission_denied")
        if (self.status != "VERIFIED" or self.provider != provider or not self.evidence
                or not self.reviewed_on or self.reviewed_on > today
                or not self.expires_on or today > self.expires_on
                or not self.hostname or not self.paths
                or not math.isfinite(self.min_interval) or self.min_interval < 2
                or type(self.request_limit) is not int or not 1 <= self.request_limit <= 500
                or type(self.page_limit) is not int or not 1 <= self.page_limit <= 250):
            raise DiscoveryPolicyError("source_permission_unverified")

    def validate_url(self, url):
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            if (any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url
                    or parts.scheme != "https" or parts.port not in (None, 443)
                    or parts.username is not None or parts.password is not None or parts.fragment
                    or host != self.hostname or not re.fullmatch(r"[a-z0-9.-]+", host)
                    or host.endswith(".") or not any(re.fullmatch(p, parts.path) for p in self.paths + self.metadata_paths)):
                raise DiscoveryPolicyError("discovery_url_policy_rejected")
        except (ValueError, TypeError):
            raise DiscoveryPolicyError("discovery_url_policy_rejected") from None
        return parts


UNOFFICIAL_PROVIDERS = frozenset({
    "kisskh", "hydrahd", "animeworld", "streamingcommunity",
    "cineblog01", "altadefinizione",
})

PERMISSIONS = {
    "kisskh": DiscoveryPermission("kisskh"),
    "streamingcommunity": DiscoveryPermission("streamingcommunity"),
    "cineblog01": DiscoveryPermission("cineblog01"),
    "altadefinizione": DiscoveryPermission("altadefinizione"),
    "hydrahd": DiscoveryPermission("hydrahd", hostname="hydrahd.gd"),
    # Official feed exposure and earlier robots access do not override the
    # documented anti-scraping terms or constitute an automation grant.
    "animeworld": DiscoveryPermission("animeworld", hostname="www.animeworld.ac"),
}


def permission_for(provider):
    return PERMISSIONS.get(provider.slug, DiscoveryPermission(provider.slug))
