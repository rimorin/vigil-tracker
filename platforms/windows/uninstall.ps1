#Requires -Version 5.1
<#
.SYNOPSIS
    Vigil Windows uninstaller.

.DESCRIPTION
    Stops and removes the Vigil Task Scheduler tasks.
    Sends an uninstall notification email (mirrors uninstall.sh behaviour).
    Optionally deletes activity data and the .env config.

.PARAMETER DeleteData
    Also delete log files and activity data from AppData (non-interactive).

.PARAMETER DeleteConfig
    Also delete the .env configuration file (non-interactive).

.PARAMETER DeleteVenv
    Not applicable on Windows (no .venv used), accepted for script parity.

.EXAMPLE
    .\uninstall.ps1
    .\uninstall.ps1 -DeleteData -DeleteConfig
#>

[CmdletBinding()]
param(
    [switch]$DeleteData,
    [switch]$DeleteConfig,
    [switch]$DeleteVenv   # accepted for parity; .venv is a macOS-only concept here
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$EnvFile   = Join-Path $RepoRoot ".env"
$TaskNames = @("Vigil Watchdog", "Vigil Tracker", "Vigil Summarizer")

function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }

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

Write-Host ""
Write-Host "  ====  Vigil - Uninstall  ====" -ForegroundColor Cyan
Write-Host ""

# -- Partner PIN check (must pass before anything is removed) ------------------
# Probe multiple executable names because the Python binary name varies on
# Windows depending on how it was installed ("python", "python3", or "py").
# See the "Find Python 3.8+" comment in install.ps1 for a full explanation.
$pinPythonExe = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $out = & $cmd --version 2>&1 | Select-Object -First 1
        if ($out -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 8)) {
                $pinPythonExe = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
                break
            }
        }
    } catch { }
}
if ($pinPythonExe) {
    & $pinPythonExe "$RepoRoot\pin_auth.py" "verify"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Uninstall aborted." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Warn "python3 not found — skipping partner PIN check."
}

# -- Send uninstall notification email (before stopping services / deleting .env) --
# Mirrors uninstall.sh which calls:  python3 summarizer.py --uninstall-notify
if (Test-Path $EnvFile) {
    # Probe the same candidate names as install.ps1 (python / python3 / py).
    $pythonExe = $null
    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $out = & $cmd --version 2>&1 | Select-Object -First 1
            if ($out -match "Python (\d+)\.(\d+)") {
                $maj = [int]$Matches[1]; $min = [int]$Matches[2]
                if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 8)) {
                    $pythonExe = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
                    break
                }
            }
        } catch { }
    }

    if ($pythonExe) {
        Start-Spinner "Sending uninstall notification email..."
        & $pythonExe "$RepoRoot\summarizer.py" "--uninstall-notify" 2>&1 | Out-Null
        $notifyExit = $LASTEXITCODE
        Stop-Spinner
        if ($notifyExit -eq 0) {
            Write-OK "Uninstall notification sent."
        } else {
            Write-Warn "Could not send uninstall notification - continuing with uninstall."
        }
    } else {
        Write-Warn "Python not found - skipping uninstall notification."
    }
} else {
    Write-Warn "Skipping uninstall notification (.env not found)."
}

Write-Host ""

# -- Write graceful sentinel before stopping services -----------------------
# Prevents the watchdog SIGTERM handler from firing a false tamper alert
# during a legitimate uninstall.
$AppDataVigilDir = Join-Path $env:APPDATA "Vigil"
New-Item -ItemType Directory -Force -Path $AppDataVigilDir | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $AppDataVigilDir "watchdog_graceful_shutdown") | Out-Null

# -- Stop and unregister tasks -----------------------------------------------
foreach ($name in $TaskNames) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Warn "$name : not registered (skipping)"
        continue
    }
    Stop-ScheduledTask        -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask  -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    Write-OK "Removed: $name"
}

# -- Kill any lingering Vigil Python processes (they may hold log file handles) --
foreach ($exe in @("python.exe", "pythonw.exe")) {
    Get-CimInstance Win32_Process -Filter "Name='$exe'" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $cl = $_.CommandLine
            if ($cl -like "*tracker.py*" -or $cl -like "*summarizer.py*" -or $cl -like "*watchdog.py*") {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                Write-Warn "Killed lingering process: $exe (PID $($_.ProcessId))"
            }
        }
}
Start-Sleep -Milliseconds 500

# -- Optionally delete log files and activity data ---------------------------
if (-not $DeleteData) {
    $resp = Read-Host "`n  Delete activity logs and data from AppData? [y/N]"
    $DeleteData = ($resp -match '^[Yy]')
}
if ($DeleteData) {
    foreach ($dir in @(
        (Join-Path $env:APPDATA      "Vigil"),
        (Join-Path $env:LOCALAPPDATA "Vigil")
    )) {
        if (Test-Path $dir) {
            Remove-Item $dir -Recurse -Force
            Write-OK "Deleted: $dir"
        }
    }
} else {
    Write-OK "Log files and data kept."
}

# -- Optionally delete .env --------------------------------------------------
if (-not $DeleteConfig) {
    $resp = Read-Host "  Delete .env config file (contains API keys)? [y/N]"
    $DeleteConfig = ($resp -match '^[Yy]')
}
if ($DeleteConfig) {
    if (Test-Path $EnvFile) {
        # Back up before deleting (mirrors uninstall.sh behaviour)
        $stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
        $backup = "${EnvFile}.bak.${stamp}"
        Copy-Item $EnvFile $backup
        Write-OK "Backed up .env to $backup"
        Remove-Item $EnvFile -Force
        Write-OK "Deleted .env"
    }
} else {
    Write-OK ".env kept."
}

# -- Clean up partner PIN from OS keychain ------------------------------------
if ($pinPythonExe) {
    & $pinPythonExe "$RepoRoot\pin_auth.py" "delete" 2>$null
}

Write-Host ""
Write-Host "  Vigil has been removed." -ForegroundColor Green
Write-Host ""
