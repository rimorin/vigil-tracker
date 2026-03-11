#!/usr/bin/env bash
# uninstall.sh — removes Vigil launchd services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
VENV_DIR="$REPO_ROOT/.venv"

# Prefer the venv Python (has all deps like keyring); fall back to system python.
if [[ -x "$VENV_DIR/bin/python" ]]; then
    PYTHON_CMD="$VENV_DIR/bin/python"
elif command -v pyenv &>/dev/null; then
    PYTHON_CMD="$(pyenv which python 2>/dev/null)" || PYTHON_CMD="$(command -v python 2>/dev/null || true)"
else
    PYTHON_CMD="$(command -v python 2>/dev/null || true)"
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[uninstall]${NC} $*"; }
warn()  { echo -e "${YELLOW}[uninstall]${NC} $*"; }

# ── Spinner (animated indicator for long-running operations) ───────────────
_SPIN_PID=""
_start_spinner() {
    [[ -t 1 ]] || return 0   # skip when output is not a terminal
    local msg="$1"
    ( local i=0 frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
      while true; do
          printf "\r  ${GREEN}%s${NC} %s" "${frames:$(( i % ${#frames} )):1}" "$msg"
          sleep 0.1
          i=$(( i + 1 ))
      done
    ) &
    _SPIN_PID=$!
}
_stop_spinner() {
    if [[ -n "${_SPIN_PID:-}" ]]; then
        kill "$_SPIN_PID" 2>/dev/null || true
        wait "$_SPIN_PID" 2>/dev/null || true
        printf "\r\033[2K"
        _SPIN_PID=""
    fi
}
trap '_stop_spinner' EXIT

# Detect macOS major version for launchctl compatibility
MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)
USER_UID=$(id -u)

launchctl_unload() {
    local plist="$1"
    if (( MACOS_MAJOR >= 13 )); then
        launchctl bootout "gui/${USER_UID}" "$plist" 2>/dev/null || true
    else
        launchctl unload "$plist" 2>/dev/null || true
    fi
}

# ── Partner PIN check (must pass before anything is removed) ──────────────
_PYTHON_PATH="$PYTHON_CMD"
if [[ -n "$_PYTHON_PATH" ]]; then
    if ! "$_PYTHON_PATH" "$REPO_ROOT/pin_auth.py" verify; then
        echo -e "${RED}[uninstall] Uninstall aborted.${NC}"
        exit 1
    fi
else
    warn "python not found — skipping partner PIN check."
fi

# ── Send uninstall notification email (before stopping services / deleting .env)
ENV_FILE="$REPO_ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
    PYTHON_PATH="$PYTHON_CMD"
    info "Sending uninstall notification email..."
    # Source .env so summarizer.py can reach the API keys
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    _start_spinner "Sending uninstall notification email..."
    _notify_result=0
    "$PYTHON_PATH" "$REPO_ROOT/summarizer.py" --uninstall-notify || _notify_result=$?
    _stop_spinner
    if (( _notify_result == 0 )); then
        info "Uninstall notification sent."
    else
        warn "Could not send uninstall notification — continuing with uninstall."
    fi
else
    warn "Skipping uninstall notification (.env not found or python unavailable)."
fi

# ── Stop and remove tracker service ───────────────────────────────────────
WEB_PLIST="$LAUNCH_AGENTS_DIR/com.vigil.tracker.plist"
if [[ -f "$WEB_PLIST" ]]; then
    launchctl_unload "$WEB_PLIST" && info "Stopped com.vigil.tracker." || warn "com.vigil.tracker was not loaded."
    rm -f "$WEB_PLIST"
    info "Removed $WEB_PLIST"
else
    warn "com.vigil.tracker.plist not found in LaunchAgents — already removed?"
fi

# ── Stop and remove summarizer service ────────────────────────────────────
SUMMARY_PLIST="$LAUNCH_AGENTS_DIR/com.vigil.summarizer.plist"
if [[ -f "$SUMMARY_PLIST" ]]; then
    launchctl_unload "$SUMMARY_PLIST" && info "Stopped com.vigil.summarizer." || warn "com.vigil.summarizer was not loaded."
    rm -f "$SUMMARY_PLIST"
    info "Removed $SUMMARY_PLIST"
else
    warn "com.vigil.summarizer.plist not found in LaunchAgents — already removed?"
fi

# ── Optionally remove log files ────────────────────────────────────────────
LOG_FILES=(
    "$HOME/Library/Application Support/Vigil/detailed_activity_log.txt"
    "$HOME/Library/Application Support/Vigil/detailed_activity_log.txt.sha256"
    "$HOME/Library/Application Support/Vigil/last_summarized_date.txt"
    "$HOME/Library/Logs/Vigil/tracker_daemon.log"
    "$HOME/Library/Logs/Vigil/tracker_stderr.log"
    "$HOME/Library/Logs/Vigil/summarizer_daemon.log"
    "$HOME/Library/Logs/Vigil/summarizer_stderr.log"
)

echo ""
read -r -p "$(echo -e "${YELLOW}Delete all log files and tracking data?${NC} [y/N] ")" DELETE_LOGS
if [[ "$DELETE_LOGS" =~ ^[Yy]$ ]]; then
    for f in "${LOG_FILES[@]}"; do
        rm -f "$f" "$f".[1-9]   # also remove rotated backups (.log.1, .log.2, etc.)
        [[ -f "$f" ]] || [[ -f "$f.1" ]] || info "Deleted $f"
    done
    rmdir "$HOME/Library/Application Support/Vigil" 2>/dev/null && info "Removed ~/Library/Application Support/Vigil/" || true
    rmdir "$HOME/Library/Logs/Vigil" 2>/dev/null && info "Removed ~/Library/Logs/Vigil/" || true
else
    info "Log files kept."
fi

# ── Optionally remove .env ─────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
    echo ""
    read -r -p "$(echo -e "${YELLOW}Delete .env (contains API keys)?${NC} [y/N] ")" DELETE_ENV
    if [[ "$DELETE_ENV" =~ ^[Yy]$ ]]; then
        BACKUP="${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
        cp "$ENV_FILE" "$BACKUP"
        info "Backed up .env to $BACKUP"
        rm -f "$ENV_FILE"
        info "Deleted .env"
    else
        info ".env kept."
    fi
fi

# ── Clean up partner PIN from OS keychain ─────────────────────────────────
if [[ -n "${_PYTHON_PATH:-}" ]]; then
    "$_PYTHON_PATH" "$REPO_ROOT/pin_auth.py" delete 2>/dev/null || true
fi

# ── Optionally remove virtual environment ─────────────────────────────────
if [[ -d "$VENV_DIR" ]]; then
    echo ""
    read -r -p "$(echo -e "${YELLOW}Delete Python virtual environment (.venv)?${NC} [y/N] ")" DELETE_VENV
    if [[ "$DELETE_VENV" =~ ^[Yy]$ ]]; then
        rm -rf "$VENV_DIR"
        info "Deleted .venv"
    else
        info ".venv kept."
    fi
fi

echo ""
echo -e "${GREEN}✅  Uninstall complete. Vigil has been removed.${NC}"
