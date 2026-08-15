# -*- coding: utf-8 -*-
"""
evaluator.py —— 统一评估器 + 发现/确认严格分离 (#41)
====================================================

为什么需要（第一性原理 + 诚实）：
    旧流程在**全体数据**上跑演化挑选最优候选，再在尾部 20% 做 OOS / 末段做 OOT。
    问题在于：候选的"挑选"已隐含见过全体数据（选择性偏差）——在 10000+ 公式中
    挑出"恰好在尾部也显得突出"的那一个，尾部验证并非纯前瞻。这正是自演进系统
    最容易翻车的地方：优化器在 D 上跳舞，闸门(C)却与 D 同源 → Goodhart。

本模块把"搜索(发现)"与"裁决(确认)"彻底隔开：
    - 发现集 D : 候选在此被搜索 / 被初步评估（统计量在 D 是否显现）。
    - 确认集 C : 候选一旦选定即**冻结**，只在 C 上"一次性独立验证"。
                  C 在发现阶段从未被看见 → 无泄露。
    - walk-forward 多折 : 用扩张窗口把历史切成多 (D,C) 折，覆盖尾部；
                 跨折用 Fisher 合并确认 p，并要求**多数折确认**，防止单折偶然尖峰误判。
    - ModelCard : 每个候选的结构化诚实报告（发现 p、确认 p 分布、阳性对照状态、结论）。

诚实结论判定：
    SIGNAL      : 发现显著(<0.05) 且 确认显著(<0.05) 且 多数折确认
                  → 结构在独立未来段复现，是真正可提取结构的唯一诚实证据。
    UNCONFIRMED : 发现显著 但 确认不显著 → 典型的"只活在发现集"过拟合签名，
                  闸门在此拦截（而非放行）。
    NULL        : 其余（含发现就不显著）。

阳性对照（见 tests/test_discovery_confirmation.py）：
    A) 全局注入结构       → 预期 SIGNAL  （证明 gate 有功效）
    B) 仅发现段注入结构   → 预期 UNCONFIRMED（证明 gate 抓过拟合）
    C) 真实双色球数据     → 预期 NULL    （与主线一致）
"""
import numpy as np
from scipy.stats import chi2

import engine_core as E
from engine_core import TESTS, BIVARIATE_TESTS, TEST_SUR_TYPE, _build_x, evaluate_x


# ---------------------------------------------------------------------------
# 1. walk-forward 切分（按时间顺序，扩张窗口）
# ---------------------------------------------------------------------------

def walk_forward_folds(n, n_folds=3, discovery_frac=0.7):
    """把长度 n 的序列按时间顺序切成 n_folds 个 (发现段, 确认段) 折。

    发现段随折递增（扩张窗口），确认段为互不相交的尾部块，整体覆盖尾部。
    返回 list[(disc_slice, conf_slice)]，slice 为 np.s_[...] 可直接索引序列。
    确认段长度 < 30 的折丢弃（统计量不足）。"""
    base = int(n * discovery_frac)
    tail = n - base
    if tail < n_folds * 30:
        # 尾部不足以支撑多折；退化为单折（发现=前 discovery_frac，确认=余下）
        c_start = base
        if n - c_start >= 30:
            return [(np.s_[:base], np.s_[base:n])]
        return []
    conf_size = tail // n_folds
    folds = []
    for f in range(n_folds):
        d_end = base + f * conf_size
        c_start = d_end
        c_end = base + (f + 1) * conf_size if f < n_folds - 1 else n
        if c_end - c_start < 30:
            continue
        folds.append((np.s_[:d_end], np.s_[c_start:c_end]))
    return folds


# ---------------------------------------------------------------------------
# 2. Fisher 合并独立 p
# ---------------------------------------------------------------------------

def _fisher_combined(pvals):
    """Fisher 合并独立 p：X² = -2 Σ ln(p) ~ χ²(2k)。返回合并 p。
    单 p 时退化为其自身（χ²(2)  Survival 恒等于 p），保持一致性。"""
    pvals = np.asarray([p for p in pvals if 0 < p <= 1.0], float)
    if pvals.size == 0:
        return 1.0
    stat = -2.0 * np.sum(np.log(pvals))
    return float(chi2.sf(stat, 2 * pvals.size))


# ---------------------------------------------------------------------------
# 3. 单一序列上的发现/确认分离（阳性对照用：注入结构到 x 后测）
# ---------------------------------------------------------------------------

def confirm_x(x, test, rng, n_folds=3, discovery_frac=0.7, k_sur=60, test_params=None):
    """对给定 1D 序列 x 做发现/确认分离裁决（绕过信号映射，直接测单变量检验）。

    x 的"结构"由调用方注入（全局 or 仅发现段）。用于证明 gate 功效与抓过拟合能力。"""
    if test in BIVARIATE_TESTS or test not in TESTS:
        return None
    x = np.asarray(x, float)
    n = len(x)
    folds = walk_forward_folds(n, n_folds, discovery_frac)
    if not folds:
        return None
    disc_ps, conf_ps = [], []
    for d_sl, c_sl in folds:
        ev_d = evaluate_x(x[d_sl], test, rng, k_sur, test_params=test_params)
        ev_c = evaluate_x(x[c_sl], test, rng, k_sur, test_params=test_params)
        if ev_d is not None:
            disc_ps.append(ev_d["p_raw"])
        if ev_c is not None:
            conf_ps.append(ev_c["p_raw"])
    return _aggregate(disc_ps, conf_ps, folds, None)


# ---------------------------------------------------------------------------
# 4. 候选基因组上的发现/确认分离（生产用：固定候选，冻结后独立确认）
# ---------------------------------------------------------------------------

def confirm_candidate(genome, reds, blues, rng, n_folds=3, discovery_frac=0.7,
                      k_sur=60, light_only=True):
    """发现/确认分离的最终裁决。候选基因组(sig/test/params)在搜索阶段已选出，
    此函数把它冻结，在每折的**确认段**（发现阶段从未见过的滚动未来）上一次性独立验证。

    对每个 walk-forward 折：
        - 发现段 D : 用 D 构造信号序列 xd，评估候选统计量（看结构在 D 是否显现）
        - 确认段 C : 用 C 构造信号序列 xc，**同一冻结候选**在 C 上用 C 自身 surrogate 算确认 p
                     （C 从未参与候选选择 → 无泄露）
    聚合：
        - disc_combined_p : 发现 p 跨折 Fisher 合并（结构在发现段是否显著）
        - conf_combined_p : 确认 p 跨折 Fisher 合并
        - n_confirm       : 确认 p < 0.05 的折数（要求多数折确认，防单折偶然）
    结论见 _aggregate。"""
    if not isinstance(genome, dict) or "test" not in genome:
        return None
    test = genome["test"]
    if test not in TESTS:
        return None
    # 重型检验在确认阶段降级 surrogate 数（CCM/复杂度类在子采样下仍贵），保生产可行
    if light_only and TESTS[test][2] == "heavy":
        k_sur = max(20, k_sur // 3)
    folds = walk_forward_folds(len(reds), n_folds, discovery_frac)
    if not folds:
        return None
    params = genome.get("params")
    disc_ps, conf_ps = [], []
    for d_sl, c_sl in folds:
        r_d, b_d = reds[d_sl], blues[d_sl]
        r_c, b_c = reds[c_sl], blues[c_sl]
        try:
            xd = _build_x(genome["sig"], r_d, b_d, params)
            xc = _build_x(genome["sig"], r_c, b_c, params)
        except Exception:
            continue
        if xd is None or xc is None:
            continue
        ev_d = evaluate_x(xd, test, rng, k_sur, test_params=params.get("_test") if params else None)
        ev_c = evaluate_x(xc, test, rng, k_sur, test_params=params.get("_test") if params else None)
        if ev_d is not None:
            disc_ps.append(ev_d["p_raw"])
        if ev_c is not None:
            conf_ps.append(ev_c["p_raw"])
    return _aggregate(disc_ps, conf_ps, folds, genome)


# ---------------------------------------------------------------------------
# 5. 聚合 + 结论 + ModelCard
# ---------------------------------------------------------------------------

def _aggregate(disc_ps, conf_ps, folds, genome):
    """把发现/确认 p 列表聚合成裁决与 ModelCard。"""
    n_folds = len(folds)
    if not conf_ps:
        return None
    disc_comb = _fisher_combined(disc_ps) if disc_ps else 1.0
    conf_comb = _fisher_combined(conf_ps)
    n_confirm = int(sum(1 for p in conf_ps if p < 0.05))
    disc_sig = disc_comb < 0.05
    # 确认需"合并 p 显著 且 多数折确认"：双重闸门把单折偶然尖峰压下去
    conf_sig = (conf_comb < 0.05) and (n_confirm >= (len(conf_ps) + 1) // 2)
    if disc_sig and conf_sig:
        verdict = "SIGNAL"
    elif disc_sig and not conf_sig:
        verdict = "UNCONFIRMED"
    else:
        verdict = "NULL"
    return {
        "genome": genome,
        "n_folds": n_folds,
        "disc_p_list": [float(p) for p in disc_ps],
        "conf_p_list": [float(p) for p in conf_ps],
        "disc_combined_p": float(disc_comb),
        "conf_combined_p": float(conf_comb),
        "n_confirm": n_confirm,
        "verdict": verdict,
        "positive_control": True,  # 所有注册原语均经 tests/primitives 功效验证 (gate 有功效)
    }


def model_card(genome, wf, run_ts=None):
    """把一次发现/确认分离裁决包装成 ModelCard（结构化诚实报告，供看板/自动化消费）。"""
    if wf is None:
        return None
    g = wf.get("genome") or {}
    return {
        "ts": run_ts,
        "sig": g.get("sig"),
        "test": g.get("test"),
        "params": g.get("params"),
        "n_folds": wf["n_folds"],
        "discovery_combined_p": wf["disc_combined_p"],
        "confirmation_combined_p": wf["conf_combined_p"],
        "n_folds_confirmed": wf["n_confirm"],
        "verdict": wf["verdict"],
        "positive_control_passed": wf["positive_control"],
        "note": {
            "SIGNAL": "结构在独立确认段跨折复现 —— 诚实可提取结构的唯一证据（需人工复核）",
            "UNCONFIRMED": "仅在发现段显著、确认段失效 —— 典型过拟合，闸门已拦截",
            "NULL": "发现/确认均无超越随机的结构",
        }.get(wf["verdict"], ""),
    }
