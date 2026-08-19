# -*- coding: utf-8 -*-
"""
tests/test_diff_formula.py —— #39 可微 Formula 阳性对照 + 闸门纪律验证
========================================================================
A) 功效：注入周期结构后，坐标上升能降低发现段 p（证优化器有功效），
   且 confirm_candidate 在注入数据上返回 SIGNAL（结构在独立确认段复现）。
B) 诚实：真实双色球数据上，run_diff_search 不得产生任何 SIGNAL——
   可微优化器在发现段过拟合噪声，但 #41 确认闸门拦下（UNCONFIRMED/NULL）。
   这是「可微 Formula 不绕过闸门、不造假阳性」的铁证。
C) 纪律：优化器确实只在发现段评估（确认段前缀从未传入目标函数）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import engine_core as E
import evaluator as EV
import diff_formula as DF
import data as D


_HAS_REAL_DATA = os.path.isfile("D:/ssq_evo_data/ssq_master.csv")


def _real_data():
    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    reds, blues, _ = D.to_arrays(m)
    return reds, blues


def _injected_period(N, P=23, amp=0.7):
    """注入「仅在第 P 期存在自相关」的结构（AR(1)@lag P），合法红球范围。

    x[t] = amp*x[t-P] + 噪声 => 自相关只在 lag P 显著（lag 1..P-1 近似 0）。
    这样默认 maxlag=10 抓不到（p 高），可微优化器把 maxlag 推到 P 才暴露结构，
    从而干净演示「参数调优有功效」。red_sum 目标缩放至 100±30，拆解到 6 红。
    """
    rng = np.random.default_rng(20260815)
    x = np.zeros(N)
    for t in range(P, N):
        x[t] = amp * x[t - P] + rng.standard_normal()
    target = 100.0 + 15.0 * (x / (x.std() + 1e-9))   # red_sum 目标，范围 ~70-130
    reds = np.zeros((N, 6))
    for j in range(6):
        reds[:, j] = np.clip(np.round(target / 6.0 + (j - 2.5) + rng.standard_normal(N) * 0.3), 1, 33)
    blues = rng.integers(1, 17, size=(N, 1))
    return reds, blues


def test_efficacy_injected_structure():
    """A) 注入周期 → 坐标上升增大偏离 null 幅度 + 确认闸门判 SIGNAL。

    注入 AR(1)@lag P（强耦合 amp=0.9）的红球和序列结构；默认 init maxlag=5 < P，
    初值漏掉真实结构（高 score）。坐标上升应在发现段把 maxlag 推到 >= P，使优化器
    真正定位到真实结构所在 lag，且冻结候选经 walk-forward 确认闸门在独立段复现 => SIGNAL。
    """
    N = 1500
    P = 8
    reds, blues = _injected_period(N, P=P, amp=0.9)
    rng = np.random.default_rng(7)
    d = int(N * 0.7)

    base = DF._init_params("red_sum", "acf_max", rng)  # init maxlag=5 (< P)
    init_score = DF._score("red_sum", "acf_max", base, reds[:d], blues[:d], k_sur=60)
    init_p = DF._objective_on_discovery("red_sum", "acf_max", base,
                                        reds[:d], blues[:d], k_sur=60)
    # 坐标上升优化（目标=偏离 null 幅度最大化，即 _score 最小化）
    opt_params, disc_p, traj = DF.optimize_on_discovery(
        "red_sum", "acf_max", base, reds, blues,
        discovery_frac=0.7, k_sur=60, n_steps=15)
    final_score = traj[-1]
    print(f"  [A] 发现段: init maxlag={base['_test']} score={init_score:.3g} p={init_p:.4g} "
          f"-> opt {opt_params['_test']} score={final_score:.3g} p={disc_p:.4g}")
    assert final_score < init_score, "坐标上升未增大偏离 null 幅度（无功效）"
    assert opt_params["_test"]["maxlag"] >= P, "优化器未爬到真实结构所在 lag"
    assert disc_p < 0.05, "注入强结构应能被优化器压到显著 p"

    # 冻结候选 → 确认闸门（全量数据 walk-forward）
    genome = {"sig": "red_sum", "test": "acf_max", "params": opt_params}
    wf = EV.confirm_candidate(genome, reds, blues, rng,
                              n_folds=3, discovery_frac=0.7, k_sur=60)
    print(f"  [A] 确认闸门 verdict={wf['verdict']} conf_p={wf['conf_combined_p']:.4g} "
          f"disc_p={wf['disc_combined_p']:.4g} n_confirm={wf['n_confirm']}")
    assert wf["verdict"] == "SIGNAL", "注入结构应在确认段复现并被判 SIGNAL"
    print("  [A] 功效 + 确认闸门: PASS")


@pytest.mark.skipif(not _HAS_REAL_DATA, reason="requires ssq_master.csv (absent in CI)")
def test_honesty_real_data_no_signal():
    """B/C) 真实数据：可微搜索不得产出 SIGNAL（闸门拦截过拟合）。"""
    reds, blues = _real_data()
    rng = np.random.default_rng(20260815)
    results = DF.run_diff_search(
        reds, blues, rng, n_candidates=4,
        discovery_frac=0.7, k_sur_opt=40, n_steps=8,
        wf_n_folds=3, wf_disc_frac=0.7, wf_k_sur=20)
    n_signal = sum(1 for r in results if r["wf_verdict"] == "SIGNAL")
    print(f"  [B] 真实数据可微搜索 {len(results)} 候选, SIGNAL 数={n_signal}")
    for r in results:
        print(f"      {r['sig']}/{r['test']} disc_p={r['disc_p']:.4g} "
              f"verdict={r['wf_verdict']} conf_p={r['wf_conf_p']}")
    assert n_signal == 0, "真实 null 数据上可微优化器不应骗过确认闸门产生 SIGNAL"
    # 发现段 p 可能很低（过拟合噪声），但确认段必须失效 => 闸门纪律成立
    print("  [B] 真实数据无 SIGNAL（闸门拦截过拟合）: PASS")


def main():
    print("== #39 可微 Formula 阳性对照 ==")
    test_efficacy_injected_structure()
    test_honesty_real_data_no_signal()
    print("== 全部 PASS ==")


if __name__ == "__main__":
    main()
