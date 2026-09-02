# fix_score_task.ps1 - 重建 ssq_evo_predict_score 计划任务（2026-09-02 闭环审计产物）
#
# 背景（审计结论）：
#   score 任务自 2026-08-18 创建以来，7 个计划窗口（22:30）一次都没触发过；
#   历史 8 次打分全部来自人工兜底/开机补跑。9/2 17:52 的运行即 StartWhenAvailable
#   开机补跑（PC 在 9/1 22:30 处于关机/睡眠）。为消除"开机时机"单点依赖，
#   改为三重冗余触发（脚本幂等，重复打分自动跳过，合并 0 期无害退出）：
#     1) 开奖日（二/四/日）22:30 —— 主触发
#     2) 开奖日（二/四/日）23:30 —— 夜间兜底
#     3) 次日（一/三/五）09:00 —— 次日兜底（覆盖整夜关机的情形）
#
# 运行方式（管理员 PowerShell，一次性）：
#   powershell -ExecutionPolicy Bypass -File D:\ssq_evo\fix_score_task.ps1
# 验证：
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
    -LogonType S4U -RunLevel HighestAvailable

Register-ScheduledTask -TaskName "ssq_evo_predict_score" `
    -Action $action -Trigger $t1,$t2,$t3 `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "[fix] ssq_evo_predict_score 已重建：22:30 / 23:30（二四日）+ 次日 09:00（一三五）"
Get-ScheduledTaskInfo -TaskName ssq_evo_predict_score | Format-List LastRunTime, NextRunTime, LastTaskResult

