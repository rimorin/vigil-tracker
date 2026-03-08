#!/usr/bin/env bash
# install.sh — installs the Web Activity Tracker as macOS launchd services.
# Run once after setting up your .env file.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_TEMPLATE="$SCRIPT_DIR/.env.template"

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()    { echo -e "${GREEN}[install]${NC} $*"; }
warn()    { echo -e "${YELLOW}[install]${NC} $*"; }
error()   { echo -e "${RED}[install]${NC} $*" >&2; exit 1; }

# ── Prerequisites ─────────────────────────────────────────────────────────
info "Checking prerequisites..."

# macOS only — this installer relies on launchd which is macOS-specific
[[ "$(uname -s)" != "Darwin" ]] && error "This installer only supports macOS."

# python3 — resolve the real binary so launchd can find it without PATH shims
if command -v pyenv &>/dev/null; then
    PYTHON_PATH="$(pyenv which python3 2>/dev/null)" || PYTHON_PATH="$(command -v python3)"
else
    PYTHON_PATH="$(command -v python3)"
fi
[[ -z "$PYTHON_PATH" ]] && error "python3 not found. Install it: brew install python"

# Python >= 3.8
PY_VERSION="$("$PYTHON_PATH" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION#*.}"
if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 8) )); then
    error "Python 3.8 or newer is required (found $PY_VERSION). Upgrade: brew upgrade python"
fi
info "Python $PY_VERSION ✓"

# pip
"$PYTHON_PATH" -m pip --version &>/dev/null \
    || error "pip not available for $PYTHON_PATH. Try: $PYTHON_PATH -m ensurepip --upgrade"
info "pip ✓"

# Required project files
for f in requirements.txt com.tracker.web.plist com.tracker.summary.plist; do
    [[ ! -f "$SCRIPT_DIR/$f" ]] && error "Required file not found: $f"
done
info "Project files ✓"

# Internet connectivity (non-fatal — services may still work later)
if ! curl -sf --max-time 5 https://api.mailersend.com >/dev/null 2>&1; then
    warn "Could not reach api.mailersend.com — check your internet connection."
fi

# ── Generate .env.template if it doesn't exist ─────────────────────────────
if [[ ! -f "$ENV_TEMPLATE" ]]; then
    cat > "$ENV_TEMPLATE" <<'EOF'
# Web Activity Tracker — environment variables
# Copy this file to .env and fill in your values.

OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

MAILERSEND_API_KEY=mlsn....
MAILERSEND_FROM=tracker@yourdomain.com
MAILERSEND_TO=you@example.com

# Hour of day (0-23) to send the daily digest email. Default: 21 (9 PM).
SUMMARY_SCHEDULE_HOUR=21
EOF
    info "Created .env.template — copy it to .env and fill in your keys."
fi

# ── Require .env ───────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
    error ".env not found. Copy .env.template to .env and fill in your API keys, then re-run install.sh."
fi

# ── Load .env ──────────────────────────────────────────────────────────────
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# ── Validate required vars ─────────────────────────────────────────────────
for var in OPENAI_API_KEY MAILERSEND_API_KEY MAILERSEND_FROM MAILERSEND_TO; do
    [[ -z "${!var:-}" ]] && error "Required variable '$var' is not set in .env."
done

# Schedule defaults
SUMMARY_SCHEDULE="${SUMMARY_SCHEDULE:-daily}"
SUMMARY_SCHEDULE_HOUR="${SUMMARY_SCHEDULE_HOUR:-21}"
SUMMARY_SCHEDULE_MINUTE="${SUMMARY_SCHEDULE_MINUTE:-0}"
SUMMARY_SCHEDULE_WEEKDAY="${SUMMARY_SCHEDULE_WEEKDAY:-mon}"
SUMMARY_SCHEDULE_DAY="${SUMMARY_SCHEDULE_DAY:-1}"
SUMMARY_SCHEDULE_INTERVAL_MINUTES="${SUMMARY_SCHEDULE_INTERVAL_MINUTES:-60}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"

# Validate schedule type
case "$SUMMARY_SCHEDULE" in
    hourly|daily|weekly|monthly|interval) ;;
    *) error "SUMMARY_SCHEDULE must be one of: hourly, daily, weekly, monthly, interval. Got: $SUMMARY_SCHEDULE" ;;
esac

# ── Install Python dependencies ────────────────────────────────────────────
info "Using Python: $PYTHON_PATH"
info "Installing Python dependencies..."
"$PYTHON_PATH" -m pip install -q -r "$SCRIPT_DIR/requirements.txt"

# ── Helper: fill placeholders in a plist template ─────────────────────────
# Uses Python str.replace() via environment variables — immune to special
# characters in API keys (slashes, ampersands, etc. that break sed).
fill_plist() {
    local src="$1" dst="$2"
    _PLIST_PYTHON_PATH="$PYTHON_PATH" \
    _PLIST_PROJECT_DIR="$SCRIPT_DIR" \
    _PLIST_OPENAI_API_KEY="$OPENAI_API_KEY" \
    _PLIST_MAILERSEND_API_KEY="$MAILERSEND_API_KEY" \
    _PLIST_MAILERSEND_FROM="$MAILERSEND_FROM" \
    _PLIST_MAILERSEND_TO="$MAILERSEND_TO" \
    _PLIST_SUMMARY_SCHEDULE="$SUMMARY_SCHEDULE" \
    _PLIST_SUMMARY_SCHEDULE_HOUR="$SUMMARY_SCHEDULE_HOUR" \
    _PLIST_SUMMARY_SCHEDULE_MINUTE="$SUMMARY_SCHEDULE_MINUTE" \
    _PLIST_SUMMARY_SCHEDULE_WEEKDAY="$SUMMARY_SCHEDULE_WEEKDAY" \
    _PLIST_SUMMARY_SCHEDULE_DAY="$SUMMARY_SCHEDULE_DAY" \
    _PLIST_SUMMARY_SCHEDULE_INTERVAL_MINUTES="$SUMMARY_SCHEDULE_INTERVAL_MINUTES" \
    _PLIST_OPENAI_MODEL="$OPENAI_MODEL" \
    "$PYTHON_PATH" - "$src" "$dst" <<'PYEOF'
import os, sys
content = open(sys.argv[1]).read()
for placeholder, env_var in [
    ("__PYTHON_PATH__",                     "_PLIST_PYTHON_PATH"),
    ("__PROJECT_DIR__",                     "_PLIST_PROJECT_DIR"),
    ("__OPENAI_API_KEY__",                  "_PLIST_OPENAI_API_KEY"),
    ("__MAILERSEND_API_KEY__",              "_PLIST_MAILERSEND_API_KEY"),
    ("__MAILERSEND_FROM__",                 "_PLIST_MAILERSEND_FROM"),
    ("__MAILERSEND_TO__",                   "_PLIST_MAILERSEND_TO"),
    ("__SUMMARY_SCHEDULE__",               "_PLIST_SUMMARY_SCHEDULE"),
    ("__SUMMARY_SCHEDULE_HOUR__",           "_PLIST_SUMMARY_SCHEDULE_HOUR"),
    ("__SUMMARY_SCHEDULE_MINUTE__",         "_PLIST_SUMMARY_SCHEDULE_MINUTE"),
    ("__SUMMARY_SCHEDULE_WEEKDAY__",        "_PLIST_SUMMARY_SCHEDULE_WEEKDAY"),
    ("__SUMMARY_SCHEDULE_DAY__",            "_PLIST_SUMMARY_SCHEDULE_DAY"),
    ("__SUMMARY_SCHEDULE_INTERVAL_MINUTES__", "_PLIST_SUMMARY_SCHEDULE_INTERVAL_MINUTES"),
    ("__OPENAI_MODEL__",                    "_PLIST_OPENAI_MODEL"),
]:
    content = content.replace(placeholder, os.environ[env_var])
open(sys.argv[2], "w").write(content)
PYEOF
}

mkdir -p "$LAUNCH_AGENTS_DIR"

# ── Install tracker plist ──────────────────────────────────────────────────
WEB_PLIST_SRC="$SCRIPT_DIR/com.tracker.web.plist"
WEB_PLIST_DST="$LAUNCH_AGENTS_DIR/com.tracker.web.plist"

# Unload existing service if loaded
launchctl unload "$WEB_PLIST_DST" 2>/dev/null || true

fill_plist "$WEB_PLIST_SRC" "$WEB_PLIST_DST"
launchctl load "$WEB_PLIST_DST"
info "Tracker service installed and started (com.tracker.web)."

# ── Install summarizer plist ───────────────────────────────────────────────
SUMMARY_PLIST_SRC="$SCRIPT_DIR/com.tracker.summary.plist"
SUMMARY_PLIST_DST="$LAUNCH_AGENTS_DIR/com.tracker.summary.plist"

launchctl unload "$SUMMARY_PLIST_DST" 2>/dev/null || true

fill_plist "$SUMMARY_PLIST_SRC" "$SUMMARY_PLIST_DST"
launchctl load "$SUMMARY_PLIST_DST"
info "Summary service installed (com.tracker.summary) — schedule: ${SUMMARY_SCHEDULE}."

# ── Send confirmation email ────────────────────────────────────────────────
info "Sending confirmation email to ${MAILERSEND_TO}..."
if "$PYTHON_PATH" "$SCRIPT_DIR/summarizer.py" --confirm; then
    info "Confirmation email sent."
else
    warn "Confirmation email failed — check your MailerSend / OpenAI credentials."
    warn "The services are still running; this does not affect normal operation."
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}✅  Installation complete!${NC}"
echo ""
echo "  Tracker logs : $SCRIPT_DIR/tracker_daemon.log"
echo "  Summarizer logs : $SCRIPT_DIR/summarizer_daemon.log"
echo "  To uninstall : bash $SCRIPT_DIR/uninstall.sh"
