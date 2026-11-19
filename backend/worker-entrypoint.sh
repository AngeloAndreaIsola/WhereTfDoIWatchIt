#!/bin/sh
set -e

# Workers only wait for the explicitly applied schema. `migrate --check` is
# read-only; schema writes are available only through the gated migration job.
until python manage.py migrate --check >/dev/null 2>&1; do
  sleep 2
done

exec "$@"
