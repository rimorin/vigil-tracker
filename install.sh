#!/usr/bin/env bash
# install.sh — installs Vigil as macOS launchd services.
#
# Usage:
#   bash install.sh              — guided install (wizard prompts for any missing .env values)
#   bash install.sh --status     — show service health and recent log output
#   bash install.sh --update     — interactively update configuration settings and reload services
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
        echo -e "  Enabled     : ${ADULT_ALERT_ENABLED:-true}"
        echo -e "  Cooldown    : ${ADULT_ALERT_COOLDOWN_MINUTES:-30} minutes"
        echo -e "  Email alert : ${ADULT_ALERT_EMAIL:-true}"
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
    for log in tracker_stderr.log summarizer_stderr.log; do
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

    # Inline helpers (use system python3; venv may not be set up yet)
    _read_env() {
        local key="$1" val
        val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')
        [[ "$val" == "sk-..." || "$val" == "your-app-password" || "$val" =~ example\.com || "$val" == "you@gmail.com" ]] && val=""
        echo "$val"
    }
    _write_env() {
        local key="$1" val="$2"
        if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
            python3 - "$key" "$val" "$ENV_FILE" <<'PYEOF'
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

    cur=$(_read_env "ADULT_ALERT_ENABLED"); cur="${cur:-${ADULT_ALERT_ENABLED:-true}}"
    read -r -p "  Enabled (true/false) [${cur}]: " val
    _write_env "ADULT_ALERT_ENABLED" "${val:-${cur}}"

    cur=$(_read_env "ADULT_ALERT_COOLDOWN_MINUTES"); cur="${cur:-${ADULT_ALERT_COOLDOWN_MINUTES:-30}}"
    read -r -p "  Cooldown minutes [${cur}]: " val
    _write_env "ADULT_ALERT_COOLDOWN_MINUTES" "${val:-${cur}}"

    cur=$(_read_env "ADULT_ALERT_EMAIL"); cur="${cur:-${ADULT_ALERT_EMAIL:-true}}"
    read -r -p "  Email alerts (true/false) [${cur}]: " val
    _write_env "ADULT_ALERT_EMAIL" "${val:-${cur}}"

    echo ""
    info "Settings saved to .env ✓"
    echo ""

    # ── Reload running services ────────────────────────────────────────────
    RELOAD_COUNT=0
    for plist_dst in \
        "$LAUNCH_AGENTS_DIR/com.vigil.tracker.plist" \
        "$LAUNCH_AGENTS_DIR/com.vigil.summarizer.plist"; do
        if [[ -f "$plist_dst" ]]; then
            launchctl_unload "$plist_dst"
            launchctl_load  "$plist_dst"
            (( RELOAD_COUNT++ )) || true
        fi
    done

    if (( RELOAD_COUNT > 0 )); then
        info "Services reloaded with new settings ✓"
    else
        warn "No installed services found — run bash install.sh to install."
    fi

    echo ""
    exit 0
fi

# ── --reinstall flag ───────────────────────────────────────────────────────
# Skips the setup wizard, credential checks, and permission prompt.
# Use after moving the project directory or to pick up code changes.
REINSTALL=false
if [[ "${1:-}" == "--reinstall" ]]; then
    REINSTALL=true
    info "Reinstall mode — skipping wizard and credential checks."
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
ADULT_ALERT_ENABLED=true
ADULT_ALERT_COOLDOWN_MINUTES=30
ADULT_ALERT_EMAIL=true
EOF
fi

# ── Setup wizard — create / complete .env ─────────────────────────────────
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

REQUIRED_VARS=(SMTP_HOST SMTP_USER SMTP_PASS SMTP_TO)
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
                echo -e "  ${CYAN}OpenAI API key${NC} ${YELLOW}(optional)${NC}"
                echo "  Leave blank to receive a plain visit list instead of an AI summary."
                echo "  → https://platform.openai.com/api-keys"
                ;;
            SMTP_HOST)
                echo -e "  ${CYAN}SMTP server hostname${NC}"
                echo "  Gmail: smtp.gmail.com  |  Outlook: smtp.office365.com  |  Fastmail: smtp.fastmail.com"
                ;;
            SMTP_USER)
                echo -e "  ${CYAN}SMTP username${NC} (usually your full email address)"
                ;;
            SMTP_PASS)
                echo -e "  ${CYAN}SMTP password / app password${NC}"
                echo "  Gmail users: create an App Password at https://myaccount.google.com/apppasswords"
                ;;
            SMTP_TO)
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
else
    info "No OpenAI API key set — digests will be sent as a plain visit list (no AI summary)."
fi

# Test SMTP credentials by connecting and authenticating (no email sent)
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
if [[ "$SMTP_TEST" == "ok" ]]; then
    info "SMTP credentials valid ✓"
elif [[ "$SMTP_TEST" == "auth_failed" ]]; then
    error "SMTP authentication failed. Check SMTP_USER and SMTP_PASS in .env."
else
    warn "Could not verify SMTP credentials: ${SMTP_TEST} — check your internet connection."
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

fi # end REINSTALL=false validation block

# ── Set up project-local virtual environment ──────────────────────────────
step "Setting up Python environment..."
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_PATH" -m venv "$VENV_DIR"
    info "Created virtual environment at .venv ✓"
else
    info "Virtual environment already exists ✓"
fi
# Use the venv Python for everything from here on (services, fill_plist, email)
PYTHON_PATH="$VENV_DIR/bin/python3"
"$PYTHON_PATH" -m pip install -q --upgrade pip
"$PYTHON_PATH" -m pip install -q -r "$SCRIPT_DIR/requirements.txt"
info "Dependencies installed ✓"

# ── Helper: fill plist templates ───────────────────────────────────────────
# Only PYTHON_PATH, PROJECT_DIR, and LOG_DIR are embedded in the plist.
# All secrets (API keys, schedule) remain in .env and are loaded at runtime
# by config.py via python-dotenv — they never touch the LaunchAgents directory.
fill_plist() {
    local src="$1" dst="$2"
    _PLIST_PYTHON_PATH="$PYTHON_PATH" \
    _PLIST_PROJECT_DIR="$SCRIPT_DIR" \
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
    if [[ -f "$SCRIPT_DIR/$f" ]]; then
        mv "$SCRIPT_DIR/$f" "$HOME/Library/Application Support/Vigil/$f"
        info "Migrated $f → ~/Library/Application Support/Vigil/"
    fi
done
for f in tracker_daemon.log summarizer_daemon.log; do
    if [[ -f "$SCRIPT_DIR/$f" ]]; then
        mv "$SCRIPT_DIR/$f" "$HOME/Library/Logs/Vigil/$f"
        info "Migrated $f → ~/Library/Logs/Vigil/"
    fi
done

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
    info "Confirmation email sent to ${SMTP_TO} ✓"
else
    warn "Confirmation email failed — check your SMTP credentials."
    warn "The services are still running; this does not affect normal operation."
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅  Installation complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Tracker logs    : ~/Library/Logs/Vigil/tracker_daemon.log"
echo "  Summarizer logs : ~/Library/Logs/Vigil/summarizer_daemon.log"
echo "  Check status    : bash $SCRIPT_DIR/install.sh --status"
echo "  Reinstall       : bash $SCRIPT_DIR/install.sh --reinstall"
echo "  To uninstall    : bash $SCRIPT_DIR/uninstall.sh"
echo ""
