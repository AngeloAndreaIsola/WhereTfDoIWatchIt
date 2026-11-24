"""Title refresh scheduling. Check timestamps never qualify offer evidence."""

from datetime import timedelta

from django.utils import timezone

from availability.domain.enums import ContentType
from availability.freshness import TITLE_AGGREGATORS, title_refresh_interval
from availability.models import Offer, Source
from availability.services.departures import confirmed_expiry, recorded_presence
from availability.services.query import AvailabilityQueryService
from availability.sources import DEFAULT_SOURCE_SLUG

from tracker.models import TitleAvailabilityCheck

from .source_policy import SOURCE_ROLES, AvailabilityRole

TRACKED_SOURCES = TITLE_AGGREGATORS | {DEFAULT_SOURCE_SLUG}
DEPARTURE_HORIZON = timedelta(days=7)
DEPARTURE_INTERVAL = timedelta(days=1)
ATTEMPT_COOLDOWN = timedelta(minutes=10)
PROVIDER_SOURCES = {slug for slug, role in SOURCE_ROLES.items() if role == AvailabilityRole.COLLECTOR}


def eligible_aggregator_sources(sources, country):
    """An explicitly disabled aggregator or an unsupported market is ineligible."""
    from availability.sources import source_registry

    aggregators = TITLE_AGGREGATORS.intersection(sources)
    disabled = set(Source.objects.filter(
        slug__in=aggregators, enabled=False,
    ).values_list("slug", flat=True))
    return {
        slug for slug in aggregators - disabled
        if country and (not (markets := source_registry.get(slug).capabilities.supported_countries)
                        or country in markets)
    }


def title_departures(tmdb_id, content_type, country, now):
    """Confirmed dates from title-specific, first-party evidence in this market."""
    return departures_for_titles({(tmdb_id, content_type)}, country, now).get((tmdb_id, content_type), {})


def departures_for_titles(identities, country, now):
    if not country or not identities:
        return {}
    horizon = now + DEPARTURE_HORIZON
    parents = {ContentType.SEASON: ContentType.SERIES}
    candidate_keys = set(Offer.objects.filter(
        tmdb_id__in={tmdb_id for tmdb_id, _ in identities},
        content_type__in=[ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON],
        country__code=country, source__slug__in=PROVIDER_SOURCES, currently_available=True,
        available_until__gt=now, available_until__lte=horizon,
    ).values_list("tmdb_id", "content_type").distinct())
    keys = {
        (tmdb_id, kind) for tmdb_id, kind in candidate_keys
        if (tmdb_id, parents.get(kind, kind)) in identities
    }
    if not keys:
        return {}

    offers = AvailabilityQueryService().get_reconciled_for_contents(keys, country=country, now=now)
    presence = recorded_presence(offers, country)
    dates = {}
    for (tmdb_id, kind), results in offers.items():
        for offer in results:
            if offer.available_until is None or offer.available_until > horizon:
                continue
            expiry, supporters = confirmed_expiry(offer, now, presence=presence)
            if expiry and any(obs.source in PROVIDER_SOURCES for obs in supporters):
                scopes = dates.setdefault((tmdb_id, parents.get(kind, kind)), {})
                scopes[kind] = min(expiry, scopes.get(kind, expiry))
    return dates


def departure_for_source(source, departures):
    if source not in TITLE_AGGREGATORS or not departures:
        return None
    dates = (date for kind, date in departures.items()
             if source == "justwatch" or kind != ContentType.SEASON)
    return min(dates, default=None)


def title_check_is_due(source, configuration, observed_at, check, departures, now):
    """Use successful checks for the cache and actual attempts for priority caps."""
    attempted = check.last_attempted_at if check else None
    succeeded = check.last_succeeded_at if check else None
    observed = max(filter(None, (succeeded, observed_at)), default=None)
    departure = departure_for_source(source, departures)
    if departure is not None:
        last_activity = max(filter(None, (attempted, observed)), default=None)
        if last_activity is None:
            return True
        if departure - now <= DEPARTURE_INTERVAL:
            return last_activity < departure - DEPARTURE_INTERVAL
        return now - last_activity >= DEPARTURE_INTERVAL

    if attempted is not None and now - attempted < ATTEMPT_COOLDOWN:
        return False
    return observed is None or now - observed >= title_refresh_interval(source, configuration)


def title_attempted_recently(movie, country, *, sources=TRACKED_SOURCES):
    return set(TitleAvailabilityCheck.objects.filter(
        movie=movie, tmdb_id=movie.tmdb_id, content_type=movie.content_type,
        country=country or "", source__in=sources,
        last_attempted_at__gt=timezone.now() - ATTEMPT_COOLDOWN,
    ).values_list("source", flat=True))


def record_title_attempt(movie, source, country):
    """Claim an attempt under the caller's drain lock and source budget."""
    if source not in TRACKED_SOURCES:
        return None
    now = timezone.now()
    check, _ = TitleAvailabilityCheck.objects.update_or_create(
        movie=movie, tmdb_id=movie.tmdb_id, content_type=movie.content_type,
        source=source, country=country or "",
        defaults={"last_attempted_at": now},
    )
    return check


def record_title_success(check):
    if check is not None:
        check.last_succeeded_at = timezone.now()
        check.save(update_fields=["last_succeeded_at"])
