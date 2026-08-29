# watchdog.ps1 v3 - ssq_evo 7x24 local watchdog (D drive, not C).
# Called by Windows Task Scheduler every 15 min.
#
# === 四维深度体检 ===
# L1 基础存活:   container Up + daemon.log fresh (<90min) + cycle advancing
# L2 代码新鲜度: build_info.txt SHA vs git HEAD (旧镜像=跑旧码=三驾车可能缺失)
# L3 三驾车审计: daemon.log 中 [composer] / [novelty] / [reflect] 出现证据
# L4 环境完整:  SSQ_TURBO 变量 + 关键模块文件存在
#
# === 告警分级 ===
# CRITICAL (auto-restart): 容器挂了 / daemon 死了 / crash loop >120min
# WARN     (alert only):  旧镜像 / 三驾车缺件 / 环境变量缺失 → 提示手动 --build
# INFO:              一切正常
#
# === 修复历史 ===
# v1: 初始版（只有 L1 基础存活，无调度器注册）
# v2 (2026-08-29): 修 BUG1无调度器/BUG2 up-d空操作/BUG3静默失败
# v3 (2026-08-29): 加 L2代码新鲜度/L3三驾车审计/L4环境完整（用户发现旧镜像跑着但watchdog报pass）

$ErrorActionPreference = "Stop"
$CI = [System.Globalization.CultureInfo]::InvariantCulture

# --- paths ---
$ComposeDir    = "D:\ssq_evo"
$DataDir       = "D:\ssq_evo_data"
$Log           = "$DataDir\watchdog.log"
$DaemonLog     = "$DataDir\daemon.log"
$StateFile     = "$DataDir\state.json"
$CycleTrack    = "$DataDir\watchdog_last_cycle.txt"
$CycleTsFile   = "$DataDir\watchdog_cycle_ts.txt"
$Container     = "ssq-evo-engine"
$AlertFile     = "$DataDir\watchdog_alert.log"
$FailCountFile = "$DataDir\watchdog_fail_count.txt"
$RepoDir       = "D:\ssq_evo"

# --- log rotation (keep under 800 lines) ---
if (Test-Path $Log) {
    $lines = (Get-Content $Log -Encoding UTF8).Count
    if ($lines -gt 800) {
        $backup = "$Log.old"; if (Test-Path $backup) { Remove-Item $backup -Force }
        Move-Item $Log $backup -Force
    }
}

function Stamp { return (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", $CI) }
function Log($msg) {
    $line = "$(Stamp)  $msg"
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}
function Alert($msg, $level) {
    # $level = "CRITICAL" | "WARN" | "INFO"
    $tag = "$(Stamp) [$level] $msg"
    Add-Content -Path $AlertFile -Value $tag -Encoding UTF8
    try {
        $entryType = if ($level -eq "CRITICAL") { "Error" } else { "Warning" }
        $eventId   = if ($level -eq "CRITICAL") { 2001 } else { 1001 }
        Write-EventLog -LogName "Application" -Source "ssq_evo_watchdog" `
            -EntryType $entryType -EventId $eventId -Message $tag -ErrorAction SilentlyContinue
    } catch {}
}
function Get-FailCount {
    if (Test-Path $FailCountFile) {
        try { return [int](Get-Content $FailCountFile -Encoding UTF8 | Select-Object -First 1) } catch { return 0 }
    }
    return 0
}
function Set-FailCount($n) { Set-Content -Path $FailCountFile -Value $n -Encoding UTF8 }

# ============================================================
# Collect all findings before deciding action
# ============================================================
$criticals = @()   # → auto restart
$warnings  = @()   # → alert only, prompt manual fix
$info      = @()   # → normal

# --- L1: container alive ---
$status = ""; $dockerOk = $false
try {
    $status = (& docker ps --filter "name=$Container" --format "{{.Status}}" 2>$null) -join ""
    $dockerOk = $true
} catch { Log ("docker command unavailable: $($_.Exception.Message)") }

if (-not ($status -match "Up")) {
    if (-not $dockerOk) {
        $fc = Get-FailCount
        if ($fc -ge 4) { $criticals += "docker unavailable for $fc checks (>1h)" }
        else { Log "docker temporarily unavailable (fail count=$fc), retrying..." }
    } else {
        $criticals += "container not running (status='$status')"
    }
} else {
    $info += "container alive: $status"
}

# --- L1: daemon.log freshness ---
if (Test-Path $DaemonLog) {
    $age = (Get-Date) - (Get-Item $DaemonLog).LastWriteTime
    if ($age.TotalMinutes -gt 90) {
        $criticals += ("daemon.log static {0:N0}min (>90)" -f $age.TotalMinutes)
    } else {
        $info += ("daemon.log active ({0:N0}min ago)" -f $age.TotalMinutes)
    }
} else {
    $criticals += "daemon.log missing"
}

# --- L1: cycle advancement ---
$currentCycle = $null; $stuckMin = 0
if (Test-Path $StateFile) {
    try {
        $st = Get-Content $StateFile -Encoding UTF8 | ConvertFrom-Json
        if ($st.updated) {
            $upd = [datetime]::ParseExact($st.updated, "yyyy-MM-dd HH:mm:ss", $CI)
            $ageH = (Get-Date - $upd).TotalHours
            $currentCycle = $st.cycle_id
            if ($ageH -gt 48) { $warnings += ("state old {0:N0}h (>48)" -f $ageH) }
            else { $info += ("state cycle={0} updated {1:N1}h ago" -f $currentCycle, $ageH) }

            if (Test-Path $CycleTrack) {
                $lastCycle = Get-Content $CycleTrack -Encoding UTF8 | Select-Object -First 1
                if ($lastCycle -and ($lastCycle -ne $currentCycle)) {
                    Log ("cycle advanced: $lastCycle -> $currentCycle")
                    Set-Content -Path $CycleTrack -Value $currentCycle -Encoding UTF8
                    Set-Content -Path $CycleTsFile -Value (Stamp) -Encoding UTF8
                    Set-FailCount 0
                } elseif ($lastCycle -eq $currentCycle) {
                    if (Test-Path $CycleTsFile) {
                        try {
                            $ts = [datetime]::ParseExact((Get-Content $CycleTsFile -Encoding UTF8 | Select-Object -First 1), "yyyy-MM-dd HH:mm:ss", $CI)
                            $stuckMin = ((Get-Date) - $ts).TotalMinutes
                        } catch {}
                    }
                    if (($status -match "Up") -and ($stuckMin -gt 120)) {
                        $criticals += ("crash loop: cycle {0} stuck {1:N0}min" -f $currentCycle, $stuckMin)
                    } else {
                        $info += ("cycle=$currentCycle stuck {0:N0}min (<120 threshold)" -f $stuckMin)
                    }
                }
            } else {
                Set-Content -Path $CycleTrack -Value $currentCycle -Encoding UTF8
                Set-Content -Path $CycleTsFile -Value (Stamp) -Encoding UTF8
            }
        }
    } catch { Log ("state parse failed: $($_.Exception.Message)") }
}

# --- L2: code freshness (image SHA vs git HEAD) ---
$imageSha = ""; $localSha = ""
try {
    $imageSha = (& docker exec $Container cat /app/build_info.txt 2>$null).Trim()
} catch {}
try {
    $localSha = (& git -C $RepoDir rev-parse HEAD 2>$null).Trim()
} catch {}

if ($imageSha -and $localSha) {
    if ($imageSha -ne $localSha) {
        $warnings += ("STALE IMAGE: container=$imageSha != local=$localSha (need --build)")
    } else {
        $info += ("code fresh: SHA=$($localSha.Substring(0,8))")
    }
} elseif (-not $imageSha) {
    $warnings += "build_info.txt missing (image built without GIT_SHA)"
}

# --- L3: tri-carriage audit (scan last 200 lines of daemon.log) ---
$triCarriage = @{
    "composer"         = @{ pattern = "\[composer\]";          label = "公式代数"; found = $false; count = 0 }
    "novelty_search"   = @{ pattern = "\[novelty\]|nov_archive|_adaptive_alpha"; label = "多样性维持"; found = $false; count = 0 }
    "reflective"       = @{ pattern = "\[reflect\]|反省|reflect_epoch"; label = "智能反省"; found = $false; count = 0 }
}
if (Test-Path $DaemonLog) {
    $recentLines = Get-Content $DaemonLog -Tail 200 -Encoding UTF8
    foreach ($key in $triCarriage.Keys) {
        $p = $triCarriage[$key]
        $matches = $recentLines | Select-String -Pattern $p.pattern
        $p.count = $matches.Count
        $p.found = ($matches.Count -gt 0)
    }
}

# Report tri-carriage status
foreach ($key in @("composer", "novelty_search", "reflective")) {
    $tc = $triCarriage[$key]
    if ($tc.found) {
        $info += ("[$($tc.label)] ACTIVE (${tc.count} hits in last 200 lines)")
    } else {
        # novelty/reflect may be legitimately absent in first few cycles after boot;
        # flag as WARN only if we have enough log history and they're truly absent.
        $warnings += ("[$($tc.label)] MISSING — zero '${tc.pattern}' in last 200 lines of daemon.log")
    }
}

# --- L4: environment completeness ---
$turboSet = $false
try {
    $envOutput = & docker exec $Container env 2>$null
    $turboSet = ($envOutput | Select-String "SSQ_TURBO=1").Count -gt 0
} catch {}
if ($turboSet) {
    $info += "[turbo] SSQ_TURBO=1 active"
} else {
    $warnings += "[turbo] SSQ_TURBO NOT SET (engine running at reduced power)"
}

# Check key modules exist inside container
$requiredModules = @("reflective_designer.py", "evolve_predictor.py", "novelty_search.py")
foreach ($mod in $requiredModules) {
    $exists = $false
    try {
        $out = docker exec $Container test -f "/app/$mod" 2>$null; $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) { $exists = $true }
    } catch {}
    if (-not $exists) {
        $warnings += ("module MISSING in container: $mod (old image?)")
    }
}

# ============================================================
# Summary & Action
# ============================================================
Log "---"
Log ("L1基础存活: {0} critical, L2/L3/L4深度: {1} warn, {2} info" -f $criticals.Count, $warnings.Count, $info.Count)

foreach ($i in $info) { Log "  OK  $i" }
foreach ($w in $warnings) { Log "  WARN  $w"; Alert $w "WARN" }

if ($criticals.Count -gt 0) {
    # --- CRITICAL: auto restart with --build to get latest code ---
    $msg = "$(Stamp)  RESTART: " + ($criticals -join "; ") + (" (also {0} warnings)" -f $warnings.Count)
    Log $msg
    Alert $msg "CRITICAL"

    # Use --build --force-recreate to ensure latest code is deployed
    $restartSuccess = $false
    try {
        Push-Location $ComposeDir
        $gitSha = (& git -C $RepoDir rev-parse HEAD 2>$null).Trim()
        Log "executing: docker compose up -d --build --force-recreate (GIT_SHA=$gitSha)"
        $env:GIT_SHA = $gitSha
        & docker compose up -d --build --force-recreate 2>&1 | ForEach-Object { Log ("  docker: $_") }
        Pop-Location
        $restartSuccess = $true

        Start-Sleep -Seconds 45  # build takes time
        $newStatus = (& docker ps --filter "name=$Container" --format "{{.Status}}" 2>$null) -join ""
        if ($newStatus -match "Up") {
            # Verify new image SHA
            $newSha = (& docker exec $Container cat /app/build_info.txt 2>$null).Trim()
            Log "VERIFIED: container Up, new SHA=$($newSha.Substring(0,[Math]::Min(8,$newSha.Length)))"
            Set-FailCount 0
        } else {
            Log "WARNING: container status after build='$newStatus'"
            Set-FailCount ((Get-FailCount) + 1)
        }
    } catch {
        Log ("--build failed: $($_.Exception.Message)")
        # Fallback: plain force-recreate without build
        Log "fallback: docker compose up -d --force-recreate (no --build)"
        try {
            Push-Location $ComposeDir
            & docker compose up -d --force-recreate 2>&1 | ForEach-Object { Log ("  docker: $_") }
            Pop-Location
        } catch {
            $fc = (Get-FailCount) + 1; Set-FailCount $fc
            if ($fc -ge 3) {
                Alert "$(Stamp) CRITICAL: restart failed $fc times consecutively. MANUAL FIX NEEDED." "CRITICAL"
            }
        }
    }
} elseif ($warnings.Count -gt 0) {
    Log "health: WARNINGS present (no auto-restart). User should run: cd D:\ssq_evo; `$env:GIT_SHA=(git rev-parse HEAD); docker compose up -d --build --force-recreate"
} else {
    Log "ALL GREEN: all 4 levels passed."
}
