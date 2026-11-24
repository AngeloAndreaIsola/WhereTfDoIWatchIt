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

from django.db.models import Max, Q
from django.utils import timezone

from .domain.enums import FreshnessState, SyncMode
from .models import Offer, Source

FRESHNESS_INTERVAL_KEY = "expected_refresh_interval_seconds"
TITLE_REFRESH_INTERVAL_KEY = "title_refresh_interval_seconds"
TITLE_AGGREGATORS = frozenset({"justwatch", "watchmode"})
TITLE_REFRESH_INTERVAL = timedelta(days=7)

#: Fallback window for a slug that declares nothing (policy entry and
#: configuration alike). Only reachable for unregistered/test slugs: every
#: source the application can write offers under is listed below.
DEFAULT_FRESHNESS = timedelta(hours=24)

#: How long a catalogue walk may go without *forward progress* before it counts
#: as stalled. Deliberately separate from ``SOURCE_FRESHNESS``: that answers
#: "is this observation still good enough to skip a refresh", this answers "has
#: a walk that is supposed to be moving actually stopped".
#:
#: Sized from the slowest documented cadence: the catalogue cron runs daily
#: (``docs/CRON.md``) and the longest walks are bounded per run (RaiPlay ~840
#: requests, Prime/Tubi 500 pages), so a healthy walk advances on every daily
#: run. Two missed runs' worth of slack is enough to absorb a skipped tick or a
#: transient upstream outage without crying wolf, while still catching a walk
#: that has genuinely wedged well inside a day.
STALL_AFTER = timedelta(hours=48)

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
    # Phase E sources: the targeted ones ride the daily title-targeted sweep
    # (03:17), the catalogue ones a daily ``refresh_provider_catalogue`` slot
    # (see docs/CRON.md for the exact slot per source) -> all 24h windows.
    "youtube": timedelta(hours=24),
    "pluto_tv": timedelta(hours=24),
    "mediaset_infinity": timedelta(hours=24),
    "pbs": timedelta(hours=24),
    "abc_iview": timedelta(hours=24),
    "public_domain_archive": timedelta(days=1),
    "legacy_scraper": timedelta(hours=12),
    # Aggregators: unofficial/fragile upstream, so their windows are longer
    # than a first-party collector's. JustWatch is walked by
    # ``refresh_provider_catalogue`` (12h) and Watchmode by a targeted sweep
    # (24h, its quota is monthly). Neither is on the metered
    # ``streaming_availability`` budget.
    "justwatch": timedelta(hours=12),
    "watchmode": timedelta(hours=24),
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
    return _configured_interval(configuration, FRESHNESS_INTERVAL_KEY) or SOURCE_FRESHNESS.get(slug)


def _configured_interval(configuration, key):
    config = configuration if isinstance(configuration, Mapping) else {}
    raw = config.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        interval = timedelta(seconds=raw)
    except (OverflowError, TypeError, ValueError):
        return None
    return interval if interval > timedelta(0) else None


def title_refresh_interval(slug: str, configuration=None) -> timedelta:
    """Operational cache lifetime for a title check, separate from offer evidence."""
    if slug in TITLE_AGGREGATORS:
        configured = _configured_interval(configuration, TITLE_REFRESH_INTERVAL_KEY)
        return min(configured, TITLE_REFRESH_INTERVAL) if configured else TITLE_REFRESH_INTERVAL
    return freshness_for(slug, configuration)


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


def source_configuration_snapshot(slugs=None) -> dict[str, Mapping]:
    """Read effective freshness configuration for a scheduler batch once.

    Missing Source rows intentionally map to the code policy. This helper is
    read-only and never creates, enables, or otherwise activates a source.
    Callers processing multiple titles should capture one snapshot and pass it
    through their per-title decisions to avoid an N+1 configuration query.
    """
    query = Source.objects.all()
    if slugs is not None:
        query = query.filter(slug__in=set(slugs))
    return {
        slug: configuration if isinstance(configuration, Mapping) else {}
        for slug, configuration in query.values_list("slug", "configuration")
    }


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


def _normalised_country(country: Optional[str]) -> Optional[str]:
    """Return an explicit ISO country code, or ``None`` for legacy-global use."""
    if country is None:
        return None
    country = country.strip().upper()
    return country or None


def source_last_seen(
    tmdb_id,
    source: str,
    *,
    country: Optional[str] = None,
    content_type: Optional[str] = None,
    targeted_only: bool = False,
) -> Optional[datetime]:
    """Newest matching ``Offer.last_seen`` for a source observation.

    An explicit ``country`` is a hard evidence boundary: observations in US,
    JP, or a historical global query never establish freshness for NL.  Passing
    ``country=None`` deliberately preserves the legacy global behaviour.
    ``content_type`` is likewise optional for compatibility, but callers with
    a known movie/series identity must provide it so colliding TMDb ids cannot
    contaminate each other.
    """
    query = Offer.objects.filter(tmdb_id=tmdb_id, source__slug=source)
    if (code := _normalised_country(country)) is not None:
        query = query.filter(country__code=code)
    if content_type is not None:
        query = query.filter(content_type=content_type)
    if targeted_only:
        # Legacy rows without run attribution can seed the operational cache.
        # A known catalogue observation cannot stand in for a title check.
        query = query.filter(Q(sync_run__mode=SyncMode.TARGETED) | Q(sync_run__isnull=True))
    return query.aggregate(latest=Max("last_seen"))["latest"]


def source_is_stale(
    tmdb_id,
    source: str,
    *,
    country: Optional[str] = None,
    content_type: Optional[str] = None,
    configuration: Optional[Mapping] = None,
    now: Optional[datetime] = None,
) -> bool:
    """True when source evidence for this country/content scope is stale.

    Explicit country and content scopes require evidence in that exact scope.
    Omitting either is an intentional legacy-global request, never an implicit
    fallback from an explicit scope.
    """
    return _is_stale_at(
        source,
        source_last_seen(
            tmdb_id, source, country=country, content_type=content_type
        ),
        configuration=(
            source_configuration_snapshot([source]).get(source, {})
            if configuration is None else configuration
        ),
        now=now,
    )


def stale_sources(
    tmdb_id,
    *,
    sources=None,
    country: Optional[str] = None,
    content_type: Optional[str] = None,
    source_configurations: Optional[Mapping[str, Mapping]] = None,
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

    scope_query = Offer.objects.filter(tmdb_id=tmdb_id)
    if (code := _normalised_country(country)) is not None:
        scope_query = scope_query.filter(country__code=code)
    if content_type is not None:
        scope_query = scope_query.filter(content_type=content_type)

    if sources is None:
        observed = set(
            scope_query.values_list("source__slug", flat=True).distinct()
        )
        candidates = observed | {DEFAULT_SOURCE_SLUG}
    else:
        candidates = set(sources)

    configurations = (
        source_configuration_snapshot(candidates)
        if source_configurations is None
        else source_configurations
    )

    latest_by_slug = {
        row["source__slug"]: row["latest"]
        for row in scope_query.filter(source__slug__in=candidates)
        .values("source__slug")
        .annotate(latest=Max("last_seen"))
    }
    moment = _now(now)
    return sorted(
        slug
        for slug in candidates
        if _is_stale_at(
            slug,
            latest_by_slug.get(slug),
            configuration=configurations.get(slug, {}),
            now=moment,
        )
    )


def _is_stale_at(
    slug: str,
    latest: Optional[datetime],
    *,
    configuration: Optional[Mapping] = None,
    now=None,
) -> bool:
    if latest is None:
        return True
    return _now(now) - _aware(latest) > freshness_for(slug, configuration)


def _aware(value: datetime) -> datetime:
    """Naive datetimes are interpreted in the active timezone (as elsewhere)."""
    if timezone.is_naive(value):
        return timezone.make_aware(value)
    return value


def _now(now: Optional[datetime]) -> datetime:
    return timezone.now() if now is None else _aware(now)
