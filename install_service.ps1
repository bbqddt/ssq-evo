#Requires -RunAsAdministrator
<#
  SSQ-Evo 7x24 守护进程安装器（原生，全部在 D 盘，不写 C 盘项目文件）
  ---------------------------------------------------------------
  以管理员运行本脚本一次即可：注册一个“开机启动(SYSTEM)”的常驻任务，
  每次系统启动即运行 daemon_loop.py（每 6h 跑一轮演化），数据全部落在 D:\ssq_evo_data。
  唯一写到 C 盘的是 Windows 计划任务/服务的元数据（系统区，无法避免，已尽量最小化）。

  用法（PowerShell 管理员）：
      D:\ssq_evo\install_service.ps1
  卸载：
      schtasks /Delete /TN "SSQ-Evo-Daemon" /F
  或（若用了 nssm）：
      nssm stop SSQ-Evo-Daemon ; nssm remove SSQ-Evo-Daemon confirm
#>
$ErrorActionPreference = "Stop"

$venvPy   = "D:\ssq_evo_venv\Scripts\python.exe"
$script   = "D:\ssq_evo\daemon_loop.py"
$launcher = "D:\ssq_evo\start_daemon.cmd"
$dataDir  = "D:\ssq_evo_data"
$log      = "D:\ssq_evo_data\daemon.log"
$taskName = "SSQ-Evo-Daemon"

if (-not (Test-Path $venvPy))   { Write-Error "找不到 venv python: $venvPy" }
if (-not (Test-Path $launcher)) { Write-Error "找不到启动器: $launcher" }

# 若系统里已有 nssm，则注册为 SYSTEM 服务（最稳，开机即起，无需登录）
$nssm = @(
    "D:\ssq_evo\nssm.exe",
    "C:\nssm\nssm.exe",
    "C:\Program Files\nssm\nssm.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($nssm) {
    Write-Host "[install] 使用 nssm 注册 SYSTEM 服务: $nssm"
    & $nssm install $taskName $venvPy $script 2>$null
    & $nssm set $taskName AppDirectory D:\ssq_evo
    & $nssm set $taskName AppEnvironment "DATA_DIR=$dataDir"
    & $nssm set $taskName AppStdout $log
    & $nssm set $taskName AppStderr $log
    & $nssm start $taskName
    Write-Host "[install] 已注册并启动 nssm 服务 $taskName"
} else {
    # 否则用计划任务：SYSTEM 账户 + 开机启动（无需登录即 7x24）
    Write-Host "[install] 未找到 nssm，改用计划任务(SYSTEM 开机启动)"
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$launcher`"" -WorkingDirectory "D:\ssq_evo"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBattery `
        -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable
    # 若已存在先删
    schtasks /Delete /TN $taskName /F 2>$null
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force
    Write-Host "[install] 已注册计划任务 $taskName（SYSTEM 开机启动）"
    # 立即触发一次，验证可运行
    Start-ScheduledTask -TaskName $taskName 2>$null
    Write-Host "[install] 已尝试立即启动一次用于验证"
}

Write-Host "[install] 完成。日志见 $log"
