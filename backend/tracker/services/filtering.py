"""Composable filtering logic for Catalog, Homepage, and Wishlist."""
from __future__ import annotations

import logging
from typing import Optional

from django.db.models import Q, QuerySet
from tracker.models import Movie, Preference
from tracker.services.availability_reads import get_local_offers, get_local_offers_bulk
from tracker.services.access import build_explicit_access, build_user_access
from tracker.models import Provider
from tracker.services.watchability import (
    WatchabilityStatus,
    evaluate_watchability,
)

logger = logging.getLogger(__name__)


def apply_composable_filters(
    queryset: QuerySet[Movie] | list[Movie],
    params: dict,
    preference: Preference,
    user_subscriptions: Optional[set[str]] = None,
    current_country: Optional[str] = None,
    allowed_provider_slugs: Optional[set[str]] = None,
    refresh_states: Optional[dict[int, str]] = None,
) -> tuple[list[dict], int]:
    """Filter Movie queryset or list using AND semantics for structured parameters & watchability.

    ``refresh_states`` (movie id -> ``RefreshState``, from
    ``refresh_requests.refresh_states_for``) lets the caller tell the read
    path *what the refresh queue knows* about each title: with no offers
    stored, a queued title evaluates to 🔵 Checking…, a checked one to ⚫
    Not available and only a never-fetched one to ⚪ Unknown. Omitted (or a
    missing id) keeps today's behavior: no offers = Unknown.
    """
    # 1. Text Search (Title/Overview)
    q = (params.get("q") or params.get("query") or "").strip()
    if q:
        if isinstance(queryset, QuerySet):
            queryset = queryset.filter(Q(title__icontains=q) | Q(overview__icontains=q))
        else:
            q_lower = q.lower()
            queryset = [
                m for m in queryset
                if q_lower in (m.title or "").lower() or q_lower in (m.overview or "").lower()
            ]

    # 2. Content Type (movie vs series)
    content_type = (params.get("content_type") or params.get("type") or "").lower().strip()
    if content_type in ("movie", "series", "tv"):
        ct = "series" if content_type == "tv" else content_type
        if isinstance(queryset, QuerySet):
            queryset = queryset.filter(content_type=ct)
        else:
            queryset = [
                m for m in queryset
                if getattr(m, "content_type", "movie") == ct
            ]

    # 3. Year / Year Range
    year_min = params.get("year_min") or params.get("year_start")
    year_max = params.get("year_max") or params.get("year_end")
    year_exact = params.get("year")
    if year_exact and str(year_exact).isdigit():
        y = int(year_exact)
        if isinstance(queryset, QuerySet):
            queryset = queryset.filter(year=y)
        else:
            queryset = [m for m in queryset if m.year == y]
    else:
        if year_min and str(year_min).isdigit():
            ymin = int(year_min)
            if isinstance(queryset, QuerySet):
                queryset = queryset.filter(year__gte=ymin)
            else:
                queryset = [m for m in queryset if m.year is not None and m.year >= ymin]
        if year_max and str(year_max).isdigit():
            ymax = int(year_max)
            if isinstance(queryset, QuerySet):
                queryset = queryset.filter(year__lte=ymax)
            else:
                queryset = [m for m in queryset if m.year is not None and m.year <= ymax]

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

    # 6. Score / Rating Filter
    score_min = params.get("score_min") or params.get("min_score") or params.get("rating_min")
    if score_min is not None and str(score_min).strip() != "":
        try:
            min_val = float(score_min)
            if isinstance(queryset, QuerySet):
                queryset = queryset.filter(tmdb_rating__gte=min_val)
            else:
                queryset = [m for m in queryset if getattr(m, "tmdb_rating", None) is not None and getattr(m, "tmdb_rating") >= min_val]
        except (ValueError, TypeError):
            pass

    # Convert queryset to list if it was evaluated
    movie_list = list(queryset)

    # 6. Evaluate Watchability & Availability filters per item.
    # ``user_subscriptions`` is an explicit override for tests/callers that
    # simulate a user; when omitted, ``evaluate_watchability`` builds the
    # access map from ``Subscription`` rows — the same path the detail page
    # uses, so list surfaces cannot disagree with it.

    raw_providers = params.get("providers") or params.get("provider") or params.get("service") or ""
    provider_slug_set = None
    if allowed_provider_slugs is not None:
        provider_slug_set = {s.lower() for s in allowed_provider_slugs}
    elif raw_providers:
        if isinstance(raw_providers, list):
            provider_slug_set = {s.strip().lower() for p in raw_providers for s in str(p).split(",") if s.strip()}
        elif isinstance(raw_providers, str) and raw_providers.strip():
            provider_slug_set = {s.strip().lower() for s in raw_providers.split(",") if s.strip()}

    country_filter = (params.get("country") or "").upper().strip()
    status_filter = (params.get("availability_status") or params.get("status") or "").lower().strip()
    watchable_only = params.get("watchable") in ("true", "1") or params.get("watchable_only") in ("true", "1")
    raw_quality = params.get("quality") or ""
    quality_filter_set = None
    if raw_quality:
        if isinstance(raw_quality, list):
            quality_filter_set = {q.strip().lower() for item in raw_quality for q in str(item).split(",") if q.strip()}
        elif isinstance(raw_quality, str) and raw_quality.strip():
            quality_filter_set = {q.strip().lower() for q in raw_quality.split(",") if q.strip()}

    # Access-type filter: subscription | addon | free | free_with_ads | rent | buy
    raw_access = params.get("access") or params.get("access_type") or ""
    access_filter_set = None
    if raw_access:
        if isinstance(raw_access, list):
            values = [a for item in raw_access for a in str(item).split(",")]
        else:
            values = str(raw_access).split(",")
        values = {v.strip().lower() for v in values if v.strip()}
        if values and "all" not in values:
            access_filter_set = values

    # The canonical reconciliation read has four bulk lookups behind it.  Run
    # it once for the title set, and share entitlement/provider context across
    # every classification instead of issuing the same two queries per title.
    try:
        offers_by_movie = get_local_offers_bulk(movie_list)
    except Exception:  # noqa: BLE001 - a report must degrade to per-title reads
        logger.exception("Bulk canonical offer read failed; falling back to isolated reads")
        offers_by_movie = {}
        for movie in movie_list:
            try:
                offers_by_movie[movie.id] = get_local_offers(movie)
            except Exception:  # noqa: BLE001 - one corrupt title must not hide peers
                logger.exception("Offer read failed for movie %s", movie.tmdb_id)
                offers_by_movie[movie.id] = []
    access = (
        build_explicit_access(user_subscriptions)
        if user_subscriptions is not None
        else build_user_access(getattr(preference, "user_id", 1) or 1)
    )
    provider_map = {provider.slug: provider for provider in Provider.objects.all()}

    evaluated_items = []
    for m in movie_list:
        offers = offers_by_movie.get(m.id, [])
        watch_res = evaluate_watchability(
            m,
            offers,
            preference,
            current_country=current_country,
            user_subscriptions=user_subscriptions,
            refresh_state=(refresh_states or {}).get(m.id),
            access=access,
            provider_map=provider_map,
        )

        # Apply Provider Filter
        if provider_slug_set:
            all_services = {
                (getattr(o, "service", None) or (o.get("service") if isinstance(o, dict) else "") or "").lower()
                for o in offers
            }
            if not any(ps in s or s in ps for ps in provider_slug_set for s in all_services):
                continue

        # Apply Country Filter
        if country_filter:
            all_countries = {
                (getattr(o, "country", None) or (o.get("country") if isinstance(o, dict) else "") or "").upper()
                for o in offers
            }
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
            if status_filter in ("watchable", "watchable_any") and watch_res.status not in (
                WatchabilityStatus.WATCHABLE_HERE,
                WatchabilityStatus.WATCHABLE_VPN,
            ):
                continue
            elif status_filter == "watchable_here" and watch_res.status != WatchabilityStatus.WATCHABLE_HERE:
                continue
            elif status_filter == "watchable_vpn" and watch_res.status != WatchabilityStatus.WATCHABLE_VPN:
                continue
            elif status_filter == "not_watchable" and watch_res.status != WatchabilityStatus.NOT_WATCHABLE:
                continue

        # Apply Access-Type Filter (subscription/addon/free/free_with_ads/rent/buy)
        if access_filter_set:
            present_types = {
                str(t).lower() for t in (watch_res.access_flags or {}).get("present_access_types", [])
            }
            if not (present_types & access_filter_set):
                continue

        # Apply Quality Filter
        if quality_filter_set:
            movie_qualities = {
                (getattr(o, "quality", None) or (o.get("quality") if isinstance(o, dict) else "") or "").lower()
                for o in offers
            }
            if not any(q in quality_filter_set for q in movie_qualities):
                continue

        evaluated_items.append({
            "movie": m,
            # Kept internal to list/report callers; it is the exact offer set
            # used for the verdict, so a caller never needs to reconcile the
            # same title a second time just to build summary fields.
            "offers": offers,
            "watchability": watch_res,
        })

    # Sort results
    sort_by = (params.get("sort_by") or params.get("sort") or "").lower().strip()
    if sort_by:
        if sort_by in ("rating_desc", "rating_high"):
            evaluated_items.sort(key=lambda x: (getattr(x["movie"], "tmdb_rating", None) or -1, getattr(x["movie"], "id", 0) or 0), reverse=True)
        elif sort_by in ("rating_asc", "rating_low"):
            evaluated_items.sort(key=lambda x: (getattr(x["movie"], "tmdb_rating", None) if getattr(x["movie"], "tmdb_rating", None) is not None else 999, getattr(x["movie"], "id", 0) or 0))
        elif sort_by in ("year_desc", "year_newest"):
            evaluated_items.sort(key=lambda x: (getattr(x["movie"], "year", None) or -1, getattr(x["movie"], "id", 0) or 0), reverse=True)
        elif sort_by in ("year_asc", "year_oldest"):
            evaluated_items.sort(key=lambda x: (getattr(x["movie"], "year", None) if getattr(x["movie"], "year", None) is not None else 9999, getattr(x["movie"], "id", 0) or 0))
        elif sort_by in ("title_asc", "title"):
            evaluated_items.sort(key=lambda x: (getattr(x["movie"], "title", "") or "").lower())
        elif sort_by in ("title_desc", "-title"):
            evaluated_items.sort(key=lambda x: (getattr(x["movie"], "title", "") or "").lower(), reverse=True)

    return evaluated_items, len(evaluated_items)
