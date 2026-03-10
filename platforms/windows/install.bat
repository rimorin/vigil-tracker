@echo off
:: Launches install.ps1 with execution policy bypassed for this session only.
:: Bypassing here does not change your system-wide PowerShell policy.
:: Flags: (none) = guided install | -Status | -Update | -Reinstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
