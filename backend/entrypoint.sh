#!/bin/sh
set -e

# Runtime startup must never change database schema implicitly. Apply schema
# changes through the explicitly confirmed `migrate` Compose operation.
python manage.py migrate --check
python manage.py collectstatic --noinput

exec "$@"
