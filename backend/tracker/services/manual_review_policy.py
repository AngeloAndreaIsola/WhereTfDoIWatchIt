"""Manual identity review is independent of source collection and availability.

No new TMDb grant is installed here. Only existing RaiPlay/NHK legacy manual
review remains eligible, using stored evidence. Request flags cannot grant rights.
"""
from dataclasses import dataclass
from datetime import date
import math

from .discovery_policy import UNOFFICIAL_PROVIDERS


class ManualReviewBlocked(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)

    def payload(self):
        return {"code": self.code, "error": self.code.replace("_", " "), "blocked": True}


def require_manual_review(provider):
    slug = (provider.slug or "").strip().lower()
    if slug in UNOFFICIAL_PROVIDERS:
        # No separately applicable manual-review grant exists for these sources.
        raise ManualReviewBlocked("source_permission_unverified")
    if not provider.is_enabled:
        raise ManualReviewBlocked("manual_review_provider_disabled")
    if slug not in {"raiplay", "nhk_world"}:
        raise ManualReviewBlocked("manual_review_permission_missing")


@dataclass(frozen=True)
class TMDbReviewGrant:
    provider: str
    status: str = "UNVERIFIED"
    evidence: str = ""
    reviewed_on: date | None = None
    expires_on: date | None = None
    daily_limit: int = 0
    monthly_limit: int = 0
    min_interval: float = 2.0


# A new reviewed, scoped authorization is required before populating this map.
# API keys, provider config, request body flags and crawler grants are unrelated.
TMDB_REVIEW_GRANTS: dict[str, TMDbReviewGrant] = {}


def require_tmdb_review_grant(provider):
    require_manual_review(provider)
    grant = TMDB_REVIEW_GRANTS.get(provider.slug)
    if grant is None:
        raise ManualReviewBlocked("tmdb_review_permission_missing", 403)
    if grant.status == "DENIED":
        raise ManualReviewBlocked("tmdb_review_permission_denied", 403)
    today = date.today()
    if grant.expires_on and grant.expires_on < today:
        raise ManualReviewBlocked("tmdb_review_permission_expired", 403)
    if (grant.status != "VERIFIED" or grant.provider != provider.slug or not grant.evidence
            or not grant.reviewed_on or grant.reviewed_on > today or not grant.expires_on
            or type(grant.daily_limit) is not int or not 1 <= grant.daily_limit <= 100
            or type(grant.monthly_limit) is not int or not 1 <= grant.monthly_limit <= 1000
            or not math.isfinite(grant.min_interval) or grant.min_interval < 2):
        raise ManualReviewBlocked("tmdb_review_permission_unverified", 403)
    return grant
