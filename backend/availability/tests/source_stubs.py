"""Shared in-process test doubles for Phase 4 source-architecture tests."""
from __future__ import annotations

from ..domain.enums import ContentType, OfferType, SourceType, SyncMode
from ..domain.models import ProviderOffer
from ..sources.base import SourceCapabilities, SourceFetchResult

# A test source claims every capability unless a test narrows it. Content
# types stay unrestricted so malformed values still reach the repository's
# own validation (rollback tests depend on that).
STUB_CAPABILITIES = SourceCapabilities(
    supported_providers=None,
    supported_countries=None,
    supported_content_types=None,
    supports_incremental_sync=True,
    supports_catalogue_sync=True,
    supports_authoritative_snapshots=True,
    supports_tmdb_ids=True,
    supports_seasons=True,
    supports_episodes=True,
    supports_languages=True,
)


class StubSource:
    """Configurable source conforming to the AvailabilitySource interface."""

    slug = "stub_source"
    name = "Stub Source"
    source_type = SourceType.API
    capabilities = STUB_CAPABILITIES

    def __init__(self, response=None, error=None, *, slug=None, name=None,
                 capabilities=None, source_type=None):
        self.response = response if response is not None else SourceFetchResult()
        self.error = error
        if slug is not None:
            self.slug = slug
        if name is not None:
            self.name = name
        if capabilities is not None:
            self.capabilities = capabilities
        if source_type is not None:
            self.source_type = source_type
        self.fetches = []

    def fetch(self, content=None, *, provider=None, country=None, cursor=None):
        self.fetches.append(content)
        if self.error is not None:
            raise self.error
        return self.response


def normalized_offer(**overrides) -> ProviderOffer:
    """A fully-specified canonical offer; override only what a test exercises."""
    payload = dict(
        provider_id="netflix",
        provider_content_id="550",
        country="US",
        content_type=ContentType.MOVIE,
        tmdb_id=550,
        season_number=None,
        episode_number=None,
        offer_type=OfferType.SUBSCRIPTION,
        provider_url="https://example.test/watch",
        quality="hd",
        audio_languages=["EN"],
        subtitle_languages=["EN"],
        source_record_id="netflix:US",
        source_version="v4",
    )
    payload.update(overrides)
    return ProviderOffer(**payload)


def fetch_result(*offers, contents=None, authoritative=False,
                 complete_snapshot=False, sync_mode=SyncMode.TARGETED,
                 cursor=None) -> SourceFetchResult:
    """A SourceFetchResult with explicit snapshot semantics."""
    return SourceFetchResult(
        offers=list(offers),
        contents=list(contents or []),
        authoritative=authoritative,
        complete_snapshot=complete_snapshot,
        sync_mode=sync_mode,
        cursor=cursor,
    )


def content_request(tmdb_id=550, content_type="movie", title="Fight Club",
                    year=1999, seasons_data=None):
    from ..sources.base import ContentRequest

    return ContentRequest(
        tmdb_id=tmdb_id,
        content_type=content_type,
        title=title,
        year=year,
        seasons_data=list(seasons_data or []),
    )
