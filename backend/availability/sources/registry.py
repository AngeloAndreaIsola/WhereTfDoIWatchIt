"""Registry/factory for availability sources.

Connects the database ``Source.slug`` (configured provenance) to the
application-code implementation:

    DB:      Source(slug="streaming_availability")
    Code:    StreamingAvailabilitySource
    Registry: source_registry.get("streaming_availability")

Registration is explicit application code — no dynamic imports from database
configuration, and no database access at all to instantiate a source.
"""
from __future__ import annotations

from typing import Callable

DEFAULT_SOURCE_SLUG = "streaming_availability"


class UnknownSourceError(LookupError):
    """Raised when no source implementation is registered for a slug."""

    def __init__(self, slug: str, known_slugs: tuple[str, ...]):
        self.slug = slug
        self.known_slugs = known_slugs
        known = ", ".join(known_slugs) or "<none>"
        super().__init__(
            f"Unknown availability source {slug!r}. Registered sources: {known}. "
            "Register it in availability.sources (application code owns implementations)."
        )


class SourceRegistry:
    """Maps source slugs to zero-argument factories returning a source."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], object]] = {}

    def register(self, slug: str, factory: Callable[[], object]) -> None:
        if slug in self._factories:
            raise ValueError(
                f"Availability source {slug!r} is already registered; "
                "registration is owned by application code, not data."
            )
        self._factories[slug] = factory

    def get(self, slug: str):
        try:
            factory = self._factories[slug]
        except KeyError:
            raise UnknownSourceError(slug, self.slugs()) from None
        return factory()

    def slugs(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


source_registry = SourceRegistry()
