# ssq_evo 引擎健康巡检报告 — 2026-08-30 12:15

## 总体结论
**有异常（1 个严重 = 元审计 BLOCK=3 Dockerfile 漏拷 3 个模块，重建阻断类部署债；运行态本身健康）。**
头号红线 / 诚实基石 / 闸门绕过 / 部署一致性（本轮首次与本地 HEAD 完全一致）/ 镜像 SHA 注入 / 数值对账 / 已部署提交的 CI 单测 全部 intact。

---

## 逐项检查结果

| # | 检查项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 容器存活 | ✅ | `ssq-evo-engine` Up About a minute |
| 2 | 进程不卡死 | ✅ | `docker top` 见 `daemon_loop.py`(常驻 C=0) + `run_cycle.py`(CPU99%, TIME=00:01:58, STIME 04:13) 活跃重型计算，非卡死 |
| 3 | state 新鲜度 | ✅ | cycle_id=494, updated=2026-08-30 11:59:21（距巡检 ~16min < 12h） |
| 4 | 防御未被绕过 | ✅ | best_sig=`red_weighted` 不在 artifact_prone=["red_recurrence_mean"]，随机对照闸门 intact |
| 5 | 崩溃痕迹 | ✅ | daemon.log 末 40 行无 Traceback/Error；cycle 494 跑完、cycle 495 进行中（[cycle]/[firewall]GA隔离/[composer]续代 gen=8/[reflect]EP0-EP6/全局 BH-FDR 正常）；仅 numpy invalid-divide RuntimeWarning（非错误） |
| 6 | 摘要时效 | ✅ | daily_digest 末行 2026-08-30 11:59:21（cycle 494）在 24h 内 |
| 7 | 数据接收 | ✅ | ssq_master.csv 最新 26099(01,12,14,18,30,31+02)，与 state.last_issue=26099 一致；上一开奖 8/27 周四、今晚 8/30 周日 21:15 未到、距上次开奖 < 2 天，数据链路正常 |
| 8 | 部署一致性 | ✅ | build_info=`32ceee5e3cbf2c672f5bde0badc534e75db3f2a3` == 本地 HEAD（本轮首次完全对齐） |
| 9 | CI 门禁 | ⚠️ 关注（非阻断） | 最新 run #18 `distributed-evolve`=failure，但 head_sha=`eade48b9`（**非当前部署提交**）；已部署提交 `32ceee5e` 的 `SSQ Evo CI` run #118=**success**（单测绿）。即运行镜像的代码已通过 CI；失败的是旧提交的云端分布式 merge 作业，不影响本地引擎 |
| 10 | 镜像 SHA 自检 | ✅ | build_info 为 40 位真 SHA，非 unknown |
| 11 | 数值闸门对账 | ✅ | frontier df_gen=8 = daemon.log df_gen=8（无 transient 尖峰）；comp 精英 总数=9、已赋 q=9 → 无空壳回归 |
| 12 | 报告对账 | ✅ | frontier.df_gen=8 = digest 末行 df_gen=8 一致 |
| 13 | 阳性对照功率 | ✅ | verified=True（verdict=SIGNAL），诚实基石 intact，下游 NULL/SIGNAL 结论可信 |
| 14 | 闸门绕过自检 | ✅ | bypass=0（无显式判 NULL/ARTIFACT 仍作精英）、unrec=0（20 精英全有 verdict）。红线未被绕过 |
| 15 | 元审计 | ❌ BLOCK=3 | dockerfile_copy：exchangeable_probe.py / power_analysis.py / random_control_oot.py 未被 Dockerfile COPY 覆盖（已 docker exec 验证 import 会 ModuleNotFoundError）；下次 `docker compose up -d --build` 任何容器侧引用这些模块会崩。WARN=0；INFO=2（reporting_honesty：pick_p=1.0 选号未优于随机、df_gen=8 但 best_verdict=None 零确认→不得称"突破"） |

---

## 对异常的明确告警与建议操作

1. **❌ 元审计 BLOCK=3（严重，重建阻断类部署债，与上轮 8/30 11:57 同款未修）**
   - 证据：Dockerfile L12 COPY 列表漏列 `exchangeable_probe.py` / `power_analysis.py` / `random_control_oot.py`；`docker exec` 逐一 import 均 `ModuleNotFoundError`，BLOCK 为真非误报。
   - 建议：**不要盲目 `docker compose up -d --build`**——当前部署在跑、引擎健康，但重建会触发 import 崩溃。根治：在 Dockerfile COPY 列表加入这 3 个文件，或改用 `COPY . /app`。修复后再重建对齐（届时构建 SHA 仍为 `32ceee5e`，已确认 CI 绿）。

2. **⚠️ CI 关注（非阻断）**
   - `distributed-evolve` 云端演进 merge 作业连续失败（run #17/#18，head=eade48b9）：仅 merge 第 6 步 `merge_evo_proposals.py` 打印 `kfold_z=None` 时 `TypeError` 非零退出，导致云端提案丢弃、无法回流主线。这是慢性问题，不影响本地引擎运行。
   - 建议：若需恢复云端分布式演进回流，修 `merge_evo_proposals.py:75` 对 `None` 容错格式化；否则本地引擎不受影响，可暂缓。

---

## 科学结论（仅基于容器内持久化状态，已部署代码经 CI 验证）
整体仍 **null**（best_q=0.438 随机区间、wf_verdict=UNCONFIRMED、OOS p=0.745 不高于随机、oot_p=0.915、pick_p=1.0、spectral 判 red_recurrence_mean 构造伪结构、leaderboard 全"随机区间"）；正对照 verified SIGNAL 管线有功效。
**进展观察（诚实标注）**：df_gen 由多轮锁定的 4 上长至 8（来源 breed(已评估精英)，跨轮保种 8 棵续代生效），代际在真实推进——但零确认结构，属"进化在转、尚未产出通过闸门的公式"，**不得表述为突破**。
