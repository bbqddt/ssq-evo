# repair_watchdog_task.ps1 - fix the broken ssq-evo-watchdog scheduled task.
# Problem found 2026-09-06: the task points to a non-existent file
#   D:\ssq-evo-watchdog.ps1  (hyphens, wrong path, every 30 min)
# and fails silently on every run. The real script is
#   D:\ssq_evo\watchdog.ps1  (underscores, repo copy, v4.1 with heartbeat check)
#
# Run ONCE as Administrator. Safe to re-run (idempotent, -Force overwrites).

$ErrorActionPreference = "Stop"

$TaskName = "ssq-evo-watchdog"
$Script   = "D:\ssq_evo\watchdog.ps1"
if (-not (Test-Path $Script)) { Write-Error "watchdog.ps1 not found at $Script"; exit 1 }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$Script`""

# triggers: 1) at logon  2) every 15 minutes forever (keeps heartbeat check tight)
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn
$triggerTimer = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

# S4U: no stored password, runs while logged off
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($triggerLogon, $triggerTimer) -Settings $settings `
    -Principal $principal -Force

# verify
$t = Get-ScheduledTask -TaskName $TaskName
Write-Host "OK: task '$TaskName' re-registered"
Write-Host "  action  : $($t.Actions.Execute) $($t.Actions.Arguments)"
Write-Host "  interval: 15 min + at logon + catch-up on boot (StartWhenAvailable)"
Write-Host "  verify  : Get-ScheduledTaskInfo -TaskName $TaskName"
