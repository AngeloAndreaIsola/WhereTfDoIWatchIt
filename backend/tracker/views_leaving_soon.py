"""Read-only, evidence-gated departures for the current owner's watchlist.

An offer's recorded end date is *not* proof that a service is leaving: the
date must agree after reconciliation and be supported by a fresh, positive,
non-JustWatch observation of the same offer. No refreshes or writes occur.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from availability.domain.enums import AvailabilityEvidence, ConflictKind, ContentType
from availability.services.query import AvailabilityQueryService
from tracker.management.commands.seed_catalog import ALL_ISO_COUNTRIES
from tracker.models import Provider, WatchlistItem
from tracker.services.access import (
    build_reach,
    build_user_access,
    classify_offer,
)
from tracker.services.availability_reads import _tracker_offer
from tracker.services.preferences import build_preference


UNSAFE_CONFLICTS = frozenset({
    ConflictKind.AVAILABILITY,
    ConflictKind.MAPPING,
    ConflictKind.WINDOW,
    ConflictKind.OFFER_TYPE,
})


def _utc_iso(value: datetime) -> str:
    return value.astimezone(dt_timezone.utc).isoformat()


def _source_declares_window(source, content_type):
    # PBS computes group windows from asset dates. Tubi copies the series
    # date to season rows. Neither declares an expiry at those scopes.
    if source == "pbs":
        return content_type == ContentType.EPISODE
    if source == "tubi" and content_type == ContentType.SEASON:
        return False
    return bool(source) and not source.startswith("justwatch")


def _confirmed_expiry(offer, now: datetime):
    """Return (expiry, supporting observations), or (None, ()) when unproven.

    Reconciliation may select an end date reported only by JustWatch while a
    different source verifies *presence*. Those separate facts must never be
    combined into a confirmed departure.
    """
    expiry = offer.available_until
    if (
        not offer.currently_available
        or offer.evidence != AvailabilityEvidence.VERIFIED
        or expiry is None
        or expiry <= now
        or any(conflict.kind in UNSAFE_CONFLICTS for conflict in offer.conflicts)
    ):
        return None, ()

    supporters = tuple(
        observation
        for observation in offer.observations
        if observation.available_until == expiry
        and observation.evidence == AvailabilityEvidence.VERIFIED
        and not observation.mapping_doubt
        and _source_declares_window(observation.source.lower(), offer.content_type)
    )
    return (expiry, supporters) if supporters else (None, ())


def _legacy_or_authenticated_owner(request) -> int:
    """Keep the current single-owner deployment configurable until #96.

    Never accept a caller-provided user ID. Authenticated sessions use their
    own identity; anonymous clients follow the existing legacy installation
    owner's ID, configurable rather than repeated throughout query paths.
    """
    principal = getattr(request, "user", None)
    if getattr(principal, "is_authenticated", False):
        return int(principal.pk)
    legacy_owner = getattr(settings, "WTDW_LEGACY_OWNER_ID", None)
    if legacy_owner is None:
        legacy_owner = WatchlistItem._meta.get_field("user_id").get_default()
    return int(legacy_owner)


def build_leaving_soon(*, user_id: int, country: str, days: int = 30, now=None) -> dict:
    """Read a country-bounded, watchlist-only departure snapshot in bulk."""
    now = now or timezone.now()
    horizon_end = now + timedelta(days=days)

    # Duplicate source memberships are historical legacy data, not distinct
    # titles. Do not change those records while producing the read-only view.
    rows = WatchlistItem.objects.filter(
        user_id=user_id, movie__is_quarantined=False
    ).select_related("movie").order_by("movie_id", "id")
    movies_by_pk = {row.movie_id: row.movie for row in rows}
    movies = list(movies_by_pk.values())
    keys_by_movie = {}
    for movie in movies:
        kinds = (movie.content_type,)
        if movie.content_type == ContentType.SERIES:
            kinds = (ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE)
        keys_by_movie[movie.pk] = [(movie.tmdb_id, kind) for kind in kinds]
    pairs = [key for keys in keys_by_movie.values() for key in keys]
    reconciled = AvailabilityQueryService().get_reconciled_for_contents(pairs, now=now)

    preference = build_preference(user_id)
    access = build_user_access(user_id)
    reach = build_reach(preference, current_country=preference.current_country or country)
    providers = {provider.slug: provider for provider in Provider.objects.all()}

    items = []
    confirmed_offer_count = 0
    confirmed_title_count = 0
    for movie in movies:
        raw = (offer for key in keys_by_movie[movie.pk] for offer in reconciled.get(key, ()))
        accessible = []
        for offer in raw:
            if (
                offer.country.upper() != country
                or not offer.currently_available
                or offer.evidence != AvailabilityEvidence.VERIFIED
            ):
                continue
            classified = classify_offer(
                _tracker_offer(offer), access=access, preference=preference,
                reach=reach, provider_map=providers,
            )
            if classified.is_accessible and classified.is_qualifying_offer_type:
                expiry, supporters = _confirmed_expiry(offer, now)
                accessible.append((offer, classified, expiry, supporters))

        dated = [row for row in accessible if row[2] is not None]
        confirmed_offer_count += len(dated)
        if dated:
            confirmed_title_count += 1

        departures = []
        for offer, classified, expiry, supporters in dated:
            if expiry > horizon_end:
                continue

            # A missing, disputed or unsupported expiration on an alternate
            # accessible offer means we cannot claim this is the last option.
            # Unnumbered offers can overlap the departing scope. Two distinct
            # numbered seasons or episodes cannot substitute for each other.
            alternatives_later = any(
                (other_expiry is None or other_expiry > expiry)
                and (other.season_number is None or offer.season_number is None
                     or other.season_number == offer.season_number)
                and (other.episode_number is None or offer.episode_number is None
                     or other.episode_number == offer.episode_number)
                for other, _class, other_expiry, _support in accessible
            )
            departure_kind = "leaving_provider" if alternatives_later else "last_option"
            departures.append({
                "provider": offer.provider,
                "provider_display": classified.service_display,
                "country": country,
                "offer_type": offer.offer_type,
                "access_type": classified.access_type,
                "locality": classified.locality,
                "season_number": offer.season_number,
                "episode_number": offer.episode_number,
                "available_until": _utc_iso(expiry),
                "last_verified_at": _utc_iso(max(obs.last_seen for obs in supporters)),
                "sources": sorted({obs.source for obs in supporters}),
                "evidence": "verified",
                "departure_kind": departure_kind,
            })

        if departures:
            departures.sort(key=lambda row: (
                row["available_until"], row["provider"],
                -1 if row["season_number"] is None else row["season_number"],
                -1 if row["episode_number"] is None else row["episode_number"],
            ))
            items.append({
                "movie": {
                    "tmdb_id": movie.tmdb_id,
                    "content_type": movie.content_type,
                    "title": movie.title,
                    "year": movie.year,
                    "poster_path": movie.poster_path,
                },
                "leaving_offers": departures,
            })

    items.sort(key=lambda item: (
        item["leaving_offers"][0]["available_until"],
        item["movie"]["title"].casefold(),
        item["movie"]["content_type"],
        item["movie"]["tmdb_id"],
    ))
    if not confirmed_offer_count:
        coverage_status = "no_confirmed_expiry_data"
    elif not items:
        coverage_status = "no_departures_within_horizon"
    else:
        coverage_status = "partial_confirmed_departures"
    return {
        "country": country,
        "horizon": {"days": days, "as_of": _utc_iso(now), "until": _utc_iso(horizon_end)},
        "items": items,
        "coverage": {
            "status": coverage_status,
            "watchlist_titles": len(movies),
            "titles_with_confirmed_expiry_data": confirmed_title_count,
            "offers_with_confirmed_expiry_data": confirmed_offer_count,
            "scope": "selected_country_watchlist_only",
            "completeness": "partial_not_all_providers_report_expiry",
        },
    }


@api_view(["GET"])
def leaving_soon(request):
    raw_country = request.query_params.get("country", "")
    country = raw_country.strip().upper()
    if (len(request.query_params.getlist("country")) != 1
            or not raw_country.isascii() or country not in ALL_ISO_COUNTRIES):
        return Response({"error": "country must be an explicit ISO 3166-1 alpha-2 code"}, status=400)

    raw_days = request.query_params.get("days", "30")
    if not re.fullmatch(r"[1-9][0-9]?", raw_days or "") or int(raw_days) > 90:
        return Response({"error": "days must be an integer between 1 and 90"}, status=400)

    owner_id = _legacy_or_authenticated_owner(request)
    if owner_id <= 0:
        return Response({"error": "no valid watchlist owner"}, status=403)
    return Response(build_leaving_soon(
        user_id=owner_id, country=country, days=int(raw_days)
    ))
