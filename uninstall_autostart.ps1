<#
.SYNOPSIS
    Disable Push-to-Talk auto-start at Windows logon.

.DESCRIPTION
    Removes whatever install_windows.ps1 created for auto-start:
      * the 'PushToTalkDictation' scheduled task, and/or
      * the Startup-folder shortcut (PushToTalk.lnk).
    Also offers to stop any running instance.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'SilentlyContinue'

$taskName = 'PushToTalkDictation'
$removed = $false

# 1. Scheduled task
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[OK] Removed scheduled task '$taskName'." -ForegroundColor Green
    $removed = $true
}

# 2. Startup shortcut
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'PushToTalk.lnk'
if (Test-Path $lnk) {
    Remove-Item $lnk -Force
    Write-Host "[OK] Removed Startup shortcut: $lnk" -ForegroundColor Green
    $removed = $true
}

if (-not $removed) {
    Write-Host '[i] No auto-start entry found. Nothing to remove.' -ForegroundColor Yellow
}

# 3. Stop running instances (pythonw/python running push_to_talk.py)
$running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'push_to_talk\.py' }
if ($running) {
    $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "[OK] Stopped $($running.Count) running Push-to-Talk process(es)." -ForegroundColor Green
}

Write-Host 'Auto-start disabled.' -ForegroundColor White
