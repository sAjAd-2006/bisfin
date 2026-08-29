#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

manifest_file="$(mktemp)"
cleanup() {
  rm -f "${manifest_file}"
}
trap cleanup EXIT

read -r series_id event_from event_to < <(
  docker compose exec -T postgres bash -Eeuo pipefail -c '
    export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
    exec psql -X --tuples-only --no-align --field-separator=" " \
      --host=127.0.0.1 --port=5432 \
      --username="${POSTGRES_USER:?POSTGRES_USER is required}" \
      --dbname="${POSTGRES_DB:?POSTGRES_DB is required}" \
      --command="
        SELECT series.bar_series_id,
               to_char(min(revision.bar_open_ts), '\''YYYY-MM-DD\"T\"HH24:MI:SSOF'\''),
               to_char(max(revision.bar_close_ts) + interval '\''1 microsecond'\'', '\''YYYY-MM-DD\"T\"HH24:MI:SSOF'\'')
        FROM market.bar_series AS series
        JOIN catalog.data_feed AS feed ON feed.feed_id = series.feed_id
        JOIN market.bar_revision AS revision ON revision.bar_series_id = series.bar_series_id
        WHERE feed.feed_code = '\''TSETMC_CANDLE_DAILY_RAW'\''
        GROUP BY series.bar_series_id
        ORDER BY series.bar_series_id
        LIMIT 1;"
  '
)

if [[ -z "${series_id:-}" || -z "${event_from:-}" || -z "${event_to:-}" ]]; then
  printf 'Fixture-backed RAW bar series was not found. Run catalog/calendar bootstrap and fixture ingestion first.\n' >&2
  exit 65
fi

printf '{"schema_version":1,"snapshot_code":"fixture-daily-raw-2026-01","knowledge_cutoff_ts":"2035-01-01T00:00:00Z","availability_mode":"PUBLIC_REPLAY","components":[{"component_key":"daily-raw","kind":"BAR_REVISION","bar_series_id":%s,"event_from":"%s","event_to":"%s"}]}' \
  "${series_id}" "${event_from}" "${event_to}" > "${manifest_file}"

uv_args=(run --frozen)
if [[ -f .env ]]; then
  uv_args+=(--env-file .env)
fi
uv "${uv_args[@]}" bisfin snapshot build --manifest "${manifest_file}" --output-dir .tmp/snapshots
