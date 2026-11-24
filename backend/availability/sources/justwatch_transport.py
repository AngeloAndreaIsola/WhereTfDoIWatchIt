"""Default JustWatch POST transport. The #97 experiment injects its own transport."""

from datetime import timedelta
from email.utils import parsedate_to_datetime
import time

import requests
from django.db import connection, transaction
from django.utils import timezone

from availability.models import JustWatchRequestBudget, Source
from availability.services.source_permissions import ENDPOINTS, reviewed_permission

from .base import SourceFetchStopped, SourceQuotaExceeded


def _retry_until(value, now):
    try:
        return now + timedelta(seconds=max(600, int(value)))
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return max(now + timedelta(minutes=10), parsedate_to_datetime(value))
    except (TypeError, ValueError, OverflowError):
        return now + timedelta(minutes=10)


def _reserve(operation, countries):
    if connection.in_atomic_block:
        raise SourceFetchStopped("JustWatch requires a durable pre-send reservation")
    while True:
        with transaction.atomic():
            now = timezone.now()
            source = Source.objects.select_for_update().filter(slug="justwatch").first()
            permission = reviewed_permission(source, countries[0], [operation], now=now)
            if permission is None or not set(countries).issubset(permission["countries"]):
                raise SourceFetchStopped("JustWatch has no reviewed permission for this operation and market")
            budget, _ = JustWatchRequestBudget.objects.get_or_create(
                evidence_reference=permission["evidence_reference"].strip(),
            )
            # Serialize across receipts and both title and catalogue callers.
            # A rotated receipt cannot erase a recent rate limit or an uncertain send.
            if JustWatchRequestBudget.objects.filter(in_progress=True).exists() or budget.denied:
                raise SourceFetchStopped("JustWatch is stopped pending permission or attempt review")
            if JustWatchRequestBudget.objects.filter(blocked_until__gt=now).exists():
                raise SourceQuotaExceeded("JustWatch rate window is still active", kind="rate_limit")
            if budget.attempts >= permission["maximum_requests"]:
                raise SourceQuotaExceeded("JustWatch permission request budget exhausted", kind="local_budget")
            latest = JustWatchRequestBudget.objects.order_by(
                "-last_reserved_at"
            ).filter(last_reserved_at__isnull=False).first()
            gap = permission["minimum_interval_seconds"]
            wait = gap - (now - latest.last_reserved_at).total_seconds() if latest else 0
            if wait <= 0:
                budget.attempts += 1
                budget.last_reserved_at = now
                budget.in_progress = True
                budget.save(update_fields=["attempts", "last_reserved_at", "in_progress"])
                return budget
            if wait >= 60:
                raise SourceQuotaExceeded("JustWatch permission pacing window is active", kind="rate_limit")
        time.sleep(wait)


def post(url, *, operation, countries, json, headers, timeout):
    if url != ENDPOINTS["justwatch"]:
        raise SourceFetchStopped("JustWatch endpoint is outside the reviewed scope")
    budget = _reserve(operation, countries)
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(url, json=json, headers=headers, timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        # The remote outcome is unknown. The committed reservation remains in
        # progress until an operator reconciles it, including after restarts.
        raise SourceFetchStopped("JustWatch request interrupted; reconciliation required") from exc

    now = timezone.now()
    status = response.status_code
    budget.in_progress = False
    budget.denied = status in (401, 403) or 300 <= status < 400
    if status == 429:
        budget.blocked_until = _retry_until(response.headers.get("Retry-After"), now)
    budget.save(update_fields=["in_progress", "denied", "blocked_until"])
    if budget.denied:
        raise SourceFetchStopped(f"JustWatch refused the request with HTTP {status}")
    if status == 429:
        raise SourceQuotaExceeded("JustWatch returned HTTP 429", kind="rate_limit", status_code=429)
    return response
