# -*- coding: utf-8 -*-
"""持续阳性对照（闸门功率监控）单测。

核心断言：注入已知 AR(1) 结构后，#41 确认闸门必须判 SIGNAL。
若此断言失败，说明闸门功率退化——这正是阳性对照要抓的回归。
"""
import os
import numpy as np
import positive_control as PC


def test_positive_control_detects_known_structure():
    """注入已知结构 -> 闸门判 SIGNAL（闸门仍有检出功效）。"""
    rng = np.random.default_rng(20260815)
    res = PC.run_positive_control(rng, n=1000, P=8, k_sur=30, n_folds=2)
    print(f"  [阳性对照] verdict={res['verdict']} conf_p={res['conf_p']} "
          f"disc_p={res['disc_p']} n_confirm={res['n_confirm']}")
    assert res["verified"] is True, (
        "阳性对照失败：已知结构未被闸门检出，闸门功率疑似退化 -> %s" % res)
    assert res["verdict"] == "SIGNAL"
    assert res["n_confirm"] is not None and res["n_confirm"] >= 1


def test_positive_control_schema():
    """返回 dict 含必要字段。"""
    rng = np.random.default_rng(1)
    res = PC.run_positive_control(rng, n=600, P=8, k_sur=20, n_folds=2)
    for k in ("verified", "verdict", "conf_p", "disc_p", "n_confirm", "note"):
        assert k in res, "positive_control 返回缺字段 %s" % k
