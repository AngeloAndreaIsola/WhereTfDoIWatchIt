#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

project="wtdwi-test-$$"

cleanup() {
  # Each invocation has its own project and ephemeral PostgreSQL storage.
  docker compose -p "$project" -f docker-compose.yml -f docker-compose.test.yml \
    --profile test down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

docker compose -p "$project" -f docker-compose.yml -f docker-compose.test.yml \
  --profile test up -d db-test
docker compose -p "$project" -f docker-compose.yml -f docker-compose.test.yml \
  --profile test run --build --rm --no-deps \
  --volume "$repo_root/docs/contracts:/app/docs/contracts:ro" \
  backend-test manage.py test --noinput "$@"
