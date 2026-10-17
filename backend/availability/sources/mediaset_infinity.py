"""Mediaset Infinity dedicated collector — title-targeted Italian AVOD lookup (Phase E).

Third title-targeted :class:`AvailabilitySource` (after streaming_availability
and YouTube), modelling Mediaset Infinity's Italian movie catalogue on its
public GraphQL frontier, no login, no cookie, no API key:

    search (POST frontier, operation ``MediasetSearch``, ``variant: "movie"``)
        -> title verification against the requested TMDb title
        -> one detail fetch (operation ``MediasetMovie``) for the rights,
           languages and availability window the search cards lack
        -> rights mapping: AVOD -> free_with_ads, SVOD -> subscription,
           TVOD (rent/buy) observed but excluded by SPEC
        -> one DiscoveredContent + at most one ProviderOffer (movie granularity)
        -> AvailabilityRefreshService (targeted path: offers already carry the
           requested tmdb id)

API contract (verified live against the gateway, 2026-10, see docs/PLAN.md
Phase E and /tmp/mediaset_NOTES.md):

- ``POST https://frontier.api-graph.mediaset.it/`` with
  ``x-m-platform: WEB``, ``x-m-property: MPLAY``, ``x-m-app-version: 1.5.2``
  and ``Origin: https://www.mediasetinfinity.mediaset.it``. The gateway
  accepts *any* well-formed GraphQL document — the site ships two ~13 KB
  operations, but this collector deliberately ships two small reduced
  documents selecting only the fields it reads (verified to return the same
  data with HTTP 200).
- A GraphQL ``errors[]`` entry, a non-dict body, HTTP != 200, or a payload
  without the ``data -> getSearchPage`` / ``data -> getMoviePage`` shell are
  retrieval failures (:class:`SourceFetchError`) — never an empty result.
- Search results nest ``areaContainers -> areas -> sections -> collections ->
  itemsConnection``; cards are ``VideoItem``/``SeriesItem`` with ``guid``,
  ``cardTitle``, ``editorialType`` and ``cardLink``, but carry **no rights and
  no year** — rights require the per-candidate detail fetch, which is why a
  matching search costs exactly two requests.

Product semantics:

- **Country is IT, fixed.** MPLAY is the Italian storefront; there is no
  egress-country probe to observe and no other country is served, so every
  offer is recorded for Italy (``supported_countries={"IT"}``).
- **Movies only, title-targeted**: series/season/episode requests return an
  empty result without network; a search with no movie card whose normalized
  title equals the request's is an empty (successful) observation.
- **Rights vocabulary is the offer-type signal** (``contentRight`` on the
  detail): ``AVOD`` -> ``free_with_ads``, ``SVOD`` -> ``subscription``,
  ``TVOD`` -> rent/buy, observed and logged but never persisted (SPEC
  excludes rent/buy by design). An absent ``contentRight`` is an upstream
  shape we cannot use (loud failure); an unrecognized value is logged and
  yields an empty result.
- **Languages come from the detail**: ``audioLanguages``/``subLanguages``
  hold ISO 639-2 codes (``ita``, ``jpn``, ``eng``) normalized through
  ``normalize_languages``; ``None`` stays ``None``. ``expirationDate``
  (``...Z``) becomes an aware ``available_until``; no expiry stays ``None``.

Deliberate limitations, documented rather than papered over:

- **Title verification, not TMDb/year verification**: neither search nor
  detail exposes a release year (``contentPublicationDate`` came back null in
  research), so a homonymous film cannot be ruled out by data — candidates
  are matched by normalized-title equality and recorded with the requested
  ``tmdb_id`` after that verification (``supports_tmdb_ids=False``). The
  request's year is therefore *not* folded into the search query: Mediaset
  search matches titles, and appending a year would only hurt recall.
- **One search page (first 12 cards)**: a title-targeted lookup never pages;
  exact-title movie cards were observed on page 1 during research.
- **The result is never authoritative**: one query's match says nothing about
  the rest of the catalogue, so absence never deactivates offers
  (``supports_authoritative_snapshots=False``).
- **Budget/rate limiting stay with the sweep**: one refresh = one shared
  rate-limiter token and at most two HTTP calls (search + detail); the
  collector implements no quota accounting of its own.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

from ..domain.enums import ContentType, OfferType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
    normalize_content_type,
    normalize_country,
    normalize_languages,
    normalize_offer_type,
)
from ..services.normalize import normalize_title as _normalize_title
from .base import (
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
)

logger = logging.getLogger(__name__)

FRONTIER_URL = "https://frontier.api-graph.mediaset.it/"
SITE_ORIGIN = "https://www.mediasetinfinity.mediaset.it"
APP_VERSION = "1.5.2"
SEARCH_PAGE_SIZE = 12
DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: The one country Mediaset Infinity's MPLAY storefront serves.
COUNTRY = "IT"

#: ``contentRight`` values that are rent/buy offers — observed, logged, and
#: excluded because the project only ranks subscription/free offers (SPEC).
EXCLUDED_RIGHTS = frozenset({"TVOD"})

#: Reduced documents (verified live; the site's 13 KB originals are not
#: required — the gateway executes any well-formed selection).
SEARCH_QUERY = """
query MediasetSearch($property: String, $query: String, $uxReference: String, $variant: String, $first: Int, $after: String) {
  getSearchPage(property: $property, query: $query, uxReference: $uxReference, variant: $variant) {
    areaContainersConnection {
      areaContainers {
        ... on AreaContainer {
          areas {
            sections {
              ... on Section {
                collections {
                  itemsConnection(first: $first, after: $after) {
                    items {
                      __typename
                      ... on VideoItem {
                        guid
                        cardTitle
                        editorialType
                        url
                        cardLink { value referenceId referenceType }
                        channelLabels { id }
                      }
                      ... on SeriesItem {
                        guid
                        cardTitle
                        cardLink { value referenceId referenceType }
                      }
                    }
                    pageInfo { endCursor hasNextPage context }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
""".strip()

MOVIE_QUERY = """
query MediasetMovie($id: ID!) {
  getMoviePage(id: $id) {
    title
    type
    url
    dataSource {
      __typename
      ... on VideoItem {
        guid
        title
        url
        description
        editorialType
        contentRight
        channelRights
        channelLabels { id }
        expirationDate
        audioLanguages
        subLanguages
        duration
      }
    }
  }
}
""".strip()


class MediasetInfinityClient:
    """Thin client for Mediaset Infinity's public GraphQL frontier.

    ``http_post`` is an injectable ``(url, payload) -> dict`` callable; tests
    pass a fixture router so no test ever performs network I/O. Retrieval
    failures (HTTP error, transport error, GraphQL ``errors[]``, unusable
    payload type) always surface as :class:`SourceFetchError` — never as an
    empty-but-valid result.
    """

    def __init__(self, *, http_post=None, timeout: int = DEFAULT_TIMEOUT):
        self.http_post = http_post
        self.timeout = timeout

    def search(self, title: str) -> dict:
        """One reduced ``GetSearchPage`` document for a movie title."""
        return self._graphql(
            "MediasetSearch",
            SEARCH_QUERY,
            {
                "property": "search",
                "query": title,
                "uxReference": "filteredSearch",
                "variant": "movie",
                "first": SEARCH_PAGE_SIZE,
                "after": None,
            },
        )

    def movie(self, guid: str) -> dict:
        """One reduced ``GetMoviePage`` document for a matched candidate."""
        return self._graphql("MediasetMovie", MOVIE_QUERY, {"id": guid})

    # ------------------------------------------------------------- transport

    def _graphql(self, operation: str, query: str, variables: dict) -> dict:
        body = {
            "operationName": operation,
            "query": query,
            "variables": variables,
        }
        if self.http_post is not None:
            try:
                payload = self.http_post(FRONTIER_URL, body)
            except SourceFetchError:
                raise
            except Exception as exc:  # fixture routers raise plain exceptions
                raise SourceFetchError(
                    f"Mediaset Infinity {operation} request failed: {exc}"
                ) from exc
        else:
            payload = self._post(body)
        if not isinstance(payload, dict):
            raise SourceFetchError(
                f"Unexpected Mediaset Infinity {operation} response type"
            )
        errors = payload.get("errors")
        if errors:
            message = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = str(errors[0].get("message") or "")
            raise SourceFetchError(
                f"Mediaset Infinity {operation} returned GraphQL errors: {message}"
            )
        return payload

    def _post(self, body: dict) -> dict:
        try:
            response = requests.post(
                FRONTIER_URL,
                json=body,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "x-m-platform": "WEB",
                    "x-m-property": "MPLAY",
                    "x-m-app-version": APP_VERSION,
                    "Origin": SITE_ORIGIN,
                    "Referer": f"{SITE_ORIGIN}/",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SourceFetchError(
                f"Mediaset Infinity request failed: {exc}"
            ) from exc
        if response.status_code != 200:
            raise SourceFetchError(
                f"Mediaset Infinity returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SourceFetchError(
                "Mediaset Infinity returned a non-JSON body"
            ) from exc


class MediasetInfinitySource:
    """Title-targeted Mediaset Infinity source (``content=None`` rejected)."""

    slug = "mediaset_infinity"
    name = "Mediaset Infinity"
    source_type = SourceType.API
    capabilities = SourceCapabilities(
        # One provider, one country: the MPLAY storefront is Italy-only.
        supported_providers=frozenset({"mediaset_infinity"}),
        supported_countries=frozenset({COUNTRY}),
        # The source is queried with a movie title and only emits movie rows;
        # series requests short-circuit to an empty result (no network).
        supported_content_types=frozenset({ContentType.MOVIE}),
        supports_incremental_sync=False,
        supports_title_fetch=True,
        supports_catalogue_sync=False,
        # One query's match is a subset of the catalogue: absence must never
        # deactivate offers.
        supports_authoritative_snapshots=False,
        # The source provides no external ids of its own; offers carry the
        # requested tmdb id after title verification.
        supports_tmdb_ids=False,
        supports_seasons=False,
        supports_episodes=False,
        # The detail carries audio/subtitle language codes.
        supports_languages=True,
    )

    def __init__(self, client=None):
        self.client = client or MediasetInfinityClient()

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is None:
            raise ValueError(
                "mediaset_infinity is title-targeted: a content request is required"
            )
        request = content if isinstance(content, ContentRequest) else ContentRequest.from_content(content)
        content_type = normalize_content_type(request.content_type)
        if content_type != ContentType.MOVIE:
            # Outside the declared scope: an empty result is the honest
            # answer and no request is spent on a query we would not model.
            logger.info(
                "mediaset_infinity_unsupported_content_type tmdb_id=%s content_type=%s",
                request.tmdb_id, request.content_type,
            )
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)
        if not (request.title or "").strip():
            logger.warning("mediaset_infinity_missing_title tmdb_id=%s", request.tmdb_id)
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        payload = self.client.search(request.title)
        card = _find_movie_candidate(payload, request)
        if card is None:
            # A well-formed search without a verified movie card observes
            # nothing — success, not failure.
            logger.info(
                "mediaset_infinity_no_movie_candidate tmdb_id=%s title=%r",
                request.tmdb_id, request.title,
            )
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        guid = str(card.get("guid") or "").strip()
        detail = self.client.movie(guid)
        data = _movie_data(detail)

        right = str(data.get("contentRight") or "").strip().upper()
        if not right:
            # A detail page fetched specifically for its rights without them
            # is a shape we cannot interpret: loud, not silently empty.
            raise SourceFetchError(
                f"Unexpected Mediaset Infinity movie payload: no contentRight for {guid}"
            )
        if right in EXCLUDED_RIGHTS:
            # Rent/buy offer — observed by design, never persisted (SPEC).
            logger.info(
                "mediaset_infinity_offer_excluded guid=%s content_right=%s",
                guid, right,
            )
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)
        offer_type = normalize_offer_type(right)
        if offer_type == OfferType.UNKNOWN:
            logger.warning(
                "mediaset_infinity_unknown_content_right guid=%s content_right=%r",
                guid, right,
            )
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        url = _detail_url(data, card)
        offer = ProviderOffer(
            provider_id=self.slug,
            provider_content_id=guid,
            country=normalize_country(COUNTRY),
            content_type=ContentType.MOVIE,
            tmdb_id=request.tmdb_id,
            season_number=None,
            episode_number=None,
            offer_type=offer_type,
            provider_url=url,
            quality="",
            audio_languages=normalize_languages(data.get("audioLanguages")),
            subtitle_languages=normalize_languages(data.get("subLanguages")),
            available_from=None,
            available_until=_parse_expiration(data.get("expirationDate")),
            source_record_id=guid,
            source_version=APP_VERSION,
        )
        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=guid,
            content_type=ContentType.MOVIE,
            title=str(data.get("title") or card.get("cardTitle") or "").strip(),
            url=url,
            description=str(data.get("description") or "").strip(),
            external_ids={"tmdb": str(request.tmdb_id)},
            source_id=self.slug,
        )
        return SourceFetchResult(
            contents=[content],
            offers=[offer],
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.TARGETED,
            cursor=None,
        )


# ----------------------------------------------------------------- parsing


def _search_items(payload: dict) -> list[dict]:
    """All search cards across every area/section/collection container.

    The ``data -> getSearchPage -> areaContainersConnection ->
    areaContainers`` shell exists even for a query with zero results, so a
    payload *without* it is an upstream shape change — loud failure, never a
    silent empty catalogue. Containers/items that are not dicts are skipped
    (a corrupt entry must not abort the scan); missing deeper keys simply
    contribute no cards.
    """
    try:
        containers = payload["data"]["getSearchPage"]["areaContainersConnection"][
            "areaContainers"
        ]
    except (KeyError, TypeError) as exc:
        raise SourceFetchError("Unexpected Mediaset Infinity search payload shape") from exc
    if not isinstance(containers, list):
        raise SourceFetchError("Unexpected Mediaset Infinity areaContainers type")
    items: list[dict] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        for area in _as_dicts(container.get("areas")):
            for section in _as_dicts(area.get("sections")):
                for collection in _as_dicts(section.get("collections")):
                    connection = collection.get("itemsConnection")
                    if not isinstance(connection, dict):
                        continue
                    items.extend(_as_dicts(connection.get("items")))
    return items


def _as_dicts(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _find_movie_candidate(payload: dict, request: ContentRequest) -> Optional[dict]:
    """First movie card whose normalized title equals the requested one.

    Verification order per card: ``VideoItem`` (series cards are outside the
    declared scope), ``editorialType == "Movie"``, normalized-title equality,
    non-empty ``guid``. Search cards carry no year, so unlike YouTube there is
    no candidate year to agree with the request — the match is title-only by
    data availability, recorded with the requested tmdb id afterwards.
    """
    wanted = _normalize_title(request.title)
    for item in _search_items(payload):
        if item.get("__typename") != "VideoItem":
            continue
        if str(item.get("editorialType") or "").strip().lower() != "movie":
            continue
        if _normalize_title(item.get("cardTitle")) != wanted:
            continue
        if not str(item.get("guid") or "").strip():
            continue
        return item
    return None


def _movie_data(payload: dict) -> dict:
    """``dataSource`` of a well-formed ``getMoviePage`` response.

    A null page (unknown guid) or a page without a usable ``dataSource`` is a
    retrieval failure: the detail fetch exists solely to read rights, so an
    uninterpretable one can never masquerade as "nothing to offer".
    """
    try:
        page = payload["data"]["getMoviePage"]
    except (KeyError, TypeError) as exc:
        raise SourceFetchError("Unexpected Mediaset Infinity movie payload shape") from exc
    if not isinstance(page, dict):
        raise SourceFetchError("Unexpected Mediaset Infinity movie page type")
    data = page.get("dataSource")
    if not isinstance(data, dict):
        raise SourceFetchError("Unexpected Mediaset Infinity movie dataSource")
    return data


def _detail_url(data: dict, card: dict) -> str:
    """Canonical URL: detail first, then the search card's own fields."""
    link = card.get("cardLink") if isinstance(card.get("cardLink"), dict) else {}
    for value in (data.get("url"), card.get("url"), link.get("value")):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return ""


def _parse_expiration(value) -> Optional[datetime]:
    """``expirationDate`` (``2026-10-27T22:59:00.000Z``) to aware datetime."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(
            "mediaset_infinity_unparsable_expiration value=%r", text
        )
        return None
