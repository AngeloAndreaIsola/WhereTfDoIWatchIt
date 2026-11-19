#!/bin/sh
set -eu

: "${EXPECTED_DATABASE_NAME:?Set EXPECTED_DATABASE_NAME for the intended target}"
: "${EXPECTED_DATABASE_HOST:?Set EXPECTED_DATABASE_HOST for the intended target}"
: "${CONFIRM_MIGRATION_TARGET:?Set CONFIRM_MIGRATION_TARGET to <database>@<host>}"

exec python manage.py guarded_migrate \
  --expected-database "$EXPECTED_DATABASE_NAME" \
  --expected-host "$EXPECTED_DATABASE_HOST" \
  --confirm-target "$CONFIRM_MIGRATION_TARGET" \
  "$@"
