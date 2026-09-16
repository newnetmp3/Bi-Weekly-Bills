#!/usr/bin/env bash
set -u

ACTION="${1:-}"
OUTPUT="${2:-/tmp/biweekly-bills-gui-output.txt}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT/.venv/bin/biweekly-bills"
PYTHON="$ROOT/.venv/bin/python"

mkdir -p "$(dirname -- "$OUTPUT")"

run_action() {
  cd "$ROOT" || return 2

  case "$ACTION" in
    self-test)
      "$CLI" self-test
      ;;
    unit-tests)
      "$PYTHON" -m unittest discover -s tests -v
      ;;
    dry-sync)
      "$CLI" sync
      ;;
    accounts)
      "$CLI" accounts
      ;;
    update-link)
      "$CLI" update-link
      ;;
    sandbox-reauth)
      "$CLI" sandbox-reauth-test
      ;;
    pre-production)
      echo "=== Configuration / safety state ==="
      "$CLI" doctor || return $?
      echo
      echo "=== ODS integrity / write safety ==="
      "$CLI" self-test || return $?
      echo
      echo "=== Plaid account access ==="
      "$CLI" accounts
      ;;
    *)
      echo "Unknown GUI action: $ACTION"
      return 2
      ;;
  esac
}

{
  if [[ ! -x "$CLI" || ! -x "$PYTHON" ]]; then
    echo "Project virtual environment is not installed."
    echo
    echo "Expected:"
    echo "  $CLI"
    echo
    echo "Run once from the project directory:"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -e ."
    rc=2
  else
    run_action
    rc=$?
  fi

  echo
  if [[ $rc -eq 0 ]]; then
    echo "__BWB_STATUS__:PASS"
  else
    echo "__BWB_STATUS__:FAIL"
  fi
} >"$OUTPUT" 2>&1

# LibreOffice Basic reads the output file to determine PASS/FAIL.
# Always return success here so the macro can display the real command result.
exit 0
