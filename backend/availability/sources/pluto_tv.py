"""Pluto TV ON-DEMAND catalogue collector — movies + shows (Phase E).

Credential-free access to the whole on-demand catalogue through the site's
own Next.js GraphQL BFF, mirroring how the web app itself pages the grids:

    collection(slug)                      -> region + the grid carousel token
        -> carouselData(token, start/rows)  paginated grid pages (24/page)
        -> DiscoveredContent (provider-native discovery record, no offers)
        -> ProviderOffer (movie / series level)
        -> AvailabilityRefreshService (ProviderContent + ContentMapping + OfferRepository)

API contract (verified end-to-end from datacenter egress, no auth):

- ``POST https://pluto.tv/api/tn/hubs/graphql/`` accepts raw query documents
  (introspection is disabled but arbitrary text queries are executed).
  Three operations matter here: ``collection(slug)`` for
  ``movies-all-movies``/``shows-all-shows``, and ``carouselData`` for the
  grid pages. GraphQL responses use inline fragments over a content union —
  selecting ``title``/``airDate`` on the bare union fails validation.
- **The grid token must be percent-encoded** before it is sent back: the raw
  token from ``collection.hubCarousels`` is rejected with
  ``INTERNAL_SERVER_ERROR``; the site itself always replays the encoded form.
- **Non-browser User-Agent required.** Any ``Mozilla/5.0*`` UA (and obvious
  collector strings) trips a geo/bot gate from datacenter IPs and returns the
  international landing page instead of JSON; ``python-requests/2.31.0`` (and
  curl/Wget/Go clients) were verified allowed across repeated rounds, so the
  client pins that value plus ``Origin: https://pluto.tv``.
- ``collection.region`` is derived **server-side from the egress IP** and
  cannot be overridden (headers, cookies and query params were all tested and
  ignored). The boot API's ``session.countryCode`` can disagree with it —
  different GeoIP databases — so the collector trusts the response that
  actually scopes the served catalogue: ``collection.region``. The region is
  an ISO country code for every market observed (``US``); the country→path
  map below comes from Pluto's own ``seoHreflang`` payload (32 countries, one
  path region per market: ``gb_ie``, ``gsa``, ``latam`` shared by several
  countries). A region outside that map (an egress Pluto serves only its
  ``/intl/`` landing to) is a successful observation of nothing: the fetch
  returns empty and logs ``pluto_tv_unsupported_region`` — no offers are
  invented and no failure is claimed.
- Watch URLs must carry the path region or the edge 301/307-redirects
  (``/nl/movies/`` -> ``/us/movies/`` from a US-detected IP), so every URL is
  built as ``https://pluto.tv/{path_region}{href}``.

Product semantics, deliberate limitations (all observed, none guessed):

- Pluto TV is pure AVOD: catalogue presence means watchable with ads ->
  ``free_with_ads`` (upstream ``subscriptionLevel: "FREE"`` was observed in
  movie detail payloads). Rent/buy offers do not exist on this service.
- **Series-level offers only**: the grid lists every show, but episodes are
  deliberately not probed (``fullEpisodes`` would add ~1000 extra requests
  per run) — season/episode granularity stays unsupported.
- **No availability dates in grid payloads**: ``airDate`` is the content's
  own date (used for ``DiscoveredContent.release_date``), and expiry only
  exists behind per-title detail probes that are not fetched — so offers
  carry ``available_from``/``available_until`` of ``None``.
- **No title-level language metadata** (``audienceLanguage`` is site-level),
  so offers carry no languages and ``supports_languages=False``.
- **Never an authoritative snapshot**: the declared total drifts between
  runs (987 -> 988 observed within minutes) and Pluto exposes no removal
  signal, so results are non-authoritative and absence never deactivates.
- **No external ids** (``supports_tmdb_ids=False``): mapping happens in the
  refresh service via local title/year matching; an unmapped item still
  counts as discovered content. ``provider_content_id`` is the movie's
  content id or the show's URL slug — Pluto's own identities and the
  ``ContentMapping`` key.
- Pagination terminates on ``hasMore=false``, an empty page, a repeated
  page signature (declared-total drift can shift the window) or a hard
  ``MAX_PAGES`` cap; a transport/shape failure raises
  :class:`SourceFetchError` and is never reported as an empty catalogue.
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import date, datetime
from typing import Optional

import requests

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import normalize_country, normalize_offer_type
from .base import (
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
)

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://pluto.tv/api/tn/hubs/graphql/"
MOVIES_HUB = "movies-all-movies"
SHOWS_HUB = "shows-all-shows"
DEFAULT_TIMEOUT = 30
PAGE_ROWS = 24  # verified page size on both grids
# Safety net: the real grids are ~1000 items (42 pages); a runaway paging
# loop (shifting windows, server bugs) stops well before this.
MAX_PAGES = 100
SOURCE_VERSION = "pluto-graphql-v1"
# Pinned to a verified-allowed value: requests' own default UA *is* this
# string, and Pluto rejects browser-shaped UAs from datacenter IPs.
USER_AGENT = "python-requests/2.31.0"
ORIGIN = "https://pluto.tv"

# Catalogue presence = watchable with ads (Pluto is pure AVOD).
FREE_WITH_ADS = normalize_offer_type("free_with_ads")

# ISO country -> Pluto path region, from Pluto's own ``seoHreflang`` payload
# (``href`` on every country entry). The path region must match the region
# the edge detected for us or it 301/307-redirects to another market.
PATH_BY_COUNTRY = {
    "AU": "au",
    "AT": "gsa",
    "BR": "br",
    "CA": "ca",
    "CH": "gsa",
    "CL": "latam",
    "DE": "gsa",
    "DK": "dk",
    "FI": "fi",
    "FR": "fr",
    "GB": "gb_ie",
    "GT": "latam",
    "HN": "latam",
    "IE": "gb_ie",
    "IT": "it",
    "MX": "latam",
    "NI": "latam",
    "NO": "no",
    "PA": "latam",
    "PE": "latam",
    "PY": "latam",
    "SE": "se",
    "SV": "latam",
    "US": "us",
    "UY": "latam",
    "VE": "latam",
    "AR": "latam",
    "BO": "latam",
    "CO": "latam",
    "CR": "latam",
    "DO": "latam",
    "EC": "latam",
}

COLLECTION_QUERY = """
    query CollectionApiResponse($slug: String, $isChildCategory: Boolean, $extraParams: JSON) {
        collection(slug: $slug, isChildCategory: $isChildCategory, extraParams: $extraParams) {
            region
            locale
            hubId
            hubSlug
            title
            pageType
            hubCarousels {
                carouselId
                title
                token
                href
                position
                carouselType
            }
        }
    }
"""

GRID_QUERY = """
    query GetHybridCarouselData($carouselParams: CarouselParams!, $token: String!) {
        carouselData(carouselParams: $carouselParams, token: $token) {
            success
            result {
                title
                total
                rows
                skipped
                hasMore
                data {
                    contentType
                    href
                    id
                    thumb
                    tileType
                    orientation
                    ... on ShowCarouselItem {
                        title
                        description
                        genre
                        numSeasons
                        premiereDate
                    }
                    ... on MovieCarouselItem {
                        title
                        description
                        genre
                        airDate
                        movieDuration
                        movieId
                    }
                }
            }
        }
    }
"""

# Presentation fields of the grid carousel; ``start``/``rows`` are appended
# per page. Reproduces the exact variables the site's react-query cache uses.
_GRID_PARAMS = {
    "title": "",
    "position": "1",
    "model": "universalContent",
    "carouselType": "grid",
    "orientation": "portrait",
    "rankModel": "default",
    "promotionalSpot": None,
    "carouselPresentationStyle": "default",
    "isContentHighlightEnabled": False,
    "isMyList": False,
    "includeVersion": False,
}


class PlutoTvClient:
    """Thin client for Pluto TV's public hubs GraphQL BFF.

    ``requester`` is a ``(query, variables) -> dict`` callable; tests inject
    a fixture router so no test performs network I/O. Retrieval failures
    (HTTP error, transport error, GraphQL ``errors``, malformed payload)
    always surface as :class:`SourceFetchError` — never as an empty result.
    """

    def __init__(self, requester=None, timeout=DEFAULT_TIMEOUT, page_rows=PAGE_ROWS):
        self.requester = requester
        self.timeout = timeout
        self.page_rows = page_rows

    def collection(self, slug: str) -> dict:
        """One hub collection: resolved ``region`` plus its grid tokens."""
        data = self._graphql(
            COLLECTION_QUERY,
            {"slug": slug, "isChildCategory": False, "extraParams": {}},
        )
        collection = data.get("collection")
        if not isinstance(collection, dict):
            raise SourceFetchError(f"Unexpected Pluto TV collection payload for {slug!r}")
        return collection

    def grid_page(self, token: str, start: int) -> dict:
        """One page of a grid carousel; returns the ``carouselData.result``."""
        params = dict(_GRID_PARAMS, start=str(start), rows=str(self.page_rows))
        # The raw token is rejected upstream; the site always replays the
        # percent-encoded form (the same encoding its data routes use).
        data = self._graphql(
            GRID_QUERY,
            {
                "carouselParams": params,
                "token": urllib.parse.quote(token, safe=""),
            },
        )
        carousel = data.get("carouselData")
        result = carousel.get("result") if isinstance(carousel, dict) else None
        if not isinstance(result, dict):
            raise SourceFetchError(
                f"Unexpected Pluto TV grid payload for start={start}"
            )
        return result

    def _graphql(self, query: str, variables: dict) -> dict:
        try:
            if self.requester is not None:
                payload = self.requester(query, variables)
            else:
                payload = self._http(query, variables)
        except SourceFetchError:
            raise
        except requests.RequestException as exc:
            raise SourceFetchError(f"Pluto TV request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceFetchError("Unexpected Pluto TV response type")
        if payload.get("errors"):
            # Partial payloads (``data`` with null children) included: an
            # upstream error is a failure, never a quietly short catalogue.
            raise SourceFetchError(f"Pluto TV GraphQL errors: {payload['errors']!r}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SourceFetchError("Pluto TV response carried no data payload")
        return data

    def _http(self, query: str, variables: dict) -> dict:
        response = requests.post(
            GRAPHQL_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": ORIGIN,
            },
            json={"query": query, "variables": variables},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise SourceFetchError(
                f"Pluto TV returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SourceFetchError("Pluto TV returned a non-JSON body") from exc


class PlutoTvSource:
    """Catalogue-driven Pluto TV source (``content=None`` fetches only)."""

    slug = "pluto_tv"
    name = "Pluto TV"
    source_type = SourceType.API
    capabilities = SourceCapabilities(
        supported_providers=frozenset({"pluto_tv"}),
        supported_countries=frozenset(PATH_BY_COUNTRY),
        supported_content_types=frozenset({ContentType.MOVIE, ContentType.SERIES}),
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # Declared totals drift between runs and Pluto has no removal
        # signal: absence must never deactivate offers.
        supports_authoritative_snapshots=False,
        # Pluto publishes no external ids; mapping happens in the service.
        supports_tmdb_ids=False,
        # Series-level offers only; episode payloads are not probed.
        supports_seasons=False,
        supports_episodes=False,
        # No per-title language metadata exists on these endpoints.
        supports_languages=False,
    )

    def __init__(self, client=None):
        self.client = client or PlutoTvClient()

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "pluto_tv is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints: this source has one
        # provider and decides its country from the observed egress region
        # (see module docstring), so both are accepted and not acted on.
        # cursor is unused — pagination is a plain offset walk inside fetch().
        movies = self.client.collection(MOVIES_HUB)
        region = str(movies.get("region") or "").strip()
        if not region:
            raise SourceFetchError("Pluto TV collection payload carried no region")
        resolved = _resolve_region(region)
        if resolved is None:
            # Egress in a market Pluto serves only its /intl/ landing to:
            # a successful observation of an empty catalogue, not a failure.
            logger.warning(
                "pluto_tv_unsupported_region region=%s offers will not be fetched",
                region,
            )
            return SourceFetchResult(sync_mode=SyncMode.FULL)
        country_code, path_region = resolved

        items = self._walk(_grid_token(movies, MOVIES_HUB), hub=MOVIES_HUB)

        shows = self.client.collection(SHOWS_HUB)
        shows_region = str(shows.get("region") or "").strip()
        if shows_region.upper() != region.upper():
            # Same egress, so this should not happen; when it does, keep the
            # region that scoped the walk we already trusted.
            logger.warning(
                "pluto_tv_region_mismatch movies=%s shows=%s", region, shows_region
            )
        items.extend(self._walk(_grid_token(shows, SHOWS_HUB), hub=SHOWS_HUB))

        contents, offers = self._parse_items(items, country_code, path_region)
        return SourceFetchResult(
            contents=contents,
            offers=offers,
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.FULL,
            cursor=None,
        )

    # ------------------------------------------------------------------ walk

    def _walk(self, token: str, *, hub: str) -> list[dict]:
        """Page through one grid until exhausted; dedupe by item id."""
        items: list[dict] = []
        start = 0
        pages = 0
        seen_pages: set[tuple[str, ...]] = set()
        while True:
            result = self.client.grid_page(token, start)
            page = result.get("data")
            if not isinstance(page, list):
                raise SourceFetchError(
                    f"Unexpected Pluto TV grid items for {hub!r} at start={start}"
                )
            page = [item for item in page if isinstance(item, dict)]
            if not page:
                break
            signature = tuple(str(item.get("id") or "") for item in page)
            if signature in seen_pages:
                # Declared totals drift, so the paging window can shift and
                # replay a page; stop instead of looping forever.
                logger.warning("pluto_tv_repeated_page hub=%s start=%s", hub, start)
                break
            seen_pages.add(signature)
            items.extend(page)
            pages += 1
            if not result.get("hasMore"):
                break
            if pages >= MAX_PAGES:
                logger.warning(
                    "pluto_tv_page_limit hub=%s pages=%s items=%s",
                    hub, pages, len(items),
                )
                break
            start += self.client.page_rows

        deduped: dict[str, dict] = {}
        for item in items:
            deduped.setdefault(str(item.get("id") or id(item)), item)
        if len(deduped) != len(items):
            logger.warning(
                "pluto_tv_overlapping_items hub=%s raw=%s unique=%s",
                hub, len(items), len(deduped),
            )
        return list(deduped.values())

    # ----------------------------------------------------------------- parse

    def _parse_items(self, items, country_code, path_region):
        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            kind = str(item.get("contentType") or "").strip().lower()
            if kind == "movie":
                parsed = self._parse_movie(item, country_code, path_region)
            elif kind == "show":
                parsed = self._parse_show(item, country_code, path_region)
            else:
                # Live channels or a tile type this source does not model.
                logger.info(
                    "pluto_tv_unsupported_item type=%r id=%r",
                    item.get("contentType"), item.get("id"),
                )
                continue
            if parsed is None:
                continue
            content, offer = parsed
            key = (content.content_type, content.provider_content_id)
            if key in seen:
                continue
            seen.add(key)
            contents.append(content)
            offers.append(offer)
        return contents, offers

    def _parse_movie(self, item, country_code, path_region):
        item_id = str(item.get("id") or "").strip()
        href = str(item.get("href") or "").strip()
        title = str(item.get("title") or "").strip()
        if not item_id or not title or not href.startswith("/movies/"):
            logger.warning(
                "pluto_tv_unstable_item type=movie id=%r href=%r",
                item.get("id"), item.get("href"),
            )
            return None
        url = f"https://pluto.tv/{path_region}{href}"
        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=item_id,
            content_type=ContentType.MOVIE,
            title=title,
            release_date=_parse_date(item.get("airDate")),
            url=url,
            poster=str(item.get("thumb") or ""),
            description=str(item.get("description") or ""),
            external_ids={},
            source_id=self.slug,
        )
        offer = ProviderOffer(
            provider_id=self.slug,
            provider_content_id=item_id,
            country=normalize_country(country_code),
            content_type=ContentType.MOVIE,
            tmdb_id=None,
            season_number=None,
            episode_number=None,
            offer_type=FREE_WITH_ADS,
            provider_url=url,
            quality="",
            audio_languages=None,
            subtitle_languages=None,
            # airDate is the content's own date, not an availability window;
            # expiry only exists behind per-title detail probes.
            available_from=None,
            available_until=None,
            source_record_id=item_id,
            source_version=SOURCE_VERSION,
        )
        return content, offer

    def _parse_show(self, item, country_code, path_region):
        href = str(item.get("href") or "").strip()
        title = str(item.get("title") or "").strip()
        slug = href.rstrip("/")
        if slug.startswith("/shows/"):
            slug = slug[len("/shows/"):]
        if not title or not href.startswith("/shows/") or not slug or "/" in slug:
            logger.warning(
                "pluto_tv_unstable_item type=show id=%r href=%r",
                item.get("id"), item.get("href"),
            )
            return None
        url = f"https://pluto.tv/{path_region}{href}"
        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=slug,
            content_type=ContentType.SERIES,
            title=title,
            # Often empty on list items; None when Pluto has no date.
            release_date=_parse_date(item.get("premiereDate")),
            url=url,
            poster=str(item.get("thumb") or ""),
            description=str(item.get("description") or ""),
            external_ids={},
            source_id=self.slug,
        )
        offer = ProviderOffer(
            provider_id=self.slug,
            provider_content_id=slug,
            country=normalize_country(country_code),
            content_type=ContentType.SERIES,
            tmdb_id=None,
            season_number=None,
            episode_number=None,
            offer_type=FREE_WITH_ADS,
            provider_url=url,
            quality="",
            audio_languages=None,
            subtitle_languages=None,
            available_from=None,
            available_until=None,
            source_record_id=slug,
            source_version=SOURCE_VERSION,
        )
        return content, offer


def _resolve_region(region: str) -> Optional[tuple[str, str]]:
    """Observed ``collection.region`` -> (ISO country, path region), else None."""
    code = region.strip().upper()
    path = PATH_BY_COUNTRY.get(code)
    return (code, path) if path else None


def _grid_token(collection: dict, hub: str) -> str:
    """The grid carousel token for a hub (its ``/category/`` carousel)."""
    carousels = collection.get("hubCarousels")
    if isinstance(carousels, list):
        for entry in carousels:
            if not isinstance(entry, dict):
                continue
            token = str(entry.get("token") or "")
            href = str(entry.get("href") or "")
            if token and href.startswith("/category/"):
                return token
    raise SourceFetchError(f"Pluto TV collection {hub!r} carried no grid token")


def _parse_date(value) -> Optional[date]:
    """ISO-8601 timestamp/date -> aware-free ``date``, else None."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
        if 1800 <= parsed.year <= 2200:
            return parsed.date()
        return None
    return None
