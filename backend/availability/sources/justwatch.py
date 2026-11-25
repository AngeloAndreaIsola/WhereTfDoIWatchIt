"""JustWatch availability source: aggregator adapter over the unofficial GraphQL API.

Why an aggregator
-----------------
The first-party collectors in this app are provider-specific and therefore
geographically narrow: a BBC iPlayer collector only knows GB, Tubi only exists
in the US (and is geo-blocked from EU egress), CBC Gem only CA. JustWatch is
the opposite — it reports availability for *any* market and *any* provider in
one request. It is added here as **broad fallback/discovery coverage**, never
as a replacement for a collector, and never as the primary source.

Provenance vs identity (the distinction this module exists to respect)
--------------------------------------------------------------------
``Source = JustWatch`` says *where the information came from*;
``Provider = Prime Video`` says *where the user can watch*. JustWatch's
``technicalName`` for a provider is resolved through the existing
``ExternalProviderMapping`` seam (country-aware — see
:mod:`.justwatch_providers`), so an observed ``amazonprimevideo`` offer becomes
an ``Offer`` on the canonical ``prime`` provider without a second Provider row
ever being created. ``SourceType.AGGREGATOR`` records the relationship.

Deactivation contract (the reason this source is permanently non-authoritative)
-------------------------------------------------------------------------------
JustWatch's absence is not evidence of unavailability:

- it is an aggregator whose coverage is incomplete by nature;
- a failed/partial run must never be readable as "no availability";
- ``supports_authoritative_snapshots = False`` means the refresh service will
  *never* call ``deactivate_unseen`` for this source, so a JustWatch run can
  only ever add or refresh rows. A Prime collector's offer therefore survives
  JustWatch not listing it, which is exactly the required behaviour.

What "empty" means, precisely
-----------------------------
An empty catalogue page returns an **empty** ``SourceFetchResult`` — a
successful fetch that reported nothing. Any transport/GraphQL problem raises
``JustWatchTransportError`` / ``JustWatchGraphQLError`` /
``JustWatchResponseError`` instead, so the refresh service records a *failed*
run and writes nothing. The two are therefore never confusable.

The measured API constraints this module encodes (see
:mod:`.justwatch_client` for the probes):

- page size <= 100 (:data:`MAX_TITLES_PER_REQUEST`), enforced here so a caller
  cannot ask for 500 and receive a ``page too large`` GraphQL error;
- offset pagination only, with a practical depth wall
  (:data:`CATALOGUE_MAX_OFFSET`) — the endpoint answers ``offset`` past ~1900
  with an *empty* edge list, not an error, so walking forever would look like
  an infinite legitimately-empty partition;
- the ``packages`` filter selects **titles**, not offers, so offers returned
  for a provider-filtered page are filtered client-side (:meth:`_offers_for`).

Country is always explicit. It is never inferred from IP, VPN state, browser
locale or request headers — ``supported_countries`` is left ``None``
(unrestricted) on purpose, because a fixed frozenset would both cap coverage
and imply a country the caller never asked for. ``_require_country`` validates
per call instead.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from copy import copy
from datetime import date, datetime
from datetime import timezone as dt_timezone
from typing import Optional

from django.utils import timezone

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
    normalize_country,
    normalize_content_type,
    normalize_justwatch_offer_type,
    normalize_justwatch_quality,
    normalize_languages,
    normalize_price,
)
from .base import ContentRequest, SourceCapabilities, SourceFetchError, SourceFetchResult
from .justwatch_client import (
    CATALOGUE_MAX_OFFSET,
    MAX_TITLES_PER_REQUEST,
    JustWatchClient,
    JustWatchError,
    JustWatchGraphQLError,
    JustWatchTitle,
)
from .justwatch_providers import (
    country_package_map,
    mapped_countries_for,
    provider_slug_for_package,
)

logger = logging.getLogger(__name__)

#: JustWatch object type -> canonical content type. The API spells a season
#: node as ``SEASON`` and an episode node as ``EPISODE`` (verified live: a
#: show's seasons came back with ``objectType == "SEASON"``), while the
#: library-facing filter vocabulary uses ``SHOW_SEASON``/``SHOW_EPISODE``.
#: Both spellings are accepted because the filter and the response disagree.
OBJECT_TYPE_CONTENT_TYPES = {
    "MOVIE": ContentType.MOVIE,
    "SHOW": ContentType.SERIES,
    "SERIES": ContentType.SERIES,
    "SEASON": ContentType.SEASON,
    "SHOW_SEASON": ContentType.SEASON,
    "EPISODE": ContentType.EPISODE,
    "SHOW_EPISODE": ContentType.EPISODE,
}

#: Objects this adapter will not turn into content rows. The endpoint's
#: ``objectTypes`` filter is not strictly enforced, so a response can contain
#: types we have no domain slot for; they are dropped with a log line rather
#: than guessed into the nearest canonical type.
IGNORED_OBJECT_TYPES = frozenset({"SHOW_SEASON_AND_EPISODE", "OTHER"})


def justwatch_offer_variant(offer, price):
    """Keep the specific package, rendition and price reported for one offer."""
    return {
        "source_offer_id": str(offer.id or ""),
        "package": getattr(offer.package, "technical_name", ""),
        "package_id": getattr(offer.package, "package_id", None),
        "package_node_id": getattr(offer.package, "id", None),
        "monetization_type": offer.monetization_type,
        "presentation_type": offer.presentation_type,
        "type": offer.offer_type,
        "amount": None if price.amount is None else str(price.amount),
        "currency": price.currency,
        "validation_error": price.error,
        "reported_price_value": None if offer.retail_price_value is None else str(offer.retail_price_value),
        "reported_price": offer.retail_price,
        "reported_currency": offer.currency,
        "url": offer.url,
        "available_to": offer.available_to,
        "element_count": offer.element_count,
        "audio_languages": offer.audio_languages,
        "subtitle_languages": offer.subtitle_languages,
        "video_technology": offer.video_technology,
        "audio_technology": offer.audio_technology,
    }


class JustWatchSource:
    """Aggregator source over the unofficial JustWatch GraphQL API."""

    slug = "justwatch"
    name = "JustWatch"
    source_type = SourceType.AGGREGATOR
    capabilities = SourceCapabilities(
        # Both paths are supported, so neither scope is restricted here.
        # Country is validated per call instead (see _require_country).
        supported_providers=None,
        supported_countries=None,
        supported_content_types=frozenset({
            ContentType.MOVIE, ContentType.SERIES,
            ContentType.SEASON, ContentType.EPISODE,
        }),
        supports_incremental_sync=True,
        # BOTH capabilities, and that is the point: JustWatch answers one
        # title by name/year (``_fetch_title``) *and* walks whole provider
        # catalogues (``_fetch_catalogue``). Marking only
        # ``supports_catalogue_sync`` was the abstraction error that kept it
        # out of per-title scheduling; the two capabilities are independent.
        supports_title_fetch=True,
        supports_catalogue_sync=True,
        # Bounded, checkpointed traversal: this walk is resumable, which is a
        # *different* property from supports_incremental_sync (see base.py).
        # JustWatch resumes each partition from its stored offset (see module docstring).
        supports_resumable_catalogue_sync=True,
        # Permanently non-authoritative: an aggregator's silence is not
        # evidence, so this source can never deactivate an unseen offer. This
        # is what guarantees a JustWatch run cannot delete a collector's row.
        supports_authoritative_snapshots=False,
        # JustWatch reports TMDb ids on most titles (measured on every sample),
        # so resolution rarely needs the local title/year matcher.
        supports_tmdb_ids=True,
        supports_seasons=True,
        supports_episodes=True,
        supports_languages=True,
    )

    def __init__(self, client=None, *, language="en", best_only=True,
                 page_size=MAX_TITLES_PER_REQUEST, max_offset=CATALOGUE_MAX_OFFSET):
        self.client = client or JustWatchClient()
        self.language = language
        self.best_only = best_only
        # Clamped defensively: a caller passing 500 would otherwise get a
        # GraphQL "page too large" error instead of a working paged walk.
        self.page_size = max(1, min(int(page_size), MAX_TITLES_PER_REQUEST))
        self.max_offset = max(0, int(max_offset))

    # -- interface --------------------------------------------------------
    def fetch(self, content=None, *, provider=None, country=None, cursor=None,
              pages: int = 1) -> SourceFetchResult:
        """Fetch offers/content for one title, or a catalogue page.

        ``content`` -> title-targeted: resolve the title, then read offers for
        ``country`` (or the countries encoded in the cursor). ``content=None``
        -> catalogue/discovery: a page of a provider+country+type partition.

        ``pages`` walks more than one page per call. A partition visit used to
        advance exactly one page, so exhausting the 2000-offset ceiling needed
        twenty separate runs; walking a bounded number of pages per visit is
        what actually closes the coverage gap. Bounded by the caller, and the
        walk stops the moment a page comes back empty.
        """
        country = self._require_country(country)
        if content is not None:
            return self._fetch_title(content, country=country)
        partition = cursor if isinstance(cursor, dict) else {}
        return self._fetch_catalogue(
            country=country,
            provider=provider,
            content_type=partition.get("content_type"),
            offset=self._cursor_offset(cursor),
            pages=pages,
            min_year=partition.get("min_year"),
            max_year=partition.get("max_year"),
            partition_id=partition.get("partition"),
        )

    # -- title-targeted ---------------------------------------------------
    def _fetch_title(self, content, *, country) -> SourceFetchResult:
        """Resolve one title by name/year, then read its offers for *country*.

        JustWatch is keyed by its own node ids, so the entry point is a search:
        the title is matched on TMDb id when it is already known (the exact
        external id the source was asked about), falling back to conservative
        exact title/year evidence. A media-namespace mismatch, a conflicting
        TMDb id, an ambiguous result or no result at all is an honest empty
        result — never the first search hit.
        """
        request = content if isinstance(content, ContentRequest) else ContentRequest.from_content(content)
        title = self._search_title(request, country=country)
        if title is None:
            logger.info(
                "justwatch_title_not_found title=%r year=%s country=%s",
                request.title, request.year, country,
            )
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        tmdb_id = title.tmdb_id_int or (request.tmdb_id or None)
        contents, offers = [], []
        content_type = self._content_type(title)

        contents.append(self._content(title, content_type, country))
        offers.extend(self._offers_for(title, country=country, content_type=content_type,
                                       tmdb_id=tmdb_id, content=title))

        # A show's season offers are distinct rows in the canonical model, so
        # they are walked explicitly rather than collapsed onto the series.
        if content_type == ContentType.SERIES:
            for season in self._season_matches(self._seasons(title.id, country)):
                if season is None:
                    continue
                contents.append(self._content(season, ContentType.SEASON, country))
                offers.extend(self._offers_for(
                    season, country=country, content_type=ContentType.SEASON,
                    tmdb_id=tmdb_id, content=season,
                ))

        return SourceFetchResult(
            contents=contents, offers=offers, sync_mode=SyncMode.TARGETED,
        )

    def _search_title(self, request, *, country):
        """Find the JustWatch node that matches a content request.

        Identity is established by evidence, never by result order:

        1. candidates are restricted to the request's media namespace, so a
           movie request can never accept a show's numerically identical TMDb
           id (TMDb movie and TV ids are separate namespaces);
        2. when the request states a TMDb id, only that id matches; a candidate
           stating a *different* id is a different title and is discarded even
           if its title/year look similar;
        3. otherwise exact normalized title (+ year, when the request has one)
           must identify exactly one distinct node.

        Either path must resolve to one typed, usable native identity. A known
        typed ID can anchor a usable node even if another search edge omits its
        native ID. Conflicting claims about the usable node prevent a match.
        Unresolved or ambiguous searches return ``None``.
        """
        expected_type = self._requested_content_type(request)
        if expected_type is None:
            return None
        try:
            page = self.client.search_titles(
                request.title or "",
                country=country,
                language=self.language,
                count=min(self.page_size, 20),
                object_types=[expected_type.value.upper()],
                best_only=self.best_only,
            )
        except JustWatchError as exc:
            raise self._as_fetch_error("search", exc) from exc

        candidates = [title for title in page.titles if self._content_type(title) == expected_type]
        if request.tmdb_id:
            exact = [t for t in candidates if t.tmdb_id_int == request.tmdb_id]
            if exact:
                native_ids = {t.id for t in exact if t.has_native_id}
                return self._merge_title_matches(
                    [t for t in candidates if t.has_native_id and t.id in native_ids], page.titles,
                )

        matches = self._title_evidence_matches(request, candidates)
        if request.tmdb_id and any(not title.tmdb_id_missing for title in matches):
            return None
        return self._merge_title_matches(matches, page.titles)

    @classmethod
    def _merge_title_matches(cls, candidates, observed_nodes):
        """Resolve one native identity and combine offers from compatible edges."""
        if not candidates or any(not title.has_native_id for title in candidates):
            return None
        if len({title.id for title in candidates}) != 1:
            return None
        native_id = candidates[0].id
        claims = [title for title in observed_nodes if title.id == native_id]
        scopes = {(cls._content_type(title), title.season_number, title.episode_number) for title in claims}
        tmdb_ids = {title.tmdb_id_int for title in claims if not title.tmdb_id_missing}
        if (len(scopes) != 1 or next(iter(scopes))[0] is None
                or len(tmdb_ids) > 1 or None in tmdb_ids):
            return None
        # Claims on the same native identity must agree on any stated title and
        # year: a sparse (title-less) anchor must not hide a conflict between
        # two titled edges sharing the native id.
        claim_titles = {title.title.casefold() for title in claims if title.title}
        claim_years = {title.year for title in claims if title.year is not None}
        if len(claim_titles) > 1 or len(claim_years) > 1:
            return None
        selected = next((title for title in candidates if title.tmdb_id_int is not None), candidates[0])
        if tmdb_ids and selected.tmdb_id_int not in tmdb_ids:
            return None
        if len(claims) == 1:
            return selected
        selected = copy(selected)
        selected.offers = [offer for title in claims for offer in title.offers]
        return selected

    @classmethod
    def _season_matches(cls, seasons):
        by_number = defaultdict(list)
        for season in seasons:
            if (cls._content_type(season) != ContentType.SEASON
                    or type(season.season_number) is not int or season.season_number < 0):
                yield None
            else:
                by_number[season.season_number].append(season)
        for candidates in by_number.values():
            yield cls._merge_title_matches(candidates, seasons)

    @staticmethod
    def _requested_content_type(request):
        """Canonical movie/series type the request declares, or ``None``."""
        raw = getattr(request, "content_type", None)
        if not raw:
            return None
        try:
            value = normalize_content_type(raw)
        except ValueError:
            return None
        return value if value in (ContentType.MOVIE, ContentType.SERIES) else None

    @staticmethod
    def _normalized_title(value):
        return " ".join(str(value or "").casefold().split())

    def _title_evidence_matches(self, request, candidates):
        """Use exact title and optional year when TMDb identity is absent."""
        if not request.title:
            return []
        wanted = self._normalized_title(request.title)
        matches = [
            title for title in candidates
            if self._normalized_title(title.title) == wanted
        ]
        if request.year is not None:
            matches = [title for title in matches if title.year == request.year]
        return matches

    def _seasons(self, node_id, country):
        try:
            return self.client.seasons(
                node_id, country=country, language=self.language,
                best_only=self.best_only,
            )
        except JustWatchError as exc:
            # Seasons are additive enrichment. Losing them must not discard the
            # series-level offers already collected, so this is logged and the
            # partial result is returned as-is — still a successful fetch.
            logger.warning("justwatch_seasons_failed node=%s country=%s error=%s",
                           node_id, country, exc)
            return []

    # -- catalogue --------------------------------------------------------
    def _fetch_catalogue(self, *, country, provider, content_type, offset,
                         pages: int = 1, min_year=None, max_year=None,
                         partition_id=None) -> SourceFetchResult:
        """One bounded walk of a provider/country/type catalogue partition.

        Paging state travels on the ``cursor`` (an offset string) so a
        scheduled walk can resume instead of re-downloading the catalogue.

        The optional ``pages`` bound walks several pages per call and returns
        their combined contents/offers as one result, advancing the cursor once
        per completed page. It stops early — correctly and deliberately — when a
        page returns no titles, which is how the endpoint signals its depth
        wall.
        """
        object_types = [self._object_type(content_type)] if content_type else None
        packages = country_package_map([provider] if provider else [], country)
        if provider and not packages:
            # Requested provider is not walkable in this market. Returned as
            # an empty page rather than an error: the partition genuinely has
            # nothing for us, and the cursor stays put for the next run.
            logger.info(
                "justwatch_catalogue_unmapped provider=%s country=%s "
                "mapped_countries=%s",
                provider, country, mapped_countries_for(provider),
            )
            return SourceFetchResult(sync_mode=SyncMode.INCREMENTAL, cursor=str(offset))

        # A walk is bounded by the requested page count *and* the offset
        # ceiling, so neither an over-eager caller nor the depth wall can turn
        # this into an unbounded loop.
        remaining_pages = max(1, int(pages))
        by_native_id = defaultdict(list)
        next_offset = offset
        exhausted = False
        # Page-size clamp state is local to *this* walk. Mutating
        # ``self.page_size`` here used to leak a halved size into every later
        # partition, and recursing started a fresh result list that discarded
        # the titles/offers already collected. Both are why the retry below is
        # a ``continue`` in one iterative loop.
        page_size = self.page_size

        while remaining_pages > 0:
            if next_offset >= self.max_offset:
                logger.info(
                    "justwatch_catalogue_boundary_reached country=%s offset=%s "
                    "provider=%s", country, next_offset, provider)
                exhausted = True
                break

            try:
                page = self.client.catalogue_titles(
                    country=country,
                    language=self.language,
                    count=page_size,
                    offset=next_offset,
                    packages=packages or None,
                    object_types=object_types,
                    min_year=min_year,
                    max_year=max_year,
                    best_only=self.best_only,
                )
            except JustWatchGraphQLError as exc:
                if exc.page_too_large and page_size > 1:
                    # Measured: the page ceiling is a server-side complexity
                    # budget (~800/title, limit 350000). Halving and retrying
                    # the *same* offset keeps a catalogue walk working if the
                    # endpoint tightens it. Accumulated pages, the page budget
                    # and the partition context all survive because this does
                    # not restart the walk.
                    page_size = max(1, page_size // 2)
                    logger.warning("justwatch_page_clamped country=%s offset=%s "
                                   "new_page_size=%s", country, next_offset,
                                   page_size)
                    continue
                # A GraphQL rejection is a failed fetch, never an empty page.
                raise self._as_fetch_error("catalogue", exc) from exc
            except JustWatchError as exc:
                raise self._as_fetch_error("catalogue", exc) from exc

            if not page.titles:
                # The endpoint answers past its depth wall with an empty page.
                # That is the signal to stop rather than to keep walking an
                # empty partition.
                logger.info(
                    "justwatch_catalogue_exhausted country=%s offset=%s "
                    "provider=%s", country, next_offset, provider)
                exhausted = True
                break

            for title in page.titles:
                if not title.has_native_id:
                    logger.warning("justwatch_native_id_missing country=%s title=%r",
                                   country, title.title)
                    continue
                by_native_id[title.id].append(title)

            next_offset += page_size
            remaining_pages -= 1
            if next_offset >= self.max_offset:
                exhausted = True
                break

        all_contents, all_offers = [], []
        for candidates in by_native_id.values():
            title = self._merge_title_matches(candidates, candidates)
            if title is None:
                logger.warning("justwatch_native_id_unresolved country=%s id=%s",
                               country, candidates[0].id)
                continue
            resolved_type = self._content_type(title)
            # The partition owns the catalogue row. The package filter selects
            # titles, so their offers also need filtering by partition provider.
            all_contents.append(self._content(title, resolved_type, country, provider=provider))
            all_offers.extend(self._offers_for(
                title, country=country, content_type=resolved_type,
                tmdb_id=title.tmdb_id_int, content=title, only_provider=provider,
            ))

        return SourceFetchResult(
            contents=all_contents,
            offers=all_offers,
            # Incremental: a catalogue page is never a complete snapshot, so
            # it must not be able to deactivate anything.
            sync_mode=SyncMode.INCREMENTAL,
            cursor=self._next_cursor(
                partition_id=partition_id,
                offset=next_offset,
                exhausted=exhausted,
            ),
        )

    @staticmethod
    def _next_cursor(*, partition_id, offset, exhausted):
        """The cursor this partition hands back, preserving its siblings'.

        ``SyncCursor`` is unique on ``(provider, source, country)``, so every
        partition of one provider/country shares a single row. Writing a bare
        offset here would erase the offsets of the sibling partitions — the
        MOVIE walk silently resetting the SERIES walk — so the row carries a
        small ``partition_id=offset`` map and only this partition's entry moves.

        Without a partition id (a direct ``fetch()`` caller, or a title-targeted
        refresh) the cursor stays a bare offset, exactly as before.
        """
        if not partition_id:
            return None if exhausted else str(offset)
        # Escaped with the same rules the coordinator decodes with, so a
        # partition id containing the map's separators cannot split its own
        # entry. See ``tracker.services.justwatch_sync._encode_cursor``.
        key = partition_id.replace("%", "%25").replace("=", "%3D").replace("&", "%26")
        if exhausted:
            # Recorded as an empty value so the partition reads as complete and
            # a later run can start it over, matching the pre-P8 behaviour for
            # a partition that hit the depth wall.
            return f"{key}="
        return f"{key}={offset}"

    # -- translation ------------------------------------------------------
    def _offers_for(self, title, *, country, content_type, tmdb_id, content,
                    only_provider=None):
        """Translate one title node's offers into canonical provider offers."""
        offers = []
        for offer in title.offers:
            provider_slug = provider_slug_for_package(offer.package)
            if not provider_slug:
                # Unmapped JustWatch package (hundreds exist per country; we
                # map the brands we track). Skipped, but logged at WARNING so
                # the coverage gap is observable in production logs rather
                # than silently swallowed: an unmapped package is why a title
                # can look unavailable when JustWatch in fact lists it.
                logger.warning(
                    "justwatch_provider_unmapped title=%s country=%s "
                    "package=%s clear=%s monetization=%s",
                    title.id, country,
                    getattr(offer.package, "technical_name", None),
                    getattr(offer.package, "clear_name", None),
                    getattr(offer, "monetization_type", None),
                )
                continue
            if only_provider and provider_slug != only_provider:
                continue
            offers.append(self._offer(
                offer, provider_slug=provider_slug, country=country,
                content_type=content_type, tmdb_id=tmdb_id, content=content,
                node=title,
            ))
        return offers

    def _offer(self, offer, *, provider_slug, country, content_type, tmdb_id,
               content, node):
        provider_content_id = str(node.id or "")
        price = normalize_price(
            offer.retail_price_value, offer.currency, offer.retail_price,
        )
        return ProviderOffer(
            provider_id=provider_slug,
            provider_content_id=provider_content_id,
            country=country,
            content_type=content_type,
            tmdb_id=tmdb_id,
            season_number=node.season_number if content_type == ContentType.SEASON else None,
            episode_number=node.episode_number if content_type == ContentType.EPISODE else None,
            offer_type=normalize_justwatch_offer_type(offer.monetization_type),
            provider_url=offer.url or "",
            quality=normalize_justwatch_quality(offer.presentation_type),
            audio_languages=normalize_languages(offer.audio_languages),
            subtitle_languages=normalize_languages(offer.subtitle_languages),
            available_until=self._parse_datetime(offer.available_to),
            source_record_id=str(offer.id or ""),
            # JustWatch titles are identified by a stable node id (tm*/ts*/tss*),
            # which is the provenance stamp for this observation.
            source_version=str(node.object_type or ""),
            price_amount=price.amount,
            price_currency=price.currency,
            price_error=price.error,
            source_variant=justwatch_offer_variant(offer, price),
        )

    def _content(self, title, content_type, country, *, provider=None):
        """Provider-native content entry, identified by JustWatch's node id.

        ``external_ids`` carries the TMDb/IMDb ids when present, so a later
        refresh can resolve the mapping without re-querying; the refresh
        service's matching step prefers them over title/year.

        ``provider`` is the partition's provider when a catalogue walk supplies
        one. A title's offers span several providers, but a provider-filtered
        catalogue row belongs to the provider whose partition discovered it —
        the first mapped package on the node is not a substitute for that
        context.
        """
        external_ids = {}
        if title.tmdb_id_int:
            external_ids["tmdb"] = str(title.tmdb_id_int)
        if title.imdb_id:
            external_ids["imdb"] = str(title.imdb_id)
        return DiscoveredContent(
            provider_id=provider or self._dominant_provider_slug(title),
            provider_content_id=str(title.id or ""),
            content_type=content_type,
            title=title.title,
            release_date=self._parse_date(title),
            season_number=title.season_number if content_type == ContentType.SEASON else None,
            episode_number=title.episode_number if content_type == ContentType.EPISODE else None,
            duration=title.runtime,
            url=title.url or "",
            poster=title.poster or "",
            description=title.description or "",
            external_ids=external_ids,
            source_id=self.slug,
        )

    @staticmethod
    def _dominant_provider_slug(title: JustWatchTitle):
        """Best-effort provider for the provider-native catalogue row.

        ``DiscoveredContent`` carries no country, so it must name a provider
        with something that resolves without one: the canonical slug of the
        first *mapped* package on the title. Only providers we track are
        considered; if none are, the content is skipped by the refresh service
        rather than attributed to a guess.
        """
        for offer in title.offers:
            slug = provider_slug_for_package(offer.package)
            if slug:
                return slug
        return ""

    @staticmethod
    def _content_type(title):
        """Canonical content type for a node, or ``None`` to skip it."""
        object_type = str(title.object_type or "").strip().upper()
        if object_type in IGNORED_OBJECT_TYPES:
            logger.debug("justwatch_content_ignored object_type=%s id=%s",
                         object_type, title.id)
            return None
        content_type = OBJECT_TYPE_CONTENT_TYPES.get(object_type)
        if content_type is None:
            logger.warning(
                "justwatch_content_unknown_object_type object_type=%s id=%s "
                "title=%r", object_type, title.id, title.title,
            )
            return None
        return content_type

    @staticmethod
    def _object_type(content_type):
        """Canonical content type -> JustWatch ``objectTypes`` token."""
        for object_type, canonical in OBJECT_TYPE_CONTENT_TYPES.items():
            if canonical == content_type:
                return object_type
        return None

    @staticmethod
    def _parse_date(title):
        """Release date from ``originalReleaseDate``, else Jan 1 of the year.

        Year precision is stored as Jan 1 (the same convention the boundary
        value documents), so a year-only release still matches on year.
        """
        raw = str(title.release_date or "").strip()
        if raw:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                pass
        if title.year:
            try:
                return date(int(title.year), 1, 1)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _parse_datetime(value):
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if timezone.is_naive(parsed):
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        return parsed

    @staticmethod
    def _require_country(country):
        """Country is an explicit caller decision — never inferred.

        There is no IP/VPN/browser fallback by design: the application's
        country selection stays authoritative, and an aggregator asked about
        the wrong market produces confidently wrong offers.
        """
        if not country:
            raise SourceFetchError(
                "justwatch requires an explicit country code; it is never "
                "inferred from IP, VPN or browser locale"
            )
        return normalize_country(country)

    @staticmethod
    def _cursor_offset(cursor):
        """Read a paging offset out of whatever cursor form was passed."""
        if cursor is None:
            return 0
        if isinstance(cursor, dict):
            return int(cursor.get("offset") or 0)
        try:
            return max(0, int(cursor))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _as_fetch_error(operation, exc):
        """Wrap a transport error as ``SourceFetchError`` — a *failure*.

        The refresh service propagates this without creating any SyncRun, so a
        failed fetch is recorded as failure and can never be mistaken for a
        successful empty catalogue.
        """
        return SourceFetchError(f"justwatch {operation} failed: {exc}")

    def offers_for_countries(self, node_id, countries, *, language=None):
        """Offers for several countries in **one** request.

        This is the multi-country path the API supports directly (one aliased
        ``offers(country: ...)`` field per country) and is why a title-targeted
        fetch does not cost one request per market.
        """
        normalized = sorted({normalize_country(c) for c in countries if c})
        if not normalized:
            raise SourceFetchError("offers_for_countries requires at least one country")
        try:
            return self.client.offers_for_countries(
                node_id, normalized,
                language=language or self.language, best_only=self.best_only,
            )
        except JustWatchError as exc:
            raise self._as_fetch_error("offers_for_countries", exc) from exc
