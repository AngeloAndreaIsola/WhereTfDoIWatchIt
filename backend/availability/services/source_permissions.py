"""Operator-reviewed outbound permissions for application availability checks."""

from datetime import datetime
from math import isfinite
from typing import Mapping

from django.utils import timezone

from availability.models import Source

PERMISSION_KEY = "outbound_availability_permission"
ENDPOINTS = {
    "justwatch": "https://apis.justwatch.com/graphql",
    "watchmode": "https://api.watchmode.com/v1",
    "streaming_availability": "https://streaming-availability.p.rapidapi.com",
}


def reviewed_permission(source, country, operations, *, now=None):
    """Return the receipt only for a current and explicitly approved scope."""
    if not source or not source.enabled or not country:
        return None
    config = source.configuration
    receipt = config.get(PERMISSION_KEY) if isinstance(config, Mapping) else None
    if not isinstance(receipt, dict):
        return None
    if not (
        receipt.get("status") == "authorized" and source.slug in ENDPOINTS
        and receipt.get("endpoint") == ENDPOINTS[source.slug]
        and all(
            isinstance(receipt.get(key), str) and 0 < len(receipt[key].strip()) <= 255
            for key in ("evidence_reference", "reviewed_by")
        )
        and isinstance(receipt.get("countries"), list) and country in receipt["countries"]
        and isinstance(receipt.get("operations"), list)
        and all(op in receipt["operations"] for op in operations)
    ):
        return None
    try:
        start = datetime.fromisoformat(receipt["valid_from"])
        stop = datetime.fromisoformat(receipt["valid_until"])
        if timezone.is_naive(start) or timezone.is_naive(stop) or not start <= (now or timezone.now()) < stop:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    if source.slug == "justwatch":
        limit = receipt.get("maximum_requests")
        interval = receipt.get("minimum_interval_seconds")
        if (
            type(limit) is not int or not 0 < limit <= 2_147_483_647
            or type(interval) not in (int, float) or not 0 < interval <= 86_400
            or not isfinite(interval)
        ):
            return None
    return receipt


def title_source_is_authorized(slug, country, content_type, *, source=None):
    if source is None:
        source = Source.objects.filter(slug=slug).first()
    if slug == "justwatch":
        operations = ("search", "seasons") if content_type == "series" else ("search",)
    elif slug == "watchmode":
        operations = ("search", "title_sources")
    else:
        operations = ("show_availability",)
    return bool(reviewed_permission(source, country, operations))
