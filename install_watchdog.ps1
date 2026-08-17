# install_watchdog.ps1 - run as Administrator on a Windows host that has Docker, to register the watchdog scheduled task.
# The sandbox/cloud cannot persist system-level scheduled tasks; this script is run manually by the user on their machine.
$ErrorActionPreference = "Stop"
$CI = [System.Globalization.CultureInfo]::InvariantCulture

$TaskName = "ssq_evo_watchdog"
$Script   = "D:\ssq_evo\watchdog.ps1"
if (-not (Test-Path $Script)) { Write-Error "watchdog.ps1 not found at $Script"; exit 1 }

# action: invoke watchdog silently
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$Script`""

# triggers: 1) at logon 2) every 30 minutes (from registration, repeats indefinitely)
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn
$triggerTimer = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

# settings: run on AC/battery, run if missed, do not require network, max 1h, ignore new on overlap
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

# run as current user (Docker Desktop is per-user; needs that user token to reach the daemon)
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($triggerLogon, $triggerTimer) -Settings $settings `
    -Principal $principal -Force

Write-Host "OK: registered scheduled task '$TaskName'"
Write-Host "  trigger: at logon + every 30 minutes"
Write-Host "  run as : $user (S4U, no stored password, can run while logged off)"
Write-Host "  prereq : enable Docker Desktop 'Start at login', otherwise daemon is down after boot and up -d fails"
Write-Host "  manage : Task Scheduler -> Task Scheduler Library -> $TaskName"
Write-Host "  manual : powershell -ExecutionPolicy Bypass -File `"$Script`""
