#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

docker compose up -d postgres
bash "${SCRIPT_DIR}/wait.sh" postgres

printf '\n==> Recreating the development database\n'
docker compose exec -T postgres bash -Eeuo pipefail -c '
  : "${POSTGRES_DB:?POSTGRES_DB is required}"
  : "${POSTGRES_USER:?POSTGRES_USER is required}"
  : "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

  case "${POSTGRES_DB}" in
    postgres|template0|template1)
      printf "Refusing to reset protected database %s.\n" "${POSTGRES_DB}" >&2
      exit 64
      ;;
  esac

  export PGPASSWORD="${POSTGRES_PASSWORD}"
  dropdb \
    --host=127.0.0.1 \
    --port=5432 \
    --username="${POSTGRES_USER}" \
    --maintenance-db=postgres \
    --if-exists \
    --force \
    -- "${POSTGRES_DB}"
  createdb \
    --host=127.0.0.1 \
    --port=5432 \
    --username="${POSTGRES_USER}" \
    --maintenance-db=postgres \
    --owner="${POSTGRES_USER}" \
    -- "${POSTGRES_DB}"
'

bash "${SCRIPT_DIR}/migrate.sh"
bash "${SCRIPT_DIR}/test.sh"

printf '\nDatabase reset, migrations, and tests completed successfully.\n'
