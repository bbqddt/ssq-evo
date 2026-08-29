# register_watchdog_task.ps1 - Register ssq_evo watchdog as Windows Scheduled Task (every 15 min)
# Run this ONCE as Administrator to activate the watchdog.
# After registration, watchdog.ps1 runs every 15 min indefinitely (survives reboot).
#
# Usage: right-click → "Run as Administrator"
# Or in elevated PowerShell: .\register_watchdog_task.ps1

$ErrorActionPreference = "Stop"

$TaskName  = "ssq_evo_watchdog"
$ScriptPath = "D:\ssq_evo\watchdog.ps1"
$Log       = "D:\ssq_evo_data\watchdog.log"

Write-Host "[1/3] Registering Event Log source for persistent alerts..."
try {
    # Must run as Admin to create event log source
    if (-not [System.Diagnostics.EventLog]::SourceExists("ssq_evo_watchdog")) {
        New-EventLog -LogName "Application" -Source "ssq_evo_watchdog"
        Write-Host "  Event log source registered."
    } else {
        Write-Host "  Event log source already exists."
    }
} catch {
    Write-Host "  WARNING: Could not register event log source (need Admin?): $_"
}

Write-Host "[2/3] Creating/Updating Scheduled Task '$TaskName'..."

# Remove old task if exists (idempotent)
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "  Removed existing task (if any)."
} catch {}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)  # ~10 years, effectively "indefinite" but within XML schema limit

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "ssq_evo engine health watchdog - checks every 15 min, auto-restarts on crash loop" `
    -Force

Write-Host "[3/3] Verifying registration..."
$task = Get-ScheduledTask -TaskName $TaskName
Write-Host ("  Task Name: " + $task.TaskName)
Write-Host ("  State:     " + $task.State)
Write-Host ("  Next Run:  " + $task.NextRunTime)

# Do a test run
Write-Host ""
Write-Host "Running immediate test execution..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
if (Test-Path $Log) {
    $lastLine = Get-Content $Log -Tail 1
    Write-Host "  Last log line: $lastLine"
}

Write-Host ""
Write-Host "DONE. Watchdog is now active and will check every 15 minutes."
Write-Host "View logs:   type $Log"
Write-Host "View alerts: type D:\ssq_evo_data\watchdog_alert.log"
Write-Host "Event Viewer: Applications Logs → Source=ssq_evo_watchdog"
