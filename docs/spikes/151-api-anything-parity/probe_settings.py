"""Throwaway in-memory settings for the offline parity probe.

Imports the project settings, then points the database at an in-memory SQLite
so no file or serving database is ever touched. The probe never issues an ORM
query; this is only a safety net for Django app loading.
"""
from config.settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
