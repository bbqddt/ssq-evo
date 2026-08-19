# -*- coding: utf-8 -*-
"""
tests/test_integration_diff_formula.py —— #39→#50 集成测试
====================================================
验证可微 Formula 作为「额外候选源」正确接入 run_cycle 的统一诚信闸门池：
  1) 默认关闭：run_diff_formula_candidates 返回 (0,0,[])，不向池中添加任何候选。
  2) 开启：候选被评估、打 diff_formula 标记、按 genome_key 去重后并入 all_evals。
  3) 诚实：真实(双色球 null)数据上，所有入池的可微候选经 #41 发现/确认分离闸门
     均不得出 SIGNAL（闸门拦截过拟合，与演化候选完全相同的待遇）。
  4) 配置接线：engine.yaml 的 diff_formula.enabled 正确落入 cfg。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import engine_core as E
import evaluator as EV
import run_cycle as RC
import data as D


def _real_data():
    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    reds, blues, _ = D.to_arrays(m)
    return reds, blues


def test_disabled_is_noop():
    """1) 默认关闭：不向池中加候选。"""
    reds, blues = _real_data()
    rng = np.random.default_rng(1)
    cfg = {"diff_formula_enabled": False, "k_light": 20}
    seen = set()
    evals = []
    g, a, recs = RC.run_diff_formula_candidates(reds, blues, rng, cfg, seen, evals)
    assert (g, a) == (0, 0), "关闭时应为 (0,0)"
    assert recs == [], "关闭时不应有公式记录"
    assert len(evals) == 0, "关闭时不应添加候选"
    print("  [整合1] 默认关闭 = no-op: PASS")


def test_enabled_adds_and_dedup():
    """2) 开启：候选入池、打标记、去重。"""
    reds, blues = _real_data()
    rng = np.random.default_rng(2)
    cfg = {"diff_formula_enabled": True, "diff_formula_candidates": 4,
           "k_light": 20, "diff_formula_k_sur": 20, "diff_formula_n_steps": 6,
           "seed": 20260813}
    seen = set()
    evals = []
    g, a, recs = RC.run_diff_formula_candidates(reds, blues, rng, cfg, seen, evals)
    assert g == 4, "应生成 4 个候选"
    assert a > 0, "应有候选入池"
    assert len(recs) == 4, "应有 4 条公式记录"
    assert all(e.get("diff_formula") is True for e in evals), "入池候选应带 diff_formula 标记"
    # 去重：再次调用（同 seed）不应重复添加已见 genome_key
    seen2 = set(seen)
    evals2 = list(evals)
    g2, a2, _ = RC.run_diff_formula_candidates(reds, blues, rng, cfg, seen2, evals2)
    assert a2 == 0, "已见 genome_key 不应重复入池"
    print(f"  [整合2] 开启: 生成 {g}, 首次入池 {a}, 二次去重入池 {a2}: PASS")


def test_honesty_no_signal_on_real_data():
    """3) 真实 null 数据：入池可微候选不得骗过 #41 闸门产生 SIGNAL。"""
    reds, blues = _real_data()
    rng = np.random.default_rng(3)
    cfg = {"diff_formula_enabled": True, "diff_formula_candidates": 4,
           "k_light": 20, "diff_formula_k_sur": 20, "diff_formula_n_steps": 6,
           "seed": 20260813, "wf_n_folds": 3, "wf_disc_frac": 0.7}
    seen = set()
    evals = []
    RC.run_diff_formula_candidates(reds, blues, rng, cfg, seen, evals)
    n_signal = 0
    for e in evals:
        genome = {"sig": e["sig"], "test": e["test"], "params": e.get("params")}
        wf = EV.confirm_candidate(genome, reds, blues, np.random.default_rng(3),
                                  n_folds=cfg["wf_n_folds"],
                                  discovery_frac=cfg["wf_disc_frac"], k_sur=20)
        if wf and wf["verdict"] == "SIGNAL":
            n_signal += 1
    print(f"  [整合3] 真实数据可微候选 {len(evals)} 个, #41 闸门 SIGNAL 数={n_signal}")
    assert n_signal == 0, "真实 null 数据上可微候选不应骗过确认闸门产生 SIGNAL"
    print("  [整合3] 真实数据无 SIGNAL（闸门拦截过拟合）: PASS")


def test_config_wiring():
    """4) engine.yaml 的 diff_formula / redteam 段正确落入 cfg（接线正确性，不绑部署态值）。"""
    cfg = RC.load_cfg()
    assert "diff_formula_enabled" in cfg, "cfg 应含 diff_formula_enabled"
    assert isinstance(cfg["diff_formula_enabled"], bool), "diff_formula_enabled 应为 bool"
    assert cfg.get("diff_formula_candidates") == 6, "默认候选数应为 6"
    # 红队自审段接线
    assert "redteam_audit_enabled" in cfg, "cfg 应含 redteam_audit_enabled"
    assert isinstance(cfg["redteam_audit_enabled"], bool), "redteam_audit_enabled 应为 bool"
    assert cfg.get("redteam_out") == "audit", "redteam_out 默认应为 audit"
    print(f"  [整合4] 配置接线: diff_formula_enabled={cfg['diff_formula_enabled']}, "
          f"candidates={cfg.get('diff_formula_candidates')}, "
          f"redteam_audit_enabled={cfg.get('redteam_audit_enabled')}: PASS")


def main():
    print("== #39→#50 集成测试 ==")
    test_disabled_is_noop()
    test_enabled_adds_and_dedup()
    test_honesty_no_signal_on_real_data()
    test_config_wiring()
    print("== 全部 PASS ==")


if __name__ == "__main__":
    main()
