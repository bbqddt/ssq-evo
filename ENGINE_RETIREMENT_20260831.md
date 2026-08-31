# 引擎退役决策记录（ENGINE RETIREMENT）

- **日期**：2026-08-31 21:00 (UTC+8)
- **决策人**：用户（2026-08-31 主动关闭 Docker Desktop；本记录将该事实实施为显式退役，取代"静默停摆"状态）
- **性质**：**资源/研究方向决策，不是科学结论。** 本文件不构成对任何域的定性。

---

## 1. 退役对象

| 组件 | 处置 |
|---|---|
| 24/7 GA 公式进化 daemon（容器 `ssq-evo-engine`，run_cycle 循环） | 停止（Docker Desktop 关闭） |
| watchdog.ps1 的 Docker 自动复活逻辑 | 改为「收尾守护」模式（检测 `D:\ssq_evo_data\ENGINE_RETIRED` flag） |
| 云端自动化 ×4（引擎监控+开奖打分 / 引擎健康巡检 / 看板自动部署 / 结构复现监控） | PAUSED（全部绑定已退役引擎，避免永久误报） |

## 2. 退役理由（研究性，逐条可核）

1. **瓶颈在数据不在算力**：N=3497 期是检测功效的天花板。同一数据上继续跑
   进化周期，边际信息量≈0，且持续存在重拟合噪声的风险。
2. **当前假设类搜索已饱和**：`df_gen` 锁定 6 天未演进；全部候选（GA/谱/因果/
   frontier）未通过统一闸门（BH-FDR + OOT 盲测 + 随机数据对照 + df 校正）。
3. **预注册前瞻对照与随机不可区分**：8 期，引擎红球总命中 8（均值 1.00）vs
   随机基线 12（1.50）。
   ⚠️ 措辞注意：n=8 功效≈0，此为「与随机不可区分」的**陈述**，
   **不是**「引擎无效」的**判定**。
4. 数据侧确认路径（σ≈3.5% 边际偏倚候选，CANDIDATE 未确认）已预注册并走
   前瞻打分，**不需要** 24/7 算力支撑。

## 3. 措辞边界（红线重申，防复发）

本退役 **不等于**「域为 null」「无须公式」「无须计算」。正确表述：

- 「当前假设类 + 当前数据量 + 当前闸门下未检出可利用结构」；
- 新假设类的设计属于人/LLM 的创造性工作，不在 24/7 算力循环内；
- 物理测量（球体称重/测径）是唯一人尺度判定路径，
  协议见 `audit/PHYSICAL_MEASUREMENT_PROTOCOL.md`。

## 4. 仍在运行的仪器（退役后必须存活，均已验证无 Docker 依赖）

| 仪器 | 载体 | 作用 |
|---|---|---|
| 开奖数据入库 | 本机计划任务 predict_cron（二/四/日 22:30 score 相 fetch+合并主表） | 主表增长；喂给预注册打分器 |
| 预注册预测登记/打分 | 同上（二/四/日 18:00 register） | 冻结引擎的纯前瞻检验（引擎已冻结=不再拟合，检验更干净） |
| 边际偏倚预注册打分 | `preregistered_scorer.py`（按需 + 周日由 watchdog 触发） | σ≈3.5% 候选唯一在途确认仪器；n_new≥50 首次正式打分（约 17 周） |
| 防篡改锚点 | `anchors/preregistered.sha256`（git 内）+ 周日 verify_anchor | 审计链完整性 |
| 收尾看门狗 | watchdog.ps1 v4（flag 模式） | 开奖日收尾核查；不再拉 Docker |

## 5. 复活程序（完全可逆，数据/代码零损失）

1. 删除 `D:\ssq_evo_data\ENGINE_RETIRED`
2. 启动 Docker Desktop
3. `cd D:\ssq_evo; $env:GIT_SHA=(git rev-parse HEAD); docker compose up -d --build`
4. `python verify_deployment.py` 全 PASS（7/7）
5. watchdog 下一轮自动恢复全量体检

已知坑（复活前须知）：
- watchdog 计划任务运行上下文读不到**用户级** gitconfig 的 safe.directory，
  若需它自动 `--build`，先跑（管理员）：
  `git config --system --add safe.directory D:/ssq_evo`
- 重建后 run_cycle 自动 bootstrap 全量评估约 10-15 分钟，CPU 满载属正常。

## 6. 遗留观察项（不阻塞，记录在案）

- 主表 3497 期由 predict_cron score 相持续增长（每周 +3 期）；
- 预注册打分器 n_new 计数从 26100 期之后累计，50 期后首次正式打分；
- 看板/verdict card 冻结在 cycle 528（退役前最后状态），不再更新属预期行为。
