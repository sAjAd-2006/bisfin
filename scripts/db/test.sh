#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

run_sql() {
  local label="$1"
  local sql_file="$2"

  if [[ ! -f "${sql_file}" ]]; then
    printf 'SQL file not found: %s\n' "${sql_file}" >&2
    return 66
  fi

  printf '\n==> %s: %s\n' "${label}" "${sql_file}"
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

run_sql "Idempotency check (migration 0001)" \
  "db/postgresql/migrations/0001_core_schema.sql"
run_sql "Idempotency check (migration 0002)" \
  "db/postgresql/migrations/0002_technical_backtest_completion.sql"
run_sql "Idempotency check (migration 0003)" \
  "db/postgresql/migrations/0003_point_in_time_hardening.sql"
run_sql "Idempotency check (migration 0004)" \
  "db/postgresql/migrations/0004_ingestion_runtime_support.sql"
run_sql "Core smoke test" \
  "db/postgresql/tests/0001_core_smoke.sql"
run_sql "Technical-backtest smoke test" \
  "db/postgresql/tests/0002_technical_backtest_smoke.sql"
run_sql "Point-in-time hardening smoke test" \
  "db/postgresql/tests/0003_point_in_time_hardening_smoke.sql"
run_sql "Ingestion runtime support smoke test" \
  "db/postgresql/tests/0004_ingestion_runtime_support_smoke.sql"

printf '\nDatabase idempotency checks and smoke tests completed successfully.\n'
