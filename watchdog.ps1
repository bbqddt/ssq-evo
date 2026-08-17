# watchdog.ps1 - ssq_evo 7x24 local watchdog (D drive, not C).
# Purpose: called by Windows Task Scheduler every 30 min to confirm the engine is really running.
#          If abnormal, restart the container and write an alert.
# Note: the cloud automation cannot read local files / Docker, so this local watchdog is the primary monitor.
# Alive criterion: container Up + daemon.log updated within last 90 min
#   (in data_driven mode it writes a line every ~60 min even when idle).
# Crash-loop criterion: container Up + daemon.log active BUT cycle_id not advancing
#   for >120 min => process loops but every cycle crashes before writing state
#   (e.g. the old UnboundLocalError silent-crash bug). Restart to break the loop.

$ErrorActionPreference = "Stop"
$CI = [System.Globalization.CultureInfo]::InvariantCulture

$ComposeDir = "D:\ssq_evo"
$DataDir    = "D:\ssq_evo_data"
$Log        = "$DataDir\watchdog.log"
$DaemonLog  = "$DataDir\daemon.log"
$StateFile  = "$DataDir\state.json"
$CycleTrack = "$DataDir\watchdog_last_cycle.txt"
$CycleTsFile = "$DataDir\watchdog_cycle_ts.txt"
$Container  = "ssq-evo-engine"
$AlertFile  = "$DataDir\watchdog_alert.log"

function Stamp {
    return (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", $CI)
}

function Log($msg) {
    $line = "$(Stamp)  $msg"
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

$needRestart = $false
$reasons = @()

# --- 1) container alive (non-fatal if docker unavailable) ---
$status = ""
try {
    $status = (& docker ps --filter "name=$Container" --format "{{.Status}}" 2>$null) -join ""
}
catch {
    $errMsg = $_.Exception.Message
    Log ("docker command unavailable (watchdog must run on a host that has docker): $errMsg")
}

if (-not ($status -match "Up")) {
    if ($status -eq "") {
        $reasons += "docker unavailable, cannot confirm container state"
    }
    else {
        $needRestart = $true
        $reasons += "container not running (status='$status')"
    }
}
else {
    Log "container alive: $status"
}

# --- 2) daemon.log freshness (primary criterion) ---
if (Test-Path $DaemonLog) {
    $age = (Get-Date) - (Get-Item $DaemonLog).LastWriteTime
    if ($age.TotalMinutes -gt 90) {
        $needRestart = $true
        $reasons += ("daemon.log static for {0:N0} min (>90)" -f $age.TotalMinutes)
    }
    else {
        Log ("daemon.log active (static {0:N0} min)" -f $age.TotalMinutes)
    }
}
else {
    $needRestart = $true
    $reasons += "daemon.log missing"
}

# --- 3) state freshness (auxiliary; >48h warns only, no auto-restart) ---
if (Test-Path $StateFile) {
    try {
        $st = Get-Content $StateFile -Encoding UTF8 | ConvertFrom-Json
        if ($st.updated) {
            $upd = [datetime]::ParseExact($st.updated, "yyyy-MM-dd HH:mm:ss", $CI)
            $age = (Get-Date) - $upd
            if ($age.TotalHours -gt 48) {
                $reasons += ("state too old {0:N0} h (>48, possible long stop)" -f $age.TotalHours)
            }
            else {
                $cid = $st.cycle_id
                Log ("state fresh (updated {0:N1} h ago, cycle {1})" -f $age.TotalHours, $cid)
            }
            if (Test-Path $CycleTrack) {
                $lastCycle = Get-Content $CycleTrack -Encoding UTF8 | Select-Object -First 1
                $currentCycle = $st.cycle_id
                if ($lastCycle -and ($lastCycle -ne $currentCycle)) {
                    Log ("cycle advanced: $lastCycle -> $currentCycle")
                    Set-Content -Path $CycleTrack -Value $currentCycle -Encoding UTF8
                    Set-Content -Path $CycleTsFile -Value (Stamp) -Encoding UTF8
                }
                elseif ($lastCycle -eq $currentCycle) {
                    # cycle not advancing: distinguish "normal idle" from "crash loop".
                    # only flag crash loop when container Up AND daemon.log still active
                    # (process is looping) but no progress for a long time.
                    $stuckMin = 0
                    if (Test-Path $CycleTsFile) {
                        try {
                            $ts = [datetime]::ParseExact(
                                (Get-Content $CycleTsFile -Encoding UTF8 | Select-Object -First 1),
                                "yyyy-MM-dd HH:mm:ss", $CI)
                            $stuckMin = ((Get-Date) - $ts).TotalMinutes
                        }
                        catch { $stuckMin = 0 }
                    }
                    if (($status -match "Up") -and ($stuckMin -gt 120)) {
                        $needRestart = $true
                        $reasons += ("crash loop: container Up but cycle $currentCycle stuck "
                                     + "{0:N0} min (every cycle crashes before state write)" -f $stuckMin)
                    }
                    else {
                        Log ("cycle unchanged ($currentCycle) for {0:N0} min (keep watching)" -f $stuckMin)
                    }
                }
            }
            else {
                Set-Content -Path $CycleTrack -Value $st.cycle_id -Encoding UTF8
                Set-Content -Path $CycleTsFile -Value (Stamp) -Encoding UTF8
            }
        }
    }
    catch {
        $errMsg = $_.Exception.Message
        Log ("state parse failed: $errMsg")
    }
}

# --- action ---
if ($needRestart) {
    $msg = "$(Stamp)  RESTART: " + ($reasons -join "; ")
    Log $msg
    Add-Content -Path $AlertFile -Value $msg -Encoding UTF8
    try {
        Push-Location $ComposeDir
        & docker compose up -d 2>&1 | ForEach-Object {
            Log ("  docker: $_")
        }
        Pop-Location
        Log "executed docker compose up -d"
    }
    catch {
        $errMsg = $_.Exception.Message
        Log ("restart failed: $errMsg")
    }
}
else {
    Log "health check passed, no action needed."
}
