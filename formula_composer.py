# formula_composer.py
# ---------------------------------------------------------------------------
# 公式代数演进驱动器（修复 "df_gen 锁死" 根因）
#
# 根因：原 df_gen 字段被 #39 diff_formula.run_diff_search 用 len(recs) 错误标成
#       "每轮候选数"，且 GA/智能层从没真正去"长出第 7、8…代"复合公式——
#       因为 df_gen 语义被偷换成了"候选数"，组合公式树的代际演进从未被驱动。
#
# 本模块职责：维护一个"复合公式树种群"，代次 gen 从当前最高精英代数起算，
#   每轮用 engine_core 已有的 _random_comp_params / _mutate_comp 做
#   「交配(crossover) + 变异(mutation)」长出下一代候选，汇入现有统一闸门。
#   让 df_gen 真正等于组合公式树的代数代次（而非假 6）。
#
# 第一性原理 / 纯经典 / 不依赖 LLM。所有候选仍须过 BH-FDR + OOT + 随机对照闸门，
# 绝不绕过统一闸门（红线）。
# ---------------------------------------------------------------------------
import copy
import numpy as np

# 复用 engine_core 已有的复合公式构造零件
from engine_core import (
    COMP_OPS, COMP_UNARY, COMP_OPS_NEST,
    _random_comp_params, _mutate_comp,
)

MAX_DEPTH = 2  # 与 _operand 的 depth>=2 限制一致，防止退化

def _comp_genome(cp, test="mi_max"):
    """把复合公式树 cp 包装成 GA 基因组（sig='comp'）。"""
    return {"sig": "comp", "test": test, "params": {"_comp": copy.deepcopy(cp)}}

def _depth_of(cp, d=0):
    """计算复合公式树的实际嵌套深度（代数的物理解释）。"""
    if not isinstance(cp, dict):
        return d
    b = cp.get("b")
    if isinstance(b, dict):
        return _depth_of(b, d + 1)
    return d

def _crossover(a, b, rng):
    """交配：以一个子树为根，用另一棵的对应子树替换（保留结构多样性）。"""
    ca, cb = copy.deepcopy(a), copy.deepcopy(b)
    # 随机决定替换 a 的 b 子树 或 a 的根算子+操作数
    if isinstance(ca.get("b"), dict) and isinstance(cb.get("b"), dict) and rng.random() < 0.5:
        ca["b"] = cb["b"]
    else:
        ca["op"] = cb.get("op", ca["op"])
        if not isinstance(ca.get("b"), dict) and isinstance(cb.get("b"), dict):
            ca["b"] = cb["b"]
        elif isinstance(ca.get("b"), dict) and not isinstance(cb.get("b"), dict):
            pass  # 保持 ca 的嵌套
        else:
            ca["a"] = cb.get("a", ca["a"])
            if not isinstance(ca.get("b"), dict):
                ca["b"] = cb.get("b", ca["b"])
    ca["read"] = rng.choice(["cont", "rev", "mean", "osc"])
    return ca

def _nest_expand(cp, rng):
    """嵌套扩展：把一层线性组合(depth=0)升级为二层嵌套(depth=1)，
    即把某个基信号操作数替换成一个子复合表达式——这是代际演进的物理解释：
    公式从"两个基信号的组合"长成"组合的组合"。只在 depth<max_depth 时生效。"""
    if _depth_of(cp) >= MAX_DEPTH:
        return cp
    child = _random_comp_params(rng, depth=0)
    # 随机替换 a 或 b（若 b 已是嵌套则跳过，避免超过 max_depth）
    if rng.random() < 0.5 or isinstance(cp.get("b"), dict):
        if not isinstance(cp.get("a"), dict):
            cp["a"] = child
    else:
        if not isinstance(cp.get("b"), dict):
            cp["b"] = child
    return cp

class FormulaComposer:
    """复合公式树种群的代际驱动器。"""

    def __init__(self, rng, start_gen=1, max_depth=MAX_DEPTH, tests=("mi_max", "acf_max")):
        self.rng = rng
        self.gen = max(1, int(start_gen))      # 当前最高代数（物理解释：组合深度）
        self.max_depth = max_depth
        self.tests = list(tests)
        self.population = []                   # 当前代公式树列表（dict）

    def seed_gen1(self, n=6):
        """第 1 代：纯随机复合公式（不依赖任何精英，启动演进）。"""
        self.population = [_random_comp_params(self.rng, depth=0) for _ in range(n)]
        # gen = 本代最大嵌套深度 + 1
        self.gen = max(_depth_of(c) for c in self.population) + 1
        return self._to_genomes()

    def breed_from_elites(self, elite_comps, n=6):
        """从通过闸门的 comp 精英里交配+变异，长出下一代。
        elite_comps: list[dict]，每个是 comp 基因组里的 _comp 树（已通过闸门，可信起点）。
        返回：下一代候选基因组 list。

        代数(gen)物理解释 = 当前种群复合公式树的最大嵌套深度 + 1。
        当交配/变异产生嵌套(depth>=1)树时，gen 自动变为 2、3…，实现真正的代际演进。
        gen 完全由本代产物决定，与精英历史深度解耦（避免被 depth=0 精英拉回 1）。
        """
        next_gen = []

        # 精英保种：直接保留通过闸门的精英（带轻微变异，防原地踏步）
        for cp in elite_comps:
            if len(next_gen) >= n // 2:
                break
            mutated = _mutate_comp(copy.deepcopy(cp), self.rng)
            if _depth_of(mutated) <= self.max_depth:
                next_gen.append(mutated)

        # 交配 + 变异产生剩余候选
        attempts = 0
        while len(next_gen) < n and attempts < n * 10:
            attempts += 1
            if len(elite_comps) >= 2 and self.rng.random() < 0.6:
                i, j = self.rng.integers(0, len(elite_comps), 2)
                child = _crossover(elite_comps[int(i)], elite_comps[int(j)], self.rng)
            else:
                if elite_comps and self.rng.random() < 0.5:
                    child = _mutate_comp(copy.deepcopy(self.rng.choice(elite_comps)), self.rng)
                else:
                    child = _random_comp_params(self.rng, depth=0)
            # 代际演进驱动：约 50% 后代做嵌套扩展，让公式从"基信号组合"长成"组合的组合"，
            # df_gen 因此能稳定往上长（而非每轮停在第 1 代）。
            if _depth_of(child) < self.max_depth and self.rng.random() < 0.5:
                child = _nest_expand(child, self.rng)
            if _depth_of(child) <= self.max_depth:
                next_gen.append(child)

        self.population = next_gen[:n]
        # gen = 本代最大嵌套深度 + 1（代际演进的物理解释）
        if self.population:
            self.gen = max(_depth_of(c) for c in self.population) + 1
        else:
            self.gen = max(1, self.gen)
        return self._to_genomes()

    def _to_genomes(self):
        """把当前 population 转成 GA 基因组列表，test 在候选 tests 间轮换。"""
        out = []
        for i, cp in enumerate(self.population):
            test = self.tests[i % len(self.tests)]
            out.append(_comp_genome(cp, test))
        return out

    def genomes_from_population(self):
        """直接取当前种群作为候选（用于 run_cycle 每轮注入 GA）。"""
        return self._to_genomes()
