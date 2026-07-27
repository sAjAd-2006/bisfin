#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if (($# > 1)); then
  printf 'Usage: %s [database]\n' "$0" >&2
  exit 64
fi

attempts="${DB_WAIT_ATTEMPTS:-60}"
interval="${DB_WAIT_INTERVAL_SECONDS:-1}"
wait_database="${1:-}"

if [[ ! "${attempts}" =~ ^[1-9][0-9]*$ ]]; then
  printf 'DB_WAIT_ATTEMPTS must be a positive integer; got %q.\n' "${attempts}" >&2
  exit 64
fi

if [[ ! "${interval}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  printf 'DB_WAIT_INTERVAL_SECONDS must be a non-negative number; got %q.\n' "${interval}" >&2
  exit 64
fi

docker compose config --quiet

compose_exec_args=(-T)
if [[ -n "${wait_database}" ]]; then
  compose_exec_args+=(-e "DB_WAIT_DATABASE=${wait_database}")
fi

for ((attempt = 1; attempt <= attempts; attempt++)); do
  if docker compose exec "${compose_exec_args[@]}" postgres bash -Eeuo pipefail -c '
    export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
    psql -X --quiet \
      --set=ON_ERROR_STOP=1 \
      --host=127.0.0.1 \
      --port=5432 \
      --username="${POSTGRES_USER:?POSTGRES_USER is required}" \
      --dbname="${DB_WAIT_DATABASE:-${POSTGRES_DB:?POSTGRES_DB is required}}" \
      --command="SELECT 1" >/dev/null
  ' >/dev/null 2>&1; then
    printf 'PostgreSQL is ready (attempt %d/%d).\n' "${attempt}" "${attempts}"
    exit 0
  fi

  if ((attempt < attempts)); then
    sleep "${interval}"
  fi
done

printf 'PostgreSQL did not become ready after %d attempts.\n' "${attempts}" >&2
docker compose ps >&2 || true
docker compose logs --no-color --tail=100 postgres >&2 || true
exit 1
