"""Rights review, provenance validation, and catalogue filtering for public domain content.

Core domain invariants:
1. Never assume that presence in an Internet Archive collection or playlist proves public-domain status.
2. Validate per-item rights: inspect explicit license URLs, dedication statements, and archival rights notices.
3. Classify ambiguous or missing rights as UNKNOWN; do not assert worldwide public domain status.
4. Record territorial caveats (e.g. US public domain vs worldwide/unrestricted).
5. Deep link strictly to original landing pages; do not download or copy video media.
6. Track source budget and health metrics.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RightsStatus(str, enum.Enum):
    PUBLIC_DOMAIN = "public_domain"
    OPEN_LICENSE = "open_license"
    AMBIGUOUS_UNKNOWN = "unknown"
    RESTRICTED = "restricted"


class TerritorialScope(str, enum.Enum):
    WORLDWIDE = "worldwide"
    US_ONLY = "us_only"
    UNKNOWN = "unknown"


# Standard known public domain license indicators
KNOWN_PD_LICENSE_URLS = {
    "http://creativecommons.org/publicdomain/mark/1.0/",
    "https://creativecommons.org/publicdomain/mark/1.0/",
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "https://creativecommons.org/publicdomain/zero/1.0/",
}

# Permissive open licenses
KNOWN_OPEN_LICENSE_URL_PREFIXES = (
    "http://creativecommons.org/licenses/by/",
    "https://creativecommons.org/licenses/by/",
    "http://creativecommons.org/licenses/by-sa/",
    "https://creativecommons.org/licenses/by-sa/",
)

# Explicitly restricted or non-commercial CC licenses
RESTRICTED_LICENSE_URL_SUBSTRINGS = (
    "-nc",  # NonCommercial
    "-nd",  # NoDerivatives
)


@dataclass(frozen=True)
class RightsEvaluation:
    status: RightsStatus
    territorial_scope: TerritorialScope
    rights_statement: str
    license_url: Optional[str]
    is_watchable_public_domain: bool
    notes: str = ""


@dataclass(frozen=True)
class ProvenanceRecord:
    identifier: str
    landing_page_url: str
    rights_url: Optional[str]
    rights_status: RightsStatus
    territorial_scope: TerritorialScope
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_rights_text: str = ""


class SourceBudgetTracker:
    """Tracks source request budget and health metrics per run."""

    def __init__(self, max_requests: int = 100):
        self.max_requests = max_requests
        self.requests_made = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.items_evaluated = 0
        self.pd_items_accepted = 0
        self.ambiguous_items_flagged = 0
        self.rejected_items = 0

    def record_request(self, success: bool = True):
        self.requests_made += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1

    @property
    def budget_exhausted(self) -> bool:
        return self.requests_made >= self.max_requests

    @property
    def health_status(self) -> dict[str, Any]:
        return {
            "requests_made": self.requests_made,
            "max_requests": self.max_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "items_evaluated": self.items_evaluated,
            "pd_accepted": self.pd_items_accepted,
            "ambiguous_flagged": self.ambiguous_items_flagged,
            "rejected": self.rejected_items,
            "healthy": self.failed_requests == 0 or (self.successful_requests / max(1, self.requests_made) >= 0.8),
        }


def evaluate_item_rights(raw_item: dict[str, Any]) -> RightsEvaluation:
    """Evaluate the rights status of an archive metadata item.

    Strictly inspects license URLs and rights declarations. Absence of explicit
    proof of public domain or open license yields AMBIGUOUS_UNKNOWN.
    """
    license_url = (raw_item.get("licenseurl") or "").strip() or None
    rights_text = str(raw_item.get("rights") or "").strip()
    jurisdiction = str(raw_item.get("jurisdiction") or "").strip().upper()

    # 1. Check license URL if present
    if license_url:
        normalized_license = license_url.lower()

        # Public Domain Dedication or Mark
        if any(normalized_license.startswith(u) for u in KNOWN_PD_LICENSE_URLS):
            return RightsEvaluation(
                status=RightsStatus.PUBLIC_DOMAIN,
                territorial_scope=TerritorialScope.WORLDWIDE,
                rights_statement=rights_text or "Public Domain Mark / Dedication",
                license_url=license_url,
                is_watchable_public_domain=True,
                notes="Verified public domain mark or zero dedication.",
            )

        # Check for restrictive CC licenses (NC / ND)
        if any(substr in normalized_license for substr in RESTRICTED_LICENSE_URL_SUBSTRINGS):
            return RightsEvaluation(
                status=RightsStatus.RESTRICTED,
                territorial_scope=TerritorialScope.UNKNOWN,
                rights_statement=rights_text or "Restricted CC License",
                license_url=license_url,
                is_watchable_public_domain=False,
                notes="Non-commercial or no-derivatives restriction present.",
            )

        # Permissive open licenses (CC BY / CC BY-SA)
        if any(normalized_license.startswith(prefix) for prefix in KNOWN_OPEN_LICENSE_URL_PREFIXES):
            return RightsEvaluation(
                status=RightsStatus.OPEN_LICENSE,
                territorial_scope=TerritorialScope.WORLDWIDE,
                rights_statement=rights_text or "Creative Commons Open License",
                license_url=license_url,
                is_watchable_public_domain=True,
                notes="Permissive Creative Commons license.",
            )

    # 2. Check institutional / Library of Congress text statements
    rights_lower = rights_text.lower()
    if "library of congress" in rights_lower and "not aware of any u.s. copyright" in rights_lower:
        return RightsEvaluation(
            status=RightsStatus.PUBLIC_DOMAIN,
            territorial_scope=TerritorialScope.US_ONLY,
            rights_statement=rights_text,
            license_url=None,
            is_watchable_public_domain=True,
            notes="Library of Congress verified US public domain determination.",
        )

    # Pre-1929 published works explicitly indicated
    if ("pre-1929" in rights_lower or "published before 1929" in rights_lower) and "public domain" in rights_lower:
        scope = TerritorialScope.US_ONLY if jurisdiction == "US" else TerritorialScope.WORLDWIDE
        return RightsEvaluation(
            status=RightsStatus.PUBLIC_DOMAIN,
            territorial_scope=scope,
            rights_statement=rights_text,
            license_url=license_url,
            is_watchable_public_domain=True,
            notes="Pre-1929 publication date indicates public domain expiration.",
        )

    # 3. If rights are ambiguous or unverified, mark as AMBIGUOUS_UNKNOWN
    logger.info(
        "public_domain_rights_ambiguous item=%s license=%s text=%r",
        raw_item.get("identifier"),
        license_url,
        rights_text[:100] if rights_text else "",
    )
    return RightsEvaluation(
        status=RightsStatus.AMBIGUOUS_UNKNOWN,
        territorial_scope=TerritorialScope.UNKNOWN,
        rights_statement=rights_text or "Rights status unverified / ambiguous",
        license_url=license_url,
        is_watchable_public_domain=False,
        notes="Unverified or absent rights metadata. Not asserted as public domain.",
    )


def build_provenance_record(raw_item: dict[str, Any], evaluation: RightsEvaluation) -> ProvenanceRecord:
    """Build a durable provenance record linking the item to its source landing page and rights claim."""
    identifier = str(raw_item.get("identifier") or "").strip()
    landing_page = str(raw_item.get("landing_page") or "").strip()
    if not landing_page:
        landing_page = f"https://archive.org/details/{identifier}"

    return ProvenanceRecord(
        identifier=identifier,
        landing_page_url=landing_page,
        rights_url=evaluation.license_url,
        rights_status=evaluation.status,
        territorial_scope=evaluation.territorial_scope,
        raw_rights_text=evaluation.rights_statement,
    )
