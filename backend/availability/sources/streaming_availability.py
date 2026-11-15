"""Adapter for the existing Streaming Availability API client.

Reference implementation of the :class:`AvailabilitySource` interface: the
adapter retrieves provider-specific data (one TMDb-targeted API call across
all countries), maps it through the shared ``normalize`` helpers into
canonical :class:`ProviderOffer` values, and returns a
:class:`SourceFetchResult`. It never writes rows and never claims
authoritativeness.

Scope hints (``provider``/``country``) and ``cursor`` are accepted for
interface compatibility but ignored: this endpoint returns every country's
options for one title in a single request, with no pagination.
"""
from __future__ import annotations

from tracker.services.streaming_api import StreamingAvailabilityClient, resolve_season_number

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
    normalize_content_type, normalize_country, normalize_languages, normalize_offer_type,
)
from .base import ContentRequest, SourceCapabilities, SourceFetchResult


class StreamingAvailabilitySource:
    slug = "streaming_availability"
    name = "Streaming Availability API"
    source_type = SourceType.API
    capabilities = SourceCapabilities(
        # Title-targeted: movie/series plus the season offers a series exposes.
        supported_content_types=frozenset(
            {ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON}
        ),
        supports_incremental_sync=False,
        supports_title_fetch=True,
        supports_catalogue_sync=False,
        # ``authoritative`` must mean: these offers are the COMPLETE snapshot of
        # the provider+country scope a SyncRun covers, so unseen offers there may
        # be deactivated. A title-targeted endpoint returning an unknown (and
        # offer-type-filtered) subset of each catalogue can never claim that, so
        # this source is permanently non-authoritative.
        supports_authoritative_snapshots=False,
        supports_tmdb_ids=True,
        supports_seasons=True,
        supports_episodes=False,
        supports_languages=True,
    )

    def __init__(self, client=None):
        self.client = client or StreamingAvailabilityClient()

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is None:
            raise ValueError(
                "streaming_availability is title-targeted: a content request is required"
            )
        request = content if isinstance(content, ContentRequest) else ContentRequest.from_content(content)
        result = self.client.get_availability(request.tmdb_id, content_type=request.content_type)
        content_type = normalize_content_type(request.content_type)
        offers: list[ProviderOffer] = []
        contents: list[DiscoveredContent] = []
        for offer in result.offers:
            offers.append(self._offer(offer, result.tmdb_id, content_type))
            contents.append(self._content(offer.service_id, str(result.tmdb_id), content_type, request))
        if content_type == ContentType.SERIES:
            for season in result.seasons or []:
                number = resolve_season_number(season, request.seasons_data)
                if number is None:
                    continue
                for offer in season.offers or []:
                    content_id = f"{result.tmdb_id}:season:{number}"
                    offers.append(self._offer(offer, result.tmdb_id, ContentType.SEASON, number, content_id))
                    contents.append(self._content(offer.service_id, content_id, ContentType.SEASON, request, number))
        return SourceFetchResult(contents=contents, offers=offers, sync_mode=SyncMode.TARGETED)

    @staticmethod
    def _content(provider_id, provider_content_id, content_type, request, season_number=None):
        return DiscoveredContent(provider_id=str(provider_id).lower(), provider_content_id=provider_content_id,
            content_type=content_type, title=request.title, season_number=season_number,
            external_ids={"tmdb": str(request.tmdb_id)}, source_id=StreamingAvailabilitySource.slug)

    @staticmethod
    def _offer(offer, tmdb_id, content_type, season_number=None, provider_content_id=None):
        provider_content_id = provider_content_id or str(tmdb_id)
        country = normalize_country(offer.country)
        provider_id = str(offer.service_id).strip().lower()
        return ProviderOffer(
            provider_id=provider_id, provider_content_id=provider_content_id, country=country,
            content_type=content_type, tmdb_id=tmdb_id, season_number=season_number,
            episode_number=None, offer_type=normalize_offer_type(offer.offer_type),
            provider_url=offer.link or "", quality=(offer.quality or "").lower(),
            audio_languages=normalize_languages(offer.audio_languages),
            subtitle_languages=normalize_languages(offer.subtitle_languages),
            source_record_id=f"{provider_id}:{country}:{content_type}:{season_number if season_number is not None else ''}:{offer.offer_type}",
            source_version="v4",
        )
