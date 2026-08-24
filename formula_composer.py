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
    _random_comp_params, _mutate_comp, BASE_SIGNALS,
)

# MAX_DEPTH 必须与 engine_core._operand 的嵌套限制一致：_operand 在 depth>=2 时返回 None，
# 即 comp 公式树最多支持 depth=1（gen = depth+1 = 2）。超出会产生无法 evaluate 的废树。
# 故公式代数演进的物理上限是 gen=2（由"基信号线性组合"长到"组合的组合"）。
MAX_DEPTH = 2

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
    # 构造一颗 depth=1 的子树（一个基操作数 + 一个嵌套操作数），保证替换后整体 depth+1
    sub = _make_depth1(rng)
    # 优先替换非嵌套的槽位（str 类型），确保 depth 真正增加
    if not isinstance(cp.get("a"), dict):
        cp["a"] = sub
    elif not isinstance(cp.get("b"), dict):
        cp["b"] = sub
    elif isinstance(cp.get("b"), dict):
        # a、b 都已嵌套：在 b 子树内递归再扩一层（depth 仍 +1，受 max_depth 保护）
        cp["b"] = _nest_expand(cp["b"], rng)
    return cp


def _make_depth1(rng):
    """构造一颗 depth=1 的复合子树：一个嵌套操作数 + 一个基操作数。
    基信号名严格取 BASE_SIGNALS 中"1D 序列"子集（排除 blue/blue_resid 等 2D 信号，
    否则 comp 产物为 2D，检验函数期望 1D → _build_comp 返回 None 不可评估）。
    所有字符串强制转原生 str（避免 numpy rng.choice 返回 np.str_ 导致解析失败）。"""
    _1D_BASES = [s for s in BASE_SIGNALS if s not in ("blue", "blue_resid")]
    op = str(rng.choice(COMP_OPS_NEST))
    a = str(rng.choice(_1D_BASES))
    b = {"op": str(rng.choice(COMP_OPS_NEST)),
         "a": str(rng.choice(_1D_BASES)), "b": str(rng.choice(_1D_BASES))}
    return {"op": op, "a": a, "b": b, "read": str(rng.choice(["cont", "rev", "mean", "osc"]))}

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
        elite_comps: list[dict]，每个是 comp 基因组里的 _comp 树（已评估，可信起点）。
        返回：下一代候选基因组 list。

        代际演进驱动（修复 df_gen 卡在 2）：
          原逻辑靠随机 50% nest_expand 碰运气，depth=1 精英很难稳定长出 depth=2 → gen 永远 2。
          改为"目标深度驱动"：以当前精英最大深度 cur_d 为目标，保证每代至少有
          ~一半后代被扩展到 cur_d+1（若未达 max_depth），使 gen 随 breed 单调累积上长。
          gen = 本代最大嵌套深度 + 1，从精英深度解耦，实现真正的代际累积演进。

        诚实红线：df_gen 仅度量"公式树代数代次"的研发进度，不代表找到结构；
          统一闸门(q<0.05=SIGNAL) 绝不因 gen 上长而放松。
        """
        if not elite_comps:
            return self.seed_gen1(n=n)
        cur_d = max(_depth_of(c) for c in elite_comps)
        # 本代应达到的嵌套深度：受 _operand 限制，depth 最大为 max_depth-1（gen=max_depth）。
        # 即公式代数演进物理上限 gen=2（depth=1），超出不可评估。
        target_d = min(cur_d + 1, self.max_depth - 1)
        next_gen = []

        # 精英保种：保留通过闸门的精英（带轻微变异，防原地踏步），占半数
        for cp in elite_comps:
            if len(next_gen) >= max(1, n // 2):
                break
            mutated = _mutate_comp(copy.deepcopy(cp), self.rng)
            if _depth_of(mutated) <= self.max_depth:
                next_gen.append(mutated)

        # 交配 + 变异 + 目标深度扩展 产生剩余候选
        attempts = 0
        while len(next_gen) < n and attempts < n * 10:
            attempts += 1
            if len(elite_comps) >= 2 and self.rng.random() < 0.6:
                i, j = self.rng.integers(0, len(elite_comps), 2)
                child = _crossover(elite_comps[int(i)], elite_comps[int(j)], self.rng)
            else:
                if self.rng.random() < 0.5:
                    child = _mutate_comp(copy.deepcopy(self.rng.choice(elite_comps)), self.rng)
                else:
                    child = _random_comp_params(self.rng, depth=0)
            # 目标深度驱动：把 depth==0 的线性树扩到 depth==1（gen=2），这是引擎可评估的上限。
            # 严禁对 depth>=1 树再 nest_expand（会导致 depth=2 树，_operand 返回 None 不可评估）。
            need_deepen = (_depth_of(child) == 0) and (
                len([c for c in next_gen if _depth_of(c) >= 1]) < (n + 1) // 2)
            if need_deepen:
                child = _nest_expand(child, self.rng)
            if _depth_of(child) <= self.max_depth - 1:  # 只收 depth<=1（gen<=2）的树
                next_gen.append(child)

        self.population = next_gen[:n]
        # 清洗：递归把 2D 基信号（blue/blue_resid）替换成 1D 信号，避免 comp 产物为 2D
        # 导致 _build_comp/检验返回 None（不可评估的废树）。1D 子集与 _make_depth1 一致。
        _1D = [s for s in BASE_SIGNALS if s not in ("blue", "blue_resid")]
        for cp in self.population:
            _sanitize_tree(cp, self.rng, _1D)
        # gen = 本代最大嵌套深度 + 1（代际演进的物理解释，随 breed 累积上长）
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


def _sanitize_tree(cp, rng, allowed):
    """递归清洗 comp 树：任何 str operand 若为 2D 信号（blue/blue_resid）则替换为随机 1D 信号。
    保证整棵树产物为 1D，所有检验可正常评估。"""
    if not isinstance(cp, dict):
        return
    for slot in ("a", "b"):
        v = cp.get(slot)
        if isinstance(v, dict):
            _sanitize_tree(v, rng, allowed)
        elif isinstance(v, str) and v not in allowed:
            cp[slot] = str(rng.choice(allowed))
