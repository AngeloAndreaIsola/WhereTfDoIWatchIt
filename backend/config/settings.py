"""
Django settings for the tracker backend.
Configuration comes from environment variables (see .env.example at repo root).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend").split(",")
    if host.strip()
]
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django.contrib.postgres",
    "tracker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "tracker" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("USE_SQLITE") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
    import django.contrib.postgres.fields
    class SQLiteArrayField(django.db.models.JSONField):
        def __init__(self, base_field=None, **kwargs):
            kwargs.pop("size", None)
            super().__init__(**kwargs)
    django.contrib.postgres.fields.ArrayField = SQLiteArrayField
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "tracker"),
            "USER": os.environ.get("POSTGRES_USER", "tracker"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "tracker_dev"),
            "HOST": os.environ.get("POSTGRES_HOST", "db"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# API is machine-facing only (single-user tool); render JSON, never browsable HTML.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

APPEND_SLASH = False

# External API keys (set via env or docker-compose env_file).
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

# Hardcoded default preferences (v0): server-side source of truth until the
# frontend preferences UI is enabled. These are returned by
# services/preferences.resolve_preferences() when no Preference row exists.
DEFAULT_PREFERENCE = {
    "audio_languages": ["JA", "EN"],
    "subtitle_languages": ["EN", "JA"],
    "candidate_countries": [],
    "accept_any_audio": False,
    "accept_any_subtitle": False,
}

# When True, cache misses for /api/prime/movie/<id>/ trigger a live Streaming
# Availability API call. When False, only cached rows are returned.
PRIME_LIVE_FETCH = os.environ.get("PRIME_LIVE_FETCH", "true").lower() == "true"

# The provider the Prime Video endpoints filter on. This is the stable
# Streaming Availability *service id* ("prime"), not the display name.
DEFAULT_PREFERENCE_PROVIDER = os.environ.get("DEFAULT_PREFERENCE_PROVIDER", "prime")
