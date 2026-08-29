"""新 OOT 闸门的阴性/阳性对照实验（宿主专用审计，不改生产状态）。

背景：engine_core.out_of_time 的旧零假设有两处缺陷：
  (1) 在**构造后序列**上做 shuffle，摧毁了变换自带的平滑/离散化结构；
  (2) 拿替代序列的预测去比**真实** actual（非自洽），替代命中率塌到 ~0.5。
二者叠加 ⇒ ewm 平滑 + rev 规则在纯随机开奖上稳定 hit≈0.85、p=0.005。

修复：零假设改到**原始数据层**（打乱期序后重走同一信号构造），并与各自 actual 自洽比对。

本脚本验证修复后的闸门同时满足：
  - 阴性对照：纯随机开奖的假显著率 ≈ 名义 5%（不再系统性假阳性）
  - 阳性对照：注入已知结构时能检出（闸门仍有功效，不是换个瞎闸门）
"""

import json
import sys

import numpy as np

import engine_core as E
import power_analysis as PA
import paths
import random_control_oot as RC

EV_A = {"sig": "comp", "test": "dfa_alpha",
        "params": {"_comp": {"op": "ewm", "a": "red_mean", "b": "red_energy",
                             "k": 5, "read": "rev"}},
        "tier": "light"}
EV_B = {"sig": "comp", "test": "fft_peak",
        "params": {"_comp": {"op": "diff", "a": "red_weighted", "b": "red_span",
                             "k": 3, "read": "osc"}},
        "tier": "light"}


def oot(ev, r, b, seed, k=100):
    rng = np.random.default_rng(seed)
    try:
        return E.out_of_time(ev, r, b, rng, train_frac=0.85, k_sur=k)
    except Exception as e:
        return {"_err": "%s: %s" % (type(e).__name__, e)}


def main(m_neg=12, k_sur=100):
    reds, blues = RC.load_real()
    n = len(reds)
    report = {"n": n, "k_sur": k_sur, "m_neg": m_neg, "cases": {}}
    print("N=%d  k_sur=%d  m_neg=%d" % (n, k_sur, m_neg))

    for name, ev in [("EV_A(ewm/rev/dfa_alpha)", EV_A),
                     ("EV_B(diff/osc/fft_peak)", EV_B)]:
        print("=" * 66)
        print(name)
        case = {"ev": ev}
        r = oot(ev, reds, blues, 4242, k_sur)
        if "_err" in r:
            print("  真实数据 ERR:", r["_err"])
            case["real_err"] = r["_err"]
            report["cases"][name] = case
            continue
        print("  真实数据 : hit=%.4f  sur=%.4f  p=%.4f  above=%s  rule=%s"
              % (r["hit_rate"], r["sur_mean"], r["p_random"],
                 r["above_random"], r["best_rule"]))
        case["real"] = {k: r.get(k) for k in
                        ("hit_rate", "sur_mean", "sur_std", "p_random",
                         "above_random", "n", "best_rule", "k_sur")}

        # 阴性对照：纯随机开奖
        ps, hits = [], []
        for m in range(m_neg):
            rng = np.random.default_rng(9000 + m)
            rr, bb = RC.gen_random_draws(n, rng)
            x = oot(ev, rr, bb, 7000 + m, k_sur)
            if "_err" in x or x is None:
                print("    [随机#%02d] ERR %s" % (m, (x or {}).get("_err")))
                continue
            ps.append(x["p_random"])
            hits.append(x["hit_rate"])
        ps = np.array(ps)
        hits = np.array(hits)
        fpr = float((ps < 0.05).mean()) if ps.size else float("nan")
        print("  阴性对照 : M=%d  hit均值=%.4f  FPR@0.05=%.0f%% (名义5%%)"
              % (ps.size, hits.mean() if hits.size else float("nan"), fpr * 100))
        case["neg"] = {"M": int(ps.size), "hit_mean": float(hits.mean()) if hits.size else None,
                       "hit_std": float(hits.std(ddof=0)) if hits.size > 1 else None,
                       "fpr": fpr, "ps": [round(float(v), 4) for v in ps]}

        # 阳性对照：注入与规则匹配的结构（lag-1 负自相关 ⇒ 反转规则所利用）
        pos = []
        for rho in (0.0, -0.5, -0.8):
            rr, bb = PA.inject_ar1(n, rho, lag=1, seed=31337)
            x = oot(ev, rr, bb, 8000 + int(abs(rho) * 100), k_sur)
            if "_err" in x or x is None:
                print("    阳性 rho=%+.2f -> ERR %s" % (rho, (x or {}).get("_err")))
                continue
            print("    阳性 rho=%+.2f : hit=%.4f  sur=%.4f  p=%.4f  above=%s"
                  % (rho, x["hit_rate"], x["sur_mean"], x["p_random"], x["above_random"]))
            pos.append({"rho": rho, "hit": x["hit_rate"], "sur": x["sur_mean"],
                        "p": x["p_random"], "above": x["above_random"]})
        case["pos"] = pos
        report["cases"][name] = case

    out = paths.p("audit", "gate_control_experiment.json")
    json.dump(report, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[gate_control] 结果已写: %s" % out)


if __name__ == "__main__":
    m = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    main(m_neg=m)
