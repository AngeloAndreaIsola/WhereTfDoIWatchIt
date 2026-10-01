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


class FreshnessState(models.TextChoices):
    """How old one observation is relative to its source's expected refresh."""

    FRESH = "fresh", "Fresh"
    STALE = "stale", "Stale"
    UNKNOWN = "unknown", "Unknown"
