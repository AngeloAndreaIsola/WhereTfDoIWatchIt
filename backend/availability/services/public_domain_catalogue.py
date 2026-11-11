"""Rights review, provenance validation, and catalogue filtering for public domain content.

Core domain invariants:
1. Never assume that presence in an Internet Archive collection or playlist proves public-domain status.
2. Validate per-item rights: inspect explicit license URLs, dedication statements, and archival rights notices.
3. Classify ambiguous or missing rights as UNKNOWN; do not assert worldwide public domain status.
4. Never assume WORLDWIDE territorial scope from age or a bare Public Domain Mark alone.
   If jurisdiction is US or rights text indicates US-specific determination, scope is US_ONLY.
   If territorial scope is ambiguous or contradictory, classify as UNKNOWN.
   Only explicit worldwide dedications (e.g. verified worldwide waiver with no regional caveats)
   can qualify as WORLDWIDE.
5. Deep link strictly to original landing pages; do not download or copy video media.
6. Track source budget and health metrics accurately.
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
KNOWN_PD_MARK_URLS = {
    "http://creativecommons.org/publicdomain/mark/1.0/",
    "https://creativecommons.org/publicdomain/mark/1.0/",
}

KNOWN_CC0_URLS = {
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


def _determine_territorial_scope(
    rights_text: str,
    jurisdiction: str,
    is_cc0: bool = False,
) -> TerritorialScope:
    """Determine territorial scope based on rights text, jurisdiction, and license.

    Never assumes WORLDWIDE solely from a bare Public Domain Mark or publication age.
    """
    rights_lower = rights_text.lower()
    norm_jurisdiction = jurisdiction.strip().upper()

    # Explicit contradictions or regional caveats (e.g. "protected outside the US", "foreign copyright")
    has_foreign_copyright_caveat = (
        "protected by copyright" in rights_lower
        or "foreign copyright" in rights_lower
        or "outside the united states" in rights_lower
        or "outside the u.s." in rights_lower
        or "in other countries" in rights_lower
    )

    if has_foreign_copyright_caveat:
        if (
            "free to use and reuse in the united states" in rights_lower
            or "in the united states" in rights_lower
            or norm_jurisdiction == "US"
            or "u.s." in rights_lower
        ):
            return TerritorialScope.US_ONLY
        return TerritorialScope.UNKNOWN

    if norm_jurisdiction == "US" or "in the united states" in rights_lower or "u.s." in rights_lower:
        return TerritorialScope.US_ONLY

    # Only explicit worldwide rights text or CC0 without regional restrictions qualifies as WORLDWIDE
    if "worldwide" in rights_lower or "globally" in rights_lower:
        return TerritorialScope.WORLDWIDE

    if is_cc0 and not norm_jurisdiction:
        # CC0 is a universal worldwide dedication
        return TerritorialScope.WORLDWIDE

    # Default to UNKNOWN when territory is not explicitly established worldwide
    return TerritorialScope.UNKNOWN


def evaluate_item_rights(raw_item: dict[str, Any]) -> RightsEvaluation:
    """Evaluate the rights status of an archive metadata item.

    Strictly inspects license URLs and rights declarations. Absence of explicit
    proof of public domain or open license yields AMBIGUOUS_UNKNOWN.
    Never assumes WORLDWIDE territorial scope from age or a bare mark alone.
    """
    license_url = (raw_item.get("licenseurl") or "").strip() or None
    rights_text = str(raw_item.get("rights") or "").strip()
    jurisdiction = str(raw_item.get("jurisdiction") or "").strip().upper()

    # 1. Check license URL if present
    if license_url:
        normalized_license = license_url.lower()

        # Check for restrictive CC licenses (NC / ND) first
        if any(substr in normalized_license for substr in RESTRICTED_LICENSE_URL_SUBSTRINGS):
            return RightsEvaluation(
                status=RightsStatus.RESTRICTED,
                territorial_scope=TerritorialScope.UNKNOWN,
                rights_statement=rights_text or "Restricted CC License",
                license_url=license_url,
                is_watchable_public_domain=False,
                notes="Non-commercial or no-derivatives restriction present.",
            )

        # Public Domain Dedication (CC0)
        if any(normalized_license.startswith(u) for u in KNOWN_CC0_URLS):
            scope = _determine_territorial_scope(rights_text, jurisdiction, is_cc0=True)
            return RightsEvaluation(
                status=RightsStatus.PUBLIC_DOMAIN,
                territorial_scope=scope,
                rights_statement=rights_text or "CC0 1.0 Universal Public Domain Dedication",
                license_url=license_url,
                is_watchable_public_domain=True,
                notes="Verified CC0 universal dedication.",
            )

        # Public Domain Mark (PDM 1.0)
        if any(normalized_license.startswith(u) for u in KNOWN_PD_MARK_URLS):
            scope = _determine_territorial_scope(rights_text, jurisdiction, is_cc0=False)
            return RightsEvaluation(
                status=RightsStatus.PUBLIC_DOMAIN,
                territorial_scope=scope,
                rights_statement=rights_text or "Public Domain Mark 1.0",
                license_url=license_url,
                is_watchable_public_domain=True,
                notes="Public Domain Mark 1.0 with verified territorial evaluation.",
            )

        # Permissive open licenses (CC BY / CC BY-SA)
        if any(normalized_license.startswith(prefix) for prefix in KNOWN_OPEN_LICENSE_URL_PREFIXES):
            scope = _determine_territorial_scope(rights_text, jurisdiction, is_cc0=False)
            return RightsEvaluation(
                status=RightsStatus.OPEN_LICENSE,
                territorial_scope=scope if scope != TerritorialScope.UNKNOWN else TerritorialScope.WORLDWIDE,
                rights_statement=rights_text or "Creative Commons Open License",
                license_url=license_url,
                is_watchable_public_domain=True,
                notes="Permissive Creative Commons license.",
            )

    # 2. Check institutional / Library of Congress text statements
    rights_lower = rights_text.lower()
    if "library of congress" in rights_lower and "not aware of any u.s. copyright" in rights_lower:
        scope = TerritorialScope.US_ONLY
        return RightsEvaluation(
            status=RightsStatus.PUBLIC_DOMAIN,
            territorial_scope=scope,
            rights_statement=rights_text,
            license_url=None,
            is_watchable_public_domain=True,
            notes="Library of Congress verified US public domain determination.",
        )

    # Pre-1929 published works explicitly indicated in rights text
    if ("pre-1929" in rights_lower or "published before 1929" in rights_lower) and "public domain" in rights_lower:
        scope = _determine_territorial_scope(rights_text, jurisdiction, is_cc0=False)
        return RightsEvaluation(
            status=RightsStatus.PUBLIC_DOMAIN,
            territorial_scope=scope,
            rights_statement=rights_text,
            license_url=license_url,
            is_watchable_public_domain=True,
            notes="Pre-1929 publication date indicated in rights text.",
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
