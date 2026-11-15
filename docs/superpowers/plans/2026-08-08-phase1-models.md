# Phase 1 — Data Model & Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the eight Phase 1 models, Django admin registration (with the `LanguageInfo` data-entry screen), and a data migration seeding one empty `Preference` row.

**Architecture:** Extends the Phase 0 Django backend (`config` project, `tracker` app). Models use unique constraints (not `unique_together`), Postgres `ArrayField` for ordered preference lists, and a plain int `user_id` default 1 as the future-accounts seam. All commands run inside the running dev container via docker compose.

**Tech Stack:** Django 5.2, DRF (unused here), Postgres 16, `django.contrib.postgres`.

## Global Constraints

- No `Co-Authored-By` trailer on any commit (user preference — see repo memory).
- Tests run inside the dev container:
  `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2`
- Migrations must be generated and applied:
  `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py makemigrations tracker`
  `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py migrate`
- Do NOT seed any `Subscription` rows and do NOT put non-empty values in `Preference` — user-managed via admin only.
- Language codes are 2-char ISO 639-1; countries are 2-char ISO 3166-1.

---

### Task 1: Model layer with tests and migration

**Files:**
- Modify: `backend/config/settings.py` (add `django.contrib.postgres` to `INSTALLED_APPS`)
- Create: `backend/tracker/models.py`
- Create: `backend/tracker/tests/__init__.py`
- Create: `backend/tracker/tests/test_models.py`
- Migration: `backend/tracker/migrations/0001_initial.py` (generated)

**Interfaces:**
- Produces: model classes `Movie`, `Region`, `Availability`, `LanguageInfo`, `LanguageEntry`, `Subscription`, `Preference`, `WatchlistItem` (exact fields/constraints below). Later tasks import these from `tracker.models`.

- [ ] **Step 1: Add `django.contrib.postgres` to INSTALLED_APPS**

In `backend/config/settings.py`, add `"django.contrib.postgres",` to `INSTALLED_APPS` (required for `ArrayField`).

- [ ] **Step 2: Write the failing tests**

`backend/tracker/tests/test_models.py`:

```python
from django.db import IntegrityError
from django.test import TestCase

from tracker.models import (
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Preference,
    Region,
    Subscription,
    WatchlistItem,
)


class MovieTests(TestCase):
    def test_create_movie(self):
        movie = Movie.objects.create(tmdb_id=123, title="Inception", year=2010)
        self.assertEqual(str(movie), "Inception (2010)")

    def test_tmdb_id_unique(self):
        Movie.objects.create(tmdb_id=123, title="A", year=2010)
        with self.assertRaises(IntegrityError):
            Movie.objects.create(tmdb_id=123, title="B", year=2011)


class RegionTests(TestCase):
    def test_create_region(self):
        region = Region.objects.create(provider="Netflix", country="JP")
        self.assertEqual(str(region), "Netflix JP")

    def test_provider_country_unique(self):
        Region.objects.create(provider="Netflix", country="JP")
        with self.assertRaises(IntegrityError):
            Region.objects.create(provider="Netflix", country="JP")


class AvailabilityTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=1, title="M", year=2020)
        self.region = Region.objects.create(provider="Netflix", country="JP")

    def test_create_availability(self):
        a = Availability.objects.create(
            movie=self.movie, region=self.region, offer_type="subscription"
        )
        self.assertTrue(a.is_available is False)
        self.assertEqual(a.get_offer_type_display(), "Subscription")

    def test_movie_region_unique(self):
        Availability.objects.create(movie=self.movie, region=self.region, offer_type="subscription")
        with self.assertRaises(IntegrityError):
            Availability.objects.create(movie=self.movie, region=self.region, offer_type="free")


class LanguageInfoTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2, title="N", year=2019)
        self.region = Region.objects.create(provider="Prime Video", country="IT")

    def test_language_info_unique_movie_region(self):
        LanguageInfo.objects.create(movie=self.movie, region=self.region)
        with self.assertRaises(IntegrityError):
            LanguageInfo.objects.create(movie=self.movie, region=self.region)

    def test_language_entry_unique_kind_code(self):
        info = LanguageInfo.objects.create(movie=self.movie, region=self.region)
        LanguageEntry.objects.create(language_info=info, kind="audio", language_code="en")
        with self.assertRaises(IntegrityError):
            LanguageEntry.objects.create(language_info=info, kind="audio", language_code="en")

    def test_audio_and_subtitle_kinds_coexist(self):
        info = LanguageInfo.objects.create(movie=self.movie, region=self.region)
        LanguageEntry.objects.create(language_info=info, kind="audio", language_code="en")
        LanguageEntry.objects.create(language_info=info, kind="subtitle", language_code="en")
        self.assertEqual(info.entries.count(), 2)


class SubscriptionTests(TestCase):
    def test_name_unique(self):
        Subscription.objects.create(name="Netflix")
        with self.assertRaises(IntegrityError):
            Subscription.objects.create(name="Netflix")

    def test_user_id_defaults_to_one(self):
        self.assertEqual(Subscription.objects.create(name="Max").user_id, 1)


class PreferenceTests(TestCase):
    def test_default_lists_are_empty(self):
        p = Preference.objects.create()
        self.assertEqual(p.audio_languages, [])
        self.assertEqual(p.subtitle_languages, [])
        self.assertEqual(p.candidate_countries, [])

    def test_user_id_defaults_to_one(self):
        self.assertEqual(Preference.objects.create().user_id, 1)


class WatchlistItemTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=3, title="O", year=2018)

    def test_movie_source_unique(self):
        WatchlistItem.objects.create(movie=self.movie, source="letterboxd")
        with self.assertRaises(IntegrityError):
            WatchlistItem.objects.create(movie=self.movie, source="letterboxd")

    def test_user_id_defaults_to_one(self):
        self.assertEqual(WatchlistItem.objects.create(movie=self.movie).user_id, 1)
```

Also create empty `backend/tracker/tests/__init__.py`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'tracker.models'` / `ImportError` on the test module.

- [ ] **Step 4: Implement the models**

`backend/tracker/models.py` (exact content):

```python
from django.contrib.postgres.fields import ArrayField
from django.db import models


class Movie(models.Model):
    tmdb_id = models.IntegerField(unique=True, db_index=True)
    title = models.CharField(max_length=255)
    year = models.IntegerField(null=True, blank=True)

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


class Subscription(models.Model):
    user_id = models.IntegerField(default=1)
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


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
```

- [ ] **Step 5: Generate the migration**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py makemigrations tracker`
Expected: `Migrations for 'tracker': tracker/migrations/0001_initial.py` created.

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2`
Expected: PASS (16 tests).

- [ ] **Step 7: Apply migration to the dev database**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py migrate`
Expected: applies `tracker.0001_initial`.

- [ ] **Step 8: Commit**

```bash
git add backend/config/settings.py backend/tracker/models.py backend/tracker/tests/ backend/tracker/migrations/
git commit -m "Add Phase 1 models: Movie, Region, Availability, LanguageInfo, LanguageEntry, Subscription, Preference, WatchlistItem"
```

---

### Task 2: Admin registration with LanguageInfo inline

**Files:**
- Modify: `backend/tracker/admin.py`
- Test: `backend/tracker/tests/test_models.py` (append one test) or new `backend/tracker/tests/test_admin.py`

**Interfaces:**
- Consumes: all model classes from `tracker.models` (Task 1), `LanguageEntry` inline target.
- Produces: admin registrations; `LanguageInfoAdmin` with `LanguageEntryInline`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tracker/tests/test_models.py`:

```python
class AdminRegistrationTests(TestCase):
    def test_language_info_admin_has_entry_inline(self):
        from tracker.admin import LanguageEntryInline, LanguageInfoAdmin

        self.assertIn(LanguageEntryInline, LanguageInfoAdmin.inlines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker.tests.test_models.AdminRegistrationTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'tracker.admin'` (admin.py is a comment stub).

- [ ] **Step 3: Implement the admin**

`backend/tracker/admin.py` (exact content):

```python
from django.contrib import admin
from django.contrib.postgres.fields import ArrayField

from .models import (
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Preference,
    Region,
    Subscription,
    WatchlistItem,
)


class LanguageEntryInline(admin.TabularInline):
    model = LanguageEntry
    extra = 1


@admin.register(LanguageInfo)
class LanguageInfoAdmin(admin.ModelAdmin):
    list_display = ("movie", "region")
    list_filter = ("region__provider",)
    search_fields = ("movie__title",)
    inlines = [LanguageEntryInline]


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ("tmdb_id", "title", "year")
    search_fields = ("title",)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("provider", "country")
    list_filter = ("provider",)
    search_fields = ("provider",)


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ("movie", "region", "is_available", "offer_type", "last_checked_at")
    list_filter = ("offer_type", "is_available")
    search_fields = ("movie__title",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("name", "user_id")
    search_fields = ("name",)


@admin.register(Preference)
class PreferenceAdmin(admin.ModelAdmin):
    list_display = ("user_id", "audio_languages", "subtitle_languages", "candidate_countries")

    def formfield_for_dbfield(self, db_field, **kwargs):
        field = super().formfield_for_dbfield(db_field, **kwargs)
        if isinstance(db_field, ArrayField):
            field.help_text = "Comma-separated ISO 639-1 codes, first = highest priority."
        return field


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("movie", "source", "added_at")
    search_fields = ("movie__title",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker.tests.test_models.AdminRegistrationTests -v 2`
Expected: PASS.

- [ ] **Step 5: Verify admin system check passes**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py check`
Expected: `System check identified no issues`.

- [ ] **Step 6: Commit**

```bash
git add backend/tracker/admin.py backend/tracker/tests/test_models.py
git commit -m "Add Django admin for Phase 1 models with LanguageInfo inline"
```

---

### Task 3: Seed migration for empty Preference

**Files:**
- Create: `backend/tracker/migrations/0002_seed_empty_preference.py`
- Modify: `backend/tracker/tests/test_models.py` (append seed test)

**Interfaces:**
- Consumes: `Preference` model (Task 1), migration `0001_initial` (Task 1).
- Produces: exactly one `Preference` row (user_id=1, all lists empty) in any DB that runs migrate.

- [ ] **Step 1: Write the failing test**

Append to `backend/tracker/tests/test_models.py`:

```python
class SeedTests(TestCase):
    def test_seed_creates_empty_preference(self):
        # Data migrations run against the test DB, so the seeded row exists.
        self.assertTrue(Preference.objects.filter(user_id=1).exists())
        self.assertEqual(Preference.objects.count(), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker.tests.test_models.SeedTests -v 2`
Expected: FAIL — `Preference.objects.count()` is 0 (no 0002 migration yet).

- [ ] **Step 3: Write the data migration**

`backend/tracker/migrations/0002_seed_empty_preference.py` (exact content):

```python
from django.db import migrations


def create_empty_preference(apps, schema_editor):
    Preference = apps.get_model("tracker", "Preference")
    if not Preference.objects.filter(user_id=1).exists():
        Preference.objects.create(user_id=1)


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_empty_preference, migrations.RunPython.noop),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker.tests.test_models.SeedTests -v 2`
Expected: PASS (test DB applies 0002).

- [ ] **Step 5: Apply migration to the dev database and verify**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py migrate`
Then verify exactly one empty Preference row exists:

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py shell -c "from tracker.models import Preference; print(list(Preference.objects.values('user_id', 'audio_languages')))"`
Expected: `[{'user_id': 1, 'audio_languages': []}]` and `Preference.objects.count() == 1`.

- [ ] **Step 6: Run the full suite**

Run: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2`
Expected: PASS (18 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/tracker/migrations/0002_seed_empty_preference.py backend/tracker/tests/test_models.py
git commit -m "Seed one empty Preference row via data migration"
```

---

### Task 4: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Confirm backend healthy**

Run: `curl -s http://localhost:8000/api/health/`
Expected: `{"status":"ok","db":"ok"}`

- [ ] **Step 2: Confirm admin pages are reachable and registered**

Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/admin/`
Expected: `302` (redirect to login).

Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/admin/tracker/languageinfo/`
Expected: `302` (redirect to login — proves the admin URL resolves).

- [ ] **Step 3: Confirm frontend still renders**

Run: `curl -s http://localhost:3001 | grep -c "db: ok"`
Expected: `1`

- [ ] **Step 4: Update CLAUDE.md architecture section**

In `/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt/CLAUDE.md`, replace the models note under "Architecture (planned, from PLAN.md)" — update to reflect that the eight Phase 1 models now exist in `backend/tracker/models.py` and admin is registered. Keep it a few lines; do not enumerate every model (discoverable in code).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md: Phase 1 models and admin now implemented"
```
