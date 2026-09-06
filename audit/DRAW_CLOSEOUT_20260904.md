# 开奖闭环核对报告 — 2026-09-04（26102 期，开奖 9/3 晚）

核对时间：2026-09-04 17:53–17:58

## 清单结果

| # | 项目 | 结果 |
|---|---|---|
| 1 | 入库 | ✅ 主表 3500 行（3499 期），尾行 26102=03,04,10,13,16,25+09 |
| 2 | 官方双源核对 | ✅ 中彩网(zhcw.com) + 新浪，均与主表一致，无污染 |
| 3 | 预测登记 | ✅ 9/3 18:00:01 准点自动登记（引擎 [1,7,10,17,26,28]+11 / 基线 [3,4,10,15,21,23]+8，开奖前时间戳） |
| 4 | 预测打分 | ✅ 待打分 0。本期引擎红 1/6 蓝否 vs 基线 3/6；累计 n=10：引擎红 0.90/期 vs 基线 1.70/期（期望 1.09），n 小无功效，与随机不可区分 |
| 5 | OBF 确证链 | ✅ 锚 3 文件 verify=True；n_new=3 < 50 ⇒ INSUFFICIENT（设计如此），已记账（17:58:04） |
| 6 | 调度触发 | ⚠️→✅ PC 昨晚关机错过 22:30/23:30；今天 17:45:37 开机后 **17:50:18 任务 4 分钟内自动补跑（StartWhenAvailable 生效）**，LastTaskResult=0。22:30 准点触发仍待实测（下次 NextRun=9/6 周日） |
| 7 | 引擎状态 | ⚠️ **引擎今天 17:46:24（开机后 47 秒）被启动**——restart=no 锁完好（RestartCount=0），非自动拉起，疑似手动启动。已再次 docker stop（Exited 137，restart=no 保持） |

## 引擎 8 分钟复活期的副作用核对

- cycle 575→576（1 轮），df_gen 8（未动），best_q 0.2995→0.3019
- 主表被原子重写一次（26102 由引擎 fetch 入库，早于 cron 补跑；内容经双源核对一致）
- 期间无 watchdog 全量监督（退役守护模式）——按红线，已停止

## 时间线备注

- 昨晚 22:30/23:30 两个触发窗口 PC 处于关机状态（错过非故障）
- 今天开机补跑（17:50:19 START）是重建后 StartWhenAvailable 的首次实战生效
- **22:30 准点触发的首次真实检验：2026-09-06（周日）晚，PC 需保持开机**

## 诚实页脚

即便确认 σ≈3.5% 边际偏倚，它不改变头奖概率的量级（1/1772 万）。这是结构，不是印钞机。

## 附记（18:20）：引擎幽灵启动排查与加固

- 用户确认未手动启动。排除法：restart=no 未触发（RestartCount=0）、watchdog flag 模式无 docker 逻辑、
  启动文件夹/计划任务/注册表 Run 键无任何 docker 钩子、Docker Desktop 无容器恢复设置。
- 唯一剩余机制：dockerd 在 WSL 硬杀（PC 断电）后的容器状态恢复（moby 已知行为类）。
- 加固：**容器已删除**（docker rm；inspect 快照在 audit/engine_container_inspect_20260904.json，
  镜像与数据卷保留，退役 flag 完好）。幽灵启动从此物理不可能；正式复活走
  ENGINE_RETIREMENT_20260831.md §5 程序（compose up 重建 + watchdog 全量监督）。

## 附记 2（18:45）：幽灵启动真凶与终极锁

- 18:29 用户重启 Docker Desktop，被删的引擎容器被**重建**（Created=StartedAt=18:29:21，
  策略回 unless-stopped），film_*/one-api 同一时刻全部重启 ⇒ daemon 重启统一恢复。
- 排除自动化（全 PAUSED）、antigravity 脚本、nssm 服务、watchdog v4（flag 模式在 compose
  逻辑前 exit 0）、计划任务、docker.sock 挂载（无）。
- 真凶 = Docker Desktop 的 compose 应用恢复：daemon 重启时按 docker-compose.yml 重建
  已删容器，绕过一切 restart 策略。
- 终极锁：容器再次删除 + **docker-compose.yml 改名 .RETIRED**（commit 5e68de0 已推 GitHub）。
  复活 = git restore 该文件 + compose up（正式程序 §5）。
