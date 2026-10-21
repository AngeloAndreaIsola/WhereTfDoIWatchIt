"""Watchmode availability source: second aggregator, kept strictly secondary.

Role in the hierarchy
---------------------
JustWatch and Watchmode are both *aggregators*: they report where a title
streams without being the service that streams it. Both are added as broad
fallback coverage for the gaps the first-party collectors cannot reach, and
both are permanently non-authoritative for the same reason — an aggregator's
silence is not evidence of unavailability. Neither replaces a collector, and
neither is the primary source.

Deliberately *not* pretending to be global
------------------------------------------
Watchmode's coverage is bounded by the account's plan. Measured on the live
key: 2500 requests/month quota, 1000/hour, and **only NL, CA and JP regions
enabled** — an unenabled region answers HTTP 400 ``"US is not enabled for
your current plan."``. That is surfaced as
:class:`~.watchmode_client.WatchmodeRegionUnsupported`, an explicit *unsupported*
outcome, never as an empty availability result. :data:`DEFAULT_REGIONS` is
therefore Watchmode-specific and constrains nothing else: JustWatch and the
direct collectors keep their own scopes.

Why it is title-targeted, not a catalogue walk
---------------------------------------------
Watchmode has no catalogue/discovery operation (only ``/sources/``,
``/search/`` and ``/title/{id}/sources/``), so it is registered as a
title-targeted source with ``supports_catalogue_sync = False``. Its quota is
small enough that spending it walking a catalogue would starve the titles the
user actually watches. It composes with the catalogue sources through the
canonical model, not through fanning out over every title twice.

Deactivation contract
---------------------
``supports_authoritative_snapshots = False``: a Watchmode run can only add or
refresh rows. Its absence can never deactivate a collector's offer, and a
failed run raises before any write so it cannot be recorded as "succeeded with
nothing found".

Provider mapping
----------------
Watchmode identifies providers by numeric ``source_id`` against its own
``/sources/`` catalogue and by display ``name``. Ids are stable per provider
across regions, so :data:`PROVIDER_NAMES` maps the canonical provider *name*
to our slug and the mapping is recorded as a country-agnostic
``ExternalProviderMapping`` row (``country=""``), which is the pre-existing
convention for ids that do not vary by market. An unknown provider is logged
and skipped — it never creates a ``Provider`` row.

Content identity
----------------
``source_id`` identifies the *provider*, not the title, so it can never be the
whole ``ProviderContent`` identity: doing that collapsed every title at a
provider into one row (see :mod:`..services.watchmode_identity`). The identity
is provider id + content type + the requested TMDb id.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
    normalize_country,
    normalize_languages,
    normalize_offer_type,
)
from .base import ContentRequest, SourceCapabilities, SourceFetchError, SourceFetchResult
from .watchmode_client import (
    WatchmodeClient,
    WatchmodeError,
    WatchmodeRegionUnsupported,
)
from ..services.watchmode_identity import build_watchmode_content_id, slug

logger = logging.getLogger(__name__)

#: Regions enabled on the current Watchmode plan (measured). Watchmode-specific
#: by design: it constrains this source only, and is overridable per call.
DEFAULT_REGIONS = ("NL", "CA", "JP")

#: Watchmode's ``type`` vocabulary -> our ``OfferType``. It is *not* the
#: generic ``normalize_offer_type`` vocabulary (which speaks
#: subscription/free/rent/buy); Watchmode speaks ``sub``/``rent``/``buy``/
#: ``free``, so it is mapped explicitly rather than pattern-matched.
WATCHMODE_OFFER_TYPES = {
    "sub": "subscription",
    "subscription": "subscription",
    "free": "free",
    "rent": "rent",
    "rental": "rent",
    "buy": "buy",
}

#: Watchmode provider display name -> canonical provider slug. Only brands this
#: project already tracks appear here: an unmapped name is skipped, never turned
#: into a new Provider row. Lookup is case-insensitive.
PROVIDER_NAMES = {
    "netflix": "netflix",
    "amazon prime video": "prime",
    "amazon video": "prime",
    "disney+": "disney",
    "disney plus": "disney",
    "apple tv+": "apple",
    "apple tv plus": "apple",
    "hbo max": "max",
    "max": "max",
    "paramount+": "paramount",
    "paramount plus": "paramount",
    "peacock": "peacock",
    "peacock premium": "peacock",
    "hulu": "hulu",
    "crunchyroll": "crunchyroll",
    "youtube tv": "youtubetv",
    "tubi": "tubi",
    "tubi tv": "tubi",
    "pluto tv": "pluto_tv",
    "pbs": "pbs",
    "abc": "abc_iview",
    "sbs on demand": "sbs_ondemand",
    "rai play": "raiplay",
    "raiplay": "raiplay",
    "mediaset infinity": "mediaset_infinity",
    "bbc iplayer": "bbc_iplayer",
    "npo start": "npo_start",
}

#: Watchmode source id -> provider slug, where the id was observed directly.
#: Ids are stable per provider, so this is a country-agnostic table; the name
#: table above remains the fallback for providers discovered by name.
PROVIDER_IDS: dict[int, str] = {
    203: "netflix",  # confirmed live: Netflix
}


class WatchmodeSource:
    """Title-targeted aggregator source over the Watchmode API."""

    slug = "watchmode"
    name = "Watchmode"
    #: Bumped with the per-title identity format (see
    #: ``services.watchmode_identity``): offers written before the bump carry
    #: the collapsed ``wm:<provider id>`` identity.
    SOURCE_VERSION = "watchmode-v2"
    source_type = SourceType.AGGREGATOR
    capabilities = SourceCapabilities(
        supported_providers=None,
        # Watchmode-specific and plan-scoped: only these regions are readable
        # on the current account. A request outside them is an explicit
        # unsupported outcome, not an empty result.
        supported_countries=frozenset(DEFAULT_REGIONS),
        supported_content_types=frozenset({ContentType.MOVIE, ContentType.SERIES}),
        supports_incremental_sync=False,
        # Title-targeted only: ``fetch(content=...)`` searches one title.
        supports_title_fetch=True,
        # No catalogue/discovery operation exists in the API.
        supports_catalogue_sync=False,
        supports_authoritative_snapshots=False,
        supports_tmdb_ids=True,
        supports_seasons=False,
        supports_episodes=False,
        # Watchmode source rows carry no language metadata (measured), so the
        # source says so instead of implying an empty list.
        supports_languages=False,
    )

    def __init__(self, client=None, *, regions=None):
        self.client = client or WatchmodeClient()
        self.regions = tuple(regions or DEFAULT_REGIONS)

    # -- interface --------------------------------------------------------
    def fetch(self, content=None, *, provider=None, country=None, cursor=None
              ) -> SourceFetchResult:
        if content is None:
            raise ValueError(
                "watchmode is title-targeted: the Watchmode API has no "
                "catalogue operation, so a content request is required"
            )
        request = content if isinstance(content, ContentRequest) else ContentRequest.from_content(content)

        # An explicit country narrows the request when it is one this plan
        # covers; otherwise the plan's own enabled regions are used. Never
        # inferred from IP/VPN/locale.
        regions = self._regions_for(country)
        self._guard_regions(regions)

        content_type = ContentType.SERIES if str(
            getattr(request, "content_type", "movie")
        ).lower() in ("series", "show", "tv") else ContentType.MOVIE
        tmdb_type = "tv" if content_type == ContentType.SERIES else "movie"

        try:
            matches = self.client.find_title(
                tmdb_id=request.tmdb_id, tmdb_type=tmdb_type,
                imdb_id=getattr(request, "imdb_id", None),
                name=request.title or None,
            )
        except WatchmodeRegionUnsupported:
            raise
        except WatchmodeError as exc:
            raise SourceFetchError(f"watchmode search failed: {exc}") from exc

        if not matches:
            logger.info("watchmode_title_not_found title=%r tmdb_id=%s",
                        request.title, request.tmdb_id)
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        title = matches[0]
        try:
            rows = self.client.title_sources(title.id, regions)
        except WatchmodeRegionUnsupported:
            raise
        except WatchmodeError as exc:
            raise SourceFetchError(f"watchmode title_sources failed: {exc}") from exc

        return self._to_result(rows, request=request, content_type=content_type)

    # -- translation ------------------------------------------------------
    def _to_result(self, rows, *, request, content_type):
        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        seen_contents: set[str] = set()

        for row in rows:
            provider_slug = self._provider_slug(row)
            if not provider_slug:
                logger.debug(
                    "watchmode_provider_unmapped name=%r source_id=%s region=%s",
                    row.name, row.source_id, row.region,
                )
                continue
            region = normalize_country(row.region)
            provider_content_id = self._content_id(
                row, request=request, content_type=content_type
            )
            if provider_content_id not in seen_contents:
                seen_contents.add(provider_content_id)
                contents.append(DiscoveredContent(
                    provider_id=provider_slug,
                    provider_content_id=provider_content_id,
                    content_type=content_type,
                    title=request.title,
                    external_ids=({"tmdb": str(request.tmdb_id)} if request.tmdb_id else {}),
                    source_id=self.slug,
                ))
            offers.append(ProviderOffer(
                provider_id=provider_slug,
                provider_content_id=provider_content_id,
                country=region,
                content_type=content_type,
                tmdb_id=request.tmdb_id,
                season_number=None,
                episode_number=None,
                offer_type=self._offer_type(row),
                provider_url=row.web_url,
                quality=(row.format or "").strip().lower(),
                # No language metadata exists on this payload: None (unknown),
                # never [] (known-empty).
                audio_languages=None,
                subtitle_languages=None,
                source_record_id=str(row.source_id or ""),
                source_version=self.SOURCE_VERSION,
            ))
        return SourceFetchResult(
            contents=contents, offers=offers, sync_mode=SyncMode.TARGETED,
        )

    @staticmethod
    def _offer_type(row):
        """Watchmode ``type`` -> canonical offer type, preserving unknown values."""
        mapped = WATCHMODE_OFFER_TYPES.get(str(row.type or "").strip().lower())
        if mapped:
            return normalize_offer_type(mapped)
        # Unknown monetization: record it as UNKNOWN rather than dropping the
        # offer or guessing a subscription.
        logger.info("watchmode_unknown_offer_type type=%r name=%r",
                    row.type, row.name)
        return normalize_offer_type("")

    @staticmethod
    def _provider_slug(row):
        if row.source_id in PROVIDER_IDS:
            return PROVIDER_IDS[row.source_id]
        return PROVIDER_NAMES.get(str(row.name or "").strip().lower(), "")

    @staticmethod
    def _content_id(row, *, request, content_type):
        """Stable provider-native id for one Watchmode source row.

        ``row.source_id`` is Watchmode's **provider** id (203 = Netflix,
        296 = Tubi), not a content id: using it alone collapsed every title
        the provider carried into one ``ProviderContent`` row, so offers for
        different films shared an identity and were hidden as "mapping
        doubt". The discriminator must therefore be the requested content,
        and the region is deliberately absent (one provider content row can
        have offers in several regions).
        """
        return build_watchmode_content_id(
            row.source_id or f"name-{slug(str(row.name or 'unknown'))}",
            content_type,
            tmdb_id=getattr(request, "tmdb_id", None),
            imdb_id=getattr(request, "imdb_id", None),
            title=getattr(request, "title", "") or "",
            year=getattr(request, "year", None),
        )

    # -- regions ----------------------------------------------------------
    def _regions_for(self, country) -> list[str]:
        """Regions to ask for, from the explicit country when it is supported."""
        if country:
            requested = normalize_country(country)
            if requested in self.regions:
                return [requested]
            # An unsupported explicit country is a caller error, not something
            # to silently substitute a different market for.
            raise WatchmodeRegionUnsupported([requested])
        return list(self.regions)

    def _guard_regions(self, regions):
        allowed = set(self.regions)
        rejected = [r for r in regions if r not in allowed]
        if rejected:
            raise WatchmodeRegionUnsupported(rejected)

    def quota_state(self) -> dict:
        """Last-observed quota/rate-limit headers, for run diagnostics."""
        return dict(getattr(self.client, "last_quota", {}) or {})