# predict_cron.ps1 - local draw-day prediction worker.
# Runs on the USER machine (can reach D:\ssq_evo_data), invoked by scheduled tasks:
#   ssq_evo_predict_register  (Tue/Thu/Sun 18:00)
#   ssq_evo_predict_score     (Tue/Thu/Sun 22:30)
# Logs every run to D:\ssq_evo_data\predict_cron.log (UTF-8) so the run is auditable locally.
param(
    [string]$Phase = "both"   # register | score | both
)
$ErrorActionPreference = "Stop"

# Ensure direct outbound (no proxy) so urllib fetch of draw results works on the host.
$env:HTTP_PROXY  = ""
$env:HTTPS_PROXY = ""
$env:http_proxy  = ""
$env:https_proxy = ""

# 强制 UTF-8：修复 9/25 编码混淆（GBK/UTF-8 混写导致日志乱码）
chcp 65001 >$null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$Repo = "D:\ssq_evo"
$Data = "D:\ssq_evo_data"
$Log  = Join-Path $Data "predict_cron.log"
$PyScript = Join-Path $Repo "predict_tonight.py"
$Wrapper = Join-Path $Repo "run_python.bat"

# 互斥锁（2026-09-25）：register 与 score 两个计划任务曾被同时触发（9/25 10:49:57 双发），
# 同时 AppendAllText 同一日志 → 文件锁 → register 相 0x1 死。日志重试只治标，
# 两个相并行跑（同时 fetch/merge 主表、同时写 predictions.jsonl）才是真正的危险面。
# 拿不到锁 = 已有实例在跑，写旁路后直接退出，业务交给先到的实例.
$Mutex = New-Object System.Threading.Mutex($false, "Global\ssq_evo_predict_cron")
if (-not $Mutex.WaitOne(0)) {
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled,
            ("[{0}] SKIP: another predict_cron instance holds the lock (phase={1}){2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Phase, [Environment]::NewLine),
            [System.Text.Encoding]::UTF8)
    } catch {}
    exit 0
}

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    for ($i = 1; $i -le 3; $i++) {
        try {
            [System.IO.File]::AppendAllText($Log, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
            return
        } catch {
            try { Start-Sleep -Milliseconds 300 } catch {}
        }
    }
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
    } catch {}
}

# Resolve python
$candidates = @(
    "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
    "python"
)
$Py = $null
foreach ($c in $candidates) {
    if (Test-Path $c -ErrorAction SilentlyContinue) { $Py = $c; break }
}
if (-not $Py) { $Py = "python" }

# 互斥锁
$Mutex = New-Object System.Threading.Mutex($false, "Global\ssq_evo_predict_cron")
if (-not $Mutex.WaitOne(0)) {
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled,
            ("[{0}] SKIP: another predict_cron instance holds the lock (phase={1}){2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Phase, [Environment]::NewLine),
            [System.Text.Encoding]::UTF8)
    } catch {}
    exit 0
}

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    for ($i = 1; $i -le 3; $i++) {
        try {
            [System.IO.File]::AppendAllText($Log, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
            return
        } catch {
            try { Start-Sleep -Milliseconds 300 } catch {}
        }
    }
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
    } catch {}
}

# Resolve python
$candidates = @(
    "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
    "python"
)
$Py = $null
foreach ($c in $candidates) {
    if (Test-Path $c -ErrorAction SilentlyContinue) { $Py = $c; break }
}
if (-not $Py) { $Py = "python" }

# 互斥锁
$Mutex = New-Object System.Threading.Mutex($false, "Global\ssq_evo_predict_cron")
if (-not $Mutex.WaitOne(0)) {
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled,
            ("[{0}] SKIP: another predict_cron instance holds the lock (phase={1}){2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Phase, [Environment]::NewLine),
            [System.Text.Encoding]::UTF8)
    } catch {}
    exit 0
}

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    for ($i = 1; $i -le 3; $i++) {
        try {
            [System.IO.File]::AppendAllText($Log, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
            return
        } catch {
            try { Start-Sleep -Milliseconds 300 } catch {}
        }
    }
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
    } catch {}
}

# Resolve python
$candidates = @(
    "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
    "python"
)
$Py = $null
foreach ($c in $candidates) {
    if (Test-Path $c -ErrorAction SilentlyContinue) { $Py = $c; break }
}
if (-not $Py) { $Py = "python" }

# 互斥锁
$Mutex = New-Object System.Threading.Mutex($false, "Global\ssq_evo_predict_cron")
if (-not $Mutex.WaitOne(0)) {
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled,
            ("[{0}] SKIP: another predict_cron instance holds the lock (phase={1}){2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Phase, [Environment]::NewLine),
            [System.Text.Encoding]::UTF8)
    } catch {}
    exit 0
}

try {
    if (-not (Test-Path $PyScript)) { throw "predict_tonight.py not found at $PyScript" }
    Log "START phase=$Phase py=$Py repo=$Repo"
    
    $wrapper = Join-Path $Repo "run_python.bat"
    $exitCode = 0
    if ($Phase -eq "both") {
        $exitCode = (cmd /c "`"$Py`" `"$PyScript`" auto" 2>&1).Length; $exitCode = $LASTEXITCODE
    } else {
        $exitCode = (cmd /c "chcp 65001 >nul && `"$Py`" `"$PyScript`" auto --phase $Phase" 2>&1).Length; $exitCode = $LASTEXITCODE
    }
    Log "DONE phase=$Phase exit=$exitCode"
    
    if (($Phase -eq "score" -or $Phase -eq "both") -and $exitCode -eq 0) {
        $hbExitCode = (cmd /c "chcp 65001 >nul && `"$Py`" `"$(Join-Path $Repo 'heartbeat_push.py')`"" 2>&1).Length; $hbExitCode = $LASTEXITCODE
        Log "HEARTBEAT exit=$hbExitCode"
        
        # Ops Audit 自动刷新
        $auditExitCode = (cmd /c "chcp 65001 >nul && `"$Py`" `"$(Join-Path $Repo 'ops_audit.py')`"" 2>&1).Length; $auditExitCode = $LASTEXITCODE
        Log "OPS_AUDIT exit=$LASTEXITCODE"
    }
}
catch {
    Log "ERROR phase=$Phase : $_"
    exit 1
}
finally {
    try { $Mutex.ReleaseMutex(); $Mutex.Dispose() } catch {}
}
