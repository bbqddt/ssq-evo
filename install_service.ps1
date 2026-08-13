#Requires -RunAsAdministrator
<#
  SSQ-Evo 7x24 守护进程安装器（原生，全部在 D 盘，不写 C 盘）
  ---------------------------------------------------------------
  以管理员运行本脚本一次即可：注册一个“登录时启动”的计划任务，
  常驻运行 daemon_loop.py（每 6h 跑一轮演化），数据全部落在 D:\ssq_evo_data。

  用法（PowerShell 管理员）：
      D:\ssq_evo\install_service.ps1
  卸载：
      schtasks /Delete /TN "SSQ-Evo-Daemon" /F
#>
$ErrorActionPreference = "Stop"

$venvPy  = "D:\ssq_evo_venv\Scripts\python.exe"
$script  = "D:\ssq_evo\daemon_loop.py"
$dataDir = "D:\ssq_evo_data"
$log     = "D:\ssq_evo_data\daemon.log"
$taskName = "SSQ-Evo-Daemon"

if (-not (Test-Path $venvPy)) { Write-Error "找不到 venv python: $venvPy" }
if (-not (Test-Path $script)) { Write-Error "找不到守护脚本: $script" }

# 用 cmd /c 包裹，确保 DATA_DIR 注入给守护进程及其子进程(run_cycle)
$cmd = 'cmd /c "set DATA_DIR=' + $dataDir + ' && ' + $venvPy + ' ' + $script + ' >> ' + $log + ' 2>&1"'

# 若系统里已有 nssm，则注册为服务（更稳，开机即起，无需登录）
$nssm = @(
    "D:\ssq_evo\nssm.exe",
    "C:\nssm\nssm.exe",
    "C:\Program Files\nssm\nssm.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($nssm) {
    Write-Host "[install] 使用 nssm 注册服务: $nssm"
    & $nssm install $taskName $venvPy $script
    & $nssm set $taskName AppDirectory D:\ssq_evo
    & $nssm set $taskName AppEnvironment "DATA_DIR=$dataDir"
    & $nssm set $taskName AppStdout $log
    & $nssm set $taskName AppStderr $log
    & $nssm start $taskName
    Write-Host "[install] 已注册并启动 nssm 服务 $taskName"
} else {
    # 否则用计划任务：当前用户登录时启动（个人常开机器即等效 7x24）
    Write-Host "[install] 未找到 nssm，改用计划任务(登录时启动)"
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c set DATA_DIR=$dataDir && `"$venvPy`" `"$script`" >> `"$log`" 2>&1" -WorkingDirectory "D:\ssq_evo"
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBattery -ExecutionTimeLimit 0
    # 若已存在先删
    schtasks /Delete /TN $taskName /F 2>$null
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force
    Write-Host "[install] 已注册计划任务 $taskName（下次登录时启动；也可手动运行测试）"
}

Write-Host "[install] 完成。日志见 $log"
