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

.PARAMETER Blocklist
    Download a fresh domain blocklist (Steven Black's porn-only hosts list)
    and restart the tracker service.

.PARAMETER Reinstall
    Re-register tasks without re-prompting for .env values (useful after
    moving the project folder or upgrading Python).

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Status
    .\install.ps1 -Update
    .\install.ps1 -Blocklist
    .\install.ps1 -Reinstall
#>

[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$Update,
    [switch]$Blocklist,
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

# ── Spinner (animated indicator for long-running operations) ─────────────
$script:SpinnerThread = $null
$script:SpinnerDone   = $null

function Start-Spinner {
    param([string]$Message)
    if ([Console]::IsOutputRedirected) { return }   # skip when output is piped
    $script:SpinnerDone = [System.Threading.ManualResetEventSlim]::new($false)
    $state = [PSCustomObject]@{ Done = $script:SpinnerDone; Message = $Message }
    $script:SpinnerThread = [System.Threading.Thread]::new(
        [System.Threading.ParameterizedThreadStart]{
            param($s)
            $frames = [string[]]@('⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏')
            $i = 0
            while (-not $s.Done.IsSet) {
                [Console]::Write("`r  " + $frames[$i % 10] + " " + $s.Message)
                [System.Threading.Thread]::Sleep(100)
                $i++
            }
            [Console]::Write("`r" + (' ' * ($s.Message.Length + 6)) + "`r")
        }
    )
    $script:SpinnerThread.IsBackground = $true
    $script:SpinnerThread.Start($state)
}

function Stop-Spinner {
    if ($null -eq $script:SpinnerDone) { return }
    $script:SpinnerDone.Set()
    if ($script:SpinnerThread) {
        $script:SpinnerThread.Join(2000) | Out-Null
        $script:SpinnerThread = $null
    }
    $script:SpinnerDone.Dispose()
    $script:SpinnerDone = $null
}

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
        $secure = Read-Host "  ${label}" -AsSecureString
        $bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain  = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        if ($plain) { return $plain } else { return $current }
    }
    $val = Read-Host "  ${label}${hint}"
    if ($val) { return $val } else { return $current }
}

# Infer SMTP host, port, and app-password URL from an email domain.
# Sets script-scope vars: SmtpDetectedHost, SmtpDetectedPort,
#                         SmtpDetectedAppPassUrl, SmtpDetectedAppPassLabel
function Invoke-SmtpAutoDetect ($email) {
    $domain = ($email -split '@')[-1].ToLower()
    $script:SmtpDetectedHost = ""; $script:SmtpDetectedPort = 587
    $script:SmtpDetectedAppPassUrl = ""; $script:SmtpDetectedAppPassLabel = ""
    switch -Wildcard ($domain) {
        { $_ -in @('gmail.com','googlemail.com') } {
            $script:SmtpDetectedHost = "smtp.gmail.com"; $script:SmtpDetectedPort = 587
            $script:SmtpDetectedAppPassUrl   = "https://myaccount.google.com/apppasswords"
            $script:SmtpDetectedAppPassLabel = "Gmail App Password"; break }
        { $_ -in @('outlook.com','hotmail.com','hotmail.co.uk','live.com','live.co.uk','msn.com') } {
            $script:SmtpDetectedHost = "smtp.office365.com"; $script:SmtpDetectedPort = 587
            $script:SmtpDetectedAppPassUrl   = "https://aka.ms/AppPasswords"
            $script:SmtpDetectedAppPassLabel = "Microsoft App Password"; break }
        { $_ -in @('yahoo.com','yahoo.co.uk','ymail.com') } {
            $script:SmtpDetectedHost = "smtp.mail.yahoo.com"; $script:SmtpDetectedPort = 587
            $script:SmtpDetectedAppPassUrl   = "https://help.yahoo.com/kb/generate-third-party-passwords-sln15241.html"
            $script:SmtpDetectedAppPassLabel = "Yahoo App Password"; break }
        { $_ -in @('icloud.com','me.com','mac.com') } {
            $script:SmtpDetectedHost = "smtp.mail.me.com"; $script:SmtpDetectedPort = 587
            $script:SmtpDetectedAppPassUrl   = "https://appleid.apple.com/account/manage"
            $script:SmtpDetectedAppPassLabel = "iCloud App-Specific Password"; break }
        { $_ -in @('fastmail.com','fastmail.fm','fastmail.net') } {
            $script:SmtpDetectedHost = "smtp.fastmail.com"; $script:SmtpDetectedPort = 587
            $script:SmtpDetectedAppPassUrl   = "https://app.fastmail.com/settings/security/devicekeys/"
            $script:SmtpDetectedAppPassLabel = "Fastmail App Password"; break }
    }
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

    # -- Partner PIN verification (required before any settings can change) --
    & $pythonExe "$RepoRoot\pin_auth.py" "status" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  🔒  A partner PIN is required to update settings." -ForegroundColor Cyan
        & $pythonExe "$RepoRoot\pin_auth.py" "verify"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Update aborted - PIN verification failed." -ForegroundColor Red
            exit 1
        }
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
    Start-Spinner "Restarting services..."
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
    Stop-Spinner
    if ($reloadCount -gt 0) {
        Write-OK "Services restarted with new settings"
    } else {
        Write-Warn "No installed services found - run .\install.ps1 to install."
    }
    Write-Host ""
    # Snapshot the (now-updated) .env so the summariser can detect future tampering.
    & $pythonExe "$RepoRoot\pin_auth.py" "env_store" 2>$null
    exit 0
}

# ============================================================================
# -Blocklist  (download fresh community blocklist and restart tracker)
# ============================================================================
if ($Blocklist) {
    $BlocklistFile = Join-Path $RepoRoot "data\domains.txt"
    $BlocklistUrl  = "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts"

    Write-Host ""
    Write-Host "  ====  Vigil - Update Domain Blocklist  ====" -ForegroundColor Green
    Write-Host ""
    Write-OK "Downloading blocklist from Steven Black's hosts list..."

    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        Invoke-WebRequest -Uri $BlocklistUrl -OutFile $tmpFile -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
    } catch {
        Remove-Item $tmpFile -ErrorAction SilentlyContinue
        Fail "Download failed: $_"
    }

    # Strip hosts-file format to bare domains, drop the 0.0.0.0 placeholder line.
    $domains = Get-Content $tmpFile |
        Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() -ne '' } |
        ForEach-Object { ($_ -split '\s+')[-1] } |
        Where-Object { $_ -ne '0.0.0.0' -and $_ -ne 'localhost' -and $_ -match '\.' }
    Remove-Item $tmpFile -ErrorAction SilentlyContinue

    $domains | Set-Content -Path $BlocklistFile -Encoding UTF8
    $domainCount = ($domains | Measure-Object).Count
    Write-OK "Blocklist updated - $domainCount domains"

    # Restart the tracker task so it picks up the new blocklist.
    $trackerTask = Get-ScheduledTask -TaskName "Vigil Tracker" -ErrorAction SilentlyContinue
    if ($null -ne $trackerTask) {
        Start-Spinner "Restarting tracker..."
        Stop-ScheduledTask  -TaskName "Vigil Tracker" -ErrorAction SilentlyContinue
        $elapsed = 0
        while ($elapsed -lt 10) {
            $state = (Get-ScheduledTask -TaskName "Vigil Tracker" -ErrorAction SilentlyContinue).State
            if ($state -eq 'Ready') { break }
            Start-Sleep -Milliseconds 500
            $elapsed += 0.5
        }
        Start-ScheduledTask -TaskName "Vigil Tracker" -ErrorAction SilentlyContinue
        Stop-Spinner
        Write-OK "Tracker restarted - new blocklist is active"
    } else {
        Write-Warn "Tracker not installed yet. The new blocklist will be loaded on next install."
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
# On Windows the Python executable name is not standardised:
#   "python"  — default when installed via python.org or winget
#   "python3" — available when installed via the Microsoft Store
#   "py"      — the Python Launcher (pyenv-win / official installer)
# We probe all three so the scripts work regardless of how Python was installed.
# macOS/Linux scripts use only "python3" because that is the unambiguous name
# on Unix-like platforms (see platforms/macos/install.sh).
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
Start-Spinner "Installing Python packages..."
# Upgrade pip quietly. Temporarily suspend Stop-on-error: with
# $ErrorActionPreference='Stop', any pip stderr (including harmless warnings
# about invalid distributions or the scripts PATH) becomes a terminating error.
$_prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $pythonExe -m pip install --upgrade pip --quiet 2>&1 | Out-Null
$ErrorActionPreference = $_prev
& $pythonExe -m pip install -r "$RepoRoot\requirements.txt" --quiet
$pipExit = $LASTEXITCODE
Stop-Spinner
if ($pipExit -ne 0) { Fail "pip install failed - see output above." }
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
if ($Reinstall) {
    # Require PIN before a reinstall so that a user can't reload modified plists
    # without the partner's knowledge.
    & $pythonExe "$RepoRoot\pin_auth.py" "status" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  🔒  A partner PIN is required to reinstall." -ForegroundColor Cyan
        & $pythonExe "$RepoRoot\pin_auth.py" "verify"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Reinstall aborted - PIN verification failed." -ForegroundColor Red
            exit 1
        }
    }
}
if (-not $Reinstall) {
    Write-Step "Checking configuration"

    # Create .env from template if missing
    if (-not (Test-Path $EnvFile)) { Copy-Item $EnvTemplate $EnvFile }

    # Initialise SMTP auto-detection state.
    $script:SmtpHostAutoFilled    = $false
    $script:SmtpPassVerified      = $false
    $script:SmtpUserEntered       = ""
    $script:SmtpDetectedHost      = ""; $script:SmtpDetectedPort = 587
    $script:SmtpDetectedAppPassUrl = ""; $script:SmtpDetectedAppPassLabel = ""

    # If SMTP_USER is already configured, pre-run auto-detection so we can silently
    # fill SMTP_HOST/PORT without asking the user for them again.
    $existingSmtpUser = Read-EnvValue "SMTP_USER"
    if ($existingSmtpUser) {
        Invoke-SmtpAutoDetect $existingSmtpUser
        if ($script:SmtpDetectedHost -and -not (Read-EnvValue "SMTP_HOST")) {
            Write-EnvValue "SMTP_HOST" $script:SmtpDetectedHost
            Write-EnvValue "SMTP_PORT" $script:SmtpDetectedPort
            $script:SmtpHostAutoFilled = $true
        }
    }

    # SMTP_USER is listed first so we can auto-detect SMTP_HOST/PORT from it.
    $requiredVars = @("SMTP_USER", "SMTP_HOST", "SMTP_PASS", "SMTP_TO")
    $missingVars  = $requiredVars | Where-Object { -not (Read-EnvValue $_) }

    if ($missingVars) {
        Write-Host ""
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host "    Setup wizard - just a few questions to get started" -ForegroundColor Yellow
        Write-Host "  ----------------------------------------------------------------" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Enter them now, or press Ctrl-C to edit .env manually."
        Write-Host ""

        foreach ($var in $missingVars) {
            # SMTP_HOST was auto-detected from the email address — skip it.
            if ($var -eq "SMTP_HOST" -and $script:SmtpHostAutoFilled) { continue }

            $entered = ""
            switch ($var) {
                "SMTP_USER" {
                    Write-Host "  Your email address (used as the sender for digest emails)" -ForegroundColor Cyan
                    do { $entered = Read-Host "  Email" } while (-not $entered)
                }
                "SMTP_HOST" {
                    Write-Host "  SMTP server hostname" -ForegroundColor Cyan
                    Write-Host "  Gmail: smtp.gmail.com  |  Outlook: smtp.office365.com  |  Fastmail: smtp.fastmail.com"
                    do { $entered = Read-Host "  SMTP_HOST" } while (-not $entered)
                }
                "SMTP_PASS" {
                    Write-Host "  SMTP password / app password" -ForegroundColor Cyan
                    if ($script:SmtpDetectedAppPassUrl) {
                        Write-Host "  Create a $($script:SmtpDetectedAppPassLabel): $($script:SmtpDetectedAppPassUrl)"
                    } else {
                        Write-Host "  Use an app password - not your regular sign-in password."
                        Write-Host "  Gmail   -> https://myaccount.google.com/apppasswords"
                        Write-Host "  iCloud  -> https://appleid.apple.com/account/manage"
                        Write-Host "  Outlook -> https://aka.ms/AppPasswords"
                    }
                    Write-Host "  After generating your app password, come back here and paste it below."
                    $testHost = Read-EnvValue "SMTP_HOST"; if (-not $testHost) { $testHost = "smtp.gmail.com" }
                    $testPort = Read-EnvValue "SMTP_PORT"; if (-not $testPort) { $testPort = "587" }
                    $testUser = Read-EnvValue "SMTP_USER"; if (-not $testUser) { $testUser = $script:SmtpUserEntered }
                    while ($true) {
                        $secure = Read-Host "  Password" -AsSecureString
                        $bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
                        $entered = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
                        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
                        if (-not $entered) {
                            Write-Host "  Value cannot be empty." -ForegroundColor Red
                            continue
                        }
                        # Base64-encode password so it passes through PowerShell arg quoting safely.
                        $encodedPw = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($entered))
                        Start-Spinner "Verifying SMTP credentials..."
                        $smtpInline = & $pythonExe -c @"
import smtplib, ssl, sys, base64
host, port, user = sys.argv[1], int(sys.argv[2]), sys.argv[3]
pw = base64.b64decode(sys.argv[4]).decode('utf-8')
try:
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=10)
    else:
        s = smtplib.SMTP(host, port, timeout=10)
        s.ehlo(); s.starttls(context=ctx); s.ehlo()
    s.login(user, pw)
    s.quit()
    print('ok')
except smtplib.SMTPAuthenticationError:
    print('auth_failed'); sys.exit(1)
except Exception as e:
    print('error: ' + str(e)); sys.exit(2)
"@ $testHost $testPort $testUser $encodedPw 2>&1
                        Stop-Spinner
                        if ($smtpInline -match "^ok") {
                            Write-OK "SMTP credentials verified"
                            $script:SmtpPassVerified = $true
                            break
                        } elseif ($smtpInline -match "auth_failed") {
                            Write-Host "  Authentication failed. Double-check your app password and try again." -ForegroundColor Red
                            Write-Host "  Ensure 2FA is enabled and you copied the app password (not your sign-in password)."
                        } else {
                            Write-Warn "Could not connect to SMTP ($smtpInline) - saving anyway. Check your internet connection."
                            break
                        }
                    }
                }
                "SMTP_TO" {
                    Write-Host "  Recipient email address(es)" -ForegroundColor Cyan
                    # Default to the sender email so the user doesn't have to type it twice.
                    $smtpToDefault = Read-EnvValue "SMTP_USER"
                    if (-not $smtpToDefault) { $smtpToDefault = $script:SmtpUserEntered }
                    if ($smtpToDefault) {
                        Write-Host "  Add your accountability partner's email too (comma-separated)."
                        $entered = Read-Host "  Send to [$smtpToDefault]"
                        if (-not $entered) { $entered = $smtpToDefault }
                    } else {
                        Write-Host "  Comma-separate: you@example.com,partner@example.com"
                        do { $entered = Read-Host "  SMTP_TO" } while (-not $entered)
                    }
                }
            }

            Write-EnvValue $var $entered
            Write-OK "Saved ${var}"

            # After SMTP_USER: auto-detect SMTP_HOST/PORT from the email domain.
            if ($var -eq "SMTP_USER") {
                $script:SmtpUserEntered = $entered
                Invoke-SmtpAutoDetect $entered
                if ($script:SmtpDetectedHost -and -not (Read-EnvValue "SMTP_HOST")) {
                    Write-EnvValue "SMTP_HOST" $script:SmtpDetectedHost
                    Write-EnvValue "SMTP_PORT" $script:SmtpDetectedPort
                    Write-OK "Auto-detected SMTP settings: $($script:SmtpDetectedHost):$($script:SmtpDetectedPort)"
                    $script:SmtpHostAutoFilled = $true
                }
            }

            # After SMTP_HOST (when entered manually): also prompt for SMTP_PORT.
            if ($var -eq "SMTP_HOST") {
                Write-Host ""
                Write-Host "  SMTP port" -ForegroundColor Cyan
                Write-Host "  587 = STARTTLS (most providers)  |  465 = SSL/TLS (implicit)"
                $curPort = Read-EnvValue "SMTP_PORT"
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
        Start-Spinner "Validating OpenAI API key..."
        try {
            $response = Invoke-WebRequest -Uri "https://api.openai.com/v1/models" `
                -Headers @{ Authorization = "Bearer $openAiKey" } `
                -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            Stop-Spinner
            Write-OK "OpenAI API key valid"
        } catch {
            Stop-Spinner
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

    # -- Validate SMTP (skipped if already verified inline during wizard) -------
    if (-not $script:SmtpPassVerified) {
        Write-Step "Validating SMTP connection"
        Start-Spinner "Testing SMTP connection..."
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

        Stop-Spinner
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
    } # end SmtpPassVerified check

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
Start-Spinner "Registering scheduled tasks..."
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
}
Stop-Spinner
foreach ($t in $Tasks) {
    Write-OK "Registered: $($t.Name)"
}

# -- Start tasks ---------------------------------------------------------------
Write-Step "Starting services"
Start-Spinner "Starting Vigil services..."
foreach ($t in $Tasks) {
    Start-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds 2
Stop-Spinner

# -- Send confirmation email ---------------------------------------------------
Write-Step "Sending confirmation email"
Start-Spinner "Sending confirmation email..."
$confirmResult = & $pythonExe "$RepoRoot\summarizer.py" "--confirm" 2>&1
$confirmExit = $LASTEXITCODE
Stop-Spinner
if ($confirmExit -eq 0) {
    Write-OK "Confirmation email sent"
} else {
    Write-Host ""
    Write-Host "  ----------------------------------------------------------------" -ForegroundColor Red
    Write-Host "    Could not send confirmation email" -ForegroundColor Red
    Write-Host "  ----------------------------------------------------------------" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Vigil is running but email delivery is not working."
    Write-Host "  You will not receive digests or alerts until this is fixed." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Configured SMTP:"
    Write-Host "    Host : $(Read-EnvValue 'SMTP_HOST'):$(Read-EnvValue 'SMTP_PORT')"
    Write-Host "    User : $(Read-EnvValue 'SMTP_USER')"
    Write-Host "    To   : $(Read-EnvValue 'SMTP_TO')"
    Write-Host ""
    Write-Host "  To fix:  .\install.ps1 -Update"
    Write-Host "  (re-enter your SMTP credentials)"
    Write-Host ""
}

# -- Snapshot .env integrity baseline -----------------------------------------
# Stored in the OS keychain so the summariser can detect silent .env edits.
& $pythonExe "$RepoRoot\pin_auth.py" "env_store" 2>$null

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
Write-Host "    vigil status      - check service health and settings"
Write-Host "    vigil update      - change settings and restart services"
Write-Host "    vigil blocklist   - download a fresh domain blocklist"
Write-Host "    vigil reinstall   - re-register tasks (e.g. after moving the folder)"
Write-Host "    vigil doctor      - diagnose configuration and service issues"
Write-Host "    vigil uninstall   - remove Vigil"
Write-Host ""
if (-not (Get-Command vigil -ErrorAction SilentlyContinue)) {
    Write-Host "  Tip: run 'pip install -e .' in the project directory to enable" -ForegroundColor DarkGray
    Write-Host "  the 'vigil' command above." -ForegroundColor DarkGray
    Write-Host ""
}

# Auto-run doctor when vigil is available, so users see health status immediately.
if (Get-Command vigil -ErrorAction SilentlyContinue) {
    Write-Host "  Running 'vigil doctor' to verify your installation..." -ForegroundColor Cyan
    Write-Host ""
    try { vigil doctor } catch { }
}
