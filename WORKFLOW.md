# ssq_evo 完整工作流（权威蓝本）

> 本文件是系统唯一权威运行手册。任何重构、排障、交接都先读它。
> 维护规则：改了任何架构/脚本/调度，必须同步更新本文件对应小节，否则视为未完成。
> 最后更新：2026-08-20

---

## 0. 一句话定调

双色球(6/33+1/16)**结构搜索引擎**，核心命题：开奖序列里是否存在**可复现的非随机结构**。
域是 **null 域**——无真实 oracle、不保证有任何可学结构。诚实护栏不可破，结论宁可判 NULL 也不造假阳性。

---

## 1. 系统总览（三驾车）

| 马车 | 角色 | 算力贡献 | 真相/数据 |
|---|---|---|---|
| **驾1** 本地 Docker 引擎 | 唯一真引擎：GA 进化 + 统一闸门裁决 + 看板数据生产 | ✅ 全部公式迭代在此 | `D:\ssq_evo_data`（卷挂载，唯一真相源）|
| **驾2** 看门狗 + 看板 | 纯监控/报警 + 看板展示 | ❌ 0 | 只读 `D:\ssq_evo_data` |
| **驾3** GitHub Actions | 分布式 GA **提案**（不同随机种子广度搜索）| ✅ 种子多样性/广度 | 静态快照 `data/ssq_history.csv`（仓库内）|

**关键分工原则（红线）**：驾3 只"提案"候选基因组，驾1 在**完整真实数据**上过统一闸门后才并入 frontier。
任何模块不得绕过闸门自动合并进生产（null 域下必造 Goodhart 假阳性）。

---

## 2. 驾1 本地 Docker 引擎（唯一真引擎）

- **容器名 `ssq-evo-engine`（中划线）**。⚠️ `docker exec ssq_evo`（下划线）会 `No such container`，这是反复踩过的坑。
- **代码 COPY 进镜像**（`Dockerfile`）；数据才挂载卷 `D:/ssq_evo_data:/app/data`。
  → **改任何引擎代码后容器不会自动生效，必须 `docker compose up -d --build` 重建**（本机直接跑 `python daemon_loop.py` 则即时生效）。
- **卷内文件**（`/app/data`）：
  - `ssq_master.csv` — 真实开奖历史（3492 期），引擎 fitness 唯一来源
  - `state.json` — 每轮状态（cycle_id / best_sig / best_q / wf_verdict）
  - `frontier.json` — 精英持久化+去重+覆盖度（进化"记忆"）
  - `daily_digest.jsonl` — 每轮追加的完整结论载荷（看板数据源，最权威）
  - `daemon.log` / `watchdog.log` — 运行/监控日志
  - `predictions.jsonl` — 开奖日登记/校对记录
- **调度**：由 `configs/engine.yaml` 的 `schedule_mode` 控制。**配置优先级陷阱**：YAML 是 canonical 源，会盖过卷内 `config.json` 的 `schedule_hours`（改卷 config.json 的调度不生效！）。**2026-08-21 起改为 `timed`（`schedule_hours=0.25` → 每 15min 跑一轮全量 GA），真正 7×24 持续公式迭代**；此前 `data_driven`（idle 360min/查 60min）导致两次开奖间算力空转（见 §8）。每轮 `run_cycle` → `make_dashboard` 重建看板。
- **自启**：compose 配 `restart: unless-stopped`，**宿主重启会自动拉起**（已验证）。

---

## 3. 驾2 看门狗 + 看板（监控，0 算力）

- **`watchdog.ps1`**（计划任务 `ssq_evo_watchdog`，每小时）：只读检查
  ① 容器 alive ② `daemon.log` 90min 内新鲜 ③ cycle 是否前进；异常才重启 + 写 `watchdog_alert.log`。
  ⚠️ 曾因 PowerShell `+`/`-f` 混用语法错**静默崩 3 天**（8/17–8/20），已修 `ee4b023`。巡检第一件事查 `watchdog.log` 末行时间戳是否新鲜。
- **看板**：`make_dashboard.py` 每轮读 `daily_digest.jsonl` 生成 `dashboard/index.html`（自包含+运行时 fetch jsonl），用户手动发布到 CloudStudio。
  ⚠️ `dashboard/` 与 `daily_digest.jsonl` **永不进 GitHub**（`.gitignore`），看板只由 CloudStudio 发布本地 `D:\ssq_evo_data\dashboard\`。
- **健康巡检自动化** `automation-1786853726747`（每 6h）：容器/进程/state 新鲜度/artifact 污染/daemon 崩溃/摘要时效/部署一致性，异常告警。

---

## 4. 驾3 GitHub Actions 分布式 GA（计算提案）

- **静态快照 `data/ssq_history.csv`**：公开开奖史，**固定参考（非增长库）**，进仓库是有意例外（引擎状态仍在 `D:\ssq_evo_data`，不分裂）。本地用 `data_refresh.py` 刷新。
- **`ci_evolve.py`**：各 runner 用不同 `--seed` 独立跑 `engine_core.Evolution`，吐 `candidates_seed_<seed>.json`（top-K 候选基因组 sig/test/params/fitness）。**只提案、不判定结论**。
  ⚠️ 输出文件名**必须** `candidates_seed_*.json`（merge 据此扫描；用错名 merge 读不到）。
- **`merge_candidates.py`**：合所有 seed → `candidates.json`。
- **`collect`** 推 `ga-candidates` 分支（orphan 孤儿分支，只存 `candidates.json`，tiny，非引擎状态），单一写入者避免冲突。
- **`ingest_candidates.py`（驾1 侧）**：git fetch 拉 `ga-candidates` → 在**真实数据**上过统一闸门
  （`label_axis` BH-FDR + OOT 盲测 + 随机对照闸门）→ **SURVIVOR 才并 frontier 精英种子**。防 Goodhart 的最后一道闸。
- **workflow jobs**：
  - `test` + `docker-build`：push / PR 触发（门禁：防坏代码合入、抓 Dockerfile 漏拷）
  - `evolve`（matrix seed 1–6 并行）+ `collect`：`schedule`（UTC 13:17≈北京 21:17）或 `workflow_dispatch` 触发（省免费分钟；GA 不每次 push 烧）
- **`docker-build` 实现要点**：必须用 `docker build` + `docker run` 直接操作，**不能用 `docker compose run`**——compose 挂载的 Windows 卷 `D:/ssq_evo_data` 在 Linux runner 上无效会 exit1（本地 Windows 正常，这是 CI 红/本地绿的根因）。Dockerfile 内另有 `RUN` 构建期冒烟双保险。

---

## 5. 验证闭环（变更铁律——违反任何一条 = 未完成）

1. **提交前 `pre_commit_check.py`（4/4）**：
   - Dockerfile COPY 列表覆盖根目录所有**生产** `.py`；
   - `HOST_ONLY_PY` 白名单放行宿主/CI 脚本（`ci_evolve.py`/`ingest_candidates.py`/`merge_candidates.py`/`data_refresh.py`/`verify_*.py`/`watchdog.ps1` 等），它们本就不进容器；
   - 容器版本一致性警告；无孤立未跟踪 `.py`；文件尾换行。
2. **部署后 `verify_deployment.py`（7/7）**：容器存活 / 文件存在 / 版本匹配 / import / daemon 健康 / Dockerfile 完整性 / 镜像 SHA==git HEAD。
3. **CI**：`test` + `docker-build`（Dockerfile 内 `RUN` 冒烟构建期拦截漏拷）。
4. **读真卷一律 `docker exec`**：沙箱 Bash 对 `D:` 是**陈旧缓存**（state.json cycle 显示旧值），只有 `docker exec` 进 `/app/data` 或 Read 工具读真实卷可靠。

---

## 6. 运维操作手册（标准变更流程）

### 改引擎代码（run_cycle / engine_core 等生产模块）
1. 编辑 `D:\ssq_evo\*.py`
2. 跑 `pre_commit_check.py`（须 4/4）
3. 提交：PowerShell 用 `GIT_CONFIG_SYSTEM=/dev/null` + `-c credential.helper=wincred`（本环境 Bash 调 git 被安全策略拦、PowerShell stdout 偶被吞，落盘或读 git status 验证）
4. `git push origin main`（需**带 `workflow` scope 的 fine-grained PAT**，否则 CI 不触发）
5. 若改了引擎模块：`docker compose up -d --build`（容器不会自动生效）
6. 跑 `verify_deployment.py`（6/6）+ 确认 daemon 跑完一轮（state cycle 递增、daemon.log 无 Traceback）

### 改 CI / 看板 / 自动化
- push 触发 `test`+`docker-build`；`evolve`/`collect` 等 cron 或手动 **Actions → Run workflow**。
- 改任何引擎代码后**必须重建镜像并验证**（见上），不能只推代码。

### 开奖日流程
- **18:00** `ssq_evo_predict_register`：登记下一期公式猜测到 `predictions.jsonl`
- **开奖后 22:30** `ssq_evo_predict_score`：样本外打分（诚实检验，null 域预期不中）
- 三计划任务均"就绪"状态；宿主重启由 nssm/计划任务自启（本机 Windows）。

---

## 7. 红线总览

- **诚实护栏 6 条**（详见 `MEMORY.md`）：禁无监督自演进过闸、统一闸门、优化器不见 holdout、强制阳性对照、随机对照闸门拦截构造伪结构、自主进化层人类保留否决权。
- **预测必须接引擎进化结论**（`engine_core.Evolution` leaderboard 存活基因组 / `run_axes` 信号），不得另起朴素频率计数器绕过引擎。
- **`dashboard/` + `daily_digest.jsonl` 永不进 GitHub**（`.gitignore`）；`ga-candidates` 只存提案非状态。
- **CI 只提案、驾1 把关**：候选绝不绕过统一闸门。

---

## 8. 已知坑与防复发（血泪清单）

| 坑 | 现象 | 根因 | 当前状态 |
|---|---|---|---|
| 沙箱陈旧视图 | Bash 读 D 盘 state.json cycle 显示旧值 | 覆盖挂载+内容陈旧缓存 | 一律 docker exec / Read 工具 |
| 容器名 | `ssq_evo` exec 报 No such container | 真名中划线 `ssq-evo-engine` | 已固化 |
| 看门狗静默崩 | LastRun 有值但 LastResult=1，3天无监控 | PS `+`/`-f` 混用语法错 | 修 `ee4b023` |
| pre-commit 编码崩 | GBK 下读 UTF-8 Dockerfile 报错 | 默认编码 | 改 `utf-8-sig` |
| PAT 缺 workflow scope | CI 不触发 | GitHub 规则 | 用 fine-grained PAT 带 scope |
| Dockerfile 漏拷 | 重建后容器 import 崩 | 新 .py 未进 COPY | `docker-build` job + Dockerfile RUN 冒烟 |
| compose run 在 CI 红 | Linux runner 挂 Windows 卷 exit1 | 卷路径平台相关 | 改用 docker build/run |
| ci_evolve/merge 命名 | merge 读 0 文件 | 文件名前缀不一致 | 统一 `candidates_seed_*.json` |
| data_driven 藏 engine.yaml | 改卷 config.json 调度不生效，daemon 仍 idle 空转 | 配置优先级：YAML(canonical) 盖过卷 config.json 的 schedule_hours | 2026-08-21 改 YAML `schedule_mode: timed` 根治 |
| 驾3→驾1 断链空转 | 驾3 云端 GA 提案推 ga-candidates 分支，但驾1 daemon 从不 ingest → 云端算力白费 | `ingest_candidates.py` 写好却无调用方 | 2026-08-21 daemon 每轮 `run_ingest_subprocess` + 看门狗 fetch 候选到卷根治 |
| Write 写沙箱 D 盘 ≠ 真卷 | `Write` 工具写的 D:\ 文件，容器内 `/app/data` 读不到 | 沙箱文件系统与真本机挂载视图分离 | 一律 `docker exec` 写卷内文件 |
| 看门狗 fetch 依赖本机代理 | ga-candidates 分支【有】候选，但卷里永远空、驾1 ingest 无货 → 驾3 云端算力白费（看似空转） | 看门狗 `curl -x 127.0.0.1:10808` 依赖用户级代理；代理宕/未起时拉取失败（curl exit=35 / 000） | 2026-08-22 经 git+openssl 后端代理手动桥接证明链路通；加「摄入成功消费即删」防重复摄入(`ffde0c3`)；**代理须常驻(建议作 Windows 服务)**，否则看门狗搬运链在代理宕窗口失效 |

---

## 9. 当前状态（2026-08-21 实测）

- **main = `c053081`**（智能段预筛降 surrogate/budget 把单轮 ~25min 压回 ~8min；开启启发式智能演进层 `intelligent_evolution_enabled=true`；开启只读红队自审；驾3 摄入消费即删；接通驾3→驾1 链路；调度 timed 持续迭代；Dockerfile 补拷驾3 脚本；镜像 SHA 追溯）
- **8/22 三驾车空转核查（关键结论）**：驾1 ✅ 持续跑(cycle 350, 每轮调 ingest)；驾3 ✅ **未空转**——8/21 cron 已产 `ga-candidates` 分支 `candidates.json`（6 seed × 48 候选）；唯一空转点是**看门狗→卷搬运链**：看门狗 fetch 走本机代理 10808，代理宕时拉空，导致驾1 每轮 ingest「无候选可摄入」。手动经 git+openssl 代理桥接把 48 候选灌入卷后，驾1 在 cycle 349 **真实摄入：通过闸门=4 拒绝=44 新增精英=4**（frontier 12→16），端到端闭环验证通过。
- **调度已修复算力空转**：`configs/engine.yaml` `schedule_mode: data_driven` → `timed`（`schedule_hours=0.25` → 每 15min 跑一轮全量 GA）。此前 data_driven 让 daemon 两次开奖间只 60min 轮询空转，公式进化实际仅 ~3 次/周；改后 ~96 轮/日，真正 7×24 持续公式迭代（cycle 339 已在定时模式下跑通验证）。
- **驾1**：cycle 344（verify 时），best_sig 仍处随机区间，**Walk-Forward NULL/UNCONFIRMED → 判 null**（无超越随机可提取结构）。诚实结论不变：仍 null 域。
- **frontier**：elites=12，coverage 持续累积（持续迭代驱动）。
- **verify_deployment 7/7 PASS**（2026-08-21）：容器存活/文件齐全/版本匹配/模块可导入/daemon 健康/Dockerfile 完整/镜像 SHA(f8e5c89)==git HEAD。
- **端到端 GA 管线本地实测贯通**：3 seed → 24 候选 → 闸门 1 过 23 拒（`red_parity/multiscale_se` 被随机对照判 `artifact=True` 正确拦截）。
- **三驾车全在岗**：容器 running + 看门狗（含 fetch 候选）+ CI（cron 北京 21:17）。
- ⚠️ 持续迭代在 null 域只扩大搜索广度，**不制造信号**；切勿因 cycle 数变多而误判"发现结构"。

### 9.2 智能模块现状（2026-08-22 核查 + 开启红队）

用户质疑"智能模块没有参与演进、没提供方向"——核查结论：

- **LLM 提案者（`LLMProposer`，`proposer.py:167`）= 预留插槽，默认 disabled**，调用即 `raise NotImplementedError`；全仓库无任何 `openai/anthropic/gpt` 端点调用。**LLM 完全没接入演进**。
- **自主进化层（`IntelligentEvolution`，`proposer.py`）— 2026-08-22 由 `false` 改为 `true` 开启**：注意它不是外部 LLM 占位那套（`LLMProposer` 仍 disabled、无 API 端点），而是**启发式结构化搜索**（`HypothesisGenerator` 突变精英/组合信号/新家族探索/参数扫描 + `MetaController` 按停滞度分配探索-利用预算 + `DiversityManager` 防近亲坍缩），全部在 `firewall.py` 数据隔离内侧运行，**绝不接触 holdout/确认段、绝不自动合并**。
- **红队自审（`redteam_audit`，`run_cycle.py:866`）= 2026-08-22 由 `false` 改为 `true` 开启**——**只读对抗审计器**，每轮 cycle 末尾读 state.json 写 `audit/report.json+md`（verdict + findings），绝不搜索结构、绝不自动合并、不改代码。
- **实质在跑的演进**：`GAProposer` + `engine_core.Evolution` 纯遗传算法（变异+选择）为底座，叠加启发式智能层每轮注入结构化候选（cycle 356 实测 `[intel] 智能层生成 21 候选 → 并入 18`；调优后 cycle 357 `[intel] 生成 12 → 并入 9`）。
- **为何此前默认关（诚信红线）**：红线#1 禁无监督自演进以过闸为目标搜并自动合并（null 域必造假阳性）；红线#6 自主进化层 LLM 绝不许看 holdout/确认段、SIGNAL 仅经独立确认段复现才成立、人类保留否决权绝不自动合并。本次开启的启发式层满足该约束（无 LLM 端点、数据隔离焊死、仅提案不合并）。
- **开启红队后的首份报告（cycle 354, 12:03）**：verdict=**ALERT**，发现 1 条——`best_z_history` 含荒谬离群值 `1.16e+09`（退化统计，某检验 stat 分母近零，显著性不可信，应从候选池剔除）。这就是"智能挑自己毛病"的方向性洞察，已每轮自动产出。
- **启用单轮耗时回归与修复（重要实操坑）**：初开智能层时 `proposer.py` 智能段预筛用 `k_sur=k_light(25)` → 单轮从 ~8min 暴涨到 ~24min（3 倍吞吐回归）。修复：`configs/engine.yaml` 加 `intelligent.intel_ksur: 12` + `intel_budget: 18`（预筛硬门用更轻的 12、预算 30→18），候选仍进主流水线走完整 `k_light/k_heavy` 显著性。**修复后实测 cycle 357 ~8min/轮**，智能段零净吞吐代价接入。
- **关于"智能参与是否过小"的判断（2026-08-22 答复用户）**：当前智能层是**启发式结构化搜索**，提供"往哪搜"的方向（突变精英/组合/新家族），但**没有 LLM 语义层**——不会基于物理直觉起草全新假设。它当前每轮注入 ~9-18 候选并入同款闸门，占每轮总量约 10-20%，属于"有效但保守"的参与比例。若要更大参与或真正的语义智能，需接入外部 LLM（当前 `LLMProposer` 仍是 NotImplementedError 占位），那一步涉及 API 密钥、成本与更强的 Goodhart 风险，应单独评估而非默认开。

### 9.1 三驾车 × 公式进化 配合（2026-08-21 接通驾3→驾1）
- **驾1（本地 Docker 引擎）= 唯一真相源**：GA 公式进化 `engine_core.Evolution`（genome=sig+test+params），每 15min 跑一轮全量 cycle → 更新 frontier（精英记忆）+ best_sig。
- **驾3（GitHub Actions 分布式 GA）= 计算提案**：evolve×6 seed 在静态快照上独立进化，collect 合并推 `ga-candidates` 分支。只【提案】不裁决。
- **驾2（看门狗+看板）= 监控+搬运**：每 30min 巡检容器/日志/state 新鲜度，顺带 `curl -x` 拉 `ga-candidates` 的 candidates.json 到数据卷（网络在宿主机解决，避开容器内代理坑）。
- **配合数据流（已接通，无空转）**：驾3 提案 → `ga-candidates` 分支 → 看门狗 fetch 到 `D:\ssq_evo_data\candidates.json` → 驾1 daemon 每轮 `run_ingest_subprocess` 调 `ingest_candidates.py --local` → 在 3493 期真实数据过统一闸门（BH-FDR+OOT+多零假设+随机对照）→ 仅 SURVIVOR 且非构造伪结构者并入 frontier，下一轮 GA 以之起种群。
- **验证**：dry-run 确认读卷+过闸门（测试候选 red_mean/mean 被正确拒 label=NULL）；重启容器后 daemon.log 出现 `[ingest]` 行（无候选时安全"无候选可摄入"）。至此驾3 云端算力不再白费。
- **剩余依赖（用户侧）**：驾3 提案需 `workflow_dispatch`（Actions 页 Run workflow）或等 cron 北京 21:17 触发；当前无候选时驾1 安全跳过。
- **2026-08-22 实测修正**：驾3 8/21 cron **确实产出** 48 候选（git_sha a7338e0），并非空转；真问题在**看门狗搬运链依赖本机代理**(10808)——代理宕时卷内无候选、驾1 白等。已验证手动桥接后驾1 摄入 4 个过闸门候选并入 frontier(12→16)。耐久修复：① daemon 摄入成功后**消费即删** `candidates.json`（候选只处理一次，防每轮重复跑 48×40 surrogate，`ffde0c3`）；② 看门狗代理须常驻(建议 Windows 服务)，否则搬运链在代理宕窗口失效。
- **诚实提醒**：4 个过闸门候选是在 48 个里按 shuffle p<0.05 单点判定（未跨 48 做 FDR 多重校正），约 2.4 个假阳性属随机预期；它们是「存活提案种子」而非「已确认结构」。最终裁决仍归 firewall + walk-forward #41 确认闸门。勿因 frontier 增多误判发现结构。
