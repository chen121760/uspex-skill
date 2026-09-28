#!/usr/bin/env bash
# Persistent driver for USPEX 10.5/10.6. Run from the calculation root.

set -u

poll_seconds="${USPEX_POLL_SECONDS:-30}"
max_failures="${USPEX_MAX_CONSECUTIVE_FAILURES:-5}"
driver_log="${USPEX_DRIVER_LOG:-uspex-driver.log}"

case "$poll_seconds" in
  ''|*[!0-9]*) echo "USPEX_POLL_SECONDS must be a positive integer" >&2; exit 2 ;;
esac
case "$max_failures" in
  ''|*[!0-9]*) echo "USPEX_MAX_CONSECUTIVE_FAILURES must be a positive integer" >&2; exit 2 ;;
esac
if [ "$poll_seconds" -le 0 ] || [ "$max_failures" -le 0 ]; then
  echo "poll interval and maximum failures must both be greater than zero" >&2
  exit 2
fi

if [ ! -f INPUT.txt ]; then
  echo "INPUT.txt not found; run this script from the USPEX calculation root" >&2
  exit 2
fi
if ! command -v USPEX >/dev/null 2>&1; then
  echo "USPEX is not available in PATH" >&2
  exit 2
fi

# Environment gate: the interpreter that will run the helpers must match the shipped
# compiled topology module and carry numpy/scipy/spglib, and USPEXPATH must describe the
# same tree as the USPEX launcher on PATH. Skipped only when explicitly requested.
if [ "${USPEX_SKIP_ENV_CHECK:-0}" != "1" ]; then
  for candidate in \
    "${USPEX_ENV_CHECK:-}" \
    "$(dirname "$0")/../scripts/check_uspex_env.py" \
    "./scripts/check_uspex_env.py"; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
      health_python="${USPEX_HEALTH_PYTHON:-python3}"
      echo "environment health check: $health_python $candidate . --uspex-version ${USPEX_VERSION:-10.5}"
      if ! "$health_python" "$candidate" . --uspex-version "${USPEX_VERSION:-10.5}"; then
        echo "environment health check failed; fix the findings above or export USPEX_SKIP_ENV_CHECK=1 to override" >&2
        exit 2
      fi
      break
    fi
  done
fi

exec 9>.uspex-runner.lock
if command -v flock >/dev/null 2>&1; then
  if ! flock -n 9; then
    echo "Another run_uspex_loop.sh process already holds .uspex-runner.lock" >&2
    exit 3
  fi
fi

failures=0
printf '[%s] driver start; poll=%ss\n' "$(date -Is)" "$poll_seconds" >> "$driver_log"

while [ ! -f USPEX_IS_DONE ]; do
  if [ -f STOP_USPEX_LOOP ]; then
    printf '[%s] STOP_USPEX_LOOP detected; exiting\n' "$(date -Is)" >> "$driver_log"
    exit 0
  fi

  printf '[%s] USPEX -r start\n' "$(date -Is)" >> "$driver_log"
  USPEX -r >> "$driver_log" 2>&1
  rc=$?
  printf '[%s] USPEX -r exit=%s\n' "$(date -Is)" "$rc" >> "$driver_log"

  if [ "$rc" -eq 0 ]; then
    failures=0
  else
    failures=$((failures + 1))
    if [ "$failures" -ge "$max_failures" ]; then
      printf '[%s] stopping after %s consecutive failures\n' "$(date -Is)" "$failures" >> "$driver_log"
      exit "$rc"
    fi
  fi

  [ -f USPEX_IS_DONE ] || sleep "$poll_seconds"
done

printf '[%s] USPEX_IS_DONE detected; driver complete\n' "$(date -Is)" >> "$driver_log"
