# fix_score_task.ps1 - Rebuild ssq_evo_predict_score scheduled task
# (closeout audit 2026-09-02; ASCII-only: PS5.1 reads BOM-less ps1 as ANSI/GBK
#  and UTF-8 Chinese comments break string parsing -- keep this file ASCII!)
#
# Audit finding: since creation on 2026-08-18 the 22:30 weekly trigger NEVER
# fired (all 8 historical runs were manual/backfill at ~17:50-18:02). The
# 9/2 17:52 run was a StartWhenAvailable boot catch-up (PC was off/asleep at
# 22:30 on 9/1). To remove the "PC power-on timing" single point of failure,
# use triple redundant triggers (idempotent script: duplicate runs are
# skipped by predict_cron itself; merge of 0 draws exits harmlessly):
#   1) draw days (Tue/Thu/Sun) 22:30 -- primary
#   2) draw days (Tue/Thu/Sun) 23:30 -- night fallback
#   3) next days (Mon/Wed/Fri) 09:00 -- next-morning fallback (overnight off)
#
# Run once (admin PowerShell):
#   powershell -ExecutionPolicy Bypass -File D:\ssq_evo\fix_score_task.ps1
# Verify:
#   Get-ScheduledTaskTrigger -TaskName ssq_evo_predict_score
#   Get-ScheduledTaskInfo   -TaskName ssq_evo_predict_score

$ErrorActionPreference = "Stop"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument '-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "D:\ssq_evo\predict_cron.ps1" -Phase score'

$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Thursday,Sunday -At 22:30
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Thursday,Sunday -At 23:30
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Wednesday,Friday -At 09:00

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId "PC-20260623DMIY\Administrator" `
    -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName "ssq_evo_predict_score" `
    -Action $action -Trigger $t1,$t2,$t3 `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "[fix] ssq_evo_predict_score rebuilt: 22:30/23:30 (Tue-Thu-Sun) + 09:00 (Mon-Wed-Fri)"
Get-ScheduledTaskInfo -TaskName ssq_evo_predict_score | Format-List LastRunTime, NextRunTime, LastTaskResult
