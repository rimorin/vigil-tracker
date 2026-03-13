#!/usr/bin/env bash
# install.sh — installs Vigil as macOS launchd services.
#
# Usage:
#   bash install.sh              — guided install (wizard prompts for any missing .env values)
#   bash install.sh --status     — show service health and recent log output
#   bash install.sh --update     — interactively update configuration settings and reload services
#   bash install.sh --blocklist  — download a fresh domain blocklist and restart the tracker
#   bash install.sh --reinstall  — re-fill plists and reload services (use after moving the project)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
ENV_FILE="$REPO_ROOT/.env"
ENV_TEMPLATE="$REPO_ROOT/.env.template"

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}[install]${NC} $*"; }
warn()  { echo -e "${YELLOW}[install]${NC} $*"; }
error() { echo -e "${RED}[install]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${BOLD}${CYAN}▶  $*${NC}"; }

# ── SMTP auto-detect: infer host/port + app-password URL from email domain ──
# Sets globals: SMTP_DETECTED_HOST, SMTP_DETECTED_PORT,
#               SMTP_DETECTED_APPPASS_URL, SMTP_DETECTED_APPPASS_LABEL
_smtp_auto_detect() {
    local email="$1"
    local domain; domain=$(echo "${email##*@}" | tr '[:upper:]' '[:lower:]')
    SMTP_DETECTED_HOST=""; SMTP_DETECTED_PORT=587
    SMTP_DETECTED_APPPASS_URL=""; SMTP_DETECTED_APPPASS_LABEL=""
    case "$domain" in
        gmail.com|googlemail.com)
            SMTP_DETECTED_HOST="smtp.gmail.com"; SMTP_DETECTED_PORT=587
            SMTP_DETECTED_APPPASS_URL="https://myaccount.google.com/apppasswords"
            SMTP_DETECTED_APPPASS_LABEL="Gmail App Password" ;;
        outlook.com|hotmail.com|hotmail.co.uk|live.com|live.co.uk|msn.com)
            SMTP_DETECTED_HOST="smtp.office365.com"; SMTP_DETECTED_PORT=587
            SMTP_DETECTED_APPPASS_URL="https://aka.ms/AppPasswords"
            SMTP_DETECTED_APPPASS_LABEL="Microsoft App Password" ;;
        yahoo.com|yahoo.co.uk|ymail.com)
            SMTP_DETECTED_HOST="smtp.mail.yahoo.com"; SMTP_DETECTED_PORT=587
            SMTP_DETECTED_APPPASS_URL="https://help.yahoo.com/kb/generate-third-party-passwords-sln15241.html"
            SMTP_DETECTED_APPPASS_LABEL="Yahoo App Password" ;;
        icloud.com|me.com|mac.com)
            SMTP_DETECTED_HOST="smtp.mail.me.com"; SMTP_DETECTED_PORT=587
            SMTP_DETECTED_APPPASS_URL="https://appleid.apple.com/account/manage"
            SMTP_DETECTED_APPPASS_LABEL="iCloud App-Specific Password" ;;
        fastmail.com|fastmail.fm|fastmail.net)
            SMTP_DETECTED_HOST="smtp.fastmail.com"; SMTP_DETECTED_PORT=587
            SMTP_DETECTED_APPPASS_URL="https://app.fastmail.com/settings/security/devicekeys/"
            SMTP_DETECTED_APPPASS_LABEL="Fastmail App Password" ;;
    esac
}

# ── Spinner (animated indicator for long-running operations) ───────────────
_SPIN_PID=""
_start_spinner() {
    [[ -t 1 ]] || return 0   # skip when output is not a terminal
    local msg="$1"
    ( local i=0 frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
      while true; do
          printf "\r  ${CYAN}%s${NC} %s" "${frames:$(( i % ${#frames} )):1}" "$msg"
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
            | grep -E "^\s+state\s*=" | head -1 | awk '{print $3}') || true
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
    for label in com.vigil.tracker com.vigil.summarizer com.vigil.watchdog; do
        svc_status=$(launchctl_service_status "$label")
        echo -e "  ${BOLD}${label}${NC}: ${svc_status}"
    done
    echo ""
    if [[ -f "$ENV_FILE" ]]; then
        set -a; source "$ENV_FILE"; set +a
        echo -e "${BOLD}${CYAN}Email / SMTP${NC}"
        echo -e "  SMTP Host   : ${SMTP_HOST:-<not set>}:${SMTP_PORT:-<not set>}"
        echo -e "  SMTP User   : ${SMTP_USER:-<not set>}"
        echo -e "  From        : ${SMTP_FROM:-${SMTP_USER:-<not set>}}"
        echo -e "  Recipient   : ${SMTP_TO:-<not set>}"
        echo ""
        echo -e "${BOLD}${CYAN}AI${NC}"
        echo -e "  Model       : ${OPENAI_MODEL:-<not set>}"
        echo -e "  API Key     : ${OPENAI_API_KEY:+set (hidden)}"
        echo ""
        echo -e "${BOLD}${CYAN}Summary Schedule${NC}"
        SCHED="${SUMMARY_SCHEDULE:-daily}"
        echo -e "  Schedule    : ${SCHED}"
        case "$SCHED" in
            daily)    echo -e "  Send time   : $(printf '%02d:%02d' "${SUMMARY_SCHEDULE_HOUR:-21}" "${SUMMARY_SCHEDULE_MINUTE:-0}")" ;;
            weekly)   echo -e "  Send time   : ${SUMMARY_SCHEDULE_WEEKDAY:-mon} at $(printf '%02d:%02d' "${SUMMARY_SCHEDULE_HOUR:-9}" "${SUMMARY_SCHEDULE_MINUTE:-0}")" ;;
            monthly)  echo -e "  Send time   : day ${SUMMARY_SCHEDULE_DAY:-1} at $(printf '%02d:%02d' "${SUMMARY_SCHEDULE_HOUR:-9}" "${SUMMARY_SCHEDULE_MINUTE:-0}")" ;;
            interval) echo -e "  Interval    : every ${SUMMARY_SCHEDULE_INTERVAL_MINUTES:-60} minutes" ;;
            hourly)   echo -e "  Send time   : every hour" ;;
        esac
        echo ""
        echo -e "${BOLD}${CYAN}Adult Content Alerts${NC}"
        echo -e "  Enabled     : ${ALERT_ENABLED:-true}"
        echo -e "  Cooldown    : ${ALERT_COOLDOWN_MINUTES:-30} minutes"
        echo -e "  Email alert : ${ALERT_EMAIL:-true}"
        echo ""
    else
        echo -e "${YELLOW}No .env file found — settings unavailable${NC}"
        echo ""
    fi
    for log in tracker_daemon.log summarizer_daemon.log; do
        log_path="$HOME/Library/Logs/Vigil/$log"
        if [[ -f "$log_path" ]]; then
            echo -e "${CYAN}── ${log} (last 5 lines) ──${NC}"
            tail -5 "$log_path"
            echo ""
        fi
    done
    for log in tracker_stderr.log summarizer_stderr.log watchdog_stderr.log; do
        log_path="$HOME/Library/Logs/Vigil/$log"
        if [[ -f "$log_path" ]] && [[ -s "$log_path" ]]; then
            echo -e "${YELLOW}── ${log} (last 5 lines) ──${NC}"
            tail -5 "$log_path"
            echo ""
        fi
    done
    exit 0
fi

# ── --update flag ─────────────────────────────────────────────────────────
if [[ "${1:-}" == "--update" ]]; then
    echo ""
    echo -e "${BOLD}${GREEN}━━━━  Vigil — Update Settings  ━━━━${NC}"
    echo ""
    if [[ ! -f "$ENV_FILE" ]]; then
        echo -e "${YELLOW}No .env file found at $ENV_FILE — run bash install.sh to install first.${NC}"
        exit 1
    fi

    # Resolve Python — prefer venv if already installed, fall back to system python
    _VENV_DIR="$REPO_ROOT/.venv"
    if [[ -x "$_VENV_DIR/bin/python" ]]; then
        PYTHON_PATH="$_VENV_DIR/bin/python"
    elif command -v pyenv &>/dev/null; then
        PYTHON_PATH="$(pyenv which python 2>/dev/null)" || PYTHON_PATH="$(command -v python)"
    else
        PYTHON_PATH="$(command -v python)"
    fi
    [[ -z "$PYTHON_PATH" ]] && { echo "python not found — cannot manage partner PIN."; PYTHON_PATH="python"; }

    # ── Partner PIN verification (required before any settings can change) ──
    if "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" status &>/dev/null; then
        echo -e "  ${BOLD}🔒  A partner PIN is required to update settings.${NC}"
        if ! "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" verify; then
            echo -e "${RED}[update] Update aborted — PIN verification failed.${NC}"
            exit 1
        fi
    fi

    # Inline helpers (use system python; venv may not be set up yet)
    _read_env() {
        local key="$1" val
        val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')
        [[ "$val" == "sk-..." || "$val" == "your-app-password" || "$val" =~ example\.com || "$val" == "you@gmail.com" ]] && val=""
        echo "$val"
    }
    _write_env() {
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

    set -a; source "$ENV_FILE"; set +a
    echo "  Press Enter to keep the value shown in [brackets]."
    echo ""

    # ── Email / SMTP ───────────────────────────────────────────────────────
    echo -e "${BOLD}${CYAN}Email / SMTP${NC}"

    cur=$(_read_env "SMTP_HOST"); cur="${cur:-${SMTP_HOST:-smtp.gmail.com}}"
    read -r -p "  SMTP Host [${cur}]: " val
    _write_env "SMTP_HOST" "${val:-${cur}}"

    cur=$(_read_env "SMTP_PORT"); cur="${cur:-${SMTP_PORT:-587}}"
    read -r -p "  SMTP Port [${cur}]: " val
    _write_env "SMTP_PORT" "${val:-${cur}}"

    cur=$(_read_env "SMTP_USER"); cur="${cur:-${SMTP_USER:-}}"
    read -r -p "  SMTP User [${cur}]: " val
    _write_env "SMTP_USER" "${val:-${cur}}"

    read -r -s -p "  SMTP Password (leave blank to keep current): " val; echo ""
    [[ -n "$val" ]] && _write_env "SMTP_PASS" "$val"

    cur=$(_read_env "SMTP_TO"); cur="${cur:-${SMTP_TO:-}}"
    read -r -p "  Recipient email(s) [${cur}]: " val
    _write_env "SMTP_TO" "${val:-${cur}}"

    echo ""

    # ── AI ─────────────────────────────────────────────────────────────────
    echo -e "${BOLD}${CYAN}AI${NC}"

    cur=$(_read_env "OPENAI_MODEL"); cur="${cur:-${OPENAI_MODEL:-gpt-4o-mini}}"
    read -r -p "  OpenAI Model [${cur}]: " val
    _write_env "OPENAI_MODEL" "${val:-${cur}}"

    read -r -s -p "  OpenAI API Key (optional — leave blank for plain visit list digest, or to keep current): " val; echo ""
    [[ -n "$val" ]] && _write_env "OPENAI_API_KEY" "$val"

    echo ""

    # ── Summary Schedule ───────────────────────────────────────────────────
    echo -e "${BOLD}${CYAN}Summary Schedule${NC}"
    CUR_SCHED=$(_read_env "SUMMARY_SCHEDULE"); CUR_SCHED="${CUR_SCHED:-${SUMMARY_SCHEDULE:-daily}}"
    echo -e "  Current schedule: ${CUR_SCHED}"
    echo "  1) daily    2) hourly    3) weekly    4) monthly    5) keep current (${CUR_SCHED})"
    echo ""
    while true; do
        read -r -p "  Choice [1-5, default 5]: " sched_choice
        sched_choice="${sched_choice:-5}"
        case "$sched_choice" in 1|2|3|4|5) break ;; esac
        echo -e "  ${RED}Enter 1, 2, 3, 4, or 5.${NC}"
    done

    case "$sched_choice" in
        1)
            _write_env "SUMMARY_SCHEDULE" "daily"
            cur=$(_read_env "SUMMARY_SCHEDULE_HOUR"); cur="${cur:-${SUMMARY_SCHEDULE_HOUR:-21}}"
            read -r -p "  Hour to send (0-23) [${cur}]: " val
            _write_env "SUMMARY_SCHEDULE_HOUR" "${val:-${cur}}"
            cur=$(_read_env "SUMMARY_SCHEDULE_MINUTE"); cur="${cur:-${SUMMARY_SCHEDULE_MINUTE:-0}}"
            read -r -p "  Minute (0-59) [${cur}]: " val
            _write_env "SUMMARY_SCHEDULE_MINUTE" "${val:-${cur}}"
            info "Schedule: daily ✓"
            ;;
        2)
            _write_env "SUMMARY_SCHEDULE" "hourly"
            cur=$(_read_env "SUMMARY_SCHEDULE_MINUTE"); cur="${cur:-${SUMMARY_SCHEDULE_MINUTE:-0}}"
            read -r -p "  Minute past the hour (0-59) [${cur}]: " val
            _write_env "SUMMARY_SCHEDULE_MINUTE" "${val:-${cur}}"
            info "Schedule: hourly ✓"
            ;;
        3)
            _write_env "SUMMARY_SCHEDULE" "weekly"
            cur=$(_read_env "SUMMARY_SCHEDULE_WEEKDAY"); cur="${cur:-${SUMMARY_SCHEDULE_WEEKDAY:-mon}}"
            read -r -p "  Day of week (mon-sun) [${cur}]: " val
            _write_env "SUMMARY_SCHEDULE_WEEKDAY" "${val:-${cur}}"
            cur=$(_read_env "SUMMARY_SCHEDULE_HOUR"); cur="${cur:-${SUMMARY_SCHEDULE_HOUR:-9}}"
            read -r -p "  Hour to send (0-23) [${cur}]: " val
            _write_env "SUMMARY_SCHEDULE_HOUR" "${val:-${cur}}"
            info "Schedule: weekly ✓"
            ;;
        4)
            _write_env "SUMMARY_SCHEDULE" "monthly"
            cur=$(_read_env "SUMMARY_SCHEDULE_DAY"); cur="${cur:-${SUMMARY_SCHEDULE_DAY:-1}}"
            read -r -p "  Day of month (1-28) [${cur}]: " val
            _write_env "SUMMARY_SCHEDULE_DAY" "${val:-${cur}}"
            cur=$(_read_env "SUMMARY_SCHEDULE_HOUR"); cur="${cur:-${SUMMARY_SCHEDULE_HOUR:-9}}"
            read -r -p "  Hour to send (0-23) [${cur}]: " val
            _write_env "SUMMARY_SCHEDULE_HOUR" "${val:-${cur}}"
            info "Schedule: monthly ✓"
            ;;
        5)
            info "Schedule unchanged."
            ;;
    esac

    echo ""

    # ── Adult Content Alerts ───────────────────────────────────────────────
    echo -e "${BOLD}${CYAN}Adult Content Alerts${NC}"

    cur=$(_read_env "ALERT_ENABLED"); cur="${cur:-${ALERT_ENABLED:-true}}"
    read -r -p "  Enabled (true/false) [${cur}]: " val
    _write_env "ALERT_ENABLED" "${val:-${cur}}"

    cur=$(_read_env "ALERT_COOLDOWN_MINUTES"); cur="${cur:-${ALERT_COOLDOWN_MINUTES:-30}}"
    read -r -p "  Cooldown minutes [${cur}]: " val
    _write_env "ALERT_COOLDOWN_MINUTES" "${val:-${cur}}"

    cur=$(_read_env "ALERT_EMAIL"); cur="${cur:-${ALERT_EMAIL:-true}}"
    read -r -p "  Email alerts (true/false) [${cur}]: " val
    _write_env "ALERT_EMAIL" "${val:-${cur}}"

    echo ""
    echo -e "${BOLD}${CYAN}Partner PIN${NC}"
    if "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" status &>/dev/null; then
        echo "  A partner PIN is currently set."
        read -r -p "  Change partner PIN? [y/N]: " CHANGE_PIN
        if [[ "$CHANGE_PIN" =~ ^[Yy]$ ]]; then
            if "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" hash; then
                info "Partner PIN updated ✓"
            else
                warn "PIN update cancelled — keeping existing PIN."
            fi
        fi
        read -r -p "  Remove partner PIN entirely? [y/N]: " REMOVE_PIN
        if [[ "$REMOVE_PIN" =~ ^[Yy]$ ]]; then
            "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" delete
            info "Partner PIN removed."
        fi
    else
        echo "  No partner PIN is currently set."
        read -r -p "  Set a partner PIN? [y/N]: " SET_PIN
        if [[ "$SET_PIN" =~ ^[Yy]$ ]]; then
            if "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" hash; then
                info "Partner PIN set ✓"
            else
                warn "PIN setup cancelled — skipping."
            fi
        fi
    fi

    echo ""
    info "Settings saved to .env ✓"
    echo ""

    # ── Reload running services ────────────────────────────────────────────
    _start_spinner "Reloading services..."
    RELOAD_COUNT=0
    # Write graceful sentinel so watchdog SIGTERM handler knows this is a
    # legitimate reload and suppresses the partner alert.
    mkdir -p "$HOME/Library/Application Support/Vigil"
    touch "$HOME/Library/Application Support/Vigil/watchdog_graceful_shutdown"
    for plist_dst in \
        "$LAUNCH_AGENTS_DIR/com.vigil.tracker.plist" \
        "$LAUNCH_AGENTS_DIR/com.vigil.summarizer.plist" \
        "$LAUNCH_AGENTS_DIR/com.vigil.watchdog.plist"; do
        if [[ -f "$plist_dst" ]]; then
            launchctl_unload "$plist_dst"
            launchctl_load  "$plist_dst"
            (( RELOAD_COUNT++ )) || true
        fi
    done
    _stop_spinner

    if (( RELOAD_COUNT > 0 )); then
        info "Services reloaded with new settings ✓"
    else
        warn "No installed services found — run bash install.sh to install."
    fi

    echo ""
    # Snapshot the (now-updated) .env so the summariser can detect future tampering.
    "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" env_store 2>/dev/null || true
    exit 0
fi
if [[ "${1:-}" == "--blocklist" ]]; then
    BLOCKLIST_FILE="$REPO_ROOT/data/domains.txt"
    BLOCKLIST_URL="https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts"

    echo ""
    echo -e "${BOLD}${GREEN}━━━━  Vigil — Update Domain Blocklist  ━━━━${NC}"
    echo ""
    info "Downloading blocklist from Steven Black's hosts list..."

    curl -fsSL --max-time 60 "$BLOCKLIST_URL" -o "$BLOCKLIST_FILE" \
        || error "Download failed. Check your internet connection and try again."

    grep -v '^#' "$BLOCKLIST_FILE" | grep -v '^$' \
        | awk '{print $2}' \
        | grep -v '^0\.0\.0\.0$' \
        > "${BLOCKLIST_FILE}.clean"
    mv "${BLOCKLIST_FILE}.clean" "$BLOCKLIST_FILE"

    DOMAIN_COUNT=$(wc -l < "$BLOCKLIST_FILE" | tr -d ' ')
    info "Blocklist updated — ${DOMAIN_COUNT} domains ✓"

    # Restart the tracker so it picks up the new blocklist.
    TRACKER_PLIST="$LAUNCH_AGENTS_DIR/com.vigil.tracker.plist"
    if [[ -f "$TRACKER_PLIST" ]]; then
        _start_spinner "Restarting tracker..."
        launchctl_unload "$TRACKER_PLIST"
        launchctl_load   "$TRACKER_PLIST"
        _stop_spinner
        info "Tracker restarted — new blocklist is active ✓"
    else
        warn "Tracker not installed yet. The new blocklist will be loaded on next install."
    fi

    echo ""
    exit 0
fi

# ── --reinstall flag ───────────────────────────────────────────────────────
# Skips the setup wizard and credential checks.
# Use after moving the project directory or to pick up code changes.
REINSTALL=false
if [[ "${1:-}" == "--reinstall" ]]; then
    REINSTALL=true
    info "Reinstall mode — skipping wizard and credential checks."
    # Resolve Python early so we can run the PIN check before proceeding.
    _VENV_DIR_EARLY="$REPO_ROOT/.venv"
    if [[ -x "$_VENV_DIR_EARLY/bin/python" ]]; then
        _PIN_PYTHON="$_VENV_DIR_EARLY/bin/python"
    elif command -v pyenv &>/dev/null; then
        _PIN_PYTHON="$(pyenv which python 2>/dev/null)" || _PIN_PYTHON="$(command -v python 2>/dev/null || true)"
    else
        _PIN_PYTHON="$(command -v python 2>/dev/null || true)"
    fi
    if [[ -n "$_PIN_PYTHON" ]] && "$_PIN_PYTHON" "$REPO_ROOT/pin_auth.py" status &>/dev/null; then
        echo -e "  ${BOLD}🔒  A partner PIN is required to reinstall.${NC}"
        if ! "$_PIN_PYTHON" "$REPO_ROOT/pin_auth.py" verify; then
            echo -e "${RED}[reinstall] Reinstall aborted — PIN verification failed.${NC}"
            exit 1
        fi
    fi
fi

# ── Prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites..."

# python — resolve the real binary so launchd can find it without PATH shims
# We use "python" here to respect pyenv's active version.
# (Windows scripts use a broader probe — see platforms/windows/install.ps1)
if command -v pyenv &>/dev/null; then
    PYTHON_PATH="$(pyenv which python 2>/dev/null)" || PYTHON_PATH="$(command -v python)"
else
    PYTHON_PATH="$(command -v python)"
fi
[[ -z "$PYTHON_PATH" ]] && error "python not found. Install it via pyenv or brew install python"

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

for f in com.vigil.tracker.plist com.vigil.summarizer.plist com.vigil.watchdog.plist; do
    [[ ! -f "$SCRIPT_DIR/$f" ]] && error "Required file not found: $f"
done
[[ ! -f "$REPO_ROOT/requirements.txt" ]] && error "Required file not found: requirements.txt"
info "Project files ✓  (macOS $(sw_vers -productVersion))"

# ── Ensure .env.template exists ────────────────────────────────────────────
if [[ ! -f "$ENV_TEMPLATE" ]]; then
    cat > "$ENV_TEMPLATE" <<'EOF'
# Vigil — environment variables
# Run bash install.sh and the wizard will fill this in for you,
# or copy to .env and edit manually.

OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# SMTP email settings — works with Gmail, Outlook, Fastmail, or any SMTP provider
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your-app-password
# SMTP_FROM defaults to SMTP_USER if not set
# SMTP_FROM=you@gmail.com
SMTP_TO=you@example.com

# Schedule: hourly | daily | weekly | monthly | interval
SUMMARY_SCHEDULE=daily
SUMMARY_SCHEDULE_HOUR=21
SUMMARY_SCHEDULE_MINUTE=0
SUMMARY_SCHEDULE_WEEKDAY=mon
SUMMARY_SCHEDULE_DAY=1
SUMMARY_SCHEDULE_INTERVAL_MINUTES=60

# ── Adult content alerts ────────────────────────────────────────────────────
# Real-time email alert when an adult/porn site is visited.
# All values below are optional — defaults shown are used if not set.
ALERT_ENABLED=true
ALERT_COOLDOWN_MINUTES=30
ALERT_EMAIL=true
EOF
fi

# ── Setup wizard — create / complete .env ─────────────────────────────────
REQUIRED_VARS=(SMTP_USER SMTP_HOST SMTP_PASS SMTP_TO)
if [[ "$REINSTALL" == false ]]; then

step "Checking configuration..."

# Read a value from .env (returns empty string if key is absent or a placeholder)
read_env_value() {
    local key="$1" val
    val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')
    [[ "$val" == "sk-..." || "$val" == "your-app-password" || "$val" =~ example\.com || "$val" == "you@gmail.com" ]] && val=""
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

# Initialise SMTP auto-detection state.
SMTP_HOST_AUTO_FILLED=false
SMTP_PASS_VERIFIED=false
SMTP_USER_ENTERED=""
SMTP_DETECTED_HOST=""; SMTP_DETECTED_PORT=587
SMTP_DETECTED_APPPASS_URL=""; SMTP_DETECTED_APPPASS_LABEL=""

# If SMTP_USER is already configured, pre-run auto-detection so we can silently
# fill SMTP_HOST/PORT without asking the user for them again.
_existing_smtp_user=$(read_env_value "SMTP_USER")
if [[ -n "$_existing_smtp_user" ]]; then
    _smtp_auto_detect "$_existing_smtp_user"
    if [[ -n "$SMTP_DETECTED_HOST" ]] && [[ -z "$(read_env_value "SMTP_HOST")" ]]; then
        write_env_value "SMTP_HOST" "$SMTP_DETECTED_HOST"
        write_env_value "SMTP_PORT" "$SMTP_DETECTED_PORT"
        SMTP_HOST_AUTO_FILLED=true
    fi
fi

# SMTP_USER is listed first so we can auto-detect SMTP_HOST/PORT from it.
MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    val=$(read_env_value "$var")
    [[ -z "$val" ]] && MISSING_VARS+=("$var")
done

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  📝  Setup wizard — just a few questions to get started${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Enter them now, or press Ctrl-C to edit .env manually."
    echo ""
    for var in "${MISSING_VARS[@]}"; do
        # SMTP_HOST was auto-detected from the email address — skip it.
        if [[ "$var" == "SMTP_HOST" && "$SMTP_HOST_AUTO_FILLED" == true ]]; then
            continue
        fi

        entered_val=""
        case "$var" in
            SMTP_USER)
                echo -e "  ${CYAN}Your email address${NC} (used as the sender for digest emails)"
                while true; do
                    read -r -p "  Email: " entered_val
                    [[ -n "$entered_val" ]] && break
                    echo -e "  ${RED}Value cannot be empty.${NC}"
                done
                ;;
            SMTP_HOST)
                echo -e "  ${CYAN}SMTP server hostname${NC}"
                echo "  Gmail: smtp.gmail.com  |  Outlook: smtp.office365.com  |  Fastmail: smtp.fastmail.com"
                while true; do
                    read -r -p "  SMTP_HOST: " entered_val
                    [[ -n "$entered_val" ]] && break
                    echo -e "  ${RED}Value cannot be empty.${NC}"
                done
                ;;
            SMTP_PASS)
                echo -e "  ${CYAN}SMTP password / app password${NC}"
                if [[ -n "$SMTP_DETECTED_APPPASS_URL" ]]; then
                    echo "  Create a ${SMTP_DETECTED_APPPASS_LABEL}: ${SMTP_DETECTED_APPPASS_URL}"
                else
                    echo "  Use an app password — not your regular sign-in password."
                    echo "  Gmail   → https://myaccount.google.com/apppasswords"
                    echo "  iCloud  → https://appleid.apple.com/account/manage"
                    echo "  Outlook → https://aka.ms/AppPasswords"
                fi
                echo "  After generating your app password, come back here and paste it below."
                _test_host=$(read_env_value "SMTP_HOST"); _test_host="${_test_host:-smtp.gmail.com}"
                _test_port=$(read_env_value "SMTP_PORT"); _test_port="${_test_port:-587}"
                _test_user=$(read_env_value "SMTP_USER"); [[ -z "$_test_user" ]] && _test_user="$SMTP_USER_ENTERED"
                while true; do
                    read -r -s -p "  Password: " entered_val; echo ""
                    if [[ -z "$entered_val" ]]; then
                        echo -e "  ${RED}Value cannot be empty.${NC}"
                        continue
                    fi
                    _start_spinner "Verifying SMTP credentials..."
                    _smtp_inline=$("$PYTHON_PATH" - \
                        "$_test_host" "$_test_port" "$_test_user" "$entered_val" <<'PYEOF' 2>&1 || true
import smtplib, ssl, sys
host, port, user, pw = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
try:
    if port == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as s:
            s.login(user, pw)
    else:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.ehlo(); s.starttls(); s.ehlo(); s.login(user, pw)
    print("ok")
except smtplib.SMTPAuthenticationError:
    print("auth_failed"); sys.exit(1)
except Exception as e:
    print(f"error: {e}"); sys.exit(2)
PYEOF
                    )
                    _stop_spinner
                    if [[ "$_smtp_inline" == "ok" ]]; then
                        info "SMTP credentials verified ✓"
                        SMTP_PASS_VERIFIED=true
                        break
                    elif [[ "$_smtp_inline" == "auth_failed" ]]; then
                        echo -e "  ${RED}Authentication failed.${NC} Double-check your app password and try again."
                        echo "  Ensure 2FA is enabled and you copied the app password (not your sign-in password)."
                    else
                        warn "Could not connect to SMTP (${_smtp_inline}) — saving anyway. Check your internet connection."
                        break
                    fi
                done
                ;;
            SMTP_TO)
                echo -e "  ${CYAN}Recipient email address(es)${NC}"
                # Default to the sender email so the user doesn't have to type it twice.
                _smtp_to_default=$(read_env_value "SMTP_USER")
                [[ -z "$_smtp_to_default" ]] && _smtp_to_default="$SMTP_USER_ENTERED"
                if [[ -n "$_smtp_to_default" ]]; then
                    echo "  Add your accountability partner's email too (comma-separated)."
                    read -r -p "  Send to [${_smtp_to_default}]: " entered_val
                    entered_val="${entered_val:-$_smtp_to_default}"
                else
                    echo "  Comma-separate: you@example.com,partner@example.com"
                    while true; do
                        read -r -p "  SMTP_TO: " entered_val
                        [[ -n "$entered_val" ]] && break
                        echo -e "  ${RED}Value cannot be empty.${NC}"
                    done
                fi
                ;;
        esac

        write_env_value "$var" "$entered_val"
        info "Saved ${var} ✓"

        # After SMTP_USER: auto-detect SMTP_HOST/PORT from the email domain.
        if [[ "$var" == "SMTP_USER" ]]; then
            SMTP_USER_ENTERED="$entered_val"
            _smtp_auto_detect "$entered_val"
            if [[ -n "$SMTP_DETECTED_HOST" ]] && [[ -z "$(read_env_value "SMTP_HOST")" ]]; then
                write_env_value "SMTP_HOST" "$SMTP_DETECTED_HOST"
                write_env_value "SMTP_PORT" "$SMTP_DETECTED_PORT"
                info "Auto-detected SMTP settings: ${SMTP_DETECTED_HOST}:${SMTP_DETECTED_PORT} ✓"
                SMTP_HOST_AUTO_FILLED=true
            fi
        fi

        # After SMTP_HOST (when entered manually): also prompt for SMTP_PORT.
        if [[ "$var" == "SMTP_HOST" ]]; then
            echo ""
            echo -e "  ${CYAN}SMTP port${NC}"
            echo "  587 = STARTTLS (most providers)  |  465 = SSL/TLS (implicit)"
            cur_port=$(read_env_value "SMTP_PORT"); cur_port="${cur_port:-587}"
            read -r -p "  SMTP_PORT [${cur_port}]: " port_val
            write_env_value "SMTP_PORT" "${port_val:-${cur_port}}"
            info "Saved SMTP_PORT ✓"
        fi

        echo ""
    done
fi

# ── OpenAI API key (optional) ──────────────────────────────────────────────
if [[ -z "$(read_env_value "OPENAI_API_KEY")" ]]; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  🤖  OpenAI API key (optional)${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  If set, digest emails include an AI-written summary with categories"
    echo "  and flagged sites. Leave blank to receive a plain visit list instead."
    echo "  → https://platform.openai.com/api-keys"
    echo ""
    read -r -p "  OPENAI_API_KEY (leave blank to skip): " openai_key
    if [[ -n "$openai_key" ]]; then
        write_env_value "OPENAI_API_KEY" "$openai_key"
        info "Saved OPENAI_API_KEY ✓"
    else
        info "Skipping OpenAI — plain digest emails will be sent."
    fi
    echo ""
fi

# ── Schedule wizard ────────────────────────────────────────────────────────
CURRENT_SCHEDULE=$(read_env_value "SUMMARY_SCHEDULE")
if [[ -z "$CURRENT_SCHEDULE" ]]; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  🕐  Digest schedule${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  How often would you like to receive your browsing digest?"
    echo ""
    echo "  1) daily    — once a day at a chosen time  (recommended)"
    echo "  2) hourly   — every hour"
    echo "  3) weekly   — once a week on a chosen day"
    echo "  4) monthly  — once a month on a chosen day"
    echo ""
    while true; do
        read -r -p "  Choice [1-4, default 1]: " sched_choice
        sched_choice="${sched_choice:-1}"
        case "$sched_choice" in 1|2|3|4) break ;; esac
        echo -e "  ${RED}Enter 1, 2, 3, or 4.${NC}"
    done

    case "$sched_choice" in
        1)
            write_env_value "SUMMARY_SCHEDULE" "daily"
            read -r -p "  Hour to send (0-23, default 21): " sched_hour
            write_env_value "SUMMARY_SCHEDULE_HOUR" "${sched_hour:-21}"
            info "Schedule: daily at ${sched_hour:-21}:00 ✓"
            ;;
        2)
            write_env_value "SUMMARY_SCHEDULE" "hourly"
            read -r -p "  Minute past the hour to send (0-59, default 0): " sched_min
            write_env_value "SUMMARY_SCHEDULE_MINUTE" "${sched_min:-0}"
            info "Schedule: hourly at :${sched_min:-00} ✓"
            ;;
        3)
            write_env_value "SUMMARY_SCHEDULE" "weekly"
            echo "  Day of week: mon tue wed thu fri sat sun"
            read -r -p "  Day (default mon): " sched_day
            write_env_value "SUMMARY_SCHEDULE_WEEKDAY" "${sched_day:-mon}"
            read -r -p "  Hour to send (0-23, default 9): " sched_hour
            write_env_value "SUMMARY_SCHEDULE_HOUR" "${sched_hour:-9}"
            info "Schedule: weekly on ${sched_day:-mon} at ${sched_hour:-9}:00 ✓"
            ;;
        4)
            write_env_value "SUMMARY_SCHEDULE" "monthly"
            read -r -p "  Day of month (1-28, default 1): " sched_dom
            write_env_value "SUMMARY_SCHEDULE_DAY" "${sched_dom:-1}"
            read -r -p "  Hour to send (0-23, default 9): " sched_hour
            write_env_value "SUMMARY_SCHEDULE_HOUR" "${sched_hour:-9}"
            info "Schedule: monthly on day ${sched_dom:-1} at ${sched_hour:-9}:00 ✓"
            ;;
    esac
    echo ""
fi

fi # end REINSTALL=false wizard block

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
if [[ "$REINSTALL" == false ]]; then
step "Validating credentials..."

if [[ -n "$OPENAI_API_KEY" ]]; then
    _start_spinner "Validating OpenAI API key..."
    OPENAI_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
        -H "Authorization: Bearer ${OPENAI_API_KEY}" \
        "https://api.openai.com/v1/models" 2>/dev/null || echo "000")
    _stop_spinner
    # 200 = valid; 403 = valid key but account/project restricted on this endpoint;
    # 429 = valid key, rate limited — all mean the key itself is accepted.
    if [[ "$OPENAI_HTTP" == "200" || "$OPENAI_HTTP" == "403" || "$OPENAI_HTTP" == "429" ]]; then
        info "OpenAI API key valid ✓"
    elif [[ "$OPENAI_HTTP" == "401" ]]; then
        error "OpenAI API key is invalid (HTTP 401). Update OPENAI_API_KEY in .env and re-run."
    else
        warn "Could not verify OpenAI API key (HTTP ${OPENAI_HTTP}) — check your internet connection."
    fi
else
    info "No OpenAI API key set — digests will be sent as a plain visit list (no AI summary)."
fi

# Test SMTP credentials by connecting and authenticating (no email sent)
if [[ "$SMTP_PASS_VERIFIED" == false ]]; then
_start_spinner "Validating SMTP credentials..."
SMTP_TEST=$("$PYTHON_PATH" - \
    "${SMTP_HOST}" "${SMTP_PORT:-587}" "${SMTP_USER}" "${SMTP_PASS}" <<'PYEOF' 2>&1 || true
import smtplib, ssl, sys
host, port, user, pw = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
try:
    if port == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as s:
            s.login(user, pw)
    else:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.ehlo(); s.starttls(); s.ehlo(); s.login(user, pw)
    print("ok")
except smtplib.SMTPAuthenticationError:
    print("auth_failed"); sys.exit(1)
except Exception as e:
    print(f"error: {e}"); sys.exit(2)
PYEOF
)
_stop_spinner
if [[ "$SMTP_TEST" == "ok" ]]; then
    info "SMTP credentials valid ✓"
elif [[ "$SMTP_TEST" == "auth_failed" ]]; then
    error "SMTP authentication failed. Check SMTP_USER and SMTP_PASS in .env."
else
    warn "Could not verify SMTP credentials: ${SMTP_TEST} — check your internet connection."
fi
fi # end SMTP_PASS_VERIFIED=false check

fi # end REINSTALL=false validation block

# ── Set up project-local virtual environment ──────────────────────────────
step "Setting up Python environment..."
VENV_DIR="$REPO_ROOT/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    _start_spinner "Creating virtual environment..."
    "$PYTHON_PATH" -m venv "$VENV_DIR"
    _stop_spinner
    info "Created virtual environment at .venv ✓"
else
    info "Virtual environment already exists ✓"
fi
# Use the venv Python for everything from here on (services, fill_plist, email)
PYTHON_PATH="$VENV_DIR/bin/python"
_start_spinner "Installing Python packages..."
"$PYTHON_PATH" -m pip install -q --upgrade pip
"$PYTHON_PATH" -m pip install -q -r "$REPO_ROOT/requirements.txt"
_stop_spinner
info "Dependencies installed ✓"

# ── Partner PIN (optional) ─────────────────────────────────────────────────
if ! "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" status &>/dev/null; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  🔒  Partner PIN (optional)${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  A partner PIN adds a barrier against impulsive uninstallation."
    echo "  Once set, the PIN must be entered to stop or remove Vigil."
    echo "  Your accountability partner should be the one to choose the PIN."
    echo ""
    read -r -p "  Set a partner PIN now? [y/N]: " SET_PIN
    if [[ "$SET_PIN" =~ ^[Yy]$ ]]; then
        if "$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" hash; then
            info "Partner PIN set ✓"
        else
            warn "PIN setup cancelled — skipping."
        fi
    else
        info "Skipping partner PIN — you can set one later by running: bash install.sh --update"
    fi
    echo ""
fi

# ── Helper: fill plist templates ───────────────────────────────────────────
# Only PYTHON_PATH, PROJECT_DIR, and LOG_DIR are embedded in the plist.
# All secrets (API keys, schedule) remain in .env and are loaded at runtime
# by config.py via python-dotenv — they never touch the LaunchAgents directory.
fill_plist() {
    local src="$1" dst="$2"
    _PLIST_PYTHON_PATH="$PYTHON_PATH" \
    _PLIST_PROJECT_DIR="$REPO_ROOT" \
    _PLIST_LOG_DIR="$HOME/Library/Logs/Vigil" \
    "$PYTHON_PATH" - "$src" "$dst" <<'PYEOF'
import os, sys
content = open(sys.argv[1]).read()
for placeholder, env_var in [
    ("__PYTHON_PATH__", "_PLIST_PYTHON_PATH"),
    ("__PROJECT_DIR__", "_PLIST_PROJECT_DIR"),
    ("__LOG_DIR__",     "_PLIST_LOG_DIR"),
]:
    content = content.replace(placeholder, os.environ[env_var])
open(sys.argv[2], "w").write(content)
PYEOF
}

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$HOME/Library/Application Support/Vigil"
mkdir -p "$HOME/Library/Logs/Vigil"

# ── Migrate any existing log/data files from the repo dir ─────────────────
for f in detailed_activity_log.txt detailed_activity_log.txt.sha256 last_summarized_date.txt; do
    if [[ -f "$REPO_ROOT/$f" ]]; then
        mv "$REPO_ROOT/$f" "$HOME/Library/Application Support/Vigil/$f"
        info "Migrated $f → ~/Library/Application Support/Vigil/"
    fi
done
for f in tracker_daemon.log summarizer_daemon.log; do
    if [[ -f "$REPO_ROOT/$f" ]]; then
        mv "$REPO_ROOT/$f" "$HOME/Library/Logs/Vigil/$f"
        info "Migrated $f → ~/Library/Logs/Vigil/"
    fi
done

# ── Install tracker service ────────────────────────────────────────────────
step "Installing launchd services..."
_start_spinner "Registering and starting launchd services..."

WEB_PLIST_SRC="$SCRIPT_DIR/com.vigil.tracker.plist"
WEB_PLIST_DST="$LAUNCH_AGENTS_DIR/com.vigil.tracker.plist"
launchctl_unload "$WEB_PLIST_DST"
fill_plist "$WEB_PLIST_SRC" "$WEB_PLIST_DST"
launchctl_load "$WEB_PLIST_DST"

# ── Install summarizer service ─────────────────────────────────────────────
SUMMARY_PLIST_SRC="$SCRIPT_DIR/com.vigil.summarizer.plist"
SUMMARY_PLIST_DST="$LAUNCH_AGENTS_DIR/com.vigil.summarizer.plist"
launchctl_unload "$SUMMARY_PLIST_DST"
fill_plist "$SUMMARY_PLIST_SRC" "$SUMMARY_PLIST_DST"
launchctl_load "$SUMMARY_PLIST_DST"

# ── Install watchdog service ───────────────────────────────────────────────
# Write graceful sentinel in case watchdog was already running (e.g. reinstall)
# so the SIGTERM handler doesn't fire a false alarm.
mkdir -p "$HOME/Library/Application Support/Vigil"
touch "$HOME/Library/Application Support/Vigil/watchdog_graceful_shutdown"
WATCHDOG_PLIST_SRC="$SCRIPT_DIR/com.vigil.watchdog.plist"
WATCHDOG_PLIST_DST="$LAUNCH_AGENTS_DIR/com.vigil.watchdog.plist"
launchctl_unload "$WATCHDOG_PLIST_DST"
fill_plist "$WATCHDOG_PLIST_SRC" "$WATCHDOG_PLIST_DST"
launchctl_load "$WATCHDOG_PLIST_DST"
_stop_spinner
info "Tracker service installed and started (com.vigil.tracker) ✓"
info "Summarizer service installed (com.vigil.summarizer) — schedule: ${SUMMARY_SCHEDULE} ✓"
info "Watchdog service installed and started (com.vigil.watchdog) ✓"

# ── Send confirmation email ────────────────────────────────────────────────
step "Sending confirmation email..."
_start_spinner "Sending..."
_confirm_result=0
"$PYTHON_PATH" "$REPO_ROOT/summarizer.py" --confirm || _confirm_result=$?
_stop_spinner
if (( _confirm_result == 0 )); then
    info "Confirmation email sent to ${SMTP_TO} ✓"
else
    echo ""
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}  ⚠️  Could not send confirmation email${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Vigil is running but email delivery is not working."
    echo "  You will not receive digests or alerts until this is fixed."
    echo ""
    echo "  Configured SMTP:"
    echo "    Host : $(read_env_value "SMTP_HOST"):$(read_env_value "SMTP_PORT")"
    echo "    User : $(read_env_value "SMTP_USER")"
    echo "    To   : $(read_env_value "SMTP_TO")"
    echo ""
    echo "  To fix:  bash $SCRIPT_DIR/install.sh --update"
    echo "  (re-enter your SMTP credentials)"
    echo ""
fi

# ── Snapshot .env integrity baseline ──────────────────────────────────────
# Stored in the OS keychain so the summariser can detect silent .env edits.
"$PYTHON_PATH" "$REPO_ROOT/pin_auth.py" env_store 2>/dev/null || true

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅  Installation complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Tracker logs    : ~/Library/Logs/Vigil/tracker_daemon.log"
echo "  Summarizer logs : ~/Library/Logs/Vigil/summarizer_daemon.log"
echo "  Check status    : vigil status      (or: bash $SCRIPT_DIR/install.sh --status)"
echo "  Update settings : vigil update      (or: bash $SCRIPT_DIR/install.sh --update)"
echo "  Manage blocklist: vigil blocklist   (or: bash $SCRIPT_DIR/install.sh --blocklist)"
echo "  Reinstall       : vigil reinstall   (or: bash $SCRIPT_DIR/install.sh --reinstall)"
echo "  Diagnose issues : vigil doctor"
echo "  To uninstall    : vigil uninstall   (or: bash $SCRIPT_DIR/uninstall.sh)"
echo ""
if ! command -v vigil &>/dev/null; then
  echo "  Tip: run 'pip install -e .' in the project directory to enable the"
  echo "  'vigil' command above."
  echo ""
fi

# Auto-run doctor when vigil is available, so users see their health status immediately.
if command -v vigil &>/dev/null; then
  echo "  Running 'vigil doctor' to verify your installation…"
  echo ""
  vigil doctor || true
fi
