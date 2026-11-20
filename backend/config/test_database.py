"""Only the dedicated, disposable PostgreSQL setup can run tests or movie fixtures."""

import os
import re

from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test.runner import DiscoverRunner

BASE_NAME = "wtdwi_test"
TEST_NAME = f"test_{BASE_NAME}"
TEST_HOSTS = {"db-test", "localhost", "127.0.0.1", "::1"}


def require_disposable_database(connection, *, for_runner=False):
    config = connection.settings_dict
    name = str(config["NAME"])
    allowed_name = (
        name == BASE_NAME if for_runner else
        name == BASE_NAME or re.fullmatch(rf"{TEST_NAME}(?:_[1-9][0-9]*)?", name)
    )
    message = (
        "Movie fixtures and database tests require the disposable WTDWI PostgreSQL "
        "environment. Use scripts/test-backend-isolated.sh or the dedicated db-test service."
    )
    if (
        os.environ.get("WTDWI_DISPOSABLE_TEST_DB") != "1"
        or connection.vendor != "postgresql"
        or config["HOST"] not in TEST_HOSTS
        or config["USER"] != BASE_NAME
        or not allowed_name
        or (for_runner and config["TEST"]["NAME"] not in (None, TEST_NAME))
        or (for_runner and config["TEST"]["MIRROR"])
    ):
        raise CommandError(message)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_database(), session_user, current_user,
                       pg_get_userbyid(datdba),
                       EXISTS (SELECT 1 FROM pg_database WHERE datname = 'tracker')
                FROM pg_database WHERE datname = current_database()
                """
            )
            actual = cursor.fetchone()
    except DatabaseError as exc:
        raise CommandError(f"{message} Could not verify PostgreSQL identity.") from exc
    if actual != (name, BASE_NAME, BASE_NAME, BASE_NAME, False):
        raise CommandError(f"{message} PostgreSQL identity or cluster isolation check failed.")


class DisposableDatabaseRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        from django.db import connections

        aliases = kwargs.get("aliases")
        for alias in connections if aliases is None else aliases:
            require_disposable_database(connections[alias], for_runner=True)
        return super().setup_databases(**kwargs)
