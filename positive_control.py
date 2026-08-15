# -*- coding: utf-8 -*-
"""持续阳性对照：验证诚信闸门在已知结构下仍灵敏（闸门功率监控）。

背景：#41 统一闸门 + 多重零假设 + OOT + 发现/确认分离，其"灵敏度"（抓真结构的能力）
只在离线测试 test_diff_formula.py 里验过一次。生产环境里数据管线/参数若悄悄漂移，
闸门可能变弱而我们永远发现不了——这是最大的诚实盲区。

本模块每 K 轮由 run_cycle 调用：注入一个**已知**的 AR(1)@lag 结构，冻结候选后过
#41 confirm_candidate 闸门，断言它被判 SIGNAL。若判不出，说明闸门功率退化，
redteam_audit 会据此 ALERT。阳性对照与生产判别逻辑走完全相同的 evaluate_x / confirm_candidate。

绝不搜结构、绝不改结论，只做"闸门还灵不灵"的体检。
"""
import numpy as np
import evaluator as EV


def _inject_ar1(N, P=8, amp=0.9, seed=12345):
    """注入 AR(1)@lag P 自相关结构到 red_sum 信号（与测试 A 同构，已验证可检）。"""
    rng = np.random.default_rng(seed)
    x = np.zeros(N)
    for t in range(P, N):
        x[t] = amp * x[t - P] + rng.standard_normal()
    target = 100.0 + 15.0 * (x / (x.std() + 1e-9))
    reds = np.zeros((N, 6))
    for j in range(6):
        reds[:, j] = np.clip(np.round(target / 6.0 + (j - 2.5) +
                                     rng.standard_normal(N) * 0.3), 1, 33)
    blues = rng.integers(1, 17, size=(N, 1))
    return reds, blues


def run_positive_control(rng, n=1000, P=8, k_sur=30, n_folds=2,
                         discovery_frac=0.7, maxlag=None):
    """对注入的已知结构跑 #41 确认闸门，返回 {verified, verdict, conf_p, disc_p, n_confirm, note}。

    verified=True 表示闸门在已知结构上仍灵敏（健康）；False 表示闸门功率退化（告警）。
    """
    reds, blues = _inject_ar1(n, P=P)
    ml = maxlag if maxlag is not None else P + 4
    genome = {"sig": "red_sum", "test": "acf_max", "params": {"_test": {"maxlag": ml}}}
    wf = EV.confirm_candidate(genome, reds, blues, rng,
                              n_folds=n_folds, discovery_frac=discovery_frac,
                              k_sur=k_sur)
    if wf is None:
        return {"verified": False, "verdict": None, "conf_p": None,
                "disc_p": None, "n_confirm": None,
                "note": "gate returned None (unexpected)"}
    verified = (wf.get("verdict") == "SIGNAL")
    return {
        "verified": verified,
        "verdict": wf.get("verdict"),
        "conf_p": (round(wf.get("conf_combined_p"), 4)
                   if wf.get("conf_combined_p") is not None else None),
        "disc_p": (round(wf.get("disc_combined_p"), 4)
                   if wf.get("disc_combined_p") is not None else None),
        "n_confirm": wf.get("n_confirm"),
        "note": "injected AR(1)@lag%d, expect SIGNAL" % P,
    }
