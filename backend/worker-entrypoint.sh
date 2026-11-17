#!/bin/sh
set -e

# The backend container owns `manage.py migrate` (see entrypoint.sh). The
# worker waits until all migrations are applied instead of running them, so
# two containers never migrate concurrently on first boot.
until python manage.py migrate --check >/dev/null 2>&1; do
  sleep 2
done

exec "$@"
