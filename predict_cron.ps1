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

$Repo = "D:\ssq_evo"
$Data = "D:\ssq_evo_data"
$Log  = Join-Path $Data "predict_cron.log"
$PyScript = Join-Path $Repo "predict_tonight.py"

function Log($msg) {
    # v2 (2026-09-25): 日志写入永不抛错。9/24 事故根因——AppendAllText 遇文件锁抛出，
    # ErrorActionPreference=Stop 直接炸掉整个 try 块，register 相在写第一行日志前就死，
    # 26111 因此漏登记。日志是观测手段，观测失败不得杀死被观测的业务。
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
    # 3 次都失败（日志被长期占用）：写到旁路溢出文件，绝不抛出
    try {
        $spilled = Join-Path $Data "predict_cron_overflow.log"
        [System.IO.File]::AppendAllText($spilled, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
    } catch {}
}

# Resolve python: prefer managed venv (has numpy/scipy), else host python on PATH.
$candidates = @(
    "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
    "python"
)
$Py = $null
foreach ($c in $candidates) {
    if (Test-Path $c -ErrorAction SilentlyContinue) { $Py = $c; break }
}
if (-not $Py) { $Py = "python" }

try {
    if (-not (Test-Path $PyScript)) { throw "predict_tonight.py not found at $PyScript" }
    Log "START phase=$Phase py=$Py repo=$Repo"
    if ($Phase -eq "both") {
        & $Py $PyScript "auto" 2>&1 | ForEach-Object { Log $_ }
    } else {
        & $Py $PyScript "auto" "--phase" $Phase 2>&1 | ForEach-Object { Log $_ }
    }
    Log "DONE phase=$Phase exit=$LASTEXITCODE"
    # Heartbeat + data snapshot to GitHub data-backup branch (cloud watchdog reads this).
    # Fire after score phase (the phase that ingests the draw) or after full run.
    if (($Phase -eq "score" -or $Phase -eq "both") -and $LASTEXITCODE -eq 0) {
        $hbOut = & $Py (Join-Path $Repo "heartbeat_push.py") 2>&1
        foreach ($line in $hbOut) { Log "hb: $line" }
        Log "HEARTBEAT exit=$LASTEXITCODE"
    }
} catch {
    Log "ERROR phase=$Phase : $_"
    exit 1
}
