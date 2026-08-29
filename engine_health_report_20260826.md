# ssq_evo 引擎健康巡检报告

巡检时间：2026-08-26 19:58 (UTC+8) ｜ 模式：只读巡检（未修改任何代码/数据）

## 总体结论：⚠️ 有异常（2 项严重 + 1 项部署不一致 + 若干观察项）

---

## 🔴 严重异常

### 1. CI 门禁失败（头号风险）
- 最新一次 GitHub Actions run（id `32941251068`，workflow `distributed-evolve`）`conclusion = failure`。
- 其 `head_sha = 939fcdc08db7ce8233ad6faf4c47e61443c9c3fd`，**与当前容器 build_info 完全一致**。
- 含义：容器正在运行的代码提交本身未通过 CI 测试，可能带病运行。
- 建议：**立即查 CI 日志修复测试**，不要在未修复前依赖该镜像的产出结论。

###  ️ 部署不一致：容器跑的是旧镜像
- 容器内 `build_info.txt` = `939fcdc08db7ce8233ad6faf4c47e61443c9c3fd`
- 宿主 git HEAD = `1e4686dbec8741dd55bcf43a2b32b767d1abcaac`
- 二者不一致 → 容器落后本地 HEAD 至少 1 个提交。
- 建议待 CI 修复后重建：`GIT_SHA=$(git -C D:/ssq_evo rev-parse HEAD) docker compose up -d --build`

---

## 🟡 自动元审计 BLOCK（已核实为误报，勿慌）

- `meta_audit.py` 报告 `BLOCK=1`：`dockerfile_copy | evolve_predictor.py 未被 Dockerfile COPY 覆盖`。
- **核实结果**：`Dockerfile` 第 12 行末尾包含 `COPY ... ./`，已覆盖整个目录，`evolve_predictor.py` 实际已随 `./` 复制；容器当前正常评估产出，证明该审计为**误报**。
- 建议：清理/修正 `meta_audit.py` 的 COPY 扫描逻辑（它只匹配显式文件清单、漏掉 `./` 通配），避免未来误触红线告警。
- 其余审计 INFO（诚实护栏）：`pick_p=1.00`（选号准确率不优于随机）、`df_gen=4 但 best_verdict=None`（代际在转但零确认）—— 仅作汇报措辞提醒，非异常。

---

## ✅ 正常 / 通过项

| 检查项 | 结果 |
|---|---|
| 容器存活 | Up（但 8 分钟前重启，见下） |
| 进程卡死 | run_cycle.py 运行中（CPU 99%，系重启后首轮重型计算，非卡死） |
| state 新鲜度 | updated=2026-08-26 17:58:15（约 2h，< 12h 阈值） |
| 防御未被绕过 | best_sig=`red_sum` 不在 artifact_prone(`red_recurrence_mean`) |
| 崩溃痕迹 | 无 Traceback/Error（仅 numpy RuntimeWarning  benign） |
| 摘要时效 | 末行 daily_digest 2026-08-26 17:58:15（< 24h） |
| 数据接收 | 最新期号 **26098**（2026-08-25 周二），与官方开奖完全一致 → 数据已接收，无缺失 |
| 镜像 SHA 注入 | build_info 为真实 40 位 SHA（非 unknown） |
| df_gen 对账 | frontier.df_gen=4 == daemon.log df_gen=4 → 无 transient 尖峰；comp 精英总数=0，无空壳回归 |
| 报告一致性 | frontier.df_gen(4) == digest.df_gen(4) |
| 阳性对照功率 | state.positive_control.verified = **True**（诚实基石 intact） |
| 闸门绕过 | frontier 中 bypass=0、未记录 verdict=0（头号红线 OK，无误报） |

---

## 🟠 观察 / 需关注

- **容器近期重启**：`docker ps` 显示 Up 仅 8 分钟，且 state 最后更新为 17:58，说明 17:58–19:50（约 1.9h）引擎存在空窗（容器下线）。现已恢复并进入首轮计算，但建议排查重启根因（OOM / 宿主抖动 / 计划任务），避免周期性崩溃。
- **reporting 诚实**：当前最佳 best_q=0.2956 但 best_verdict=NULL、选号命中未优于随机（pick_p=1.0），后续任何汇报须标注「未确认」，不得称「突破/准确率提升」。

---

## 下一步（按重要性）
1. **立即**：查 GitHub Actions `32941251068` 失败日志，修复测试（CI 挂 = 当前镜像带病）。
2. 修复后用 `GIT_SHA=$(git -C D:/ssq_evo rev-parse HEAD) docker compose up -d --build` 重建，对齐 HEAD。
3. 排查 19:50 容器重启原因，确认是否为崩溃循环。
4. 修正 `meta_audit.py` 的 Dockerfile COPY 误判逻辑。
