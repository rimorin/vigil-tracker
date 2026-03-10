#Requires -Version 5.1
<#
.SYNOPSIS
    Vigil Windows installer.

.DESCRIPTION
    Installs Vigil Tracker and Vigil Summarizer as Windows Task Scheduler
    services that start automatically at logon.

    Run without parameters for a guided first-time setup.

.PARAMETER Status
    Show current service health, .env settings, and recent log output.

.PARAMETER Update
    Interactively update configuration settings and restart services.

.PARAMETER Reinstall
    Re-register tasks without re-prompting for .env values (useful after
    moving the project folder or upgrading Python).

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Status
    .\install.ps1 -Update
    .\install.ps1 -Reinstall
#>

[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$Update,
    [switch]$Reinstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Guard: detect if we were somehow launched under a policy that may block child scripts.
$policy = $null
try {
    $policy = Get-ExecutionPolicy -Scope CurrentUser -ErrorAction Stop
} catch {
    $policy = 'Unknown'
}
if ($policy -eq 'Restricted' -or $policy -eq 'AllSigned') {
    Write-Host ""
    Write-Host "  [ERROR] PowerShell execution policy '$policy' blocks this script." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Easiest fix - run via the provided batch wrapper instead:" -ForegroundColor Yellow
    Write-Host "      install.bat" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Or allow user-level scripts once and re-run:" -ForegroundColor Yellow
    Write-Host "      Set-ExecutionPolicy -Scope CurrentUser RemoteSigned" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$EnvFile     = Join-Path $RepoRoot ".env"
$EnvTemplate = Join-Path $RepoRoot ".env.template"
$LogDir      = Join-Path $env:LOCALAPPDATA "Vigil\Logs"

$Tasks = @(
    @{ Name = "Vigil Tracker";    Script = "tracker.py";    DaemonLog = "tracker_daemon.log";    StderrLog = "tracker_stderr.log"    }
    @{ Name = "Vigil Summarizer"; Script = "summarizer.py"; DaemonLog = "summarizer_daemon.log"; StderrLog = "summarizer_stderr.log" }
)

function Write-Step  { param($msg) Write-Host "`n  $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Fail        { param($msg) Write-Host "`n  [ERROR] $msg`n" -ForegroundColor Red; exit 1 }
function Coalesce    { param($value, $fallback) if ([string]::IsNullOrEmpty($value)) { $fallback } else { $value } }

# Read a single key from .env; returns empty string if absent or a placeholder
function Read-EnvValue ($key) {
    if (-not (Test-Path $EnvFile)) { return "" }
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match "^${key}=(.*)$") {
            $v = $Matches[1].Trim()
            if ($v -in @("sk-...", "your-app-password") -or $v -match "example\.com" -or $v -eq "you@gmail.com") { return "" }
            return $v
        }
    }
    return ""
}

# Write or update a key=value pair in .env
function Write-EnvValue ($key, $value) {
    if (Test-Path $EnvFile) {
        $content = Get-Content $EnvFile -Raw
        if ($content -match "(?m)^${key}=") {
            $content = $content -replace "(?m)^${key}=.*$", "${key}=${value}"
            Set-Content $EnvFile $content -Encoding UTF8 -NoNewline
        } else {
            Add-Content $EnvFile "${key}=${value}" -Encoding UTF8
        }
    } else {
        Add-Content $EnvFile "${key}=${value}" -Encoding UTF8
    }
}

# Prompt for a value; shows current in brackets; masks input if -Secret
function Prompt-Val ($label, $current, [switch]$Secret) {
    $hint = if ($current) { " [$current]" } else { "" }
    if ($Secret) {
        $val = Read-Host "  ${label}"
        if ($val) { return $val } else { return $current }
    }
    $val = Read-Host "  ${label}${hint}"
    if ($val) { return $val } else { return $current }
}

# ============================================================================
# -Status
# ============================================================================
if ($Status) {
    Write-Host ""
    Write-Host "  ====  Vigil - Status  ====" -ForegroundColor Green
    Write-Host ""

    foreach ($t in $Tasks) {
        $task = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            Write-Host ("    {0,-24}: not registered" -f $t.Name) -ForegroundColor Yellow
        } else {
            $state = $task.State
            $color = if ($state -eq "Running") { "Green" } else { "Yellow" }
            Write-Host ("    {0,-24}: {1}" -f $t.Name, $state) -ForegroundColor $color
        }
    }
    Write-Host ""

    if (Test-Path $EnvFile) {
        $ev = @{}
        foreach ($line in Get-Content $EnvFile) {
            if ($line -match '^([^#=\s][^=]*)=(.*)$') { $ev[$Matches[1].Trim()] = $Matches[2].Trim() }
        }

        Write-Host "  Email / SMTP" -ForegroundColor Cyan
        $smtpHost = Coalesce $ev["SMTP_HOST"] "<not set>"
        $smtpPort = Coalesce $ev["SMTP_PORT"] "<not set>"
        $smtpUser = Coalesce $ev["SMTP_USER"] "<not set>"
        $smtpFrom = Coalesce $ev["SMTP_FROM"] (Coalesce $ev["SMTP_USER"] "<not set>")
        $smtpTo   = Coalesce $ev["SMTP_TO"]   "<not set>"
        Write-Host "    SMTP Host   : ${smtpHost}:${smtpPort}"
        Write-Host "    SMTP User   : ${smtpUser}"
        Write-Host "    From        : ${smtpFrom}"
        Write-Host "    Recipient   : ${smtpTo}"
        Write-Host ""

        Write-Host "  AI" -ForegroundColor Cyan
        $model  = Coalesce $ev["OPENAI_MODEL"]   "<not set>"
        $apiKey = if ($ev["OPENAI_API_KEY"]) { "set (hidden)" } else { "<not set>" }
        Write-Host "    Model       : ${model}"
        Write-Host "    API Key     : ${apiKey}"
        Write-Host ""

        Write-Host "  Summary Schedule" -ForegroundColor Cyan
        $sched = Coalesce $ev["SUMMARY_SCHEDULE"] "daily"
        Write-Host "    Schedule    : ${sched}"
        switch ($sched) {
            "daily"   { $h = Coalesce $ev["SUMMARY_SCHEDULE_HOUR"] "21"; $m = Coalesce $ev["SUMMARY_SCHEDULE_MINUTE"] "0"
                        Write-Host ("    Send time   : {0:D2}:{1:D2}" -f [int]$h, [int]$m) }
            "weekly"  { $wd = Coalesce $ev["SUMMARY_SCHEDULE_WEEKDAY"] "mon"; $h = Coalesce $ev["SUMMARY_SCHEDULE_HOUR"] "9"; $m = Coalesce $ev["SUMMARY_SCHEDULE_MINUTE"] "0"
                        Write-Host ("    Send time   : {0} at {1:D2}:{2:D2}" -f $wd, [int]$h, [int]$m) }
            "monthly" { $d = Coalesce $ev["SUMMARY_SCHEDULE_DAY"] "1"; $h = Coalesce $ev["SUMMARY_SCHEDULE_HOUR"] "9"; $m = Coalesce $ev["SUMMARY_SCHEDULE_MINUTE"] "0"
                        Write-Host ("    Send time   : day {0} at {1:D2}:{2:D2}" -f $d, [int]$h, [int]$m) }
            "hourly"  { Write-Host "    Send time   : every hour" }
            "interval"{ $mins = Coalesce $ev["SUMMARY_SCHEDULE_INTERVAL_MINUTES"] "60"
                        Write-Host "    Interval    : every ${mins} minute(s)" }
        }
        Write-Host ""

        Write-Host "  Adult Content Alerts" -ForegroundColor Cyan
        Write-Host "    Enabled     : $(Coalesce $ev['ALERT_ENABLED'] 'true')"
        Write-Host "    Cooldown    : $(Coalesce $ev['ALERT_COOLDOWN_MINUTES'] '30') minutes"
        Write-Host "    Email alert : $(Coalesce $ev['ALERT_EMAIL'] 'true')"
        Write-Host ""
    } else {
        Write-Host "  No .env file found - settings unavailable." -ForegroundColor Yellow
        Write-Host ""
    }

    foreach ($t in $Tasks) {
        $daemonLog = Join-Path $LogDir $t.DaemonLog
        if (Test-Path $daemonLog) {
            Write-Host "  -- $($t.DaemonLog) (last 5 lines) --" -ForegroundColor Cyan
            Get-Content $daemonLog -Tail 5 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            Write-Host ""
        }
        $stderrLog = Join-Path $LogDir $t.StderrLog
        if ((Test-Path $stderrLog) -and (Get-Item $stderrLog).Length -gt 0) {
            Write-Host "  -- $($t.StderrLog) (last 5 lines) --" -ForegroundColor Yellow
            Get-Content $stderrLog -Tail 5 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            Write-Host ""
        }
    }

    exit 0
}

# ============================================================================
# -Update  (guided in-place settings editor - mirrors install.sh --update)
# ============================================================================
if ($Update) {
    Write-Host ""
    Write-Host "  ====  Vigil - Update Settings  ====" -ForegroundColor Green
    Write-Host ""

    if (-not (Test-Path $EnvFile)) {
        Write-Host "  No .env file found at $EnvFile - run .\install.ps1 to install first." -ForegroundColor Yellow
        exit 1
    }

    Write-Host "  Press Enter to keep the value shown in [brackets]."
    Write-Host ""

    # -- Email / SMTP --
    Write-Host "  Email / SMTP" -ForegroundColor Cyan
    Write-EnvValue "SMTP_HOST" (Prompt-Val "SMTP Host" (Coalesce (Read-EnvValue "SMTP_HOST") "smtp.gmail.com"))
    Write-EnvValue "SMTP_PORT" (Prompt-Val "SMTP Port" (Coalesce (Read-EnvValue "SMTP_PORT") "587"))
    Write-EnvValue "SMTP_USER" (Prompt-Val "SMTP User" (Read-EnvValue "SMTP_USER"))
    $newPass = Prompt-Val "SMTP Password (leave blank to keep current)" "" -Secret
    if ($newPass) { Write-EnvValue "SMTP_PASS" $newPass }
    Write-EnvValue "SMTP_TO"   (Prompt-Val "Recipient email(s)" (Read-EnvValue "SMTP_TO"))
    Write-Host ""

    # -- AI --
    Write-Host "  AI" -ForegroundColor Cyan
    Write-EnvValue "OPENAI_MODEL" (Prompt-Val "OpenAI Model" (Coalesce (Read-EnvValue "OPENAI_MODEL") "gpt-4o-mini"))
    $newKey = Prompt-Val "OpenAI API Key (optional - leave blank to keep current)" "" -Secret
    if ($newKey) { Write-EnvValue "OPENAI_API_KEY" $newKey }
    Write-Host ""

    # -- Summary Schedule --
    Write-Host "  Summary Schedule" -ForegroundColor Cyan
    $curSched = Coalesce (Read-EnvValue "SUMMARY_SCHEDULE") "daily"
    Write-Host "    Current schedule: $curSched"
    Write-Host "    1) daily    2) hourly    3) weekly    4) monthly    5) interval    6) keep current ($curSched)"
    Write-Host ""
    do {
        $sc = Read-Host "    Choice [1-6, default 6]"
        if (-not $sc) { $sc = "6" }
    } while ($sc -notin @("1","2","3","4","5","6"))

    switch ($sc) {
        "1" {
            Write-EnvValue "SUMMARY_SCHEDULE" "daily"
            Write-EnvValue "SUMMARY_SCHEDULE_HOUR"   (Prompt-Val "Hour to send (0-23)" (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_HOUR") "21"))
            Write-EnvValue "SUMMARY_SCHEDULE_MINUTE" (Prompt-Val "Minute (0-59)"       (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_MINUTE") "0"))
            Write-OK "Schedule: daily"
        }
        "2" {
            Write-EnvValue "SUMMARY_SCHEDULE" "hourly"
            Write-EnvValue "SUMMARY_SCHEDULE_MINUTE" (Prompt-Val "Minute past the hour (0-59)" (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_MINUTE") "0"))
            Write-OK "Schedule: hourly"
        }
        "3" {
            Write-EnvValue "SUMMARY_SCHEDULE" "weekly"
            Write-EnvValue "SUMMARY_SCHEDULE_WEEKDAY" (Prompt-Val "Day of week (mon-sun)" (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_WEEKDAY") "mon"))
            Write-EnvValue "SUMMARY_SCHEDULE_HOUR"    (Prompt-Val "Hour to send (0-23)"   (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_HOUR") "9"))
            Write-OK "Schedule: weekly"
        }
        "4" {
            Write-EnvValue "SUMMARY_SCHEDULE" "monthly"
            Write-EnvValue "SUMMARY_SCHEDULE_DAY"  (Prompt-Val "Day of month (1-28)"  (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_DAY") "1"))
            Write-EnvValue "SUMMARY_SCHEDULE_HOUR" (Prompt-Val "Hour to send (0-23)"  (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_HOUR") "9"))
            Write-OK "Schedule: monthly"
        }
        "5" {
            Write-EnvValue "SUMMARY_SCHEDULE" "interval"
            Write-EnvValue "SUMMARY_SCHEDULE_INTERVAL_MINUTES" (Prompt-Val "Interval in minutes (e.g. 5, 30, 60)" (Coalesce (Read-EnvValue "SUMMARY_SCHEDULE_INTERVAL_MINUTES") "60"))
            Write-OK "Schedule: interval"
        }
        "6" { Write-OK "Schedule unchanged." }
    }
    Write-Host ""

    # -- Adult Content Alerts --
    Write-Host "  Adult Content Alerts" -ForegroundColor Cyan
    Write-EnvValue "ALERT_ENABLED"          (Prompt-Val "Enabled (true/false)"       (Coalesce (Read-EnvValue "ALERT_ENABLED") "true"))
    Write-EnvValue "ALERT_COOLDOWN_MINUTES" (Prompt-Val "Cooldown minutes"            (Coalesce (Read-EnvValue "ALERT_COOLDOWN_MINUTES") "30"))
    Write-EnvValue "ALERT_EMAIL"            (Prompt-Val "Email alerts (true/false)"   (Coalesce (Read-EnvValue "ALERT_EMAIL") "true"))
    Write-Host ""

    # -- Partner PIN --
    Write-Host "  Partner PIN" -ForegroundColor Cyan
    & $pythonExe "$RepoRoot\pin_auth.py" "status" 2>$null
    $pinIsSet = ($LASTEXITCODE -eq 0)
    if ($pinIsSet) {
        Write-Host "  A partner PIN is currently set."
        $changePin = Read-Host "  Change partner PIN? [y/N]"
        if ($changePin -match '^[Yy]') {
            & $pythonExe "$RepoRoot\pin_auth.py" "hash"
            if ($LASTEXITCODE -eq 0) { Write-OK "Partner PIN updated" }
            else { Write-Warn "PIN update cancelled - keeping existing PIN." }
        }
        $removePin = Read-Host "  Remove partner PIN entirely? [y/N]"
        if ($removePin -match '^[Yy]') {
            & $pythonExe "$RepoRoot\pin_auth.py" "delete"
            Write-OK "Partner PIN removed."
        }
    } else {
        Write-Host "  No partner PIN is currently set."
        $setPin = Read-Host "  Set a partner PIN? [y/N]"
        if ($setPin -match '^[Yy]') {
            & $pythonExe "$RepoRoot\pin_auth.py" "hash"
            if ($LASTEXITCODE -eq 0) { Write-OK "Partner PIN set" }
            else { Write-Warn "PIN setup cancelled - skipping." }
        }
    }
    Write-Host ""

    Write-OK "Settings saved to $EnvFile"
    Write-Host ""

    # Restart running services so they pick up the new .env
    $reloadCount = 0
    foreach ($t in $Tasks) {
        $task = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            Stop-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
            # Wait for the task to fully stop before restarting.
            # Stop-ScheduledTask is asynchronous; if the task still shows as Running
            # when Start-ScheduledTask is called, MultipleInstancesPolicy=IgnoreNew
            # silently drops the new start and the old process keeps running.
            $elapsed = 0
            while ($elapsed -lt 10) {
                $state = (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue).State
                if ($state -eq 'Ready') { break }
                Start-Sleep -Milliseconds 500
                $elapsed += 0.5
            }
            Start-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
            $reloadCount++
        }
    }
    if ($reloadCount -gt 0) {
        Write-OK "Services restarted with new settings"
    } else {
        Write-Warn "No installed services found - run .\install.ps1 to install."
    }
    Write-Host ""
    exit 0
}

# ============================================================================
# Main install flow
# ============================================================================

# -- Windows 10 build 17763+ check --------------------------------------------
Write-Host ""
Write-Host "  ====  Vigil - Installer  ====" -ForegroundColor Green

Write-Step "Checking system requirements"
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
if ($build -lt 17763) {
    Fail "Vigil requires Windows 10 (build 17763) or newer.  Your build: $build"
}
Write-OK "Windows build $build"

# -- Find Python 3.8+ ---------------------------------------------------------
Write-Step "Locating Python 3.8+"
$pythonExe = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $out = & $cmd --version 2>&1 | Select-Object -First 1
        if ($out -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 8)) {
                $pythonExe = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
                Write-OK "Python $maj.$min  ->  $pythonExe"
                break
            }
        }
    } catch { }
}
if (-not $pythonExe) {
    Fail "Python 3.8+ is required.  Install from https://python.org (tick 'Add to PATH')."
}

# Prefer pythonw.exe (no console window) in the same directory
$pythonwExe = $pythonExe -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path $pythonwExe)) { $pythonwExe = $pythonExe }

# -- pip upgrade + install ------------------------------------------------------
Write-Step "Installing Python packages"
# Upgrade pip quietly. Temporarily suspend Stop-on-error: with
# $ErrorActionPreference='Stop', any pip stderr (including harmless warnings
# about invalid distributions or the scripts PATH) becomes a terminating error.
$_prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $pythonExe -m pip install --upgrade pip --quiet 2>&1 | Out-Null
$ErrorActionPreference = $_prev
& $pythonExe -m pip install -r "$RepoRoot\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed - see output above." }
Write-OK "Packages installed"

# -- Ensure .env.template exists -----------------------------------------------
if (-not (Test-Path $EnvTemplate)) {
    @'
# Vigil - environment variables
# Run .\install.ps1 and the wizard will fill this in for you,
# or copy to .env and edit manually.

OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# SMTP email settings - works with Gmail, Outlook, Fastmail, or any SMTP provider
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
# Used only when SUMMARY_SCHEDULE=interval
SUMMARY_SCHEDULE_INTERVAL_MINUTES=60

# -- Adult content alerts -----------------------------------------------------
ALERT_ENABLED=true
ALERT_COOLDOWN_MINUTES=30
ALERT_EMAIL=true
'@ | Set-Content $EnvTemplate -Encoding UTF8
}

# -- Setup wizard (skipped on -Reinstall) --------------------------------------
if (-not $Reinstall) {
    Write-Step "Checking configuration"

    # Create .env from template if missing
    if (-not (Test-Path $EnvFile)) { Copy-Item $EnvTemplate $EnvFile }

    # Check which required values are missing / still placeholders
    $requiredVars = @("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_TO")
    $missingVars  = $requiredVars | Where-Object { -not (Read-EnvValue $_) }

    if ($missingVars) {
        Write-Host ""
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host "    Setup wizard - enter your API keys" -ForegroundColor Yellow
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  The following values are missing from .env."
        Write-Host "  Enter them now, or press Ctrl-C to edit .env manually."
        Write-Host ""

        foreach ($var in $missingVars) {
            switch ($var) {
                "SMTP_HOST" {
                    Write-Host "  SMTP server hostname" -ForegroundColor Cyan
                    Write-Host "  Gmail: smtp.gmail.com  |  Outlook: smtp.office365.com  |  Fastmail: smtp.fastmail.com"
                }
                "SMTP_USER" {
                    Write-Host "  SMTP username (usually your full email address)" -ForegroundColor Cyan
                }
                "SMTP_PASS" {
                    Write-Host "  SMTP password / app password" -ForegroundColor Cyan
                    Write-Host "  Gmail users: create an App Password at https://myaccount.google.com/apppasswords"
                }
                "SMTP_TO" {
                    Write-Host "  Recipient email address(es)" -ForegroundColor Cyan
                    Write-Host "  Comma-separate multiple: you@example.com,partner@example.com"
                }
            }
            do {
                if ($var -eq "SMTP_PASS") {
                    $entered = Read-Host "  ${var}"
                } else {
                    $entered = Read-Host "  ${var}"
                }
            } while (-not $entered)
            Write-EnvValue $var $entered
            Write-OK "Saved ${var}"
            # Immediately prompt for SMTP_PORT after SMTP_HOST (has a sensible default)
            if ($var -eq "SMTP_HOST") {
                Write-Host ""
                Write-Host "  SMTP port" -ForegroundColor Cyan
                Write-Host "  587 = STARTTLS (most providers)  |  465 = SSL/TLS (implicit)"
                $curPort = (Read-EnvValue "SMTP_PORT")
                if (-not $curPort) { $curPort = "587" }
                $portVal = Read-Host "  SMTP_PORT [$curPort]"
                if (-not $portVal) { $portVal = $curPort }
                Write-EnvValue "SMTP_PORT" $portVal
                Write-OK "Saved SMTP_PORT"
            }
            Write-Host ""
        }
    }

    # -- OpenAI API key (optional) -----------------------------------------
    if (-not (Read-EnvValue "OPENAI_API_KEY")) {
        Write-Host ""
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host "    OpenAI API key (optional)" -ForegroundColor Yellow
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  If set, digest emails include an AI-written summary with categories"
        Write-Host "  and flagged sites. Leave blank to receive a plain visit list instead."
        Write-Host "  -> https://platform.openai.com/api-keys"
        Write-Host ""
        $openaiKey = Read-Host "  OPENAI_API_KEY (leave blank to skip)"
        if ($openaiKey) {
            Write-EnvValue "OPENAI_API_KEY" $openaiKey
            Write-OK "Saved OPENAI_API_KEY"
        } else {
            Write-OK "Skipping OpenAI - plain digest emails will be sent."
        }
        Write-Host ""
    }

    # -- Schedule wizard (only shown if SUMMARY_SCHEDULE not already set) --
    if (-not (Read-EnvValue "SUMMARY_SCHEDULE")) {
        Write-Host ""
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host "    Digest schedule" -ForegroundColor Yellow
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  How often would you like to receive your browsing digest?"
        Write-Host ""
        Write-Host "    1) daily   - once a day at a chosen time  (recommended)"
        Write-Host "    2) hourly  - every hour"
        Write-Host "    3) weekly  - once a week on a chosen day"
        Write-Host "    4) monthly - once a month on a chosen day"
        Write-Host "    5) interval - every N minutes"
        Write-Host ""
        do {
            $sc = Read-Host "    Choice [1-5, default 1]"
            if (-not $sc) { $sc = "1" }
        } while ($sc -notin @("1","2","3","4","5"))

        switch ($sc) {
            "1" {
                Write-EnvValue "SUMMARY_SCHEDULE" "daily"
                $h = Read-Host "    Hour to send (0-23, default 21)"; if (-not $h) { $h = "21" }
                Write-EnvValue "SUMMARY_SCHEDULE_HOUR" $h
                Write-OK "Schedule: daily at ${h}:00"
            }
            "2" {
                Write-EnvValue "SUMMARY_SCHEDULE" "hourly"
                $m = Read-Host "    Minute past the hour (0-59, default 0)"; if (-not $m) { $m = "0" }
                Write-EnvValue "SUMMARY_SCHEDULE_MINUTE" $m
                Write-OK "Schedule: hourly at :$m"
            }
            "3" {
                Write-EnvValue "SUMMARY_SCHEDULE" "weekly"
                $wd = Read-Host "    Day of week (mon-sun, default mon)"; if (-not $wd) { $wd = "mon" }
                Write-EnvValue "SUMMARY_SCHEDULE_WEEKDAY" $wd
                $h = Read-Host "    Hour to send (0-23, default 9)"; if (-not $h) { $h = "9" }
                Write-EnvValue "SUMMARY_SCHEDULE_HOUR" $h
                Write-OK "Schedule: weekly on ${wd} at ${h}:00"
            }
            "4" {
                Write-EnvValue "SUMMARY_SCHEDULE" "monthly"
                $d = Read-Host "    Day of month (1-28, default 1)"; if (-not $d) { $d = "1" }
                Write-EnvValue "SUMMARY_SCHEDULE_DAY" $d
                $h = Read-Host "    Hour to send (0-23, default 9)"; if (-not $h) { $h = "9" }
                Write-EnvValue "SUMMARY_SCHEDULE_HOUR" $h
                Write-OK "Schedule: monthly on day ${d} at ${h}:00"
            }
            "5" {
                Write-EnvValue "SUMMARY_SCHEDULE" "interval"
                $mins = Read-Host "    Interval in minutes (e.g. 5, 30, 60, default 60)"; if (-not $mins) { $mins = "60" }
                Write-EnvValue "SUMMARY_SCHEDULE_INTERVAL_MINUTES" $mins
                Write-OK "Schedule: every ${mins} minute(s)"
            }
        }
        Write-Host ""
    }

    # -- Validate OpenAI API key -----------------------------------------------
    $openAiKey = Read-EnvValue "OPENAI_API_KEY"
    if ($openAiKey) {
        Write-Step "Validating OpenAI API key"
        try {
            $response = Invoke-WebRequest -Uri "https://api.openai.com/v1/models" `
                -Headers @{ Authorization = "Bearer $openAiKey" } `
                -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            Write-OK "OpenAI API key valid"
        } catch {
            $statusCode = $_.Exception.Response.StatusCode.value__
            if ($statusCode -eq 401) {
                Fail "OpenAI API key is invalid (HTTP 401). Update OPENAI_API_KEY in .env and re-run."
            } elseif ($statusCode -in @(403, 429)) {
                Write-OK "OpenAI API key valid"
            } else {
                Write-Warn "Could not verify OpenAI API key (HTTP ${statusCode}) - check your internet connection."
            }
        }
    } else {
        Write-Warn "No OpenAI API key set - digests will be sent as a plain visit list (no AI summary)."
    }

    # -- Validate SMTP ---------------------------------------------------------
    Write-Step "Validating SMTP connection"
    $smtpResult = & $pythonExe -c @"
import smtplib, ssl, sys
try:
    sys.path.insert(0, r'$RepoRoot')
    from dotenv import load_dotenv
    load_dotenv(r'$EnvFile')
    import config as c
    ctx = ssl.create_default_context()
    if c.SMTP_PORT == 465:
        s = smtplib.SMTP_SSL(c.SMTP_HOST, c.SMTP_PORT, context=ctx, timeout=15)
    else:
        s = smtplib.SMTP(c.SMTP_HOST, c.SMTP_PORT, timeout=15)
        s.ehlo(); s.starttls(context=ctx); s.ehlo()
    s.login(c.SMTP_USER, c.SMTP_PASS)
    s.quit()
    print('OK')
except smtplib.SMTPAuthenticationError:
    print('AUTH_FAILED')
except Exception as e:
    print('FAIL: ' + str(e))
"@ 2>&1

    if ($smtpResult -match "^OK") {
        Write-OK "SMTP connection verified"
    } elseif ($smtpResult -match "AUTH_FAILED") {
        Write-Warn "SMTP authentication failed - check SMTP_USER and SMTP_PASS in .env."
        $cont = Read-Host "`n  Continue anyway? [y/N]"
        if ($cont -notmatch '^[Yy]') { exit 1 }
    } else {
        Write-Warn "Could not verify SMTP: $smtpResult - check your internet connection."
        $cont = Read-Host "`n  Continue anyway? [y/N]"
        if ($cont -notmatch '^[Yy]') { exit 1 }
    }

    # -- Partner PIN (optional) ------------------------------------------------
    & $pythonExe "$RepoRoot\pin_auth.py" "status" 2>$null
    $existingPinSet = ($LASTEXITCODE -eq 0)
    if (-not $existingPinSet) {
        Write-Host ""
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host "    Partner PIN (optional)" -ForegroundColor Yellow
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  A partner PIN adds a barrier against impulsive uninstallation."
        Write-Host "  Once set, the PIN must be entered to stop or remove Vigil."
        Write-Host "  Your accountability partner should be the one to choose the PIN."
        Write-Host ""
        $setPin = Read-Host "  Set a partner PIN now? [y/N]"
        if ($setPin -match '^[Yy]') {
            & $pythonExe "$RepoRoot\pin_auth.py" "hash"
            if ($LASTEXITCODE -eq 0) {
                Write-OK "Partner PIN set"
            } else {
                Write-Warn "PIN setup cancelled - skipping."
            }
        } else {
            Write-OK "Skipping partner PIN - set one later via: .\install.ps1 -Update"
        }
        Write-Host ""
    }
}

# -- Create log/data directories -----------------------------------------------
[System.IO.Directory]::CreateDirectory($LogDir) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $env:APPDATA "Vigil")) | Out-Null

# -- Register Task Scheduler tasks ---------------------------------------------
Write-Step "Registering Task Scheduler tasks"
$user = "$env:USERDOMAIN\$env:USERNAME"

foreach ($t in $Tasks) {
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

    $action   = New-ScheduledTaskAction `
        -Execute          $pythonwExe `
        -Argument         "`"$(Join-Path $RepoRoot $t.Script)`"" `
        -WorkingDirectory $RepoRoot

    $trigger  = New-ScheduledTaskTrigger -AtLogOn -User $user

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit      ([System.TimeSpan]::Zero) `
        -RestartCount            999 `
        -RestartInterval         (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable `
        -MultipleInstances       IgnoreNew `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries

    Register-ScheduledTask `
        -TaskName  $t.Name `
        -Action    $action `
        -Trigger   $trigger `
        -Settings  $settings `
        -RunLevel  Limited `
        -Force | Out-Null

    Write-OK "Registered: $($t.Name)"
}

# -- Start tasks ---------------------------------------------------------------
Write-Step "Starting services"
foreach ($t in $Tasks) {
    Start-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds 2

# -- Send confirmation email ---------------------------------------------------
Write-Step "Sending confirmation email"
$confirmResult = & $pythonExe "$RepoRoot\summarizer.py" "--confirm" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-OK "Confirmation email sent"
} else {
    Write-Warn "Confirmation email failed - check your SMTP credentials."
    Write-Warn "Services are still running; this does not affect normal operation."
}

# -- Summary ------------------------------------------------------------------
Write-Host ""
Write-Host "  ====  Vigil installed successfully  ====" -ForegroundColor Green
Write-Host ""
foreach ($t in $Tasks) {
    $task  = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    $state = if ($null -eq $task) { "unknown" } else { $task.State }
    $color = if ($state -eq "Running") { "Green" } else { "Yellow" }
    Write-Host ("    {0,-24}: {1}" -f $t.Name, $state) -ForegroundColor $color
}
Write-Host ""
Write-Host "  Tracker logs    : $LogDir\tracker_daemon.log" -ForegroundColor DarkGray
Write-Host "  Summarizer logs : $LogDir\summarizer_daemon.log" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Tips:" -ForegroundColor Cyan
Write-Host "    .\install.ps1 -Status     - check service health and settings"
Write-Host "    .\install.ps1 -Update     - change settings and restart services"
Write-Host "    .\install.ps1 -Reinstall  - re-register tasks (e.g. after moving the folder)"
Write-Host "    .\uninstall.ps1           - remove Vigil"
Write-Host ""
