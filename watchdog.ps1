# watchdog.ps1 —— ssq_evo 7x24 本机看门狗（放 D 盘，不碰 C 盘）
# 用途：由 Windows 计划任务每 30min 调用，确认引擎真在跑；异常则重启并写告警。
# 关键：云端 automation 读不到本机文件/Docker，是盲的；本看门狗才是主监控。
# 存活判据：容器 Up + daemon.log 近 90min 内有更新（数据驱动模式下空闲也会每~60min写一行）。

$ErrorActionPreference = "Stop"
$CI = [System.Globalization.CultureInfo]::InvariantCulture

$ComposeDir = "D:\ssq_evo"
$DataDir    = "D:\ssq_evo_data"
$Log        = "$DataDir\watchdog.log"
$DaemonLog  = "$DataDir\daemon.log"
$StateFile  = "$DataDir\state.json"
$CycleTrack = "$DataDir\watchdog_last_cycle.txt"
$Container  = "ssq-evo-engine"
$AlertFile  = "$DataDir\watchdog_alert.log"

function Stamp { (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", $CI) }
function Log($msg) {
    $line = "$(Stamp)  $msg"
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

$needRestart = $false
$reasons = @()

# 1) 容器存活（docker 不可用时不致命：记录并跳过重启尝试，但其余检查继续）
$status = ""
try {
    $status = (& docker ps --filter "name=$Container" --format "{{.Status}}" 2>$null) -join ""
} catch {
    Log ("docker 命令不可用(看门狗需在含 docker 的本机环境运行): $_")
}
if (-not ($status -match "Up")) {
    if ($status -eq "") {
        $reasons += "docker 不可用，无法确认容器状态"
    } else {
        $needRestart = $true
        $reasons += "容器未运行(status='$status')"
    }
} else {
    Log "容器存活: $status"
}

# 2) daemon.log 新鲜度（主判据：空闲也每~60min写一行，>90min无更新=进程死）
if (Test-Path $DaemonLog) {
    $age = (Get-Date) - (Get-Item $DaemonLog).LastWriteTime
    if ($age.TotalMinutes -gt 90) {
        $needRestart = $true
        $reasons += ("daemon.log 静止 {0:N0}min(>90)" -f $age.TotalMinutes)
    } else {
        Log ("daemon.log 活跃(静止 {0:N0}min)" -f $age.TotalMinutes)
    }
} else {
    $needRestart = $true
    $reasons += "daemon.log 缺失"
}

# 3) state 新鲜度（辅助；非开奖日会正常停更，仅>48h才计入警告，不单独触发重启）
if (Test-Path $StateFile) {
    try {
        $st = Get-Content $StateFile -Encoding UTF8 | ConvertFrom-Json
        if ($st.updated) {
            $upd = [datetime]::ParseExact($st.updated, "yyyy-MM-dd HH:mm:ss", $CI)
            $age = (Get-Date) - $upd
            if ($age.TotalHours -gt 48) {
                $reasons += ("state 过旧 {0:N0}h(>48,疑似长期停摆)" -f $age.TotalHours)
            } else {
                Log ("state 新鲜(更新于 {0:N1}h 前, cycle {1})" -f $age.TotalHours, $st.cycle_id)
            }
            # cycle_id 停滞追踪（仅警告，不自动重启：非开奖日合法停更）
            if (Test-Path $CycleTrack) {
                $last = (Get-Content $CycleTrack -Encoding UTF8 | Select-Object -First 1)
                if ($last -and $last -ne $st.cycle_id) {
                    Log ("cycle 推进: $last -> $($st.cycle_id)")
                } elseif ($last -eq $st.cycle_id) {
                    Log ("cycle 未变($($st.cycle_id))：可能非开奖日空闲，持续观察")
                }
            }
            Set-Content -Path $CycleTrack -Value $st.cycle_id -Encoding UTF8
        }
    } catch { Log ("state 解析失败: $_") }
}

if ($needRestart) {
    $msg = "$(Stamp)  RESTART: " + ($reasons -join "; ")
    Log $msg
    Add-Content -Path $AlertFile -Value $msg -Encoding UTF8
    try {
        Push-Location $ComposeDir
        # up -d 兼具"缺失则创建 / 存在则重启"，不重建镜像（镜像更新需另跑 --build）
        & docker compose up -d 2>&1 | ForEach-Object { Log ("  docker: $_") }
        Pop-Location
        Log "已执行 docker compose up -d"
    } catch {
        Log ("restart 失败: $_")
    }
} else {
    Log "健康检查通过，无需动作。"
}
