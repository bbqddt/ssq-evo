# install_watchdog.ps1 —— 在"含 Docker 的 Windows 本机"以管理员运行，注册看门狗计划任务。
# 说明：沙箱/云端无法落盘系统级计划任务，此脚本由用户在本机手动运行（开"系统级工具"策略后）。
$ErrorActionPreference = "Stop"
$CI = [System.Globalization.CultureInfo]::InvariantCulture

$TaskName = "ssq_evo_watchdog"
$Script   = "D:\ssq_evo\watchdog.ps1"
if (-not (Test-Path $Script)) { Write-Error "未找到 $Script，请确认 watchdog.ps1 在 D:\ssq_evo"; exit 1 }

# 动作：静默调用看门狗
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$Script`""

# 触发器：①用户登录时 ②每 30 分钟（注册起无限重复）
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn
$triggerTimer = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

# 设置：插电/电池都跑、错过也补跑、不卡网络、单次最长 1h、重复实例忽略
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

# 以当前用户身份运行（Docker Desktop 是每用户应用，需该用户令牌连 daemon）
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest

# 注册（已存在则覆盖）
Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($triggerLogon, $triggerTimer) -Settings $settings `
    -Principal $principal -Force

Write-Host "OK: 已注册计划任务 '$TaskName'"
Write-Host "  触发: 登录时 + 每 30 分钟"
Write-Host "  运行: $user (S4U, 无需存密码, 可无人登录时运行)"
Write-Host "  前置: Docker Desktop 设置里开启 'Start at login'，否则开机后 daemon 未起，看门狗无法 up -d"
Write-Host "  管理: 任务计划程序 -> 任务计划程序库 -> $TaskName"
Write-Host "  手动跑一次验证: powershell -ExecutionPolicy Bypass -File `"$Script`""
