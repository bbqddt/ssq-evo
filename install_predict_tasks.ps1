# install_predict_tasks.ps1 - register local draw-day prediction scheduled tasks.
# Run as Administrator on the Windows host (the sandbox/cloud cannot persist system tasks).
# These replace the cloud automations that cannot reach D:\ssq_evo_data.
$ErrorActionPreference = "Stop"
$CI = [System.Globalization.CultureInfo]::InvariantCulture

$TaskReg   = "ssq_evo_predict_register"
$TaskScore = "ssq_evo_predict_score"
$Worker    = "D:\ssq_evo\predict_cron.ps1"
if (-not (Test-Path $Worker)) { Write-Error "predict_cron.ps1 not found at $Worker"; exit 1 }

$actionReg = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$Worker`" -Phase register"

$actionScore = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$Worker`" -Phase score"

# Draw days: Tuesday, Thursday, Sunday. Register before draw, score after draw.
$trigReg   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Thursday,Sunday -At "18:00"
$trigScore = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Thursday,Sunday -At "22:30"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskReg -Action $actionReg `
    -Trigger $trigReg -Settings $settings -Principal $principal -Force

Register-ScheduledTask -TaskName $TaskScore -Action $actionScore `
    -Trigger $trigScore -Settings $settings -Principal $principal -Force

Write-Host "OK: registered local tasks"
Write-Host "  $TaskReg   : Tue/Thu/Sun 18:00  (predict_tonight.py auto --phase register)"
Write-Host "  $TaskScore : Tue/Thu/Sun 22:30  (predict_tonight.py auto --phase score)"
Write-Host "  log        : D:\ssq_evo_data\predict_cron.log"
Write-Host "  manage     : Task Scheduler -> Task Scheduler Library"
Write-Host "  manual run : powershell -ExecutionPolicy Bypass -File `"$Worker`" -Phase register"
