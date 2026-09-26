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


class ProviderCategory(models.TextChoices):
    OFFICIAL = "official", "Official / Major Provider"
    BVOD = "bvod", "Broadcaster Video on Demand"
    AVOD = "avod", "Ad-Supported Video on Demand"
    TVOD = "tvod", "Transactional Video on Demand"
    FAST = "fast", "Free Ad-Supported Streaming TV"
    SCRAPER = "scraper", "Scraper / Small Source"


CONTENT_TYPES = [
    ("movie", "Movie"),
    ("series", "Series"),
]


class Movie(models.Model):
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
    director = models.JSONField(default=list, blank=True,
                                help_text="List of director names (movie) or creators (series).")
    seasons_data = models.JSONField(default=list, blank=True)
    runtime_minutes = models.IntegerField(null=True, blank=True)
    tmdb_rating = models.FloatField(null=True, blank=True)
    first_air_date = models.CharField(max_length=20, blank=True, default="")
    release_date = models.CharField(max_length=20, blank=True, default="")

    def get_release_date(self):
        if self.release_date:
            return self.release_date
        return self.first_air_date

    def __str__(self):
        return f"{self.title} ({self.year})"


class Preference(models.Model):
    user_id = models.IntegerField(default=1)
    audio_languages = models.JSONField(default=list, blank=True)
    subtitle_languages = models.JSONField(default=list, blank=True)
    candidate_countries = models.JSONField(default=list, blank=True)
    current_country = models.CharField(max_length=2, blank=True, default="", null=True)
    language_rule = models.JSONField(default=dict, blank=True, null=True)

    def __str__(self):
        return f"Preference user {self.user_id}"


class Subscription(models.Model):
    user_id = models.IntegerField(default=1)
    name = models.CharField(max_length=50, unique=True)
    provider = models.ForeignKey(
        "Provider",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="subscriptions",
    )

    def __str__(self):
        return f"Subscription {self.name}"


class Region(models.Model):
    provider = models.CharField(max_length=50)
    country = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "country"],
                name="uniq_region_provider_country",
            ),
        ]

    def __str__(self):
        return f"{self.provider} - {self.country}"


class LanguageInfo(models.Model):
    movie = models.ForeignKey(
        Movie, on_delete=models.CASCADE, related_name="language_infos"
    )
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="language_infos")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["movie", "region"],
                name="uniq_languageinfo_movie_region",
            ),
        ]

    def __str__(self):
        return f"{self.movie.title} - {self.region}"


class LanguageEntry(models.Model):
    language_info = models.ForeignKey(
        LanguageInfo,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    kind = models.CharField(max_length=10)
    language_code = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["language_info", "kind", "language_code"],
                name="uniq_languageentry_info_kind_code",
            ),
        ]

    def __str__(self):
        return f"{self.language_info} ({self.kind}={self.language_code})"


class Provider(models.Model):
    slug = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    category = models.CharField(
        max_length=50,
        choices=[
            ("major", "Major"),
            ("free", "Free"),
            ("scraper", "Scraper"),
        ],
    )
    is_enabled = models.BooleanField(default=True)
    config = models.JSONField(default=dict, blank=True)
    country_code = models.CharField(max_length=2, blank=True, default="")
    domain = models.CharField(max_length=200, blank=True, default="")
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    url = models.CharField(max_length=500, blank=True, default="")
    poster_path = models.CharField(max_length=500, blank=True, default="")
    price_info = models.JSONField(default=dict, blank=True)
    source_indicator = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Label for the source: 'Official', 'BVOD', 'Scraper', etc.",
    )
    is_major = models.BooleanField(default=False)
    icon_name = models.CharField(max_length=100, blank=True, default="")

    def __str__(self):
        return f"{self.name} ({self.category})"


class Availability(models.Model):
    movie = models.ForeignKey(
        Movie, on_delete=models.CASCADE, related_name="availabilities"
    )
    region = models.ForeignKey(
        Region, on_delete=models.CASCADE, related_name="availabilities"
    )
    is_available = models.BooleanField(default=False)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    offer_type = models.CharField(max_length=20, blank=True, default="")
    quality = models.CharField(max_length=10, blank=True, default="")
    url = models.CharField(max_length=500, blank=True, default="")
    price_info = models.CharField(max_length=50, blank=True, default="")
    seasons_available = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["movie", "region"],
                name="uniq_availability_movie_region",
            ),
        ]

    def __str__(self):
        return f"{self.region.provider} in {self.region.country} for {self.movie.title}"


class WatchlistItem(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="watchlist_items")
    user_id = models.IntegerField(default=1)
    source = models.CharField(default="letterboxd", max_length=20)
    added_at = models.DateTimeField(auto_now_add=True)
    watched = models.BooleanField(default=False)
    watched_at = models.DateTimeField(null=True, blank=True)
    rating = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("movie", "source")

    def __str__(self):
        return f"{self.movie.title} for user {self.user_id}"


class ApiCallCounter(models.Model):
    service = models.CharField(max_length=50, unique=True)
    call_date = models.DateField(auto_now_add=True)
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["service", "call_date"],
                name="uniq_apicallcounter_service_date",
            ),
        ]

    def __str__(self):
        return f"{self.service} on {self.call_date}"


class RefreshRun(models.Model):
    service = models.CharField(default="streaming_availability", max_length=50)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(blank=True, null=True)
    rows_refreshed = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    rate_limited = models.BooleanField(default=False)

    def __str__(self):
        return f"RefreshRun {self.service} @ {self.started_at}"


class ExternalProviderMapping(models.Model):
    """Links a Provider to an external integration (e.g. Streaming Availability API)."""
    provider = models.ForeignKey(
        Provider, on_delete=models.CASCADE, related_name="external_mappings"
    )
    integration_name = models.CharField(max_length=50)
    external_id = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "integration_name"],
                name="uniq_external_provider_mapping",
            ),
        ]

    def __str__(self):
        return f"{self.provider.name} -> {self.integration_name}:{self.external_id}"


class MovieWatchEvent(models.Model):
    user_id = models.IntegerField(default=1)
    watched_date = models.DateField(blank=True, null=True)
    rating = models.FloatField(blank=True, null=True)
    review = models.TextField(blank=True, default="")
    rewatch = models.BooleanField(default=False)
    letterboxd_uri = models.CharField(blank=True, default="", max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="watch_events",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "movie", "watched_date", "letterboxd_uri"],
                name="uniq_moviewatchevent_user_movie_date_uri",
            ),
        ]

    def __str__(self):
        return f"{self.movie.title} watched by user {self.user_id} on {self.watched_date}"


class UserMovieData(models.Model):
    user_id = models.IntegerField(default=1)
    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        related_name="user_data",
    )
    is_watched = models.BooleanField(default=False)
    rating = models.FloatField(blank=True, null=True)
    review = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "movie"],
                name="uniq_usermoviedata_user_movie",
            ),
        ]

    def __str__(self):
        return f"{self.movie.title} data for user {self.user_id}"


class ScrapedContentMapping(models.Model):
    """Links a scraped title/year/URL to a Provider (and optionally a Movie)."""
    scraped_title = models.CharField(max_length=255)
    scraped_year = models.IntegerField(blank=True, null=True)
    scraped_url = models.URLField(blank=True, default="", max_length=500)
    is_confirmed = models.BooleanField(default=False)
    confidence_score = models.FloatField(default=0.0)
    raw_payload = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    movie = models.ForeignKey(
        Movie,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="scraped_mappings",
    )
    provider = models.ForeignKey(
        Provider,
        on_delete=models.CASCADE,
        related_name="scraped_mappings",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "scraped_title", "scraped_year"],
                name="uniq_scraped_content_mapping",
            ),
        ]

    def __str__(self):
        return f"{self.scraped_title} ({self.scraped_year}) -> {self.provider.name}"
