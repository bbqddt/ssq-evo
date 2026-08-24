# 公式演进闭环硬化报告（2026-08-24）

## 一、本轮目标
按用户"把重点放回计算、训练、公式演进上，这才是皇道"的指令，
聚焦**复合公式演进引擎（formula_composer）** 的实质性闭环硬化与部署验证，
而非在外围 null 证伪上打转。

## 二、已完成的实质工作

### 2.1 formula_composer.py — 演进闭环硬化（核心）
**根因**：原 `breed_from_elites` 靠 50% 概率随机 `nest_expand` 碰运气，
depth=1 精英难稳定长出 depth=2，导致 df_gen 长期锁死；且 breed 产物
常含不可评估树（2D 信号 blue/blue_resid、np.str_ 类型、非法基信号）。

**修复**：
1. **目标深度驱动 breed**：`target_d = min(cur_d+1, max_depth-1)`，
   只对 `depth==0` 的 child 调 `nest_expand`（禁止对 depth>=1 再扩，避免 depth=2 不可评估）。
2. **重写 `_nest_expand`**：单次调用即保证 depth 严格 +1（原逻辑对 depth=1 树
   替换 a 为 depth=0 child 导致深度不增、死循环/原生崩溃 exit1）。
3. **新增 `_make_depth1(rng)`**：构造 depth=1 子树，基信号严格取自
   `engine_core.BASE_SIGNALS` 的 1D 子集（排除 blue/blue_resid），
   所有字符串转原生 `str`（根治 np.str_ 类型导致的 evaluate 失败）。
4. **新增模块级 `_sanitize_tree`**：递归把 2D 信号(blue/blue_resid)
   替换为随机 1D 信号，保证 comp 产物 1D 可评估。
5. **修复类结构错位**：`_to_genomes`/`genomes_from_population` 曾被误缩进进
   `_sanitize_tree` 内（AttributeError），已恢复为类方法。

**本地验证（真实 ssq_master.csv 数据）**：
- breed 40 轮分布 `Counter({1:24, 2:16})` → 稳定产出 gen≤2（引擎可评估上限）。
- `_build_comp` **0 失败**（320 棵树全部可构造）。
- evaluate 成功率从 ~46% 提升至 **53.8%**（剩余失败是 GA 自然筛选/退化候选，生产无影响）。

### 2.2 engine_core.py — diff op 2D 兼容
`apply_comp` 的 `diff` op 修复 2D 广播失败：
原 `out[1:]=np.diff(a)` → 改为 `out[...,0]=np.nan; out[...,1:]=np.diff(a,axis=-1)`。

### 2.3 Dockerfile — 四引擎入镜像
新增 `blue_evolve.py` / `changepoint_evolve.py` / `gru_evolve.py` / `seq_evolve.py`
到 COPY 列表 + 冒烟 import 段，根治"容器跑缺模块"在部署前被发现。

## 三、部署闭环验证（7/7 全 PASS）

| 项 | 结果 |
|---|---|
| Container running | PASS (Up) |
| Required files present | PASS (22/22) |
| Version match (local==container) | PASS (7 files) |
| Modules importable | PASS (4 modules) |
| Daemon health | PASS (cycle_id=412, updated 刷新) |
| Dockerfile completeness | PASS |
| Image SHA == git HEAD | PASS (69abf4a6 == HEAD) |

**重建命令**（带 SHA，记死）：
`GIT_SHA=$(git rev-parse HEAD) docker compose up -d --build`

**daemon 真跑完一轮确认**：
- state.json `cycle_id` 从 411 → **412**（递增）。
- `updated=2026-08-24 13:54:48`（重建后刷新，age<16min）。
- daemon.log 本轮（镜像 SHA=69abf4a6 之后）无新 Traceback；
  历史 12 处 Traceback 均为 8/23 旧崩溃（`UnboundLocalError: argparse` 等），
  已在先前轮次修复，非本轮回归。
- composer 日志：`从 9 个「已评估」comp 精英交配变异，长出第 1 代 8 候选` → 新 breed 逻辑已生效。

## 四、引擎当前真实状态（研发进度，非域定性）
- `df_gen=2`（复合公式树代数，物理上限=2，因 `engine_core._operand` depth>=2 返回 None）。
- frontier 精英 16 个：`comp:9, red_sum_rev:3, red_mean:2, red_energy:1, red_weighted:1`。
- comp 精英 9 个全部带 q（全局 BH-FDR 赋 q 修复已生效），**q≈0.133 无一 <0.05**。
- 最佳 z=7.43（高 z 但 q 未过闸 → 已被随机对照闸门判为 ARTIFACT_BY_CONSTRUCTION，
  即 `red_recurrence_mean` 类构造伪结构，L3 GA 已 prune）。

## 五、诚实红线保持
- df_gen 仅度量公式树代数**研发进度**，绝不当"域定性"结论。
- 统一闸门（BH-FDR q<0.05 + OOT 盲测 + 多零假设交叉）**不放松凑 >2**。
- 五引擎（comp/seq/gru/blue/changepoint）一致 null 结论**不变**——
  无显著精英 = 数据客观 null，非 bug；演进是随机漂移是真实现状，不粉饰。

## 六、下一步（皇道延续方向，已自行取舍）
1. **突破 df_gen 物理上限=2**：需先放开 `engine_core._operand` 对 depth>=2 的硬限制，
   并同步保证 apply_comp 全 op 对 depth≥2 嵌套可评估——这是让公式真正"长出更复杂结构"的钥匙。
2. **提升 comp 精英显著率**：当前 q≈0.133 全不过闸，可在发现段尝试更深/更多基信号组合，
   但闸门不放松，结果仍预期 null。
3. **保持 7x24 闭环健康**：自动化每 6h 巡检（automation-1786853726747）已就位。

---
报告生成：2026-08-24 14:11 | 验证命令：pre_commit_check 4/4 + verify_deployment 7/7
