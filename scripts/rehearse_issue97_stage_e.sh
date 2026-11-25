#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
network="wtdw97-stage-e-$$"
database="$network-db"
db_image="postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
backend_image="${STAGE_E_TEST_IMAGE:-wtdw-issue97-offline:latest}"

docker image inspect "$db_image" "$backend_image" >/dev/null
docker network create --internal "$network" >/dev/null
cleanup() {
    docker rm -f "$database" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

docker run --pull=never -d --name "$database" --network "$network" \
    --network-alias db-test --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
    -e POSTGRES_PASSWORD=wtdwi_test "$db_image" >/dev/null

remaining=30
until docker exec "$database" pg_isready -h 127.0.0.1 -U wtdwi_test -d wtdwi_test >/dev/null; do
    if [ "$remaining" -eq 0 ]; then
        docker logs "$database" >&2
        exit 1
    fi
    remaining=$((remaining - 1))
    sleep 1
done

docker exec "$database" psql -U wtdwi_test -d wtdwi_test -Atc 'SHOW server_version'
docker run --pull=never --rm --network "$network" \
    --mount "type=bind,source=$PWD/backend,target=/app,readonly" \
    -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
    -e POSTGRES_PASSWORD=wtdwi_test -e POSTGRES_HOST=db-test \
    -e WTDWI_DISPOSABLE_TEST_DB=1 -e DJANGO_SECRET_KEY=wtdwi-test-only -e DJANGO_DEBUG=1 \
    -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python "$backend_image" \
    manage.py test --noinput -v 2 "$@"
