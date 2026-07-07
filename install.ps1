# FixMyPrompt installer (Windows) — thin wrapper around the cross-platform
# install.py. Run from PowerShell:  .\install.ps1     (add -uninstall to remove)
# If PowerShell blocks it: powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Error "Python 3 not found. Install it from python.org (tick 'Add python.exe to PATH'), then re-run."
  exit 1
}
& $py.Source "$PSScriptRoot\install.py" @args
