"""Canonical freshness policy for availability sources (Phase D).

One module answers the single question *when does an observation from a given
``AvailabilitySource`` become stale?*:

* ``SOURCE_FRESHNESS`` declares one window per source slug, derived from each
  source's documented refresh cadence (``docs/CRON.md``) and from the refresh
  paths that feed it — never one global timeout applied uniformly;
* ``Source.configuration["expected_refresh_interval_seconds"]`` overrides the
  declared window for one source (the same key the reconciliation read path
  has always understood);
* ``freshness_for`` always answers a window (``DEFAULT_FRESHNESS`` only covers
  slugs with neither declaration, so the refresh path never has to guess),
  while ``declared_freshness`` answers ``None`` when nothing is declared, so
  the read path can report ``FreshnessState.UNKNOWN`` instead of pretending.

Freshness is deliberately *separate from refresh priority*. The priority
ladder (``tracker.RefreshPriority``) decides which pending refresh request
runs first; this module only decides whether a scope's last observation is
still good enough to skip a refresh. Sources are evaluated independently: for
the same title, one source's recent observation never makes another source's
stale observation fresh.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping, Optional

from django.db.models import Max
from django.utils import timezone

from .domain.enums import FreshnessState
from .models import Offer

FRESHNESS_INTERVAL_KEY = "expected_refresh_interval_seconds"

#: Fallback window for a slug that declares nothing (policy entry and
#: configuration alike). Only reachable for unregistered/test slugs: every
#: source the application can write offers under is listed below.
DEFAULT_FRESHNESS = timedelta(hours=24)

#: Declared freshness window per source slug. Values follow the crontab
#: cadence documented in ``docs/CRON.md`` — at or above the slowest
#: *recommended* slot for that source, so a scheduled run never lands after
#: its own window closed:
#:
#: - ``streaming_availability``: title-targeted, budget-capped sweep, daily
#:   at 03:17 (100 req/day free tier) -> 24h. This is the window the refresh
#:   path used before the policy became per source.
#: - catalogue sources: refreshed by ``refresh_provider_catalogue`` slots
#:   (``cbc_gem`` 6-hourly, the heavy ones daily) -> their slot cadence.
#: - ``legacy_scraper``: re-ingested by the scrapers, which change faster
#:   than official availability data (same rationale as the unofficial
#:   scrape display threshold in ``tracker.api_v2``).
SOURCE_FRESHNESS: dict[str, timedelta] = {
    "streaming_availability": timedelta(hours=24),
    "cbc_gem": timedelta(hours=6),
    "bbc_iplayer": timedelta(hours=24),
    "tubi": timedelta(hours=24),
    "prime_video": timedelta(hours=24),
    "rai_play": timedelta(hours=24),
    "legacy_scraper": timedelta(hours=12),
}


def declared_freshness(
    slug: str,
    configuration: Optional[Mapping] = None,
) -> Optional[timedelta]:
    """Declared refresh window for *slug*, or ``None`` when nothing declares one.

    Configuration override first (``expected_refresh_interval_seconds``, a
    positive number of seconds), then the source's ``SOURCE_FRESHNESS`` entry.
    An unconfigured unregistered slug answers ``None`` — the read path turns
    that into ``FreshnessState.UNKNOWN`` rather than a silent ``fresh``.
    """
    raw = (configuration or {}).get(FRESHNESS_INTERVAL_KEY)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        return SOURCE_FRESHNESS.get(slug)
    return timedelta(seconds=raw)


def freshness_for(
    slug: str,
    configuration: Optional[Mapping] = None,
) -> timedelta:
    """Window in which *slug*'s observations count as fresh.

    Same resolution order as :func:`declared_freshness`, but always answers:
    a refresh decision needs a window even for a slug nothing declares
    (``DEFAULT_FRESHNESS``).
    """
    return declared_freshness(slug, configuration) or DEFAULT_FRESHNESS


def freshness_state(
    slug: str,
    observed_at: datetime,
    *,
    configuration: Optional[Mapping] = None,
    now: Optional[datetime] = None,
) -> FreshnessState:
    """Classify one observation against the source's declared window.

    ``UNKNOWN`` when the source declares no window at all; otherwise ``FRESH``
    while the age is at most the window (exactly at the boundary is fresh) and
    ``STALE`` once it is passed. Never inspects the database — pass the
    observation's ``last_seen`` and the source's configuration.
    """
    interval = declared_freshness(slug, configuration)
    if interval is None:
        return FreshnessState.UNKNOWN
    return (
        FreshnessState.FRESH
        if _now(now) - _aware(observed_at) <= interval
        else FreshnessState.STALE
    )


def source_last_seen(tmdb_id, source: str) -> Optional[datetime]:
    """Newest ``Offer.last_seen`` of *source* for that TMDb id, or ``None``.

    ``None`` means the source has never observed the title — which reads as
    stale for refresh decisions (there is no evidence the scope was checked).
    """
    return Offer.objects.filter(
        tmdb_id=tmdb_id, source__slug=source
    ).aggregate(latest=Max("last_seen"))["latest"]


def source_is_stale(
    tmdb_id,
    source: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True when *source*'s observation of the title is missing or too old.

    Evaluated against that source's own window (``freshness_for``), never a
    shared one: the default scope can be stale while a catalogue source for
    the same title is fresh, and the other way around. A source that has
    never observed the title is always stale for its own scope.
    """
    return _is_stale_at(source, source_last_seen(tmdb_id, source), now=now)


def stale_sources(
    tmdb_id,
    *,
    sources=None,
    now: Optional[datetime] = None,
) -> list[str]:
    """Slugs whose observation of the title is stale, each on its own window.

    ``sources`` defaults to every source that has observed the title plus the
    default title-targeted scope — a scope that never observed the title is
    stale by definition, so the default scope is always a candidate. Pass an
    explicit iterable to evaluate exactly those slugs (useful to compare
    windows: a 13h-old ``cbc_gem`` observation is stale under its 6h window
    while a 13h-old ``bbc_iplayer`` observation is still fresh under 24h).

    Returns slugs in a stable (sorted) order; it classifies, it does not
    decide what a refresh run may touch. One aggregate query, regardless of
    how many sources observed the title.
    """
    from .sources.registry import DEFAULT_SOURCE_SLUG

    if sources is None:
        observed = set(
            Offer.objects.filter(tmdb_id=tmdb_id)
            .values_list("source__slug", flat=True)
            .distinct()
        )
        candidates = observed | {DEFAULT_SOURCE_SLUG}
    else:
        candidates = set(sources)

    latest_by_slug = {
        row["source__slug"]: row["latest"]
        for row in Offer.objects.filter(
            tmdb_id=tmdb_id, source__slug__in=candidates
        )
        .values("source__slug")
        .annotate(latest=Max("last_seen"))
    }
    moment = _now(now)
    return sorted(
        slug
        for slug in candidates
        if _is_stale_at(slug, latest_by_slug.get(slug), now=moment)
    )


def _is_stale_at(slug: str, latest: Optional[datetime], *, now=None) -> bool:
    if latest is None:
        return True
    return _now(now) - _aware(latest) > freshness_for(slug)


def _aware(value: datetime) -> datetime:
    """Naive datetimes are interpreted in the active timezone (as elsewhere)."""
    if timezone.is_naive(value):
        return timezone.make_aware(value)
    return value


def _now(now: Optional[datetime]) -> datetime:
    return timezone.now() if now is None else _aware(now)
