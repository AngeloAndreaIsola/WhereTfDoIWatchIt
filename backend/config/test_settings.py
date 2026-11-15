
from config.settings import *
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/tmp/test_wtf.sqlite3",
    }
}

# SQLite has no PostgreSQL advisory locks, so the remediation guard treats the
# write barrier as unverified. This opt-in lets SQLite-only tests exercise code
# past that gate; it does not prove writer exclusion. The real shared/exclusive
# protocol is covered by the cross-connection PostgreSQL tests in
# availability.tests.test_remediate_mapping_audit, run against an isolated
# test_* database. Never set this in a production settings module.
AVAILABILITY_REMEDIATION_ALLOW_UNVERIFIED_BARRIER = True
