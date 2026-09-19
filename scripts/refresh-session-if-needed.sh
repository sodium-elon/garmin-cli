#!/usr/bin/env bash
# Keep the garmin-cli OAuth session usable. Healthy runs are intentionally silent.
set -euo pipefail

ROOT="${GARMIN_CLI_ROOT:-/home/john/projects/garmin-cli.workspace/garmin-cli}"
CLI="${GARMIN_CLI_BIN:-$ROOT/.venv/bin/garmin-cli}"
UV_BIN="${UV_BIN:-/home/john/.local/bin/uv}"
ENV_FILE="${GARMIN_ENV_FILE:-/home/john/projects/garmin-cli.workspace/env-profiles/local.env}"
LOCK_FILE="${GARMIN_REFRESH_LOCK_FILE:-${XDG_RUNTIME_DIR:-/tmp}/garmin-cli-session-refresh.lock}"
LOGIN_TIMEOUT_SECONDS="${GARMIN_LOGIN_TIMEOUT_SECONDS:-180}"
export HOME="${HOME:-/home/john}"

log=$(mktemp /tmp/garmin-cli-session-refresh.XXXXXX.log)
trap 'rm -f "$log"' EXIT

ensure_cli() {
  if [[ -x "$CLI" ]] && env -u PYTHONPATH -u PYTHONHOME "$CLI" --version >/dev/null 2>&1; then
    return 0
  fi

  if ! (cd "$ROOT" && "$UV_BIN" sync --frozen --python 3.13) >"$log" 2>&1; then
    echo "ERROR: Garmin CLI environment is unavailable and uv could not rebuild it."
    return 1
  fi

  [[ -x "$CLI" ]] || {
    echo "ERROR: Garmin CLI environment rebuilt without producing $CLI."
    return 1
  }
}

check_session() {
  env -u PYTHONPATH -u PYTHONHOME "$CLI" auth check >/dev/null 2>&1
}

ensure_cli || exit 1

if check_session; then
  exit 0
fi

# Serialize recovery across scheduled and manual invocations.
exec 8>"$LOCK_FILE"
if ! flock -n 8; then
  exit 0
fi

# Another process may have refreshed the session while this process acquired the lock.
if check_session; then
  exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: Garmin CLI session is invalid and the login environment file is missing."
  exit 1
fi

if ! timeout "$LOGIN_TIMEOUT_SECONDS" env -u PYTHONPATH -u PYTHONHOME \
  "$CLI" --env-file "$ENV_FILE" auth login >"$log" 2>&1; then
  if grep -qiE '(^|[^0-9])429([^0-9]|$)|rate.?limit' "$log"; then
    echo "ERROR: Garmin rejected the single CLI recovery attempt with a rate limit. No retry was made."
  else
    echo "ERROR: The single Garmin CLI session recovery attempt failed. No retry was made."
  fi
  exit 1
fi

if ! check_session; then
  echo "ERROR: Garmin CLI login completed, but the refreshed session failed verification."
  exit 1
fi
