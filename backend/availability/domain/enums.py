from django.db import models


class ContentType(models.TextChoices):
    MOVIE = "movie", "Movie"
    SERIES = "series", "Series"
    SEASON = "season", "Season"
    EPISODE = "episode", "Episode"


class OfferType(models.TextChoices):
    SUBSCRIPTION = "subscription", "Subscription"
    FREE = "free", "Free"
    FREE_WITH_ADS = "free_with_ads", "Free with ads"
    RENT = "rent", "Rent"
    BUY = "buy", "Buy"
    ADDON = "addon", "Addon"
    UNKNOWN = "unknown", "Unknown"


class SourceType(models.TextChoices):
    API = "api", "API"
    FEED = "feed", "Feed"
    WEB = "web", "Web"
    AGGREGATOR = "aggregator", "Aggregator"


class SyncMode(models.TextChoices):
    FULL = "full", "Full"
    INCREMENTAL = "incremental", "Incremental"
    TARGETED = "targeted", "Targeted"


class SyncStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    #: Some partitions succeeded and others failed (page 0 ok, page 2 error).
    #: Explicitly *not* ``SUCCEEDED``: downstream code must be able to tell
    #: "this source saw everything it was asked to see" from "this source saw
    #: part of it". Deactivation is refused for both ``PARTIAL`` and
    #: ``FAILED`` — ``OfferRepository.deactivate_unseen`` only accepts
    #: ``SUCCEEDED`` — so an incomplete observation can never erase evidence.
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"


class MatchMethod(models.TextChoices):
    MANUAL = "manual", "Manual"
    PROVIDER_MAPPING = "provider_mapping", "Provider mapping"
    EXTERNAL_ID = "external_id", "External ID"
    TITLE_YEAR = "title_year", "Title/year"
    FUZZY_TITLE_YEAR = "fuzzy_title_year", "Fuzzy title/year"


class ConflictKind(models.TextChoices):
    """Ways raw observations for one reconciliation key may disagree.

    Read-side only: no Offer column stores these values, so they never
    reach a migration.
    """

    AVAILABILITY = "availability", "Availability"
    OFFER_TYPE = "offer_type", "Offer type"
    MAPPING = "mapping", "Mapping"
    WINDOW = "window", "Window"
    QUALITY = "quality", "Quality"
    LANGUAGE = "language", "Language"
    FRESHNESS = "freshness", "Freshness"
    PRICE = "price", "Price"


class FreshnessState(models.TextChoices):
    """How old one observation is relative to its source's expected refresh."""

    FRESH = "fresh", "Fresh"
    STALE = "stale", "Stale"
    UNKNOWN = "unknown", "Unknown"


class AvailabilityEvidence(models.TextChoices):
    """How strongly the stored observations support "watchable right now".

    Read-side presentation state derived at reconciliation time; it never
    rewrites the raw lifecycle (``Offer.currently_available``) or timestamps.

    * ``VERIFIED`` — a positive observation, inside its offer window, observed
      within its source's declared freshness window.
    * ``STALE`` — the same positive evidence, but older than that window: the
      offer was last seen available and needs a recheck before it may count
      as currently watchable.
    * ``UNKNOWN`` — positive but uncorroborated evidence: the source declares
      no freshness window, or the provider content's TMDB mapping is
      ambiguous. Never read as unavailable.
    * ``UNAVAILABLE`` — genuine negative evidence: deactivated by an
      authoritative sync, or the offer window is closed.
    """

    VERIFIED = "verified", "Verified"
    SOURCE_REPORTED = "source_reported", "Source-reported"
    STALE = "stale", "Last seen"
    UNKNOWN = "unknown", "Unverified"
    UNAVAILABLE = "unavailable", "Unavailable"


class CandidateReviewState(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    CONFLICT = "conflict", "Conflict"
    AMBIGUOUS = "ambiguous", "Ambiguous"


class CandidateResolutionCategory(models.TextChoices):
    UNIQUE_CANDIDATE = "unique_candidate", "Unique candidate"
    AMBIGUOUS = "ambiguous", "Ambiguous"
    CONFLICT = "conflict", "Conflict"
    NO_RESULTS = "no_results", "No results"
    REJECTED = "rejected", "Rejected"
