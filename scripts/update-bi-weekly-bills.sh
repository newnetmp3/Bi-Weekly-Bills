#!/usr/bin/env bash
# Update Bi-Weekly Bills in place without reconnecting your bank.
#
# What this does, in order:
#   1. Records which bank connection (Plaid Item) you have now.
#   2. Makes a verified backup of the bills database and a private snapshot of
#      ~/.config/bi-weekly-bills (bank connection + API keys).
#   3. Saves connection state that older versions kept inside the install
#      folder, before anything in that folder is touched.
#   4. Installs the new version into the same app environment. If an Arch
#      Python upgrade broke that environment, it is rebuilt first.
#   5. Moves the saved state into ~/.config/bi-weekly-bills/state and checks,
#      without contacting the bank, that the same connection is still in use.
#
# It never opens the bank sign-in window and never deletes your data.
#
# Usage:
#   update-bi-weekly-bills.sh                      # latest GitHub release
#   update-bi-weekly-bills.sh --wheel FILE.whl     # a wheel you downloaded
#   update-bi-weekly-bills.sh --source DIR         # a local Git checkout
#   update-bi-weekly-bills.sh --venv DIR           # app environment location
#
# Without --venv, the existing install is found from the biweekly-bills-app
# command (normally ~/.local/bin/biweekly-bills-app):
#   * a virtual environment or pipx install is updated in place;
#   * a "pip install --user" install is moved into a private environment at
#     ~/.local/opt/bi-weekly-bills, and ~/.local/bin/biweekly-bills-app is
#     pointed at it, so the menu entry and command keep working.

set -euo pipefail

REPO="newnetmp3/Bi-Weekly-Bills"
VENV=""
DEFAULT_VENV="${HOME}/.local/opt/bi-weekly-bills"
USER_BIN="${HOME}/.local/bin"
USER_SITE_INSTALL=0
WHEEL=""
SOURCE=""
CONFIG_DIR="${HOME}/.config/bi-weekly-bills"
DATA_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/bi-weekly-bills"
DB_PATH="${DATA_DIR}/biweekly-bills.sqlite3"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nUpdate stopped: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --wheel) WHEEL="$(realpath "${2:?--wheel needs a file}")"; shift 2 ;;
    --source) SOURCE="$(realpath "${2:?--source needs a folder}")"; shift 2 ;;
    --venv) VENV="${2:?--venv needs a folder}"; shift 2 ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

if [ -n "$WHEEL" ] && [ -n "$SOURCE" ]; then
  fail "use either --wheel or --source, not both"
fi
if [ -n "$WHEEL" ] && [ ! -f "$WHEEL" ]; then
  fail "wheel not found: $WHEEL"
fi
if [ -n "$SOURCE" ] && [ ! -f "$SOURCE/pyproject.toml" ]; then
  fail "$SOURCE does not look like a Bi-Weekly Bills checkout"
fi

SYSTEM_PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$SYSTEM_PYTHON" ] || fail "python is not installed (sudo pacman -S python)"

# Match only a Python process running the app, not editors or shells that
# merely mention its name.
if RUNNING="$(pgrep -f 'python[0-9.]* [^ ]*biweekly-bills-app( |$)|-m biweekly_bills\.desktop_app' 2>/dev/null)"; then
  fail "Bi-Weekly Bills is open (process $(echo $RUNNING)). Close it, then run this again."
fi

# --- 0. Find the existing install ------------------------------------------
find_launcher() {
  if [ -e "$USER_BIN/biweekly-bills-app" ]; then
    printf '%s\n' "$USER_BIN/biweekly-bills-app"
  else
    command -v biweekly-bills-app 2>/dev/null || true
  fi
}

if [ -z "$VENV" ]; then
  LAUNCHER="$(find_launcher)"
  if [ -n "$LAUNCHER" ]; then
    # Follow symlinks (pipx and hand-made links) to the real script, then read
    # its first line to learn which Python it runs with. The interpreter path
    # itself is not resolved: inside a venv it is a link to system Python.
    REAL_LAUNCHER="$(realpath "$LAUNCHER")"
    SHEBANG_PY="$(head -n1 "$REAL_LAUNCHER" | sed -n 's/^#![[:space:]]*//p' | awk '{print $1}')"
    if [ "$(basename "${SHEBANG_PY:-x}")" = "sh" ]; then
      # pip writes a /bin/sh wrapper when the interpreter path is very long;
      # its second line is: '''exec' /path/to/venv/bin/python "$0" "$@"
      SHEBANG_PY="$(sed -n '2p' "$REAL_LAUNCHER" | sed "s/^'''exec' *//" | awk '{print $1}' | tr -d '"')"
    fi
    CANDIDATE="$(dirname "$(dirname "$SHEBANG_PY")")"
    if [ -n "$SHEBANG_PY" ] && [ -f "$CANDIDATE/pyvenv.cfg" ]; then
      VENV="$CANDIDATE"
      echo "Found existing install: $LAUNCHER -> environment $VENV"
    else
      USER_SITE_INSTALL=1
      VENV="$DEFAULT_VENV"
      echo "Found existing install: $LAUNCHER (installed with pip --user)"
      echo "It will be moved into its own environment at $VENV."
    fi
  else
    VENV="$DEFAULT_VENV"
    echo "No existing biweekly-bills-app command found; using $VENV"
  fi
fi

STAMP="$(date -u +%Y%m%dT%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"

# --- 1. Record the current bank connection ---------------------------------
ITEM_ID=""
if [ -f "$CONFIG_DIR/plaid_item_production.json" ]; then
  ITEM_ID="$("$SYSTEM_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("item_id") or "")' \
    "$CONFIG_DIR/plaid_item_production.json" 2>/dev/null || true)"
fi
if [ -n "$ITEM_ID" ]; then
  say "Current bank connection: Item …${ITEM_ID: -6}"
else
  say "No saved bank connection found yet (that's fine for a fresh setup)."
fi

# --- 2. Backups --------------------------------------------------------------
if [ -f "$DB_PATH" ]; then
  mkdir -p "$DATA_DIR/backups"
  BACKUP="$DATA_DIR/backups/${STAMP}000000Z__pre-update.sqlite3"
  "$SYSTEM_PYTHON" - "$DB_PATH" "$BACKUP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
a = sqlite3.connect(src); b = sqlite3.connect(dst)
a.backup(b); a.close()
ok = b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
b.close()
sys.exit(0 if ok else 1)
PY
  say "Database backed up: $BACKUP"
fi

if [ -d "$CONFIG_DIR" ]; then
  SNAP_DIR="$DATA_DIR/update-snapshots"
  mkdir -p "$SNAP_DIR"; chmod 700 "$SNAP_DIR"
  SNAP="$SNAP_DIR/config-${STAMP}.tar.gz"
  (umask 077; tar -czf "$SNAP" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")")
  say "Bank connection settings snapshot (private): $SNAP"
fi

# --- 3. Save state that 1.0 kept inside the install folder -------------------
LEGACY="$WORK/legacy"
mkdir -p "$LEGACY/data"
collect_legacy() {
  local root="$1"
  [ -d "$root" ] || return 0
  for f in PRODUCTION_ITEM_CREATED.lock .env; do
    if [ -f "$root/$f" ] && [ ! -e "$LEGACY/$f" ]; then cp -p "$root/$f" "$LEGACY/$f"; fi
  done
  for f in "$root"/data/local_config*.json "$root"/data/transactions*.json; do
    if [ -f "$f" ] && [ ! -e "$LEGACY/data/$(basename "$f")" ]; then cp -p "$f" "$LEGACY/data/"; fi
  done
}
for libdir in "$VENV"/lib/python3*; do collect_legacy "$libdir"; done
if [ "$USER_SITE_INSTALL" = 1 ]; then
  # 1.0 installed with pip --user kept its state in ~/.local/lib/python3.X
  for libdir in "${HOME}"/.local/lib/python3*; do collect_legacy "$libdir"; done
fi
[ -n "$SOURCE" ] && collect_legacy "$SOURCE"

# --- 4. Install ---------------------------------------------------------------
venv_healthy() {
  [ -x "$VENV/bin/python" ] || return 1
  "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1 || return 1
  # Arch upgrades Python in place; a venv built for an older minor version
  # can no longer see its own packages.
  local venv_ver sys_ver
  venv_ver="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  sys_ver="$("$SYSTEM_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  [ "$venv_ver" = "$sys_ver" ] && [ -d "$VENV/lib/python$venv_ver/site-packages" ]
}

if ! venv_healthy; then
  if [ -d "$VENV" ]; then
    say "App environment no longer matches system Python; rebuilding it."
  else
    say "Creating app environment at $VENV"
  fi
  mkdir -p "$VENV"
  "$SYSTEM_PYTHON" -m venv --clear "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip

if [ -z "$WHEEL" ] && [ -z "$SOURCE" ]; then
  say "Looking up the latest release"
  WHEEL_URL="$("$SYSTEM_PYTHON" - "$REPO" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request(
    f"https://api.github.com/repos/{sys.argv[1]}/releases/latest",
    headers={"Accept": "application/vnd.github+json", "User-Agent": "bi-weekly-bills-updater"},
)
with urllib.request.urlopen(req, timeout=30) as resp:
    release = json.load(resp)
for asset in release.get("assets", []):
    if asset.get("name", "").endswith("-py3-none-any.whl"):
        print(asset["browser_download_url"]); break
PY
)" || fail "could not reach GitHub. Download the wheel yourself and use --wheel FILE"
  [ -n "$WHEEL_URL" ] || fail "the latest release has no wheel attached"
  WHEEL="$WORK/$(basename "$WHEEL_URL")"
  say "Downloading $(basename "$WHEEL_URL")"
  "$SYSTEM_PYTHON" -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$WHEEL_URL" "$WHEEL"
fi

if [ -n "$SOURCE" ]; then
  say "Installing from $SOURCE"
  "$VENV/bin/python" -m pip install --quiet --upgrade "$SOURCE"
else
  say "Installing $(basename "$WHEEL")"
  "$VENV/bin/python" -m pip install --quiet --upgrade "$WHEEL"
fi

# --- 5. Carry state forward and verify ---------------------------------------
if [ "$USER_SITE_INSTALL" = 1 ]; then
  say "Pointing $USER_BIN at the new environment"
  mkdir -p "$USER_BIN"
  for cmd in biweekly-bills-app biweekly-bills; do
    if [ -e "$USER_BIN/$cmd" ] && [ ! -L "$USER_BIN/$cmd" ]; then
      mv "$USER_BIN/$cmd" "$USER_BIN/$cmd.before-update-$STAMP"
    fi
    ln -sfn "$VENV/bin/$cmd" "$USER_BIN/$cmd"
  done
  echo "  Old commands kept as $USER_BIN/*.before-update-$STAMP"
fi

say "Carrying saved connection state forward"
"$VENV/bin/biweekly-bills" migrate-state --from "$LEGACY"
# Refresh the app-menu entry so it starts the updated version.
PATH="$USER_BIN:$VENV/bin:$PATH" "$VENV/bin/biweekly-bills" install-desktop >/dev/null 2>&1 || true

say "Checking the bank connection (offline, no sign-in)"
if [ -n "$ITEM_ID" ]; then
  "$VENV/bin/biweekly-bills" verify-update --expect-item "$ITEM_ID" || {
    printf '\nThe app was updated, but the check above needs attention.\n'
    printf 'Your previous settings are saved in %s\n' "${SNAP:-(no snapshot)}"
    printf 'Do NOT use "Connect bank". Run: %s/bin/biweekly-bills doctor\n' "$VENV"
    exit 2
  }
else
  "$VENV/bin/biweekly-bills" verify-update || true
fi

say "Update complete. Start Bi-Weekly Bills from your app menu or run:"
printf '    %s/bin/biweekly-bills-app\n' "$VENV"
