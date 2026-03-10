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
$TaskNames = @("Vigil Tracker", "Vigil Summarizer")

function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  ====  Vigil - Uninstall  ====" -ForegroundColor Cyan
Write-Host ""

# -- Send uninstall notification email (before stopping services / deleting .env) --
# Mirrors uninstall.sh which calls:  python3 summarizer.py --uninstall-notify
if (Test-Path $EnvFile) {
    # Find python3 the same way install.ps1 does
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
        Write-Host "  Sending uninstall notification email..." -ForegroundColor Cyan
        & $pythonExe "$RepoRoot\summarizer.py" "--uninstall-notify" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
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
            if ($cl -like "*tracker.py*" -or $cl -like "*summarizer.py*") {
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

Write-Host ""
Write-Host "  Vigil has been removed." -ForegroundColor Green
Write-Host ""
