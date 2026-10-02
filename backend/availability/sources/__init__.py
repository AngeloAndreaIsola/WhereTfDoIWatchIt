"""External availability sources. Sources return normalized values, never ORM rows."""
from .base import (
    AvailabilitySource,
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
)
from .registry import (
    DEFAULT_SOURCE_SLUG,
    SourceRegistry,
    UnknownSourceError,
    source_registry,
)
from .abc_iview import AbcIviewSource
from .bbc_iplayer import BBCIPlayerSource
from .cbc_gem import CBCGemSource
from .mediaset_infinity import MediasetInfinitySource
from .pbs import PbsSource
from .pluto_tv import PlutoTvSource
from .prime_video import PrimeVideoSource
from .rai_play import RaiPlaySource
from .streaming_availability import StreamingAvailabilitySource
from .tubi import TubiSource
from .youtube import YouTubeSource

# Built-in implementations live here; the DB Source.slug only *identifies* a
# configured source — this registration is what connects it to code.
source_registry.register(StreamingAvailabilitySource.slug, StreamingAvailabilitySource)
source_registry.register(CBCGemSource.slug, CBCGemSource)
source_registry.register(BBCIPlayerSource.slug, BBCIPlayerSource)
source_registry.register(TubiSource.slug, TubiSource)
source_registry.register(PrimeVideoSource.slug, PrimeVideoSource)
source_registry.register(RaiPlaySource.slug, RaiPlaySource)
source_registry.register(YouTubeSource.slug, YouTubeSource)
source_registry.register(PlutoTvSource.slug, PlutoTvSource)
source_registry.register(MediasetInfinitySource.slug, MediasetInfinitySource)
source_registry.register(PbsSource.slug, PbsSource)
source_registry.register(AbcIviewSource.slug, AbcIviewSource)

__all__ = [
    "AvailabilitySource",
    "BBCIPlayerSource",
    "CBCGemSource",
    "ContentRequest",
    "DEFAULT_SOURCE_SLUG",
    "MediasetInfinitySource",
    "PlutoTvSource",
    "PbsSource",
    "AbcIviewSource",
    "PrimeVideoSource",
    "RaiPlaySource",
    "SourceCapabilities",
    "SourceFetchError",
    "SourceFetchResult",
    "SourceRegistry",
    "StreamingAvailabilitySource",
    "TubiSource",
    "UnknownSourceError",
    "YouTubeSource",
    "source_registry",
]
