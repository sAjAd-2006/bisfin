#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

run_sql() {
  local sql_file="$1"

  if [[ ! -f "${sql_file}" ]]; then
    printf 'SQL file not found: %s\n' "${sql_file}" >&2
    return 66
  fi

  printf '\n==> Applying %s\n' "${sql_file}"
  docker compose exec -T postgres bash -Eeuo pipefail -c '
    export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
    exec psql -X \
      --set=ON_ERROR_STOP=1 \
      --echo-errors \
      --host=127.0.0.1 \
      --port=5432 \
      --username="${POSTGRES_USER:?POSTGRES_USER is required}" \
      --dbname="${POSTGRES_DB:?POSTGRES_DB is required}" \
      --file=-
  ' < "${sql_file}"
}

bash "${SCRIPT_DIR}/wait.sh"

migrations=(
  "db/postgresql/001_core_schema.sql"
  "db/postgresql/003_technical_backtest_completion.sql"
)

for migration in "${migrations[@]}"; do
  run_sql "${migration}"
done

printf '\nDatabase migrations completed successfully.\n'
