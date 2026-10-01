import os
from django.db import models
from django.utils import timezone

if os.environ.get("USE_SQLITE") == "1":
    class ArrayField(models.JSONField):
        def __init__(self, base_field=None, **kwargs):
            self.base_field = base_field
            kwargs.pop("size", None)
            super().__init__(**kwargs)

        def deconstruct(self):
            name, path, args, kwargs = super().deconstruct()
            if self.base_field:
                args = [self.base_field] + list(args)
            return name, path, args, kwargs
else:
    from django.contrib.postgres.fields import ArrayField


class Movie(models.Model):
    CONTENT_TYPES = [
        ("movie", "Movie"),
        ("series", "Series"),
    ]

    tmdb_id = models.IntegerField(unique=True, db_index=True)
    title = models.CharField(max_length=255)
    year = models.IntegerField(null=True, blank=True)
    content_type = models.CharField(
        max_length=10,
        choices=CONTENT_TYPES,
        default="movie",
        db_index=True,
    )
    poster_path = models.CharField(max_length=255, blank=True, default="")
    overview = models.TextField(blank=True, default="")
    genres = models.JSONField(default=list, blank=True)
    genre_ids = models.JSONField(default=list, blank=True)
    cast = models.JSONField(default=list, blank=True)
    director = models.JSONField(default=list, blank=True, help_text="List of director names (movie) or creators (series).")
    seasons_data = models.JSONField(default=list, blank=True)
    runtime_minutes = models.IntegerField(null=True, blank=True)
    tmdb_rating = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} ({self.year})" if self.year else self.title


class Region(models.Model):
    provider = models.CharField(max_length=50)
    country = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "country"], name="uniq_region_provider_country")
        ]

    def __str__(self):
        return f"{self.provider} {self.country}"


class Availability(models.Model):
    OFFER_TYPES = [
        ("subscription", "Subscription"),
        ("free", "Free"),
        ("rent", "Rent"),
        ("buy", "Buy"),
    ]
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="availabilities")
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="availabilities")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    is_available = models.BooleanField(default=False)
    offer_type = models.CharField(max_length=20, choices=OFFER_TYPES)
    quality = models.CharField(max_length=10, blank=True)
    price_info = models.CharField(max_length=50, blank=True, default="")
    url = models.URLField(max_length=500, blank=True, default="")
    seasons_available = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["movie", "region"], name="uniq_availability_movie_region")
        ]

    def __str__(self):
        return f"{self.movie} @ {self.region} ({self.get_offer_type_display()})"


class LanguageInfo(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="language_infos")
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="language_infos")
    # Series rows may be per-season (season 1..N); NULL means show-level —
    # the only shape movies use and the fallback when a season has no row.
    season = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            # One row per (movie, region, season). Postgres treats NULL seasons
            # as distinct, so show-level uniqueness is enforced separately.
            models.UniqueConstraint(
                fields=["movie", "region", "season"],
                name="uniq_languageinfo_movie_region_season",
            ),
            models.UniqueConstraint(
                fields=["movie", "region"],
                condition=models.Q(season__isnull=True),
                name="uniq_languageinfo_show_level",
            ),
        ]

    def __str__(self):
        suffix = f" S{self.season}" if self.season is not None else ""
        return f"{self.movie} @ {self.region}{suffix}"


class LanguageEntry(models.Model):
    KINDS = [("audio", "Audio"), ("subtitle", "Subtitle")]
    language_info = models.ForeignKey(LanguageInfo, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=10, choices=KINDS)
    language_code = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["language_info", "kind", "language_code"],
                name="uniq_languageentry_info_kind_code",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.language_code}"


class ProviderCategory(models.TextChoices):
    OFFICIAL = "official", "Official / Major Provider"
    BVOD = "bvod", "Broadcaster Video on Demand"
    AVOD = "avod", "Ad-Supported Video on Demand"
    TVOD = "tvod", "Transactional Video on Demand"
    FAST = "fast", "Free Ad-Supported Streaming TV"
    SCRAPER = "scraper", "Scraper / Small Source"


class Provider(models.Model):
    slug = models.SlugField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    category = models.CharField(
        max_length=20,
        choices=ProviderCategory.choices,
        default=ProviderCategory.OFFICIAL,
    )
    is_major = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    icon_name = models.CharField(max_length=50, blank=True, default="")
    domain = models.CharField(max_length=100, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="", help_text="Primary country for this provider, if region-specific.")
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    config = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.name} ({self.slug})"

    @property
    def source_indicator(self) -> str:
        """Return a short label for the source type badge in the UI."""
        if isinstance(self.config, dict) and self.config.get("business_model"):
            return str(self.config["business_model"]).upper()
        if self.category == ProviderCategory.OFFICIAL:
            return "SVOD" if self.is_major else "Official"
        if self.category == ProviderCategory.SCRAPER:
            return "Scraper"
        return self.category.upper()


class ExternalProviderMapping(models.Model):
    provider = models.ForeignKey(
        Provider, on_delete=models.CASCADE, related_name="external_mappings"
    )
    integration_name = models.CharField(max_length=50)  # e.g. "streaming_availability", "tmdb"
    external_id = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "integration_name"],
                name="uniq_external_provider_mapping",
            )
        ]

    def __str__(self):
        return f"{self.provider.slug} -> {self.integration_name}:{self.external_id}"


class ScrapedContentMapping(models.Model):
    provider = models.ForeignKey(
        Provider, on_delete=models.CASCADE, related_name="scraped_mappings"
    )
    scraped_title = models.CharField(max_length=255)
    scraped_year = models.IntegerField(null=True, blank=True)
    scraped_url = models.URLField(max_length=500, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="", help_text="Country this scrape result applies to.")
    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scraped_mappings",
    )
    is_confirmed = models.BooleanField(default=False)
    confidence_score = models.FloatField(default=0.0)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_scraped_at = models.DateTimeField(null=True, blank=True, help_text="When the source page was last scraped; null = never or unknown.")
    is_available = models.BooleanField(default=True, help_text="Whether the title is currently available on this source.")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "scraped_title", "scraped_year"],
                name="uniq_scraped_content_mapping",
            )
        ]

    def __str__(self):
        status_str = "Confirmed" if self.is_confirmed else "Unconfirmed"
        return f"{self.provider.slug}: '{self.scraped_title}' -> {self.movie} ({status_str})"


class Subscription(models.Model):
    user_id = models.IntegerField(default=1)
    provider = models.ForeignKey(
        Provider,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "provider"],
                name="uniq_user_provider_subscription",
            )
        ]

    def __str__(self):
        if self.provider:
            return f"User {self.user_id} -> {self.provider.name}"
        return f"User {self.user_id} -> {self.name}"


class Preference(models.Model):
    user_id = models.IntegerField(default=1)
    audio_languages = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    subtitle_languages = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    candidate_countries = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    current_country = models.CharField(max_length=2, blank=True, default="", null=True)
    language_rule = models.JSONField(default=dict, blank=True, null=True)

    def __str__(self):
        return f"Preference user {self.user_id}"


class WatchlistItem(models.Model):
    user_id = models.IntegerField(default=1)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="watchlist_items")
    source = models.CharField(max_length=20, default="letterboxd")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["movie", "source"], name="uniq_watchlistitem_movie_source")
        ]

    def __str__(self):
        return f"{self.source}: {self.movie}"


class ApiCallCounter(models.Model):
    service = models.CharField(max_length=50, unique=True)
    call_date = models.DateField(auto_now_add=True)
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["service"], name="uniq_apicallcounter_service"),
        ]

    def __str__(self):
        return f"{self.service}: {self.count} on {self.call_date}"


class RefreshRun(models.Model):
    service = models.CharField(max_length=50, default="streaming_availability")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    rows_refreshed = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    rate_limited = models.BooleanField(default=False)
    # Non-empty only when the run failed. A finished run with an empty error
    # is a genuine success (including a genuine empty result), so a failed
    # catalogue fetch can never masquerade as a successful refresh.
    error = models.TextField(blank=True, default="")

    def __str__(self):
        return f"RefreshRun {self.service} @ {self.started_at}"


class UserMovieData(models.Model):
    user_id = models.IntegerField(default=1)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="user_data")
    is_watched = models.BooleanField(default=False)
    rating = models.FloatField(null=True, blank=True)
    review = models.TextField(blank=True, default="")
    letterboxd_uri = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    # watched_date field for manual watchlist management
    watched_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id", "movie"], name="uniq_usermoviedata_user_movie")
        ]

    def __str__(self):
        return f"User {self.user_id} data for {self.movie}"


class MovieWatchEvent(models.Model):
    user_id = models.IntegerField(default=1)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="watch_events")
    watched_date = models.DateField(null=True, blank=True)
    rating = models.FloatField(null=True, blank=True)
    review = models.TextField(blank=True, default="")
    rewatch = models.BooleanField(default=False)
    letterboxd_uri = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "movie", "watched_date", "letterboxd_uri"],
                name="uniq_moviewatchevent_user_movie_date_uri",
            )
        ]

    def __str__(self):
        return f"User {self.user_id} watched {self.movie} on {self.watched_date}"
