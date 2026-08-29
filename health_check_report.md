# ssq_evo 引擎健康巡检报告
巡检时间：2026-08-29 18:15 (GMT+8) — 自动触发

## 总体结论：⚠️ 有异常（1 项严重 / 1 项警告）

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 容器存活 | ✅ 健康 | `ssq-evo-engine` 状态 Up（约 1 小时） |
| 2 | 进程不卡死 | ✅ 健康 | `daemon_loop.py` 在跑，CPU 时间 00:00:03，无飙高 |
| 3 | state 新鲜度 | �, 健康 | cycle_id=475，updated=2026-08-29 18:09:57（约 5 分钟前） |
| 4 | 防御未被绕过 | ✅ 健康 | best_sig=comp，不在 artifact_prone=[red_recurrence_mean] |
| 5 | 崩溃痕迹 | ✅ 健康 | daemon.log 无 Traceback/Error，存在 [cycle] 输出 |
| 6 | 摘要时效 | ✅ 健康 | daily_digest 末行 ts=18:09:57，在 24h 内 |
| 7 | 数据接收 | △ 基本正常 | 最新期号 26099；今日周六（非开奖日，最近开奖周四），未触发「>2 天无新期号」。⚠️ CSV 无日期列，无法精确核验时效，建议补日期列 |
| 8 | 部署一致性 | ⚠️ 警告 | 容器内 build_info=b650816d，仓库 HEAD=018f5ff6 → **容器跑的是旧镜像**，建议 `docker compose up -d --build` 重建 |
| 9 | CI 门禁 | ⛔ 严重 | GitHub Actions 最新 run #16 结论 **failure**（commit 939fcdc，run 时间 2026-08-29 07:02Z）→ CI 挂，本地提交可能带病 |
| 10 | 镜像 SHA 自检 | ✅ 健康 | build_info 为真实 hash，非 unknown |
| 11 | 数值闸门对账 | ✅ 健康 | frontier.df_gen=8，comp 精英 评估8/8，daemon.log df_gen=8 一致，无 transient 尖峰、无空壳回归 |
| 12 | 报告对账 | ✅ 健康 | frontier.df_gen=8 == digest.df_gen=8 |
| 13 | 阳性对照功率 | ✅ 健康 | positive_control.verified=True（诚实基石有效） |
| 14 | 闸门不可绕过 | ✅ 健康 | bypass=0，unrec=0（无绕过、无未记录 verdict） |
| 15 | 元审计 | ✅ 健康 | BLOCK=0 / WARN=0，仅 2 条 INFO（汇报诚实提示：pick_p=1.0 未优于随机、df_gen=8 但零确认，不得称「突破」）|

## 必须处理的异常

1. **⛔ CI 失败（严重）**：仓库 GitHub Actions 最近一次运行 #16 结论为 failure。CI 挂意味着可能有代码未过测试，需立即查看 CI 日志并修复测试后再合并/部署。`https://github.com/bbqddt/ssq-evo/actions/runs/33239888633`

2. **⚠️ 部署陈旧（警告）**：容器镜像 build_commit `b650816d` 落后于仓库最新 `018f5ff6`。本地部署跑的是旧镜像，**建议执行 `docker compose up -d --build`（并带 `GIT_SHA=$(git rev-parse HEAD)`）** 重建，否则新修复不会生效。

## 结论
引擎本身运行正常（存活、不卡死、阳性对照有效、闸门无绕过、数值对账一致）。当前核心风险不在引擎运行层面，而在 **交付管道**：CI 门禁失败 + 容器内镜像落后于仓库。修复 CI 并重建容器后再观察一轮即可。
