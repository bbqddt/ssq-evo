# ssq_evo 引擎健康巡检报告
**时间**: 2026-08-25 18:15 (GMT+8)  |  **自动化**: automation-1786853726747
**结论**: ❌ 严重异常（3 个严重项）

---

## 严重项（需立即处理）

### 1. ❌ 引擎容器已停止/被移除（步骤1）
- 首轮 `docker ps` 时容器 `ssq-evo-engine` 为 **Up 13 min**，`run_cycle.py` CPU 99%、TIME=00:17:05（活跃重型计算，非卡死）。
- 复核时 `docker ps -a` **已无任何 ssq-evo-engine 容器**（仅 film_*/one-api 在跑），`docker compose ps` 为空 → 容器被移除/退出，引擎当前 **DOWN**。
- 本次巡检为纯只读，未改动任何代码/数据。容器消失为独立事件。
- **建议**：查 nssm 服务状态 / `docker compose` 配置 / 是否主机重启或 OOM kill，并重启引擎。

### 2. ❌ CI 挂（步骤9）
- 最新 run #5（`distributed-evolve`，head_sha=`939fcdc08db7ce8233ad6faf4c47e61443c9c3fd` = 当前 HEAD）`conclusion=failure`（created 2026-08-25T07:07Z）。
- CI 挂 = 当前 HEAD 代码未过测试，本地提交可能带病。
- 日志：https://github.com/bbqddt/ssq-evo/actions/runs/32820062584
- **建议**：立即查 CI 日志修复测试后重新提交。

### 3. ❌ 诚实基石失效：positive_control.verified != True（步骤13 / 元审计 honesty_redline）
- meta_audit.py 在容器尚在时成功读取 `state.positive_control` 并判 `honesty_redline` BLOCK → `verified != True`。
- 此前 8/24、8/25 18:06 多轮均 `verified=True`，**本次首次转非 True**，统一闸门失去分辨功率。
- 后果：下游所有 NULL / SIGNAL / UNCONFIRMED 结论当前**不可信**，任何"无结构"结论在恢复 verified=True 前**不得出口**。
- **建议**：立即排查正对照为何未 verified（闸门功率），恢复 `verified=True`。

---

## 元审计（步骤15，容器尚在时运行）
- **BLOCK=2**
  1. `dockerfile_copy`：容器内需要的 `evolve_predictor.py` 未被 Dockerfile COPY 覆盖（已知旧债，与 8/24–8/25 多轮同款）→ 下次 `docker compose up -d --build` 容器内 import 会崩。**SUGGEST**：Dockerfile COPY 列表加 `evolve_predictor.py`（或 `COPY . /app`）。
  2. `honesty_redline`：见严重项 #3。
- **WARN=0**
- **INFO=1**：`known_debt` — `df_gen_source` 未记录（元数据 bug，不影响 df_gen 真值）。

---

## 容器尚在时抓到的真实快照（步骤2–8、10，非实时）
> 以下为容器消失前捕获，非当前实时状态；仅作排查参考。

| 检查 | 结果 |
|---|---|
| 进程（步骤2） | daemon_loop.py(常驻) + run_cycle.py(CPU99% TIME=17:05) 活跃重型计算，非卡死 ✅ |
| state 新鲜度（步骤3） | cycle_id=426, updated=2026-08-24 20:41:07（~21.5h，同"重启后首轮全量评估进行中"模式）⚠️ |
| 防御红线（步骤4） | best_sig=red_delta_mean 不在 artifact_prone=["red_recurrence_mean"]，防线未绕过 ✅ |
| 崩溃痕迹（步骤5） | daemon.log 无 Traceback；cycle 进行中（[ingest]拒绝/谱扫描/GA隔离正常）✅ |
| 摘要时效（步骤6） | daily_digest 末行 2026-08-24 20:41:07（<24h）✅ |
| 数据接收（步骤7） | ssq_master.csv 最新 26097(05,16,24,26,29,30+02) 与 state 一致；周日8/23已到、今晚8/25周二21:15未到未超2天 ✅ |
| 部署一致（步骤8） | build_info=939fcdc08... = 本地 HEAD 完全一致 ✅ |
| 镜像SHA（步骤10） | 40位真 SHA，非 unknown ✅ |

## 未完成实时对账（步骤11/12/14）
- 因容器中途消失，frontier.json / daemon.log df_gen 实时值 / bypass 数无法再经 `docker exec` 读取。
- 首轮抓到的 digest 末行 `df_gen=4` 与 daemon.log 打印 `df_gen=4` 一致（无 transient 尖峰），但属"消失前"快照。

## 科学结论（带重大保留）
- 此前快照整体仍 null（wf_verdict=NULL、pick_p=1.0、OOS 不高于随机、spectral 判 red_recurrence_mean 构造伪结构、leaderboard 全随机区间）。
- **但因 positive_control.verified != True，所有"null/SIGNAL"结论目前不可信**，须待正对照恢复 verified=True 后才能出口任何结构性判断。
