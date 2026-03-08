#!/usr/bin/env bash
# uninstall.sh — removes Vigil launchd services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[uninstall]${NC} $*"; }
warn()  { echo -e "${YELLOW}[uninstall]${NC} $*"; }

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

# ── Send uninstall notification email (before stopping services / deleting .env)
ENV_FILE="$SCRIPT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
    if command -v pyenv &>/dev/null; then
        PYTHON_PATH="$(pyenv which python3 2>/dev/null)" || PYTHON_PATH="$(command -v python3 2>/dev/null || true)"
    else
        PYTHON_PATH="$(command -v python3 2>/dev/null || true)"
    fi
    info "Sending uninstall notification email..."
    # Source .env so summarizer.py can reach the API keys
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    if "$PYTHON_PATH" "$SCRIPT_DIR/summarizer.py" --uninstall-notify; then
        info "Uninstall notification sent."
    else
        warn "Could not send uninstall notification — continuing with uninstall."
    fi
else
    warn "Skipping uninstall notification (.env not found or python3 unavailable)."
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
    "$SCRIPT_DIR/detailed_activity_log.txt"
    "$SCRIPT_DIR/detailed_activity_log.txt.sha256"
    "$SCRIPT_DIR/universal_activity_log.txt"
    "$SCRIPT_DIR/web_activity_log.txt"
    "$SCRIPT_DIR/tracker_daemon.log"
    "$SCRIPT_DIR/tracker_stderr.log"
    "$SCRIPT_DIR/summarizer_daemon.log"
    "$SCRIPT_DIR/summarizer_stderr.log"
    "$SCRIPT_DIR/last_summarized_date.txt"
)

echo ""
read -r -p "$(echo -e "${YELLOW}Delete all log files and tracking data?${NC} [y/N] ")" DELETE_LOGS
if [[ "$DELETE_LOGS" =~ ^[Yy]$ ]]; then
    for f in "${LOG_FILES[@]}"; do
        rm -f "$f" "$f".[1-9]   # also remove rotated backups (.log.1, .log.2, etc.)
        [[ -f "$f" ]] || [[ -f "$f.1" ]] || info "Deleted $f"
    done
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

# ── Optionally remove virtual environment ─────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
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
