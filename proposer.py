# -*- coding: utf-8 -*-
"""
proposer.py —— 可插拔提案者（为将来智能模块预留边界清晰的插槽）
===============================================================
提案者 = 只负责"生成候选假设"，绝不裁决、绝不看确认段/实盘段。
所有候选都必须经 firewall 的随机重放 + 审计账本 + #41 闸门，且晋级须人类签字。

设计（对齐防火墙四机制 + 红队 + 否决权）：
  - FormulaSpec : 去叙事化的结构化候选（sig/test/params/source/rationale_hash）。
                  将来 LLM 提的"我捕捉到规律…"叙事直接丢弃，闸门只认数字。
  - Proposer(ABC): propose(discovery_reds, discovery_blues, rng) -> list[FormulaSpec]。
                   签名强制只收发现段数组；它拿不到全量/确认段（物理隔离）。
  - GAProposer   : 包装 engine_core.Evolution，但只喂发现段（数据隔离）。
  - LLMProposer  : 预留插槽（默认 disabled）。将来接 LLM 端点或本助手扮演；
                   输出同样收敛为 FormulaSpec，并强制先过随机重放。
  - run_proposers: 编排——收集候选 → 随机重放 → 审计账本 → 返回"待闸候选"（绝不自动晋级）。

注意：本模块不直接 import 重型依赖（engine_core 在 GAProposer 内惰性 import），
保证防火墙层轻量、可单独测试。全部产物落在 D:\\ssq_evo，不写 C 盘。
"""
import hashlib
import numpy as np
from abc import ABC, abstractmethod

import firewall as FW


class FormulaSpec:
    """去叙事化候选：一个数字化的 (信号, 检验, 参数) 三元组 + 来源 + 叙事指纹(用于查重/红队)。"""

    __slots__ = ("sig", "test", "params", "source", "rationale_hash")

    def __init__(self, sig, test, params, source, rationale=""):
        self.sig = sig
        self.test = test
        self.params = params or {}
        self.source = source            # "GA" | "LLM" | "manual"
        self.rationale_hash = (hashlib.sha256(rationale.encode("utf-8")).hexdigest()[:16]
                               if rationale else None)

    def genome(self):
        return {"sig": self.sig, "test": self.test, "params": self.params}

    def __repr__(self):
        return "FormulaSpec(sig=%s,test=%s,source=%s)" % (self.sig, self.test, self.source)


class Proposer(ABC):
    """提案者基类：只许读发现段，只许生成候选，不许裁决/晋级。"""

    name = "base"

    @abstractmethod
    def propose(self, discovery_reds, discovery_blues, rng):
        """返回 list[FormulaSpec]。discovery_* 已是发现段（前 discovery_frac）。"""
        raise NotImplementedError


class GAProposer(Proposer):
    """遗传算法提案者：包装 engine_core.Evolution，但只喂发现段 → 数据隔离。"""

    name = "GA"

    def __init__(self, k_light=25, k_heavy=10, epochs=6, pop=24, elites=None,
                 frontier=None, eval_cache=None, top_k=8):
        self.k_light = k_light
        self.k_heavy = k_heavy
        self.epochs = epochs
        self.pop = pop
        self.elites = elites
        self.frontier = frontier
        self.eval_cache = eval_cache
        self.top_k = top_k

    def propose(self, discovery_reds, discovery_blues, rng):
        import engine_core as E
        evo = E.Evolution(discovery_reds, discovery_blues, rng,
                          k_light=self.k_light, k_heavy=self.k_heavy,
                          epochs=self.epochs, pop=self.pop,
                          elites=self.elites, frontier=self.frontier,
                          eval_cache=self.eval_cache)
        leaderboard, _ = evo.run()
        items = sorted(leaderboard.values(), key=lambda e: e.get("p_raw", 1.0))[:self.top_k]
        return [FormulaSpec(sig=e.get("sig"), test=e.get("test"), params=e.get("params"),
                            source="GA", rationale="ga_evolution_topk") for e in items]


class LLMProposer(Proposer):
    """预留：智能模块提案者（默认 disabled）。
    将来接 LLM 端点或本助手扮演；输出同样收敛为 FormulaSpec，并强制先过随机重放。
    绝不在此做"叙事欺诈"——任何自然语言解释被丢弃，只留 numeric spec。"""

    name = "LLM"
    ENABLED = False   # 默认关闭：防火墙先焊死，再放智能模块进来

    def __init__(self, endpoint=None):
        self.endpoint = endpoint  # 未来 LLM 端点；None=由本助手在会话内扮演

    def propose(self, discovery_reds, discovery_blues, rng):
        if not self.ENABLED:
            # 默认不触发：避免 null 域里无监管地放智能模块进来
            return []
        # 将来实现：调用 endpoint 生成假设 → 解析为 FormulaSpec 列表。
        # 约束：只许读 discovery_reds/blues 摘要；输出去叙事化；随后必须由 run_proposers 过随机重放。
        raise NotImplementedError("LLMProposer 尚未接入端点；请先焊死防火墙并显式 enable。")


def run_proposers(proposers, reds, blues, rng, discovery_frac=0.7, seed=20260815):
    """编排：发现段隔离 → 各提案者生成 → 随机重放 → 审计账本 → 待闸候选。
    返回 list[FormulaSpec]（全部已通过随机重放；随机数据上也 SURVIVOR 的直接丢弃）。
    绝不晋级生产；晋级须 firewall.promote(..., human_signoff=True)。"""
    disc_r, disc_b, conf_r, conf_b = FW.discovery_split(reds, blues, discovery_frac)
    disc_fp = FW.discovery_fingerprint(disc_r, disc_b)
    n_dropped = 0
    pending = []
    for p in proposers:
        specs = p.propose(disc_r, disc_b, rng)
        for s in specs:
            label, passed = FW.random_replay_check(
                s.sig, s.test, disc_r.shape[0], seed=seed + (hash(s.sig) % 1000))
            FW.record_candidate(s.genome(), source=s.source, disc_fp=disc_fp,
                                 seed=seed, random_replay_label=label,
                                 random_replay_passed=passed)
            if not passed:
                # 构造伪结构：随机数据上也幸存 → 直接丢弃，绝不进入候选池
                n_dropped += 1
                continue
            pending.append(s)
    return pending, n_dropped


if __name__ == "__main__":
    print("proposer 模块自检：GAProposer 可用，LLMProposer 默认关闭(ENABLED=%s)。"
          % LLMProposer.ENABLED)
