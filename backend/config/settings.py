"""
Django settings for the tracker backend.
Configuration comes from environment variables (see .env.example at repo root).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

_DEV_SECRET_KEY = "dev-insecure-secret"


def validate_security_settings(*, secret_key: str, debug: bool) -> None:
    """Reject an accidental production process with a public placeholder key."""
    if not debug and secret_key in ("", _DEV_SECRET_KEY, "change-me-in-production"):
        raise RuntimeError(
            "DJANGO_SECRET_KEY must be set to a non-placeholder value when DJANGO_DEBUG=0"
        )


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _DEV_SECRET_KEY)
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

# A missing key is acceptable only for an explicitly local debug process.  A
# deployment that accidentally omits its environment file must fail at startup
# rather than serve sessions, CSRF tokens, and signed values with a public key.
validate_security_settings(secret_key=SECRET_KEY, debug=DEBUG)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend").split(",")
    if host.strip()
]
EXTENSION_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("EXTENSION_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
USE_X_FORWARDED_HOST = True

# Internal frontend proxy configuration for containerized local Compose deployments.
# In docker-compose, Next.js rewrites forward /api requests from the 'frontend' container.
INTERNAL_FRONTEND_PROXY_HOST = os.environ.get("INTERNAL_FRONTEND_PROXY_HOST", "frontend")
INTERNAL_FRONTEND_PROXY_IPS = [
    ip.strip()
    for ip in os.environ.get("INTERNAL_FRONTEND_PROXY_IPS", "").split(",")
    if ip.strip()
]

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
    "availability",
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

TEST_RUNNER = "config.test_database.DisposableDatabaseRunner"

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

# Container log visibility.
#
# Django's stock logging config puts a RequireDebugTrue filter on the console
# handler. Deployments run with DJANGO_DEBUG=0, so every application record was
# dropped: the handler still counted as "found", which also suppressed Python's
# last-resort WARNING output. Operational events such as RaiPlay's
# ``rai_play_walk_resume`` therefore never appeared in ``docker compose logs``
# and could only be captured by attaching a private handler.
#
# One explicit config covers every container because the backend, title worker
# and catalogue worker are all Django processes importing this module. Unrelated
# components keep the WARNING default; only the RaiPlay source logger is raised
# to INFO. ``propagate: False`` on that logger keeps each event to a single line
# rather than emitting it again through the root handler.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "container": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "container",
        },
    },
    "loggers": {
        # Django installs its own INFO default for this logger before applying
        # the dict below; state WARNING explicitly so framework chatter stays
        # out of ordinary logs. Errors still reach the root console handler.
        "django": {
            "handlers": [],
            "level": "WARNING",
            "propagate": True,
        },
        "availability.sources.rai_play": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
}

# External API keys (set via env or docker-compose env_file).
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
WTDW_LEGACY_OWNER_ID = os.environ.get("WTDW_LEGACY_OWNER_ID")
# Watchmode aggregator API key. Read from the environment like the other
# provider keys and never hard-coded; an empty value makes the source raise
# an explicit auth error rather than silently report no availability.
WATCHMODE_API_KEY = os.environ.get("WATCHMODE_API_KEY", "")

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

# The provider the Prime Video endpoints filter on. This is the stable
# Streaming Availability *service id* ("prime"), not the display name.
DEFAULT_PREFERENCE_PROVIDER = os.environ.get("DEFAULT_PREFERENCE_PROVIDER", "prime")

# RaiPlay operational bounds (worker/cron default: None = must be explicitly configured)
RAIPLAY_MAX_CONTENTS = (
    int(os.environ["RAIPLAY_MAX_CONTENTS"])
    if os.environ.get("RAIPLAY_MAX_CONTENTS")
    else None
)
RAIPLAY_MAX_HTTP_REQUESTS = (
    int(os.environ["RAIPLAY_MAX_HTTP_REQUESTS"])
    if os.environ.get("RAIPLAY_MAX_HTTP_REQUESTS")
    else None
)
