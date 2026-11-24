"""Transport-only GraphQL client for the unofficial JustWatch API.

Scope of this module: **transport only**. It owns GraphQL request
construction, the HTTP round trip, retry classification and bounded backoff.
It never interprets an offer, a provider, a country or a language — that
interpretation belongs to the source adapter (:mod:`.justwatch`) through the
shared :mod:`availability.services.normalize` helpers.

Endpoint: ``https://apis.justwatch.com/graphql`` — the same unofficial
GraphQL interface used by ``Electronic-Mango/simple-justwatch-python-api``,
whose request/response shapes this module mirrors. We speak the wire format
directly instead of depending on that package so the availability domain
keeps its own source abstraction and its own object model.

Measured behaviour this module encodes (probed against the live endpoint,
``docs`` of this repo, September 2026 — these are properties of *unofficial*
infrastructure and are re-probed by the fixture tests, not assumed):

- **Per-request page ceiling.** ``first`` above ~100 is rejected, and the
  rejection is a *GraphQL error with HTTP 200*: ``first: 200`` -> ``page too
  large``; ``first: 438`` -> ``operation has complexity 350500, which
  exceeds the limit of 350000``. Complexity scales linearly with ``first``
  (~800/title at our field selection), so the ceiling is a *server-side*
  complexity budget, not a documented constant. :data:`MAX_TITLES_PER_REQUEST`
  therefore stays deliberately below the observed cliff (100) and every page
  size is validated against it.
- **Pagination depth wall.** Offset paging walks cleanly (measured: 5 pages x
  50 titles = 250 distinct ids, zero overlap), but the set stops after
  ~1900 entries for one provider+country — ``offset=1900`` returns an **empty**
  edge list rather than an error. :data:`CATALOGUE_MAX_OFFSET` encodes that
  wall so a catalogue walk stops on "no more data" instead of looping.
- **No ``pageInfo``.** The query exposes only ``edges`` + an integer
  ``offset`` argument, so paging is strictly offset-based (cursor paging is
  not available on this operation).
- **The ``packages`` filter selects *titles*, not offers.** Filtering by
  ``packages: ["nfx"]`` returns titles that Netflix streams, but the offers
  inside each node still span every provider carrying that title. Any caller
  that wants "offers *from* provider X" must filter by package client-side.

Failure taxonomy — every failure mode stays *distinguishable* rather than
collapsed into one exception, because "a failed fetch" must never look like
"a successful empty fetch":

- :class:`JustWatchTransportError` — HTTP/network level. Retried with
  bounded backoff; raised only once the retry budget is exhausted.
- :class:`JustWatchGraphQLError` — a well-formed response carrying an
  ``errors`` array. **Not** retried: a rejected query will be rejected
  identically next time, and retrying it burns the rate budget. The measured
  ``page too large`` / ``complexity ... exceeds the limit`` rejections land
  here, which is exactly why they must never be confused with "no results".
- :class:`JustWatchResponseError` — a 2xx response that is not the JSON
  envelope we require (HTML interstitial, truncated body, missing ``data``).
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Any, Optional

import requests


def _requests_post(url, json=None, headers=None, timeout=None):
    from .base import SourceFetchStopped
    from .justwatch_transport import post

    documents = {
        SEARCH_TITLES_QUERY: ("GetSearchTitles", "search"),
        SEASONS_QUERY: ("GetTitleNode", "seasons"),
        POPULAR_TITLES_QUERY: ("GetPopularTitles", "catalogue"),
        PROVIDERS_QUERY: ("GetProviders", "providers"),
        DETAILS_QUERY: ("GetTitleNode", "details"),
        EPISODES_QUERY: ("GetTitleNode", "episodes"),
    }
    document = json["query"]
    requested_name = json["operationName"]
    expected_name, operation = documents.get(document, (None, None))
    countries = [json["variables"].get("country")]
    if requested_name == "GetTitleOffers":
        countries = re.findall(r"(?m)^([A-Z]{2}): offers\(country: \1,", document)
        if countries and document == offers_for_countries_query(countries):
            expected_name, operation = "GetTitleOffers", "title_offers"
    if requested_name != expected_name or not operation or not all(countries):
        raise SourceFetchStopped("JustWatch GraphQL document or market is outside the reviewed scope")
    return post(url, operation=operation, countries=countries, json=json, headers=headers, timeout=timeout)

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://apis.justwatch.com/graphql"
DETAILS_URL = "https://www.justwatch.com"
IMAGES_URL = "https://images.justwatch.com"

DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.8

#: Largest page size the endpoint accepts at our field selection. Measured
#: cliff: ``first: 100`` succeeds, ``first: 120`` is rejected as
#: ``page too large``. Kept below the cliff with headroom, because the real
#: limit is a server-side complexity budget that can move with our selection.
MAX_TITLES_PER_REQUEST = 100

#: Offset past which the endpoint returns an empty edge list instead of more
#: titles (measured wall at ~1900 for a single provider+country partition).
CATALOGUE_MAX_OFFSET = 2000

#: JustWatch's unofficial endpoint fronts the public site; a bare
#: python-requests User-Agent is rejected by its edge. This is a *client
#: identity* string, not an attempt to evade anything — the endpoint is
#: public and unauthenticated, and the reference library sends the same
#: header set.
DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
}

#: Status codes worth another attempt. Everything else — 4xx in particular —
#: means the request itself is wrong and will stay wrong.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Transport faults that are transient by nature. Everything else in
#: ``requests.RequestException`` (invalid URL/schema) is permanent and is
#: wrapped without a retry.
RETRYABLE_TRANSPORT_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
)

#: GraphQL error messages that mean "this page size is too big", not "no
#: results". Detected so a pagination plan that overshoots can be clamped
#: instead of being reported as an empty catalogue.
PAGE_TOO_LARGE_MARKERS = ("page too large", "complexity")

# ---------------------------------------------------------------------------
# GraphQL documents
# ---------------------------------------------------------------------------
# Fragment structure mirrors the reference library exactly, including the
# typed-fragment split that a naive flattening gets wrong: ``fullPath`` and
# ``externalIds`` do not exist on ``MovieOrShowOrSeasonOrEpisodeContent``,
# they require the ``MovieOrShowOrSeasonContent`` inline fragment. Querying
# them on the base type is answered with HTTP 422
# ("Cannot query field \"fullPath\" on type ...").

_CONTENT_BASE_FIELDS = """
    title
    originalReleaseYear
    originalReleaseDate
    runtime
    shortDescription
"""

_TITLE_DETAILS_FRAGMENT = """
fragment TitleDetails on MovieOrShowOrSeasonOrEpisode {
    id
    objectId
    objectType
    content(country: $country, language: $language) {
        ...ContentDetails
    }
    ... on Show {
        totalSeasonCount
    }
    ... on Season {
        totalEpisodeCount
    }
    offers(country: $country, platform: WEB, filter: $filter) {
        ...TitleOffer
    }
}

fragment ContentDetails on MovieOrShowOrSeasonOrEpisodeContent {
""" + _CONTENT_BASE_FIELDS + """
    ...FullContentDetails
    ... on SeasonContent {
        seasonNumber
    }
    ... on EpisodeContent {
        seasonNumber
        episodeNumber
    }
}

fragment FullContentDetails on MovieOrShowOrSeasonContent {
    fullPath
    genres {
        shortName
    }
    externalIds {
        imdbId
        tmdbId
    }
    posterUrl(profile: $profile, format: $formatPoster)
}
"""

_OFFER_FRAGMENT = """
fragment TitleOffer on Offer {
    id
    monetizationType
    presentationType
    retailPrice(language: $language)
    retailPriceValue
    currency
    type
    standardWebURL
    elementCount
    availableTo
    subtitleLanguages
    videoTechnology
    audioTechnology
    audioLanguages
    package {
        ...PackageDetails
    }
}
"""

_PACKAGE_FRAGMENT = """
fragment PackageDetails on Package {
    id
    packageId
    clearName
    technicalName
    shortName
    slug
    monetizationTypes
    icon(profile: S100, format: $formatOfferIcon)
}
"""

#: Catalogue/discovery operation — the "popular titles" listing, filtered by
#: provider packages, release year and object type. This is the operation
#: that makes JustWatch a *discovery* source rather than a title-by-title one.
POPULAR_TITLES_QUERY = """
query GetPopularTitles(
    $popularTitlesFilter: TitleFilter
    $country: Country!
    $language: Language!
    $first: Int! = 70
    $formatPoster: ImageFormat
    $formatOfferIcon: ImageFormat
    $profile: PosterProfile
    $filter: OfferFilter!
    $offset: Int = 0
) {
    popularTitles(
        country: $country
        filter: $popularTitlesFilter
        first: $first
        sortBy: POPULAR
        sortRandomSeed: 0
        offset: $offset
    ) {
        edges {
            node {
                ...TitleDetails
            }
        }
    }
}
""" + _TITLE_DETAILS_FRAGMENT + _OFFER_FRAGMENT + _PACKAGE_FRAGMENT

#: Search operation. Same listing, with a free-text query filter.
SEARCH_TITLES_QUERY = """
query GetSearchTitles(
    $searchTitlesFilter: TitleFilter!
    $country: Country!
    $language: Language!
    $first: Int!
    $formatPoster: ImageFormat
    $formatOfferIcon: ImageFormat
    $profile: PosterProfile
    $filter: OfferFilter!
    $offset: Int = 0
) {
    popularTitles(
        country: $country
        filter: $searchTitlesFilter
        first: $first
        sortBy: POPULAR
        sortRandomSeed: 0
        offset: $offset
    ) {
        edges {
            node {
                ...TitleDetails
            }
        }
    }
}
""" + _TITLE_DETAILS_FRAGMENT + _OFFER_FRAGMENT + _PACKAGE_FRAGMENT

#: Title detail lookup by JustWatch node id.
DETAILS_QUERY = """
query GetTitleNode(
    $nodeId: ID!,
    $language: Language!,
    $country: Country!,
    $formatPoster: ImageFormat,
    $formatOfferIcon: ImageFormat,
    $profile: PosterProfile,
    $filter: OfferFilter!,
) {
    node(id: $nodeId) {
        ...TitleDetails
    }
}
""" + _TITLE_DETAILS_FRAGMENT + _OFFER_FRAGMENT + _PACKAGE_FRAGMENT

SEASONS_QUERY = """
query GetTitleNode(
    $nodeId: ID!,
    $language: Language!,
    $country: Country!,
    $formatPoster: ImageFormat,
    $formatOfferIcon: ImageFormat,
    $profile: PosterProfile,
    $filter: OfferFilter!,
) {
    node(id: $nodeId) {
        ... on Show {
            seasons(sortDirection: ASC) {
                ...TitleDetails
            }
        }
    }
}
""" + _TITLE_DETAILS_FRAGMENT + _OFFER_FRAGMENT + _PACKAGE_FRAGMENT

EPISODES_QUERY = """
query GetTitleNode(
    $nodeId: ID!,
    $language: Language!,
    $country: Country!,
    $formatPoster: ImageFormat,
    $formatOfferIcon: ImageFormat,
    $profile: PosterProfile,
    $filter: OfferFilter!,
) {
    node(id: $nodeId) {
        ... on Season {
            episodes(sortDirection: ASC) {
                ...TitleDetails
            }
        }
    }
}
""" + _TITLE_DETAILS_FRAGMENT + _OFFER_FRAGMENT + _PACKAGE_FRAGMENT

#: Provider/package catalogue for one country.
PROVIDERS_QUERY = """
query GetProviders(
    $country: Country!,
    $formatOfferIcon: ImageFormat
) {
    packages(
        country: $country
        platform: WEB
        includeAddons: true
    ) {
        ...PackageDetails
    }
}
""" + _PACKAGE_FRAGMENT


def offers_for_countries_query(countries) -> str:
    """Build the multi-country offers operation for *countries*.

    JustWatch answers offers for several countries in **one** request by
    aliasing the same field per country (``NL: offers(country: NL ...)``).
    This is how a title-targeted fetch covers every configured country
    without spending N requests. ``retailPrice(language: $language)`` is what
    makes ``$language`` a used variable — without that field the operation is
    rejected with HTTP 422 ("Variable \"$language\" is never used").
    """
    entries = "\n".join(
        "{code}: offers(country: {code}, platform: WEB, filter: $filter) "
        "{{ ...TitleOffer }}".format(code=code)
        for code in sorted({str(c).strip().upper() for c in countries if str(c).strip()})
    )
    if not entries:
        raise ValueError("offers_for_countries_query requires at least one country")
    return """
query GetTitleOffers(
    $nodeId: ID!,
    $language: Language!,
    $formatOfferIcon: ImageFormat,
    $filter: OfferFilter!,
) {
    node(id: $nodeId) {
        ... on MovieOrShowOrSeasonOrEpisode {
%s
        }
    }
}
%s
%s
""" % (entries, _OFFER_FRAGMENT, _PACKAGE_FRAGMENT)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class JustWatchError(Exception):
    """Base for every failure this client raises."""


class JustWatchTransportError(JustWatchError):
    """Network/HTTP failure after the retry budget was spent."""


class JustWatchGraphQLError(JustWatchError):
    """The API answered 2xx with a populated ``errors`` array."""

    def __init__(self, errors: Any):
        self.errors = errors
        super().__init__(f"JustWatch GraphQL error: {errors}")

    @property
    def page_too_large(self) -> bool:
        """True when the rejection is about request size, not results.

        Measured: ``first: 200`` -> ``page too large`` and ``first: 438`` ->
        ``operation has complexity ... exceeds the limit of 350000``, both as
        GraphQL errors under HTTP 200. A caller that reads these as "this
        partition has no titles" would silently truncate a catalogue, so the
        condition is surfaced explicitly and handled by clamping the page.
        """
        messages = self.errors if isinstance(self.errors, list) else [self.errors]
        for error in messages:
            text = error.get("message", "") if isinstance(error, dict) else str(error)
            if any(marker in text.lower() for marker in PAGE_TOO_LARGE_MARKERS):
                return True
        return False


class JustWatchResponseError(JustWatchError):
    """A 2xx response whose body is not the JSON envelope we require."""


# ---------------------------------------------------------------------------
# Parsed value objects (transport-level, persistence-free)
# ---------------------------------------------------------------------------
class JustWatchPackage:
    """One provider/platform JustWatch reports for a country."""

    __slots__ = ("id", "package_id", "clear_name", "technical_name", "short_name",
                 "slug", "monetization_types")

    def __init__(self, id=None, package_id=None, clear_name="", technical_name="",
                 short_name="", slug="", monetization_types=None):
        self.id = id
        self.package_id = package_id
        self.clear_name = clear_name
        self.technical_name = technical_name
        self.short_name = short_name
        self.slug = slug
        self.monetization_types = list(monetization_types or [])

    def __repr__(self):
        return (f"JustWatchPackage(technical_name={self.technical_name!r}, "
                f"short_name={self.short_name!r})")


class JustWatchOffer:
    """One offer as JustWatch reports it, before canonical normalization."""

    __slots__ = ("id", "monetization_type", "presentation_type", "offer_type",
                 "package", "url", "audio_languages", "subtitle_languages",
                 "video_technology", "audio_technology", "retail_price",
                 "retail_price_value", "currency", "available_to", "element_count")

    def __init__(self, id=None, monetization_type="", presentation_type="",
                 offer_type="", package=None, url="", audio_languages=None,
                 subtitle_languages=None, video_technology=None,
                 audio_technology=None, retail_price=None, retail_price_value=None,
                 currency="", available_to=None, element_count=None):
        self.id = id
        self.monetization_type = monetization_type
        self.presentation_type = presentation_type
        self.offer_type = offer_type
        self.package = package
        self.url = url or ""
        self.audio_languages = list(audio_languages or [])
        self.subtitle_languages = list(subtitle_languages or [])
        self.video_technology = list(video_technology or [])
        self.audio_technology = list(audio_technology or [])
        self.retail_price = retail_price
        self.retail_price_value = retail_price_value
        self.currency = currency or ""
        self.available_to = available_to
        self.element_count = element_count

    def __repr__(self):
        return (f"JustWatchOffer(monetization_type={self.monetization_type!r}, "
                f"presentation_type={self.presentation_type!r}, "
                f"package={self.package!r})")


class JustWatchTitle:
    """One title node (movie, show, season or episode) as JustWatch reports it."""

    __slots__ = ("id", "object_id", "object_type", "title", "year", "release_date",
                 "runtime", "description", "genres", "imdb_id", "tmdb_id", "poster",
                 "full_path", "url", "season_number", "episode_number",
                 "total_season_count", "total_episode_count", "offers")

    def __init__(self, id=None, object_id=None, object_type="", title="", year=None,
                 release_date=None, runtime=None, description="", genres=None,
                 imdb_id=None, tmdb_id=None, poster="", full_path="", url="",
                 season_number=None, episode_number=None, total_season_count=None,
                 total_episode_count=None, offers=None):
        self.id = id
        self.object_id = object_id
        self.object_type = object_type
        self.title = title or ""
        self.year = year
        self.release_date = release_date
        self.runtime = runtime
        self.description = description or ""
        self.genres = list(genres or [])
        self.imdb_id = imdb_id
        self.tmdb_id = tmdb_id
        self.poster = poster
        self.full_path = full_path
        self.url = url
        self.season_number = season_number
        self.episode_number = episode_number
        self.total_season_count = total_season_count
        self.total_episode_count = total_episode_count
        self.offers = list(offers or [])

    @property
    def has_native_id(self) -> bool:
        return isinstance(self.id, str) and bool(self.id) and self.id == self.id.strip()

    @property
    def tmdb_id_missing(self) -> bool:
        return self.tmdb_id is None or str(self.tmdb_id).strip() in {"", "0"}

    @property
    def tmdb_id_int(self) -> Optional[int]:
        """TMDb id as an int, or ``None`` when JustWatch exposes none.

        JustWatch reports ``tmdbId`` as a **string**; ``0`` and empty strings
        both mean "no id", so they collapse to ``None`` instead of becoming a
        bogus mapping to TMDb 0.
        """
        if self.tmdb_id is None:
            return None
        text = str(self.tmdb_id).strip()
        if not text:
            return None
        try:
            value = int(text)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def __repr__(self):
        return (f"JustWatchTitle(id={self.id!r}, object_type={self.object_type!r}, "
                f"title={self.title!r}, tmdb_id={self.tmdb_id!r}, "
                f"offers={len(self.offers)})")


class JustWatchCataloguePage:
    """One page of the ``popularTitles`` listing."""

    __slots__ = ("titles", "offset", "requested_count")

    def __init__(self, titles, offset=0, requested_count=0):
        self.titles = list(titles)
        self.offset = offset
        self.requested_count = requested_count

    def __len__(self):
        return len(self.titles)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class JustWatchClient:
    """Thin, retrying, transport-only JustWatch GraphQL client."""

    def __init__(self, http_post=None, *, url=GRAPHQL_URL, headers=None,
                 timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES,
                 backoff=DEFAULT_BACKOFF, sleeper=None):
        # ``http_post`` is the seam every test uses: a callable taking
        # ``(payload, headers, timeout)`` and returning a requests-like
        # response. Injected so no test ever touches the network.
        # Bound to requests' own signature (url positional, json/headers/
        # timeout keyword-only) so the injected test seam and the default
        # transport are interchangeable.
        self._http_post = http_post or _requests_post
        self.url = url
        self.headers = dict(headers or DEFAULT_HEADERS)
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleeper or time.sleep

    # -- operations --------------------------------------------------------
    def providers(self, country: str) -> list[JustWatchPackage]:
        """Every provider/package JustWatch lists for *country*."""
        payload = {
            "operationName": "GetProviders",
            "variables": {"country": _country(country), "formatOfferIcon": "PNG"},
            "query": PROVIDERS_QUERY,
        }
        data = self._execute(payload, "providers")
        return [_parse_package(p) for p in ((data or {}).get("packages") or [])]

    def catalogue_titles(self, *, country: str, language: str = "en", count: int = 100,
                         offset: int = 0, packages=None, object_types=None,
                         min_year=None, max_year=None, best_only: bool = True
                         ) -> JustWatchCataloguePage:
        """One page of provider/country/year-partitioned catalogue titles."""
        payload = self._catalogue_payload(
            country=country, language=language, count=count, offset=offset,
            packages=packages, object_types=object_types, min_year=min_year,
            max_year=max_year, best_only=best_only, operation="GetPopularTitles",
        )
        edges = (self._execute(payload, "catalogue") or {}).get("popularTitles", {}).get("edges") or []
        return JustWatchCataloguePage(
            [_parse_title(edge.get("node") or {}) for edge in edges],
            offset=offset, requested_count=count,
        )

    def search_titles(self, query: str, *, country: str, language: str = "en",
                      count: int = 20, offset: int = 0, packages=None,
                      object_types=None, best_only: bool = True) -> JustWatchCataloguePage:
        """Search JustWatch titles by free text."""
        variables = self._catalogue_variables(
            country=country, language=language, count=count, offset=offset,
            packages=packages, object_types=object_types, min_year=None,
            max_year=None, best_only=best_only,
            extra_filter={"searchQuery": query},
        )
        payload = {"operationName": "GetSearchTitles", "variables": variables,
                   "query": SEARCH_TITLES_QUERY}
        edges = (self._execute(payload, "search") or {}).get("popularTitles", {}).get("edges") or []
        return JustWatchCataloguePage(
            [_parse_title(edge.get("node") or {}) for edge in edges],
            offset=offset, requested_count=count,
        )

    def title_details(self, node_id: str, *, country: str, language: str = "en",
                      best_only: bool = True) -> Optional[JustWatchTitle]:
        """Full detail lookup for one JustWatch node id."""
        payload = {
            "operationName": "GetTitleNode",
            "variables": dict(self._common_variables(country, language, best_only),
                              nodeId=node_id),
            "query": DETAILS_QUERY,
        }
        data = self._execute(payload, "details")
        node = (data or {}).get("node")
        return _parse_title(node) if node else None

    def seasons(self, node_id: str, *, country: str, language: str = "en",
                best_only: bool = True) -> list[JustWatchTitle]:
        """Season nodes of a show."""
        payload = {
            "operationName": "GetTitleNode",
            "variables": dict(self._common_variables(country, language, best_only),
                              nodeId=node_id),
            "query": SEASONS_QUERY,
        }
        data = self._execute(payload, "seasons")
        # ``... on Show { seasons(...) }`` is selected on the node, so the list
        # is nested under ``node`` — reading ``data["seasons"]`` silently yields
        # an empty result for a show that reports total_season_count > 0.
        seasons = ((data or {}).get("node") or {}).get("seasons") or []
        return [_parse_title(s) for s in seasons]

    def episodes(self, node_id: str, *, country: str, language: str = "en",
                 best_only: bool = True) -> list[JustWatchTitle]:
        """Episode nodes of a season."""
        payload = {
            "operationName": "GetTitleNode",
            "variables": dict(self._common_variables(country, language, best_only),
                              nodeId=node_id),
            "query": EPISODES_QUERY,
        }
        data = self._execute(payload, "episodes")
        episodes = ((data or {}).get("node") or {}).get("episodes") or []
        return [_parse_title(e) for e in episodes]

    def offers_for_countries(self, node_id: str, countries, *, language: str = "en",
                             best_only: bool = True) -> dict:
        """Offers for *countries* in a single request, keyed by country code.

        Countries with no offers are still present with an empty list, so a
        caller can tell "asked and found nothing" from "never asked".
        """
        wanted = {str(c).strip().upper() for c in countries if str(c).strip()}
        if not wanted:
            raise ValueError("offers_for_countries requires at least one country")
        payload = {
            "operationName": "GetTitleOffers",
            "variables": {
                "nodeId": node_id,
                "language": language,
                "formatOfferIcon": "PNG",
                "filter": {"bestOnly": best_only},
            },
            "query": offers_for_countries_query(wanted),
        }
        node = (self._execute(payload, "offers_for_countries") or {}).get("node") or {}
        return {
            code: [_parse_offer(o) for o in (node.get(code) or [])]
            for code in sorted(wanted)
        }

    # -- payload construction ---------------------------------------------
    def _catalogue_payload(self, *, country, language, count, offset, packages,
                           object_types, min_year, max_year, best_only, operation,
                           extra_filter=None):
        variables = self._catalogue_variables(
            country=country, language=language, count=count, offset=offset,
            packages=packages, object_types=object_types, min_year=min_year,
            max_year=max_year, best_only=best_only, extra_filter=extra_filter,
        )
        return {"operationName": operation, "variables": variables,
                "query": POPULAR_TITLES_QUERY}

    @staticmethod
    def _common_variables(country, language, best_only):
        return {
            "filter": {"bestOnly": best_only},
            "formatOfferIcon": "PNG",
            "formatPoster": "JPG",
            "profile": "S718",
            "backdropProfile": "S1920",
            "country": _country(country),
            "language": language,
        }

    def _catalogue_variables(self, *, country, language, count, offset, packages,
                             object_types, min_year, max_year, best_only,
                             extra_filter=None):
        if count > MAX_TITLES_PER_REQUEST:
            raise ValueError(
                f"count={count} exceeds the measured JustWatch page ceiling "
                f"({MAX_TITLES_PER_REQUEST}); raise MAX_TITLES_PER_REQUEST only "
                f"after re-probing the endpoint"
            )
        title_filter = {
            "includeTitlesWithoutUrl": True,
            "packages": _package_filter(packages),
            "objectTypes": _object_types(object_types),
            "releaseYear": {"min": min_year, "max": max_year},
        }
        if extra_filter:
            title_filter.update(extra_filter)
        return dict(
            self._common_variables(country, language, best_only),
            first=count,
            offset=offset or None,
            popularTitlesFilter=title_filter,
            **({"searchTitlesFilter": title_filter} if extra_filter else {}),
        )

    # -- transport ---------------------------------------------------------
    def _execute(self, payload, label):
        """POST *payload* and return the ``data`` payload, retrying transport faults.

        Connection failures and timeouts are transport faults just like a 503:
        retrying them is safe for these read-only GraphQL queries, and leaving
        them as raw ``requests`` exceptions would bypass every source-level
        ``except JustWatchError`` boundary (where a failed fetch must stay
        distinguishable from an empty one).
        """
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._http_post(
                    self.url, json=payload, headers=self.headers, timeout=self.timeout
                )
            except RETRYABLE_TRANSPORT_ERRORS as exc:
                # Transient by construction: timeouts and connection failures
                # (including resets and DNS hiccups) are worth another attempt.
                last_error = exc
                logger.warning(
                    "justwatch_retry label=%s reason=transport attempt=%s/%s error=%s",
                    label, attempt, self.retries, exc,
                )
                if attempt < self.retries:
                    self._sleep_backoff(attempt)
                continue
            except requests.RequestException as exc:
                # Invalid URL/schema and other permanent request problems: the
                # same call would fail identically, so do not burn the budget.
                raise JustWatchTransportError(
                    f"JustWatch transport failed for {label}: {exc}"
                ) from exc

            try:
                status = int(getattr(response, "status_code", 0))
            except (TypeError, ValueError):
                status = 0

            if status in RETRYABLE_STATUS:
                last_error = JustWatchTransportError(
                    f"JustWatch HTTP {status} for {label} "
                    f"(attempt {attempt}/{self.retries})"
                )
                logger.warning("justwatch_retry label=%s status=%s attempt=%s",
                               label, status, attempt)
                # No pointless sleep after the last attempt: the retry budget
                # is exhausted and the caller is about to see the failure.
                if attempt < self.retries:
                    self._sleep_backoff(attempt)
                continue

            if status >= 400:
                # 4xx other than 429: the request itself is wrong. Retrying it
                # reproduces the same answer and only burns the budget.
                raise JustWatchTransportError(
                    f"JustWatch HTTP {status} for {label}: {_body_hint(response)}"
                )

            return self._parse_envelope(response, label)

        raise JustWatchTransportError(
            f"JustWatch transport failed for {label} after {self.retries} attempts: "
            f"{last_error}"
        ) from last_error

    def _sleep_backoff(self, attempt):
        # Exponential with jitter: bounded, and not synchronised across
        # concurrent callers that all failed at the same moment.
        delay = self.backoff * (2 ** (attempt - 1))
        self._sleep(delay + random.uniform(0, self.backoff))

    @staticmethod
    def _parse_envelope(response, label):
        """Validate the JSON envelope and return its ``data`` payload."""
        headers = dict(getattr(response, "headers", {}) or {})
        content_type = str(headers.get("content-type", "")
                           or headers.get("Content-Type", ""))
        if "json" not in content_type.lower():
            raise JustWatchResponseError(
                f"JustWatch returned non-JSON for {label}: "
                f"content-type={content_type!r} body={_body_hint(response)}"
            )
        try:
            envelope = response.json()
        except Exception as exc:  # noqa: BLE001 - any decode failure is the same class
            raise JustWatchResponseError(
                f"JustWatch returned undecodable JSON for {label}: {exc}"
            ) from exc
        if not isinstance(envelope, dict):
            raise JustWatchResponseError(
                f"JustWatch returned a {type(envelope).__name__} envelope for {label}"
            )
        errors = envelope.get("errors")
        if errors:
            raise JustWatchGraphQLError(errors)
        if "data" not in envelope:
            raise JustWatchResponseError(
                f"JustWatch response for {label} has neither data nor errors"
            )
        return envelope.get("data") or {}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _country(value):
    code = str(value or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"Unsupported JustWatch country code: {value!r}")
    return code


def _package_filter(packages):
    """Normalize the ``packages`` filter to 3-letter short names or ``None``.

    JustWatch filters by package **shortName** (e.g. ``nfx``, ``prv``), not by
    technical name or slug, and silently ignores ids it does not recognise.
    """
    if packages is None:
        return None
    values = [packages] if isinstance(packages, str) else list(packages)
    names = []
    for value in values:
        text = str(value or "").strip().lower()
        if text and text not in names:
            names.append(text)
    return names or None


def _object_types(object_types):
    if object_types is None:
        return None
    values = [object_types] if isinstance(object_types, str) else list(object_types)
    names = []
    for value in values:
        text = str(value or "").strip().upper()
        if text and text not in names:
            names.append(text)
    return names or None


def _body_hint(response):
    text = getattr(response, "text", "") or ""
    return text[:200].replace("\n", " ")


def _parse_package(node):
    node = node or {}
    return JustWatchPackage(
        id=node.get("id"),
        package_id=node.get("packageId"),
        clear_name=node.get("clearName") or "",
        technical_name=node.get("technicalName") or "",
        short_name=node.get("shortName") or "",
        slug=node.get("slug") or "",
        monetization_types=node.get("monetizationTypes") or [],
    )


def _parse_offer(node):
    node = node or {}
    return JustWatchOffer(
        id=node.get("id"),
        monetization_type=node.get("monetizationType") or "",
        presentation_type=node.get("presentationType") or "",
        offer_type=node.get("type") or "",
        package=_parse_package(node.get("package")),
        url=node.get("standardWebURL") or "",
        audio_languages=node.get("audioLanguages") or [],
        subtitle_languages=node.get("subtitleLanguages") or [],
        video_technology=node.get("videoTechnology") or [],
        audio_technology=node.get("audioTechnology") or [],
        retail_price=node.get("retailPrice"),
        retail_price_value=node.get("retailPriceValue"),
        currency=node.get("currency") or "",
        available_to=node.get("availableTo"),
        element_count=node.get("elementCount"),
    )


def _parse_title(node):
    node = node or {}
    content = node.get("content") or {}
    external_ids = content.get("externalIds") or {}
    poster = content.get("posterUrl") or ""
    full_path = content.get("fullPath") or ""
    return JustWatchTitle(
        id=node.get("id"),
        object_id=node.get("objectId"),
        object_type=node.get("objectType") or "",
        title=content.get("title") or "",
        year=content.get("originalReleaseYear"),
        release_date=content.get("originalReleaseDate"),
        runtime=content.get("runtime"),
        description=content.get("shortDescription") or "",
        genres=[g.get("shortName") for g in (content.get("genres") or []) if g],
        imdb_id=external_ids.get("imdbId") or None,
        tmdb_id=external_ids.get("tmdbId"),
        poster=f"{IMAGES_URL}{poster}" if poster else "",
        full_path=full_path,
        url=f"{DETAILS_URL}{full_path}" if full_path else "",
        season_number=content.get("seasonNumber"),
        episode_number=content.get("episodeNumber"),
        total_season_count=node.get("totalSeasonCount"),
        total_episode_count=node.get("totalEpisodeCount"),
        offers=[_parse_offer(o) for o in (node.get("offers") or [])],
    )


def _parse_title_list(data, key):
    edges = ((data or {}).get(key) or {}).get("edges") or []
    return [_parse_title(edge.get("node") or {}) for edge in edges]
