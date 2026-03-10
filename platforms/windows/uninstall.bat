@echo off
:: Launches uninstall.ps1 with execution policy bypassed for this session only.
:: Flags: (none) = interactive | -DeleteData | -DeleteConfig
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
