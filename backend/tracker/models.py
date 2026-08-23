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
    tmdb_id = models.IntegerField(unique=True, db_index=True)
    title = models.CharField(max_length=255)
    year = models.IntegerField(null=True, blank=True)
    poster_path = models.CharField(max_length=255, blank=True, default="")

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
    url = models.URLField(max_length=500, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["movie", "region"], name="uniq_availability_movie_region")
        ]

    def __str__(self):
        return f"{self.movie} @ {self.region} ({self.get_offer_type_display()})"


class LanguageInfo(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="language_infos")
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="language_infos")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["movie", "region"], name="uniq_languageinfo_movie_region")
        ]

    def __str__(self):
        return f"{self.movie} @ {self.region}"


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
    last_scraped_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.slug})"


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
