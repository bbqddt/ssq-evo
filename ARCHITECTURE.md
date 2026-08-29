# ssq_evo · 架构与工作流（ARCHITECTURE）

> 最后更新：2026-08-17 ｜ 状态：生产常驻（Docker + CloudStudio 看板）｜ 诚实结论：**null 域，未发现可复现结构**

---

## 0. 一句话定位

ssq_evo 是一个**双色球开奖序列的结构搜索引擎**，不是预测器。

- 域是 **null 域**：没有真实 oracle，不保证存在任何可学结构。这是项目的定性前提，决定了一切架构选择。
- 目标：在开奖序列里**寻找是否存在可复现的非随机结构**；若存在则经严格闸门复现，若不存在则严谨确立 null。
- **诚实结论是有效结论**。持续 null（证据的缺席随测试增强）本身就是科学结果，不构成任何预测/下注权。

---

## 1. 诚实护栏契约（不可协商，对所有模块一视同仁）

这是项目头号红线。任何"进化/智能"手段都**不得绕过**以下闸门：

| 机制 | 落点 | 作用 |
|------|------|------|
| **四道物理防火墙** | `firewall.py` | ① 数据隔离（发现段）② 指标隔离（proposer 适应度 ≠ 闸门 q）③ 审计账本 ④ 随机重放（`ARTIFACT_BY_CONSTRUCTION`）|
| **随机数据对照闸门** | `run_axes.py` `random_control_label` | 任何轴/信号先在同款分层标签下跑纯随机双色球；若随机也 SURVIVOR → 判构造伪结构并降级，绝不计入真实候选 |
| **统一裁决 Spine（#41）** | `evaluator.py` | 发现/确认严格分离：候选冻结后在独立确认段 walk-forward 复现才成立（SIGNAL）|
| **BH-FDR + OOT 盲测 + 多零假设交叉** | `run_cycle.py` | 演化/谱/因果/公式候选汇入同一闸门池，跨全部候选校正 |
| **持续阳性对照** | `positive_control.py` | 每轮注入已知结构（AR(1)）验证闸门功率；真实数据应得 NULL，闸门据此拦截过拟合 |
| **强制阳性对照 + 随机对照** | `run_cycle.py` / `run_axes.py` | 保证 pipeline 诚实：能检出注入结构，又能把伪结构降级 |

**红线（来自用户反复强调）：**
1. 禁止无监督自演进优化器以"通过闸门"为目标搜索并自动合并进生产（null 域必造假阳性 / Goodhart）。
2. 任何候选源（GA / 谱 / 因果 / 公式 / 智能层 / frontier）**汇入同一统一闸门**，不得旁路。
3. 优化器/LLM **绝不许看见 holdout/确认段**，绝不许把"过闸指标"当优化目标。
4. 预测/选号产出**必须接入引擎进化出的结论**，不得另起手写朴素频率计数器绕过引擎。

---

## 2. 系统架构（模块地图 + 数据流）

```
                         ┌─────────────────────────────────────────────┐
                         │           开奖数据 (D:/ssq_evo_data)           │
                         │  ssq_master.csv + ssq_evo.db (SQLite)         │
                         └───────────────────┬─────────────────────────┘
                                             │ 增量抓取/合并
                  ┌──────────────────────────▼──────────────────────────┐
                  │                  run_cycle.py  (一轮编排)              │
                  │  ① 演化搜索(engine_core.Evolution + frontier 跨轮)    │
                  │  ② 谱扫描(spectral_scan) / 因果(Granger+CCM)          │
                  │  ③ #39 可微 Formula 候选(diff_formula)                │
                  │  ④ 非平稳监控(nonstationarity)                        │
                  │  ⑤ 随机对照闸门(构造伪结构拦截)                       │
                  │  ⑥ 智能演进层(proposer, 默认关)                       │
                  │  → 全部汇入 BH-FDR + OOT + #41 确认闸门               │
                  │  → 持续阳性对照(功率监控)                             │
                  └───────────────────────────┬──────────────────────────┘
                                             │ 写
                  ┌──────────────────────────▼──────────────────────────┐
                  │  daily_digest.jsonl  (追加式完整结论载荷, ~69 字段)    │
                  │  frontier.json  (精英种子+覆盖度, 跨轮累积)            │
                  │  evidence_ledger.json (不可变证据账本)                 │
                  │  formula_language.json/html (公式可视化+确认闸门)      │
                  │  state.json  (看板兼容字段)                            │
                  └───────────────────────────┬──────────────────────────┘
                                             │ make_dashboard.py 读取
                  ┌──────────────────────────▼──────────────────────────┐
                  │         CloudStudio 看板 (dashboard/index.html)        │
                  │         运行时 fetch('./daily_digest.jsonl') 自动刷新  │
                  └───────────────────────────────────────────────────────┘

   守护层：daemon_loop.py (数据驱动调度) → watchdog.ps1 (崩溃循环检测, 每30min)
   自动化：Automation (开奖日 18:00 注册 / 22:30 校对, 每6h 系统健康巡检)
```

---

## 3. 核心工作流

### 3.1 一轮 cycle（run_cycle.py）
抓取/合并数据 → 多源候选生成（演化/谱/因果/公式/智能）→ 统一闸门（BH-FDR + OOT + #41 + 随机对照 + 阳性对照）→ 写 digest/state/frontier/ledger → 嵌入式自检（self_check 主动暴露 Dockerfile 漏拷、锁残留、闸门功率退化等）。

### 3.2 7×24 常驻（daemon_loop.py + Docker）
- 调度模式：`engine.yaml` 的 `schedule_mode: data_driven`（无新数据 idle 检查，新开奖到达即全量评估+摘要）。
- 容器 `restart: unless-stopped`，数据卷挂 `D:/ssq_evo_data:/app/data`，代码 COPY 进镜像。
- **改任何引擎代码后必须 `docker compose up -d --build` 重建镜像**（数据卷不丢，但代码不自动更新）。

### 3.3 开奖日自动化（predict_tonight.py + Automation）
- `auto` 子命令：开奖前预注册候选（引擎进化公式驱动，非朴素法）→ 开奖后抓取校对 → 累计样本外评分。
- Automation：每开奖日 18:00 注册、22:30 校对（用户本机 cron 触发，沙箱无法 push）。

### 3.4 看门狗（watchdog.ps1 + install_watchdog.ps1）
- 检测：容器 Up + daemon.log 静止 >90min + state 过旧(>48h) 告警；崩溃循环（cycle 卡 >120min 但日志活跃）自动 `docker compose up -d`。
- 安装：本机以 UTF-8 BOM + 纯 ASCII 写 `install_watchdog.ps1`，计划任务登录时+每30min（S4U），需 Docker 设为"登录时启动"。

---

## 4. 模块清单（每个 .py 的职责）

| 文件 | 职责 |
|------|------|
| `engine_core.py` | 信号映射库(SIGMAPS) + 检验统计(TESTS) + surrogate(AAFT/shuffle/twin) + 演化(Evolution: 参数基因组+精英+覆盖度) + FDR + 样本外验证 + 复合公式(_build_comp) |
| `data.py` | 增量抓取(500彩票网) + 按期号合并 + SQLite 读写 |
| `store.py` | SQLite 持久化(runs / evals) |
| `frontier.py` | 演化前沿跨轮持久化：精英种子 + tried 去重 + 覆盖度 + z 轨迹 |
| `run_cycle.py` | 一轮编排：多源候选 → 统一闸门 → 写 digest/state/frontier → self_check |
| `run_axes.py` | 轴驱动器 + 证据账本：representation_zoo 轴 + layered_null 分层标签 + 随机对照闸门 |
| `spectral_scan.py` 逻辑 | 谱扫描闸门（接入 run_cycle 主流程） |
| `nonstationarity.py` | 非平稳检测（球频率漂移 + 短期动量），独立成门 |
| `evaluator.py` (#41) | 统一裁决 Spine：发现/确认分离 walk-forward |
| `cache.py` (#40) | 增量评估缓存（同 genome+数据指纹严格复用提速） |
| `diff_formula.py` (#39) | 可微 Formula 候选生成器（发现段数值优化连续超参，冻结后经统一闸门） |
| `firewall.py` | 四道物理防火墙 + `firewall_gate()` 硬接线 + `verify_data_isolation()` |
| `proposer.py` | 智能演进子系统（ProposerContext 构造级隔离 + HypothesisGenerator + DiversityManager + MetaController + IntelligentEvolution），默认关；LLMProposer 预留(ENABLED=False) |
| `scoring.py` | 正确评分规则（log-loss + bernoulli edge + Wilson CI）+ live leaderboard |
| `positive_control.py` | 持续阳性对照（注入 AR(1) 验证闸门功率） |
| `representation_zoo.py` | 扩轴编码器（代数 mod / 组合 / 质心 等新增信号注入 SIGMAPS） |
| `layered_null.py` | 分层空模型（permute 摧毁时间序 / marginal 摧毁组合结构） |
| `predict_tonight.py` | 开奖日自动流程（注册+校对+累计评分） |
| `daemon_loop.py` | 7×24 常驻循环（数据驱动调度 + 日志落盘 + 看板重建） |
| `make_dashboard.py` | 生成 CloudStudio 看板（读 daily_digest.jsonl，自包含+运行时 fetch 刷新） |
| `serve.py` | 本地看板 http.server（备用，CloudStudio 部署不依赖） |
| `formula_viz.py` | **可视化 Formula 语言（带确认闸门）**：渲染嵌套公式基因组为可读表达式 + 挂 #41 闸门状态，输出 JSON/HTML |
| `verify_firewall.py` | 防火墙验证（15/15 通过：构造隔离/随机重放/阳性对照/智能层同源闸门） |
| `tests/verify_iteration.py` | 验证演化跨轮累积（frontier 覆盖度单调增长） |
| `tests/test_representation_zoo.py` | representation_zoo + layered_null 阳性对照测试 |
| `watchdog.ps1` / `install_watchdog.ps1` | 7×24 看门狗 + 计划任务安装器 |
| `benchmark_speed.py` | 引擎速度基准（1 进程 vs 全核并行） |

---

## 5. 部署与运维红线

1. **绝不写 C 盘**：所有运行数据/库落在 `D:/ssq_evo_data`，代码在 `D:/ssq_evo`。
2. **改引擎代码后必须重建镜像**：`docker compose up -d --build`。新增 .py 生产模块必须同步加进 `Dockerfile` 的 COPY 列表（否则容器内 import 失败、cycle 崩溃；`run_cycle.self_check` 第3项会检测并发警告）。
3. **看板产物永不进 GitHub**：`dashboard/`、`daily_digest.jsonl` 已入 `.gitignore`；看板只由 CloudStudio 直接发布本地 `D:/ssq_evo_data/dashboard/`。
4. **沙箱无法 push**：GitHub 连接器只读；本地 commit 后由用户本机 `sync_push.sh` 推（走 Windows 凭据管理器的 GitHub PAT，命令中绝不出现 token）。
5. **主动监管**：Automation `automation-1786853726747`（每6h）巡检容器存活/进程卡死/state 新鲜度/best_sig 是否被 artifact 污染/daemon 崩溃痕迹/摘要时效/数据接收/部署一致性。
6. **改完必须验证 daemon 真跑完一轮**：state.json 的 cycle_id 递增 + updated 刷新 + daemon.log 无 Traceback（防静默崩溃）。

### 5.1 交付验证闭环（2026-08-18 立项：防止"写了代码但容器跑旧码"反复发生）

> **核心教训**：AI 多次 commit 后声明"完成"，但 Dockerfile 漏拷、镜像未重建、云端自动化碰不到 D 盘——全靠用户截图抓出。**不再靠自觉，靠脚本强制验证。**

| 时机 | 必跑脚本 | 拦截什么 |
|------|---------|---------|
| **git commit 前** | `python pre_commit_check.py` | Dockerfile 漏拷 .py / 孤立未跟踪文件 / 容器版本落后 |
| **docker build 后** | `python verify_deployment.py` | 容器内文件版本≠本地 / 新模块 import 失败 / daemon 有新 Traceback / state 不递增 |
| **注册自动化前** | `python verify_automation_reachability.py` | 云端沙箱碰不到 D:\ 路径 → 自动化静默失败 |

**铁律：不跑验证、不出示 PASS 报告，不得声明"完成"。**

---

## 6. 预测/选号产出红线

- 本项目本体是"公式研发 + 进化 + 优胜劣汰"。任何"今晚预测/选号"产出**必须接入引擎进化出的结论**（Evolution leaderboard 存活基因组 / run_axes 信号 / representation_zoo 轴），**不得另起手写朴素频率计数器绕过引擎**。
- 当前诚实裁决：best_sig 未过 #41 确认 → NULL；`red_recurrence_mean` 已被随机对照闸门判**构造伪结构**杀掉。故任何预测本质仍是 null 域预注册猜测，开奖前登记、开奖后 `score` 做样本外检验。
- 朴素全局边际法有系统性低号偏倚（破同分规则小球优先+分数挤在一起），须用引擎信号作"态筛选器"(`predict_from_signal`) 替代。

---

## 7. 当前科学结论（**带界限的待检验猜想**）

> 2026-08-29 重写。旧版写的是"真的没有可复现结构 / 域大概率为 null"——
> 那是**把"未检出"说成"证明了不存在"**，违反本项目第一红线。以下为修正版。

### 7.0 铁律：缺乏证据 ≠ 证据缺乏

功率分析给出的只是**当前闸门灵敏度下限**，不是物理上界。
任何"未检出"都必须带上**可检出的效应量下限**，否则就是无信息的陈述。
禁止出现"证明了 null""真的没有结构""域是 null"等定论式措辞。

### 7.1 已测定的检出下限（功率 0.8 · N=3496 · k_sur=60~100 · BH-FDR q<0.05）

| 结构类别 | 对应检验 | 最小可检出效应量 | 来源 |
|---|---|---|---|
| AR(1) 自相关 | `acf_max` | **ρ ≈ 0.100** | `power_report.md` |
| 正弦周期 | `fft_peak` | **amp/σ ≈ 0.200** | `power_report.md` |
| 非线性耦合 | `mi_max` | **amp ≈ 0.600** | `power_report.md` |

> 注意三类的"效应量"单位不同，**不可压缩成一个数字**（如"d≥0.15"）——
> 那会对 AR(1) 过于保守、对非线性耦合严重乐观。分开陈述是硬性要求。

### 7.2 未做功率分析 → **不可给界限**（不得写成"未检出"）

| 区域 | 状态 |
|---|---|
| 分形维数 / 长程相关（`dfa_alpha`） | **未功率分析**，无下限 ⇒ 不可声称"未检出" |
| 因果耦合（`ccm` / `granger`） | **未功率分析**，无下限 ⇒ 不可声称"未检出" |
| 组合结构（号码共现 / 间隔 / 区域分布） | **未功率分析**，无下限 ⇒ 不可声称"未检出" |
| 静态边际偏倚（球体物理属性） | 见 §9：**CANDIDATE**，σ≈3.0–3.5%，样本外 p=0.0988 **未确认** |

### 7.3 当前状态的正确表述（可对外复述）

> 在 3496 期观测数据、k_light=100 / k_heavy=60、BH-FDR q<0.05、
> #41 walk-forward 3 折、OOT 盲测、随机对照 + 阳性对照全开条件下：
>
> - **时序结构**：自相关 ≥0.10、周期 ≥0.20、非线性耦合 ≥0.60 的效应量**未检出**
>   （分形/长程相关、因果、组合结构**尚未做功率分析，不给结论**）
> - **静态边际偏倚**：**CANDIDATE 未确认**（σ≈3.0–3.5%，样本外 p=0.0988，功效不足）
> - **非平稳漂移 / 动量**：`ns_verdict = NULL (无显著非平稳)`（q<0.05 门槛下零检出）
> - **构造伪结构**：`red_recurrence_mean` 已被随机对照闸门识别并降级
>
> **结论**：在当前检测灵敏度下，无可复现结构通过统一闸门。
> **这不等价于"不存在结构"，仅等价于"当前数据 + 当前闸门 + 当前假设类 未检出"。**

### 7.4 为什么"穷尽"的说法不成立（2026-08-29 实证）

- **零假设保留了什么，什么就是系统看不见的**：全库 13 个检验的零假设
  100% 是 shuffle / AAFT / 期序打乱——全是**重排类零假设，精确保留边际分布**。
  ⇒ 无论多强的**静态边际偏倚**，替代序列带同样偏倚、差异恒为 0、所有闸门一律报 NULL。
  而物理上最可能的偏倚（球重 / 磨损 / 机器偏好）恰恰是静态的、不依赖期序。
- 3496 期在高维假设空间里是**极稀疏采样**；GA / 智能层 / 谱扫描 / 因果扫描
  覆盖的是"已想到的假设类"，**不是假设空间本身**。
- 覆盖度 `coverage≈6076`、`df_gen=8` —— 距离"穷尽"有量级差距。

### 7.5 页脚（刻进看板 / 预注册 / 每份报告）

> **即便确认 σ≈3.5% 边际偏倚，它不改变头奖概率的量级（1/1772 万）。
> 这是结构，不是印钞机。**

单一事实来源：`honesty_footer.py :: HONESTY_FOOTER`（改这里，全站同步）。

---

## 8. 待办 / 已知边界（不伪装完成）

- 引擎速度：artifact 闸门首跑过重 + UnboundLocalError 已修；整体速度仍有优化空间（见 `benchmark_speed.py`），属持续改进而非一次性任务。
- 智能演进层（`intelligent_evolution_enabled`）默认关；LLMProposer 仍为 `ENABLED=False` 占位（需用户授权端点后启用）。
- 公式语言可视化（`formula_viz.py`）已接入 digest（`formula_language` 字段）+ 独立 HTML；当 `diff_formula_enabled=False` 时无候选，看板块自动隐藏。
- **功率分析缺口（P0）**：`dfa_alpha`、`ccm`/`granger`、组合结构（共现/间隔/区域）
  三类**尚未做功率分析** ⇒ 目前**不可**对其声称"未检出"（见 §7.2）。
  补齐前，任何"全类 null"的对外陈述都缺证据。
- **OOT 闸门遗留缺口（P1）**：单向闸门对下尾失明（`oot_p_low` 仅观测不判定）；
  `osc` 规则含未来值 ⇒ OOT 命中率非可落地准确率。双向化须先跑独立对照重新标定。
- **静态边际偏倚（P0，见 §9）**：CANDIDATE 未确认。**靠等开奖确认需 7~22 年，
  人的时间尺度不可行**；唯一现实路径是物理测量球体（数周）。

---

## 9. 静态边际偏倚：系统此前完全失明的大区

**失明机制**：全库 13 个检验的零假设 100% 是 shuffle / AAFT / 期序打乱
= **重排类零假设，精确保留边际分布**。因此无论多强的静态边际偏倚
（某球系统性偏多/偏少），替代序列都带同样偏倚、差异恒为 0、所有闸门一律报 NULL。
而物理上最可能的偏倚（球重 / 磨损 / 机器偏好 / 批次）恰恰是**静态的、不依赖期序**。

**判据（通用）**：**零假设保留了什么，什么就是系统看不见的。**

**探针**：`marginal_bias_probe.py` —— 不跟替代比，直接跟**理论均匀分布**比
+ 蒙特卡洛秩 p 校准（遵守"不用正态近似 p"铁律）。8 步协议见
`audit/MARGINAL_BIAS_20260829.md`。

**当前判定 = CANDIDATE 未确认**
- 段内持续性 K=4/6/8/12/16/24 **6/6 档显著**，Fisher 合并 p=2.34e-06
- 时不变（连续分段 r=+0.0885 ≈ 随机分段 r=+0.0614），排除数据分块假象
- 排除漂移（r 随段长的变化方向与"漂移/换球"预测**相反**）
- **但**样本外（前 70% 估 → 预测后 30%）r=+0.2299，**p=0.0988 未达显著**；
  探针在 σ=3.46% 时仅 ~37% 功效 ⇒ 不显著与"真实存在"完全相容
- **蓝球（独立机器）同法未复现**（r=-0.42, p=0.94）—— 真实不确定性，不掩盖
- 隐含效应量：**σ ≈ 3.0–3.5% / 球**

**预注册**：`audit/marginal_bias_preregistered.json`（33 球偏差方向+幅度+打分规则）。
**禁止**在确认前表述为"发现了结构"。

**下一步（唯一可行）**：物理测量球体 —— 见
`audit/PHYSICAL_MEASUREMENT_PROTOCOL.md`。等开奖确认需 7（50% 功效）~22 年（80% 功效），
**在人的时间尺度上不可行**。

---

## 10. 换战场（跨域迁移）强制清单

把本架构迁到有结构的新域（股票因子、脑信号、其他彩票……）时，
**源域偏见会随架构一起迁移**，必须在放真实数据**之前**重新标定：

1. **先跑阳性对照**：在新域注入已知结构，验证闸门能检出（证明有功效）。
2. **再跑阴性对照**：在新域跑纯噪声数据，确认假显著率回名义水平（二项检验，不用硬阈值）。
3. **重新校准灵敏度/特异度**：新域的样本量、信噪比、多重比较规模都不同，
   旧域的阈值/权重**不可直接照搬**。
4. **功率分析重做**：新域的可检出效应量下限必须重新测定，否则"未检出"仍是无信息陈述。
5. **警惕源域偏见**：本架构携带的先验（偏好复合公式、倾向相信长程相关、
   多重比较的惩罚强度）都是在本域调出来的，迁移即成为**未经验证的先验**。
6. **首周禁止对外出结论**：完成 1~5 并留痕后，才允许放真实数据。

> 同理适用于本域：改任何闸门后，必须同时交**阴性 + 阳性**对照两份证据
> （只证伪旧闸门 = 可能换了个瞎闸门）。
