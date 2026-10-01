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
from .bbc_iplayer import BBCIPlayerSource
from .cbc_gem import CBCGemSource
from .prime_video import PrimeVideoSource
from .rai_play import RaiPlaySource
from .streaming_availability import StreamingAvailabilitySource
from .tubi import TubiSource

# Built-in implementations live here; the DB Source.slug only *identifies* a
# configured source — this registration is what connects it to code.
source_registry.register(StreamingAvailabilitySource.slug, StreamingAvailabilitySource)
source_registry.register(CBCGemSource.slug, CBCGemSource)
source_registry.register(BBCIPlayerSource.slug, BBCIPlayerSource)
source_registry.register(TubiSource.slug, TubiSource)
source_registry.register(PrimeVideoSource.slug, PrimeVideoSource)
source_registry.register(RaiPlaySource.slug, RaiPlaySource)

__all__ = [
    "AvailabilitySource",
    "BBCIPlayerSource",
    "CBCGemSource",
    "ContentRequest",
    "DEFAULT_SOURCE_SLUG",
    "PrimeVideoSource",
    "RaiPlaySource",
    "SourceCapabilities",
    "SourceFetchError",
    "SourceFetchResult",
    "SourceRegistry",
    "StreamingAvailabilitySource",
    "TubiSource",
    "UnknownSourceError",
    "source_registry",
]
