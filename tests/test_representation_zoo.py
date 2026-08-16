# -*- coding: utf-8 -*-
"""
tests/test_representation_zoo.py —— 扩轴 + 分层空模型 阳性对照
==========================================================
A) register() 把新信号注入 engine_core.SIGMAPS，驱动器能识别。
B) subset_marginal 摧毁「每期组合约束」（3 低 + 3 高球，边际频率不编码），
   permute_draws 保留每期内容 => 证明分层 null 能在「组合结构」与「时间序」两维度分别破坏结构。
C) 注入周期结构：shuffle（摧毁时间序）检出(p<0.05)、AAFT（保留谱/周期）不检出
   => 判 LINEAR_TIME_ARTIFACT，证明分层对比能识别「时间在做功」的 temptation。
这些阳性对照证明新增 machinery 有功效、且判别方向正确（不直接断言真实数据有结构）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import engine_core as E
import representation_zoo as RZ
import layered_null as LN
import run_axes as RA


def _inject_periodic(N, P=23, amp=0.7, seed=20260815):
    """注入周期结构到红球和序列（合法红球范围）。fft_peak 应检出周期。"""
    rng = np.random.default_rng(seed)
    t = np.arange(N)
    x = amp * np.sin(2 * np.pi * t / P) + rng.standard_normal(N) * 0.3
    target = 100.0 + 15.0 * (x / (x.std() + 1e-9))
    reds = np.zeros((N, 6), dtype=int)
    for j in range(6):
        reds[:, j] = np.clip(np.round(target / 6.0 + (j - 2.5) + rng.standard_normal(N) * 0.3), 1, 33).astype(int)
    blues = rng.integers(1, 17, size=(N,))
    return reds, blues


def test_register_adds_signals():
    """A) register 注入新信号且标入 SIGMAPS。"""
    RZ.register()
    for sig in ("red_sum_mod3", "red_sum_mod7", "red_sum_mod31", "red_prod_mod33",
                "red_pair_meandist", "red_centroid"):
        assert sig in E.SIGMAPS, "新信号未注册: %s" % sig
    assert "red_gap_mean" in E.SIGMAPS and "red_parity" in E.SIGMAPS
    print("  [A] register 注入 %d 个新信号: PASS" % len(RZ.NEW_SIGNALS))


def test_subset_marginal_destroys_composition():
    """B) subset_marginal 摧毁每期组合约束（3低+3高），permute_draws 保留每期内容。"""
    N = 500
    rng = np.random.default_rng(1)
    reds = np.zeros((N, 6), dtype=int)
    for i in range(N):
        low = rng.choice(np.arange(1, 17), size=3, replace=False)
        high = rng.choice(np.arange(18, 34), size=3, replace=False)
        reds[i] = np.sort(np.concatenate([low, high]))
    blues = rng.integers(1, 17, size=(N,))
    n_low = lambda row: int(np.sum((row >= 1) & (row <= 16)))
    frac_bal = np.mean([n_low(row) == 3 for row in reds])
    assert frac_bal == 1.0, "输入应全为 3低+3高"

    r_perm, _ = LN.permute_draws(reds, blues, rng)
    r_marg, b_marg = LN.subset_marginal(reds, blues, rng)

    assert np.mean([n_low(row) == 3 for row in r_perm]) == 1.0, "permute 应保留每期内容"
    frac_marg = np.mean([n_low(row) == 3 for row in r_marg])
    # 边际重抽下 #低 ~ Binomial(6,0.5)，恰为 3 的比例仅 ~0.31 => 组合约束被摧毁
    assert frac_marg < 0.6, "subset_marginal 应摧毁『3低+3高』组合约束 (frac=%.3f)" % frac_marg
    assert r_marg.shape == reds.shape and r_perm.shape == reds.shape
    assert r_marg.min() >= 1 and r_marg.max() <= 33
    assert b_marg.shape == blues.shape
    print("  [B] 3低+3高比例 input=1.0 permute=1.0 marginal=%.3f : PASS" % frac_marg)


def test_layered_null_detects_linear_time_artifact():
    """C) 注入周期：shuffle 检出、AAFT 不检出 => LINEAR_TIME_ARTIFACT。"""
    reds, blues = _inject_periodic(3000, P=23, amp=0.7, seed=20260815)
    rng = np.random.default_rng(7)
    rec = RA.label_axis("red_sum", ["fft_peak"], reds, blues, rng, k_sur=150)
    print("  [C] 周期注入: p_shuffle=%.4g p_aaft=%.4g p_marg=%.4g label=%s"
          % (rec["p_shuffle"], rec["p_aaft"], rec["p_marg"], rec["label"]))
    assert rec["p_shuffle"] is not None and rec["p_shuffle"] < 0.05, "shuffle 应检出周期（证明有功效）"
    assert rec["p_aaft"] > 0.5, "AAFT(保留谱)应不检出周期（p_aaft=%.4g）" % rec["p_aaft"]
    assert rec["label"] == "LINEAR_TIME_ARTIFACT", "周期结构应判 LINEAR_TIME_ARTIFACT"


def test_axes_on_fair_data_labels_valid():
    """冒烟：公平合成数据上，各轴 label_axis 只产出合法标签、无崩溃（不直接断言无结构）。"""
    N = 1200
    rng0 = np.random.default_rng(0)
    reds = np.sort(rng0.integers(1, 34, size=(N, 6)), axis=1)
    blues = rng0.integers(1, 17, size=(N,))
    rng = np.random.default_rng(123)
    labels = set()
    for ax in RZ.AXES:
        if ax["sig"] not in E.SIGMAPS:
            continue
        rec = RA.label_axis(ax["sig"], ax["tests"], reds, blues, rng, k_sur=40)
        labels.add(rec["label"])
        assert rec["p_shuffle"] is not None
    assert labels.issubset({"NULL", "SURVIVOR", "LINEAR_TIME_ARTIFACT"})
    print("  [smoke] fair 数据各轴标签集=%s : PASS" % labels)


def test_random_control_gate_flags_recurrence_artifact():
    """随机数据对照闸门必须把 red_recurrence_mean 的构造伪结构判为 ARTIFACT_BY_CONSTRUCTION。

    根因：sm_red_recurrence_mean 对首次出现的球用惩罚值 N 当回访期数，使序列开头出现巨大尖峰，
    该尖峰在纯随机数据上同样存在 => 随机对照下也 SURVIVOR => 证明显著源自构造而非彩票结构。
    """
    N = 2000
    reds, blues = RA.proper_random(N, np.random.default_rng(5))
    recs = RA.run(reds, blues, seed=5, k_sur=40)
    rec = next(r for r in recs if r["sig"] == "red_recurrence_mean")
    print("  [gate] red_recurrence_mean label=%s artifact_prone=%s"
          % (rec["label"], rec.get("artifact_prone")))
    assert rec.get("artifact_prone") is True
    assert rec["label"] == "ARTIFACT_BY_CONSTRUCTION"


def test_random_control_gate_keeps_real_structure():
    """对照闸门不应误杀真实结构：注入周期后，red_sum+fft_peak 在随机对照下 label!=SURVIVOR。"""
    ctrl = RA.random_control_label("red_sum", ["fft_peak"], 3000, seed=20260815, k_sur=60)
    print("  [gate] 周期轴随机对照 label=%s" % ctrl)
    assert ctrl != "SURVIVOR", "随机对照不应把周期结构误判为 SURVIVOR（闸门不会错杀真实结构）"


if __name__ == "__main__":
    test_register_adds_signals()
    test_subset_marginal_destroys_composition()
    test_layered_null_detects_linear_time_artifact()
    test_axes_on_fair_data_labels_valid()
    test_random_control_gate_flags_recurrence_artifact()
    test_random_control_gate_keeps_real_structure()
    print("== 全部 PASS ==")
