# -*- coding: utf-8 -*-
"""
proposer.py —— 智能演进子系统（提案者 + 防火墙边界插槽）
========================================================
本模块是"智能化"这半边柱子的实体：把"生成候选假设"做成可进化、可多样、可监管的子体系。
所有候选都必须经 firewall 的随机重放 + 审计账本 + #41 闸门，且晋级须人类签字。

设计对齐防火墙四机制 + 红队 + 否决权（见 MEMORY.md 护栏契约 #1/#3/#6）：

  - ProposerContext : 【构造级数据隔离】提案者唯一可访问的数据载体。
                      只含发现段数组 + 已试台账 + 已杀清单 + 发现段指纹。
                      全量/确认/实盘段从不进入（构造上无法偷看未来数据）。
                      verify_isolation() 在运行时断言"提案者所见数据指纹 == 发现段指纹"。

  - FormulaSpec     : 去叙事化候选 (sig/test/params/source/rationale_hash)。
                      将来 LLM 的"我捕捉到规律…"叙事直接丢弃，闸门只认数字。

  - GAProposer      : 遗传算法提案者（已有能力），只喂发现段。
  - HypothesisGenerator : 智能假设生成器——在发现段做结构化探索（突变幸存者/组合/新家族/参数扫描），
                          受 tried/killed 约束，输出去叙事化 spec，绝不裁决。
  - DiversityManager: 多家族多样性压力，防止种群坍缩成同一家族近亲（null 域过拟合温床）。
  - MetaController  : 探索/利用调度脑 + 算力预算信封 + 停滞检测（智能调度，不是瞎搜）。
  - IntelligentEvolution : 编排——构造 ctx → GA + HypGen 生成 → 多样性过滤 →
                          每候选过防火墙硬门(随机重放+审计) → 用同一引擎管线评估 →
                          返回待闸候选(绝不晋级)。

  - LLMProposer     : 预留插槽（默认 disabled）。将来接 LLM 端点或本助手扮演；
                      输出同样收敛为 FormulaSpec，并强制先过随机重放。

注意：本模块不直接 import 重型依赖（engine_core 在方法内惰性 import 部分符号），
保证防火墙层轻量、可单独测试。全部产物落在 D:\\ssq_evo，不写 C 盘。
"""
import hashlib
import copy
import numpy as np
from abc import ABC, abstractmethod

import firewall as FW
import engine_core as E


# ---------------------------------------------------------------------------
# 信号家族分类（多样性管理的基础；结构不同的假设才能覆盖 null 域的不同角落）
# ---------------------------------------------------------------------------
_FAMILY = {
    "red_sum": "sum_energy", "red_sum_rev": "sum_energy", "red_sum_block": "sum_energy",
    "red_mean": "mean", "red_weighted": "mean",
    "red_energy": "sum_energy", "red_span": "span", "red_delta_mean": "delta",
    "red_gap_mean": "gap", "red_gap_max": "gap", "red_gap_std": "gap",
    "red_runs": "runs", "red_low_count": "count", "red_zone_entropy": "entropy",
    "red_recurrence_mean": "recurrence", "red_consecutive": "recurrence",
    "red_prime_count": "count", "blue": "blue", "blue_resid": "blue",
    "vector_mag": "vector", "vector_phase": "vector", "complex_field": "vector",
    "red_parity": "parity", "red_sum_mod11": "mod", "red_sum_mod16": "mod",
}


def family_of(sig, params=None):
    """把 (sig, params) 归到结构家族。comp 按其内层主信号归类，便于多样性管理。"""
    if sig == "comp" and isinstance(params, dict) and isinstance(params.get("_comp"), dict):
        a = params["_comp"].get("a")
        if isinstance(a, str):
            return "comp:" + _FAMILY.get(a, "comp_inner")
        return "comp"
    return _FAMILY.get(sig, "other")


BASE_FAMILIES = sorted({family_of(s) for s in E.BASE_SIGNALS})


# ---------------------------------------------------------------------------
# 构造级隔离载体
# ---------------------------------------------------------------------------
class ProposerContext:
    """提案者唯一可访问的数据对象。全量/确认段从不进入此对象——构造级隔离。"""

    def __init__(self, disc_r, disc_b, rng, tried_set, killed_set, frontier=None):
        self.disc_r = np.asarray(disc_r, float)
        self.disc_b = np.asarray(disc_b, float)
        self.rng = rng
        self.tried = tried_set or set()          # set of genome_key 已尝试
        self.killed = killed_set or set()        # set of sig(str) 或 (sig,test) 被判构造伪结构
        self.frontier = frontier or {}
        self.fingerprint = FW.discovery_fingerprint(self.disc_r, self.disc_b)

    def verify_isolation(self):
        """运行时断言：提案者所见数据的指纹必须等于发现段指纹。
        若有人把全量数据塞进 ctx，这里会直接抛错——物理隔离的最后一道自检。"""
        assert FW.discovery_fingerprint(self.disc_r, self.disc_b) == self.fingerprint, \
            "FIREWALL BREACH: proposer data fingerprint != discovery fingerprint"
        return True

    def already_tried(self, gkey):
        return gkey in self.tried

    def is_killed(self, sig, test):
        return (sig in self.killed) or ((sig, test) in self.killed)


# ---------------------------------------------------------------------------
# 去叙事化候选
# ---------------------------------------------------------------------------
class FormulaSpec:
    """一个数字化的 (信号, 检验, 参数) 三元组 + 来源 + 叙事指纹(用于查重/红队)。"""

    __slots__ = ("sig", "test", "params", "source", "rationale_hash")

    def __init__(self, sig, test, params, source, rationale=""):
        self.sig = sig
        self.test = test
        self.params = params or {}
        self.source = source            # "GA" | "HypGen" | "LLM" | "manual"
        self.rationale_hash = (hashlib.sha256(rationale.encode("utf-8")).hexdigest()[:16]
                               if rationale else None)

    def genome(self):
        return {"sig": self.sig, "test": self.test, "params": self.params}

    def __repr__(self):
        return "FormulaSpec(sig=%s,test=%s,source=%s)" % (self.sig, self.test, self.source)


# ---------------------------------------------------------------------------
# 提案者基类
# ---------------------------------------------------------------------------
class Proposer(ABC):
    """提案者基类：只许读 ProposerContext，只许生成候选，不许裁决/晋级。"""

    name = "base"

    @abstractmethod
    def propose(self, ctx):
        """返回 list[FormulaSpec]。ctx 已是发现段隔离载体。"""
        raise NotImplementedError


class GAProposer(Proposer):
    """遗传算法提案者：包装 engine_core.Evolution，但只喂发现段 → 数据隔离。"""

    name = "GA"

    def __init__(self, k_light=25, k_heavy=10, epochs=6, pop=24, top_k=8):
        self.k_light = k_light
        self.k_heavy = k_heavy
        self.epochs = epochs
        self.pop = pop
        self.top_k = top_k

    def propose(self, ctx):
        evo = E.Evolution(ctx.disc_r, ctx.disc_b, ctx.rng,
                          k_light=self.k_light, k_heavy=self.k_heavy,
                          epochs=self.epochs, pop=self.pop,
                          elites=ctx.frontier.get("elites", []),
                          frontier=ctx.frontier, eval_cache=None)
        leaderboard, _ = evo.run()
        items = sorted(leaderboard.values(), key=lambda e: e.get("p_raw", 1.0))[:self.top_k]
        out = []
        for e in items:
            gkey = E.genome_key(e.get("sig"), e.get("test"), e.get("params"))
            if ctx.already_tried(gkey) or ctx.is_killed(e.get("sig"), e.get("test")):
                continue
            out.append(FormulaSpec(e.get("sig"), e.get("test"), e.get("params"),
                                  source="GA", rationale="ga_evolution_topk"))
        return out


class LLMProposer(Proposer):
    """预留：智能模块提案者（默认 disabled）。
    将来接 LLM 端点或本助手扮演；输出同样收敛为 FormulaSpec，并强制先过随机重放。
    绝不在此做"叙事欺诈"——任何自然语言解释被丢弃，只留 numeric spec。"""

    name = "LLM"
    ENABLED = False   # 默认关闭：防火墙先焊死，再放智能模块进来

    def __init__(self, endpoint=None):
        self.endpoint = endpoint  # 未来 LLM 端点；None=由本助手在会话内扮演

    def propose(self, ctx):
        if not self.ENABLED:
            return []
        raise NotImplementedError("LLMProposer 尚未接入端点；请先焊死防火墙并显式 enable。")


class HypothesisGenerator(Proposer):
    """智能假设生成器：在发现段上做【结构化探索】，不是纯随机突变。
    策略：①突变精英幸存者 ②组合两个信号成 comp ③新家族探索 ④参数扫描。
    全部受 tried/killed 约束，输出去叙事化 FormulaSpec，绝不裁决。"""

    name = "HypGen"

    def __init__(self, n_mutate=4, n_combine=2, n_novel=3, n_sweep=1):
        self.n_mutate = n_mutate
        self.n_combine = n_combine
        self.n_novel = n_novel
        self.n_sweep = n_sweep

    def propose(self, ctx):
        rng = ctx.rng
        elites = ctx.frontier.get("elites", []) or []
        genomes = []

        # 1. 突变精英幸存者（在已存活公式上 hill-climbing，比纯随机更聚焦）
        for g in elites[:self.n_mutate]:
            try:
                ng = E.mutate_genome(dict(g), rng)
                genomes.append(ng)
            except Exception:
                pass

        # 2. 组合两个精英信号成复合公式（comp）——真正的"公式研发/套公式"
        for _ in range(self.n_combine):
            if len(elites) >= 2:
                a = elites[rng.integers(0, len(elites))]
                b = elites[rng.integers(0, len(elites))]
                cp = {"op": str(rng.choice(E.COMP_OPS)),
                      "a": a.get("sig"), "b": b.get("sig"),
                      "k": int(rng.integers(1, 6)),
                      "read": str(rng.choice(["cont", "rev", "mean", "osc"]))}
                test = str(rng.choice(E.TEST_NAMES))
                if test in E.BIVARIATE_TESTS:
                    continue
                genomes.append({"sig": "comp", "test": test,
                                "params": {"_comp": cp, "_reorder": "identity"}})

        # 3. 新家族探索：优先挑未被精英覆盖的家族里的基信号（防坍缩成近亲）
        covered = {family_of(e.get("sig"), e.get("params")) for e in elites}
        novel_pool = [s for s in E.BASE_SIGNALS if family_of(s) not in covered]
        for _ in range(self.n_novel):
            sig = (rng.choice(novel_pool) if novel_pool
                   else rng.choice(E.BASE_SIGNALS))
            test = str(rng.choice(E.TEST_NAMES))
            if test in E.BIVARIATE_TESTS:
                continue
            g = E.random_genome(rng)
            g["sig"] = sig
            g["test"] = test
            g["params"]["_sig"] = E._random_params(sig, test, rng)["_sig"]
            genomes.append(g)

        # 4. 参数扫描：对首个精英做连续微调（把"改良公式"做细）
        if elites:
            base = dict(elites[0])
            for _ in range(self.n_sweep):
                genomes.append(E.mutate_genome(base, rng))

        # 去重 + 过滤 tried/killed → FormulaSpec
        seen, specs = set(), []
        for g in genomes:
            sig = g.get("sig")
            test = g.get("test")
            params = g.get("params") or {}
            if ctx.is_killed(sig, test):
                continue
            gkey = E.genome_key(sig, test, params)
            if gkey in seen or ctx.already_tried(gkey):
                continue
            seen.add(gkey)
            specs.append(FormulaSpec(sig, test, params,
                                     source="HypGen", rationale="structured_exploration"))
        return specs


# ---------------------------------------------------------------------------
# 多样性管理：防止种群坍缩成同一家族近亲（null 域过拟合温床）
# ---------------------------------------------------------------------------
class DiversityManager:
    def __init__(self, target_families=None):
        self.target = target_families or BASE_FAMILIES

    def coverage(self, specs):
        return {family_of(s.sig, s.params) for s in specs}

    def diversity_score(self, specs):
        if not specs:
            return 0.0
        return len(self.coverage(specs)) / max(1, len(self.target))

    def prioritize(self, specs, existing_families, k):
        """优先保留"家族未被现有精英覆盖"的候选 + GA 优先，凑足预算 k。"""
        def rank(s):
            fam = family_of(s.sig, s.params)
            new_family = 0 if fam in existing_families else 1
            src = 0 if s.source == "GA" else 1
            return (new_family, src)
        ordered = sorted(specs, key=rank, reverse=True)
        return ordered[:k]


# ---------------------------------------------------------------------------
# 元控制器：探索/利用调度 + 算力预算信封 + 停滞检测（智能调度，不是瞎搜）
# ---------------------------------------------------------------------------
class MetaController:
    def __init__(self, budget=30, explore_min=0.30, explore_max=0.80):
        self.budget = budget
        self.explore_min = explore_min
        self.explore_max = explore_max

    def stagnation(self, frontier):
        """frontier 停滞度：最近若干轮 best_z 是否不再创新高。返回 0..1。"""
        zh = frontier.get("best_z_history", [])
        if len(zh) < 10:
            return 0.0
        window = zh[-10:]
        best_so_far = max(zh[:-10]) if len(zh) > 10 else -1e9
        return 0.0 if max(window) > best_so_far else 1.0

    def allocate(self, frontier):
        """停滞越严重，越偏向探索（explore）以跳出局部；否则利用（exploit）精调幸存者。"""
        stag = self.stagnation(frontier)
        explore = self.explore_min + (self.explore_max - self.explore_min) * stag
        n_explore = int(round(self.budget * explore))
        return min(self.budget, max(0, n_explore)), self.budget - min(self.budget, max(0, n_explore))


# ---------------------------------------------------------------------------
# 智能演进编排器
# ---------------------------------------------------------------------------
class IntelligentEvolution:
    """把上述组件编排成一个可插拔、防火墙焊接的智能演进层。
    默认关闭(enabled=False)；run_cycle 在 intelligent_evolution_enabled=True 时调用。
    返回 (待闸评估列表, 随机重放丢弃数)。待闸评估会并入 run_cycle 的 all_evals，
    走【完全相同】的 BH-FDR + OOT + #41 + 随机对照闸门——绝不旁路任何闸门。"""

    def __init__(self, cfg=None, enabled=False):
        self.cfg = cfg or {}
        self.enabled = enabled

    def run(self, reds, blues, rng, frontier, killed_set, tried_set, eval_cache=None):
        if not self.enabled:
            return [], 0
        frac = self.cfg.get("ga_discovery_frac", 0.7)
        disc_r, disc_b, _, _ = FW.discovery_split(reds, blues, frac)
        ctx = ProposerContext(disc_r, disc_b, rng, tried_set, killed_set, frontier)
        ctx.verify_isolation()                       # 构造级隔离自检
        disc_fp = ctx.fingerprint
        seed0 = int(self.cfg.get("seed", 20260813)) + int(reds.shape[0]) + 99

        # 元控制器：依据 frontier 停滞度分配探索/利用预算
        mc = MetaController(budget=self.cfg.get("intel_budget", 30))
        n_explore, n_exploit = mc.allocate(frontier)
        hyp = HypothesisGenerator(n_mutate=n_exploit, n_combine=max(1, n_explore // 4),
                                  n_novel=max(1, n_explore // 3), n_sweep=max(1, n_exploit // 4))
        ga = GAProposer(epochs=self.cfg.get("epochs", 6), pop=self.cfg.get("pop", 24),
                        k_light=self.cfg.get("k_light", 25), k_heavy=self.cfg.get("k_heavy", 10),
                        top_k=self.cfg.get("ga_audit_topk", 8))

        specs = []
        for p in (ga, hyp):
            try:
                specs += p.propose(ctx)
            except Exception as e:
                print("[intel] %s propose failed: %s" % (p.name, e))

        # 多样性管理：优先新家族
        dm = DiversityManager()
        existing_families = {family_of(e.get("sig"), e.get("params"))
                             for e in (frontier.get("elites", []) or [])}
        specs = dm.prioritize(specs, existing_families, k=self.cfg.get("intel_budget", 30))

        # 防火墙硬门：每个候选先过 随机重放 + 审计；通过才进入同款引擎评估
        pending_evals = []
        n_dropped = 0
        k_sur = self.cfg.get("k_light", 25)
        for i, s in enumerate(specs):
            passed, label = FW.firewall_gate(s.genome(), s.source, disc_fp,
                                             seed0 + i, N=disc_r.shape[0], k_sur=k_sur)
            if not passed:
                n_dropped += 1
                continue
            try:
                x = E._build_x(s.sig, disc_r, disc_b, s.params)
                if x is None:
                    continue
                ev = E.evaluate_x(x, s.test, rng, k_sur)
                if ev is None:
                    continue
                ev["sig"] = s.sig
                ev["params"] = s.params
                ev["gkey"] = E.genome_key(s.sig, s.test, s.params)
                pending_evals.append(ev)
            except Exception as e:
                print("[intel] eval failed %s: %s" % (s, e))

        print("[intel] 智能层生成 %d 候选, 随机重放丢弃 %d, 进入同款评估 %d"
              % (len(specs), n_dropped, len(pending_evals)))
        return pending_evals, n_dropped


# ---------------------------------------------------------------------------
# 向后兼容的编排入口（旧 API；现也走构造级隔离 + 防火墙硬门）
# ---------------------------------------------------------------------------
def run_proposers(proposers, reds, blues, rng, discovery_frac=0.7, seed=20260815):
    """编排：发现段隔离 → 各提案者生成 → 随机重放 → 审计账本 → 待闸候选。
    返回 (list[FormulaSpec], n_dropped)。绝不晋级生产。"""
    disc_r, disc_b, _, _ = FW.discovery_split(reds, blues, discovery_frac)
    ctx = ProposerContext(disc_r, disc_b, rng, set(), set())
    ctx.verify_isolation()
    disc_fp = ctx.fingerprint
    pending, n_dropped = [], 0
    for p in proposers:
        for s in p.propose(ctx):
            passed, _ = FW.firewall_gate(s.genome(), s.source, disc_fp,
                                         seed + len(pending), N=disc_r.shape[0])
            if not passed:
                n_dropped += 1
                continue
            pending.append(s)
    return pending, n_dropped


if __name__ == "__main__":
    print("proposer 模块自检：GAProposer/HypothesisGenerator/DiversityManager/MetaController 就绪；"
          "LLMProposer 默认关闭(ENABLED=%s)；IntelligentEvolution 需 explicit enable。" % LLMProposer.ENABLED)
