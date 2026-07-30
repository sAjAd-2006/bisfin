#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

load_dotenv() {
  local env_file="$1"
  local line name value

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"

    if [[ -z "${line}" || "${line}" == \#* ]]; then
      continue
    fi

    if [[ "${line}" != *=* ]]; then
      printf 'Invalid environment entry in %s: %s\n' "${env_file}" "${line}" >&2
      return 64
    fi

    name="${line%%=*}"
    value="${line#*=}"
    name="${name#"${name%%[![:space:]]*}"}"
    name="${name%"${name##*[![:space:]]}"}"

    if [[ ! "${name}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      printf 'Invalid environment variable name in %s: %s\n' "${env_file}" "${name}" >&2
      return 64
    fi

    if [[ ! -v "${name}" ]]; then
      printf -v "${name}" '%s' "${value}"
      export "${name}"
    fi
  done < "${env_file}"
}

bash "${SCRIPT_DIR}/wait.sh"

if [[ -f "${REPO_ROOT}/.env" ]]; then
  load_dotenv "${REPO_ROOT}/.env"
fi

uv run --frozen alembic upgrade head
