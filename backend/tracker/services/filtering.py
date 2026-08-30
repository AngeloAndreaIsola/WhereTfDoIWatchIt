"""Composable filtering logic for Catalog, Homepage, and Wishlist."""
from __future__ import annotations

import logging
from typing import Optional

from django.db.models import Q, QuerySet
from tracker.models import Movie, Preference
from tracker.services.cache import AvailabilityCache
from tracker.services.watchability import (
    WatchabilityStatus,
    evaluate_watchability,
    get_user_subscriptions,
)

logger = logging.getLogger(__name__)


def apply_composable_filters(
    queryset: QuerySet[Movie],
    params: dict,
    preference: Preference,
    user_subscriptions: Optional[set[str]] = None,
    current_country: Optional[str] = None,
) -> tuple[list[dict], int]:
    """Filter Movie queryset using AND semantics for structured parameters & watchability."""
    # 1. Text Search (Title/Overview)
    q = (params.get("q") or params.get("query") or "").strip()
    if q:
        queryset = queryset.filter(Q(title__icontains=q) | Q(overview__icontains=q))

    # 2. Content Type (movie vs series)
    content_type = (params.get("content_type") or params.get("type") or "").lower().strip()
    if content_type in ("movie", "series", "tv"):
        ct = "series" if content_type == "tv" else content_type
        queryset = queryset.filter(content_type=ct)

    # 3. Year / Year Range
    year_min = params.get("year_min") or params.get("year_start")
    year_max = params.get("year_max") or params.get("year_end")
    year_exact = params.get("year")
    if year_exact and year_exact.isdigit():
        queryset = queryset.filter(year=int(year_exact))
    else:
        if year_min and year_min.isdigit():
            queryset = queryset.filter(year__gte=int(year_min))
        if year_max and year_max.isdigit():
            queryset = queryset.filter(year__lte=int(year_max))

    # 4. Genre Filter (by ID or Name)
    genre_req = params.get("genre") or params.get("genre_id")
    if genre_req:
        if str(genre_req).isdigit():
            gid = int(genre_req)
            queryset = [m for m in queryset if gid in (m.genre_ids or [])]
        else:
            g_str = str(genre_req).lower().strip()
            queryset = [
                m for m in queryset
                if any(g_str in (g.get("name") or "").lower() for g in (m.genres or []))
            ]

    # 5. Actor Filter (multi-actor AND support)
    actors = params.getlist("actor_id") if hasattr(params, "getlist") else params.get("actor_id")
    if not actors and "actor" in params:
        actors = params.getlist("actor") if hasattr(params, "getlist") else params.get("actor")
    if actors:
        if isinstance(actors, (str, int)):
            actors = [actors]
        actor_ids = [int(a) for a in actors if str(a).isdigit()]
        actor_names = [str(a).lower().strip() for a in actors if not str(a).isdigit()]

        filtered_by_actor = []
        for m in queryset:
            m_cast_ids = {c.get("id") for c in (m.cast or []) if c.get("id")}
            m_cast_names = {(c.get("name") or "").lower() for c in (m.cast or [])}

            # Must satisfy all actor_ids AND actor_names
            match_ids = all(aid in m_cast_ids for aid in actor_ids)
            match_names = all(any(an in cn for cn in m_cast_names) for an in actor_names)
            if match_ids and match_names:
                filtered_by_actor.append(m)
        queryset = filtered_by_actor

    # Convert queryset to list if it was evaluated
    movie_list = list(queryset)

    # 6. Evaluate Watchability & Availability filters per item
    cache = AvailabilityCache()
    if user_subscriptions is None:
        user_subscriptions = get_user_subscriptions(preference.user_id if preference else 1)

    provider_filter = (params.get("provider") or params.get("service") or "").lower().strip()
    country_filter = (params.get("country") or "").upper().strip()
    status_filter = (params.get("availability_status") or params.get("status") or "").lower().strip()
    watchable_only = params.get("watchable") in ("true", "1") or params.get("watchable_only") in ("true", "1")

    evaluated_items = []
    for m in movie_list:
        offers = cache._read_offers(m)
        watch_res = evaluate_watchability(
            m,
            offers,
            preference,
            current_country=current_country,
            user_subscriptions=user_subscriptions,
        )

        # Apply Provider Filter
        if provider_filter:
            all_services = {
                (o.get("service") or "").lower() for o in offers
            }
            if not any(provider_filter in s for s in all_services):
                continue

        # Apply Country Filter
        if country_filter:
            all_countries = {(o.get("country") or "").upper() for o in offers}
            if country_filter not in all_countries:
                continue

        # Apply Watchable Only filter (🟢 or 🟡)
        if watchable_only and watch_res.status not in (
            WatchabilityStatus.WATCHABLE_HERE,
            WatchabilityStatus.WATCHABLE_VPN,
        ):
            continue

        # Apply Availability Status filter
        if status_filter:
            if status_filter == "watchable_here" and watch_res.status != WatchabilityStatus.WATCHABLE_HERE:
                continue
            elif status_filter == "watchable_vpn" and watch_res.status != WatchabilityStatus.WATCHABLE_VPN:
                continue
            elif status_filter == "not_watchable" and watch_res.status != WatchabilityStatus.NOT_WATCHABLE:
                continue

        evaluated_items.append({
            "movie": m,
            "watchability": watch_res,
        })

    return evaluated_items, len(evaluated_items)
