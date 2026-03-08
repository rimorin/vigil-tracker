#!/usr/bin/env bash
# install.sh — installs Vigil as macOS launchd services.
#
# Usage:
#   bash install.sh              — guided install (wizard prompts for any missing .env values)
#   bash install.sh --status     — show service health and recent log output
#   bash install.sh --reinstall  — re-fill plists and reload services (use after moving the project)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_TEMPLATE="$SCRIPT_DIR/.env.template"

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}[install]${NC} $*"; }
warn()  { echo -e "${YELLOW}[install]${NC} $*"; }
error() { echo -e "${RED}[install]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${BOLD}${CYAN}▶  $*${NC}"; }

# ── macOS check ────────────────────────────────────────────────────────────
[[ "$(uname -s)" != "Darwin" ]] && error "This installer only supports macOS."

# Detect macOS major version for launchctl compatibility
MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)
USER_UID=$(id -u)

# ── launchctl helpers (bootstrap/bootout on macOS 13+, load/unload on older) ─
launchctl_load() {
    local plist="$1"
    if (( MACOS_MAJOR >= 13 )); then
        launchctl bootstrap "gui/${USER_UID}" "$plist"
    else
        launchctl load "$plist"
    fi
}

launchctl_unload() {
    local plist="$1"
    if (( MACOS_MAJOR >= 13 )); then
        launchctl bootout "gui/${USER_UID}" "$plist" 2>/dev/null || true
    else
        launchctl unload "$plist" 2>/dev/null || true
    fi
}

launchctl_service_status() {
    local label="$1"
    if (( MACOS_MAJOR >= 13 )); then
        local state
        state=$(launchctl print "gui/${USER_UID}/${label}" 2>/dev/null \
            | grep -E "^\s+state\s*=" | awk '{print $3}') || true
        [[ -n "$state" ]] && echo "$state" || echo "not loaded"
    else
        local pid exit_code
        pid=$(launchctl list "$label" 2>/dev/null \
            | grep '"PID"' | awk -F'= ' '{gsub(/[";]/,"",$2); print $2}') || true
        exit_code=$(launchctl list "$label" 2>/dev/null \
            | grep '"LastExitStatus"' | awk -F'= ' '{gsub(/[";]/,"",$2); print $2}') || true
        if [[ -n "$pid" ]]; then
            echo "running (PID $pid)"
        elif launchctl list 2>/dev/null | grep -q "$label"; then
            echo "stopped (last exit: ${exit_code:-unknown})"
        else
            echo "not loaded"
        fi
    fi
}

# ── --status flag ──────────────────────────────────────────────────────────
if [[ "${1:-}" == "--status" ]]; then
    echo ""
    echo -e "${BOLD}${GREEN}━━━━  Vigil — Status  ━━━━${NC}"
    echo ""
    for label in com.vigil.tracker com.vigil.summarizer; do
        svc_status=$(launchctl_service_status "$label")
        echo -e "  ${BOLD}${label}${NC}: ${svc_status}"
    done
    echo ""
    for log in tracker_daemon.log summarizer_daemon.log; do
        log_path="$SCRIPT_DIR/$log"
        if [[ -f "$log_path" ]]; then
            echo -e "${CYAN}── ${log} (last 5 lines) ──${NC}"
            tail -5 "$log_path"
            echo ""
        fi
    done
    for log in tracker_stderr.log summarizer_stderr.log; do
        log_path="$SCRIPT_DIR/$log"
        if [[ -f "$log_path" ]] && [[ -s "$log_path" ]]; then
            echo -e "${YELLOW}── ${log} (last 5 lines) ──${NC}"
            tail -5 "$log_path"
            echo ""
        fi
    done
    exit 0
fi

# ── Prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites..."

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

"$PYTHON_PATH" -m pip --version &>/dev/null \
    || error "pip not available for $PYTHON_PATH. Try: $PYTHON_PATH -m ensurepip --upgrade"
info "pip ✓"

for f in requirements.txt com.vigil.tracker.plist com.vigil.summarizer.plist; do
    [[ ! -f "$SCRIPT_DIR/$f" ]] && error "Required file not found: $f"
done
info "Project files ✓  (macOS $(sw_vers -productVersion))"

# ── Ensure .env.template exists ────────────────────────────────────────────
if [[ ! -f "$ENV_TEMPLATE" ]]; then
    cat > "$ENV_TEMPLATE" <<'EOF'
# Vigil — environment variables
# Run bash install.sh and the wizard will fill this in for you,
# or copy to .env and edit manually.

OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

MAILERSEND_API_KEY=mlsn....
MAILERSEND_FROM=tracker@yourdomain.com
MAILERSEND_TO=you@example.com

# Schedule: hourly | daily | weekly | monthly | interval
SUMMARY_SCHEDULE=daily
SUMMARY_SCHEDULE_HOUR=21
SUMMARY_SCHEDULE_MINUTE=0
SUMMARY_SCHEDULE_WEEKDAY=mon
SUMMARY_SCHEDULE_DAY=1
SUMMARY_SCHEDULE_INTERVAL_MINUTES=60
EOF
fi

# ── Setup wizard — create / complete .env ─────────────────────────────────
step "Checking configuration..."

# Read a value from .env (returns empty string if key is absent or a placeholder)
read_env_value() {
    local key="$1" val
    val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')
    [[ "$val" == sk-... || "$val" == mlsn.... || "$val" =~ yourdomain || "$val" =~ example\.com ]] && val=""
    echo "$val"
}

# Write or update a key=value pair in .env
write_env_value() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        "$PYTHON_PATH" - "$key" "$val" "$ENV_FILE" <<'PYEOF'
import re, sys
key, val, path = sys.argv[1], sys.argv[2], sys.argv[3]
content = open(path).read()
content = re.sub(r'^' + re.escape(key) + r'=.*$', key + '=' + val, content, flags=re.MULTILINE)
open(path, 'w').write(content)
PYEOF
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

# Create .env from template if missing
[[ ! -f "$ENV_FILE" ]] && cp "$ENV_TEMPLATE" "$ENV_FILE"

REQUIRED_VARS=(OPENAI_API_KEY MAILERSEND_API_KEY MAILERSEND_FROM MAILERSEND_TO)
MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    val=$(read_env_value "$var")
    [[ -z "$val" ]] && MISSING_VARS+=("$var")
done

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  📝  Setup wizard — enter your API keys${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  The following values are missing from .env."
    echo "  Enter them now, or press Ctrl-C to edit .env manually."
    echo ""
    for var in "${MISSING_VARS[@]}"; do
        case "$var" in
            OPENAI_API_KEY)
                echo -e "  ${CYAN}OpenAI API key${NC}"
                echo "  → https://platform.openai.com/api-keys"
                ;;
            MAILERSEND_API_KEY)
                echo -e "  ${CYAN}MailerSend API key${NC}"
                echo "  → https://app.mailersend.com/api-tokens"
                ;;
            MAILERSEND_FROM)
                echo -e "  ${CYAN}Sender email address${NC} (must be a verified domain in MailerSend)"
                echo "  e.g. tracker@yourdomain.com"
                ;;
            MAILERSEND_TO)
                echo -e "  ${CYAN}Recipient email address(es)${NC}"
                echo "  Comma-separate to add an accountability partner, e.g. you@example.com,partner@example.com"
                ;;
        esac
        while true; do
            read -r -p "  ${var}: " entered_val
            [[ -n "$entered_val" ]] && break
            echo -e "  ${RED}Value cannot be empty.${NC}"
        done
        write_env_value "$var" "$entered_val"
        info "Saved ${var} ✓"
        echo ""
    done
fi

# ── Load .env ──────────────────────────────────────────────────────────────
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# ── Validate required vars ─────────────────────────────────────────────────
for var in "${REQUIRED_VARS[@]}"; do
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

case "$SUMMARY_SCHEDULE" in
    hourly|daily|weekly|monthly|interval) ;;
    *) error "SUMMARY_SCHEDULE must be one of: hourly, daily, weekly, monthly, interval. Got: $SUMMARY_SCHEDULE" ;;
esac

# ── Pre-flight credential validation ──────────────────────────────────────
step "Validating API credentials..."

OPENAI_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "Authorization: Bearer ${OPENAI_API_KEY}" \
    "https://api.openai.com/v1/models" 2>/dev/null || echo "000")
# 200 = valid; 403 = valid key but account/project restricted on this endpoint;
# 429 = valid key, rate limited — all mean the key itself is accepted.
if [[ "$OPENAI_HTTP" == "200" || "$OPENAI_HTTP" == "403" || "$OPENAI_HTTP" == "429" ]]; then
    info "OpenAI API key valid ✓"
elif [[ "$OPENAI_HTTP" == "401" ]]; then
    error "OpenAI API key is invalid (HTTP 401). Update OPENAI_API_KEY in .env and re-run."
else
    warn "Could not verify OpenAI API key (HTTP ${OPENAI_HTTP}) — check your internet connection."
fi

# /v1/domains lists sender domains — a reliable auth check on all account types.
MS_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "Authorization: Bearer ${MAILERSEND_API_KEY}" \
    "https://api.mailersend.com/v1/domains" 2>/dev/null || echo "000")
if [[ "$MS_HTTP" == "200" || "$MS_HTTP" == "403" ]]; then
    info "MailerSend API key valid ✓"
elif [[ "$MS_HTTP" == "401" ]]; then
    error "MailerSend API key is invalid (HTTP 401). Update MAILERSEND_API_KEY in .env and re-run."
else
    warn "Could not verify MailerSend API key (HTTP ${MS_HTTP}) — check your internet connection."
fi

# ── macOS permission reminder ──────────────────────────────────────────────
# Only show this if Accessibility permission hasn't been granted yet.
# A successful osascript call to System Events confirms the permission is in place.
if ! osascript -e 'tell application "System Events" to return count of processes' &>/dev/null; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  ⚙️   macOS permissions required (one-time setup)${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    if (( MACOS_MAJOR >= 13 )); then
        echo "  System Settings → Privacy & Security"
    else
        echo "  System Preferences → Security & Privacy → Privacy"
    fi
    echo "    • Accessibility  → add Terminal (or your terminal app)"
    echo "    • Automation     → allow Terminal to control Safari, Chrome, etc."
    echo ""
    echo -e "  Press ${BOLD}Enter${NC} to open System Settings now, or type ${BOLD}s${NC} to skip."
    read -r -p "  > " PERM_CHOICE
    if [[ "${PERM_CHOICE,,}" != "s" ]]; then
        open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true
        echo ""
        read -r -p "  Press Enter once you have granted permissions to continue..."
    fi
else
    info "macOS permissions already granted ✓"
fi

# ── Install Python dependencies ────────────────────────────────────────────
step "Installing Python dependencies..."
info "Using Python: $PYTHON_PATH"
"$PYTHON_PATH" -m pip install -q -r "$SCRIPT_DIR/requirements.txt"
info "Dependencies installed ✓"

# ── Helper: fill plist templates ───────────────────────────────────────────
# Only PYTHON_PATH and PROJECT_DIR are embedded in the plist.
# All secrets (API keys, schedule) remain in .env and are loaded at runtime
# by config.py via python-dotenv — they never touch the LaunchAgents directory.
fill_plist() {
    local src="$1" dst="$2"
    _PLIST_PYTHON_PATH="$PYTHON_PATH" \
    _PLIST_PROJECT_DIR="$SCRIPT_DIR" \
    "$PYTHON_PATH" - "$src" "$dst" <<'PYEOF'
import os, sys
content = open(sys.argv[1]).read()
for placeholder, env_var in [
    ("__PYTHON_PATH__", "_PLIST_PYTHON_PATH"),
    ("__PROJECT_DIR__", "_PLIST_PROJECT_DIR"),
]:
    content = content.replace(placeholder, os.environ[env_var])
open(sys.argv[2], "w").write(content)
PYEOF
}

mkdir -p "$LAUNCH_AGENTS_DIR"

# ── Install tracker service ────────────────────────────────────────────────
step "Installing launchd services..."

WEB_PLIST_SRC="$SCRIPT_DIR/com.vigil.tracker.plist"
WEB_PLIST_DST="$LAUNCH_AGENTS_DIR/com.vigil.tracker.plist"
launchctl_unload "$WEB_PLIST_DST"
fill_plist "$WEB_PLIST_SRC" "$WEB_PLIST_DST"
launchctl_load "$WEB_PLIST_DST"
info "Tracker service installed and started (com.vigil.tracker) ✓"

# ── Install summarizer service ─────────────────────────────────────────────
SUMMARY_PLIST_SRC="$SCRIPT_DIR/com.vigil.summarizer.plist"
SUMMARY_PLIST_DST="$LAUNCH_AGENTS_DIR/com.vigil.summarizer.plist"
launchctl_unload "$SUMMARY_PLIST_DST"
fill_plist "$SUMMARY_PLIST_SRC" "$SUMMARY_PLIST_DST"
launchctl_load "$SUMMARY_PLIST_DST"
info "Summarizer service installed (com.vigil.summarizer) — schedule: ${SUMMARY_SCHEDULE} ✓"

# ── Send confirmation email ────────────────────────────────────────────────
step "Sending confirmation email..."
if "$PYTHON_PATH" "$SCRIPT_DIR/summarizer.py" --confirm; then
    info "Confirmation email sent to ${MAILERSEND_TO} ✓"
else
    warn "Confirmation email failed — check your MailerSend / OpenAI credentials."
    warn "The services are still running; this does not affect normal operation."
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅  Installation complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Tracker logs    : $SCRIPT_DIR/tracker_daemon.log"
echo "  Summarizer logs : $SCRIPT_DIR/summarizer_daemon.log"
echo "  Check status    : bash $SCRIPT_DIR/install.sh --status"
echo "  To uninstall    : bash $SCRIPT_DIR/uninstall.sh"
echo ""
