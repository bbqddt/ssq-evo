"""EV_A(ewm/rev) 的针对性阳性对照：注入**能穿过该变换**的结构。

上一轮 gate_control_experiment 用 lag-1 负自相关做阳性对照，EV_A 未检出
（hit 反而低于 rho=0）。原因不是闸门瞎，而是 EV_A 的信号是
`comp(op=ewm, k=5)` —— 5 期指数平滑会把 lag-1 的交替结构滤掉，
注入的结构根本没到达被评估的序列。

所以阳性对照必须注入**该变换通带内**的结构，才能检验闸门功效：
  - 长周期正弦（period=40，远大于平滑窗口 5）→ ewm(5) 后仍是相干振荡
  - AR(1)@lag8 / lag20（周期远大于平滑窗口）
判据：注入后 OOT p 应显著(<0.05)，且注入强度越大 p 越小（单调性）。
"""

import json

import numpy as np

import engine_core as E
import power_analysis as PA
import paths
import random_control_oot as RC

EV_A = {"sig": "comp", "test": "dfa_alpha",
        "params": {"_comp": {"op": "ewm", "a": "red_mean", "b": "red_energy",
                             "k": 5, "read": "rev"}},
        "tier": "light"}

K_SUR = 100


def oot(ev, r, b, seed, k=K_SUR):
    rng = np.random.default_rng(seed)
    try:
        return E.out_of_time(ev, r, b, rng, train_frac=0.85, k_sur=k)
    except Exception as e:
        return {"_err": "%s: %s" % (type(e).__name__, e)}


def main():
    reds, blues = RC.load_real()
    n = len(reds)
    out_rows = []
    print("EV_A = %s" % json.dumps(EV_A["params"], ensure_ascii=False))
    print("平滑窗口 k=5 ⇒ 注入结构周期须 >> 5 才能穿过变换\n")

    cases = [
        ("periodic period=40 amp=0.5", lambda: PA.inject_periodic(n, 0.5, period=40, seed=777)),
        ("periodic period=40 amp=1.0", lambda: PA.inject_periodic(n, 1.0, period=40, seed=777)),
        ("periodic period=40 amp=2.0", lambda: PA.inject_periodic(n, 2.0, period=40, seed=777)),
        ("AR(1) lag=8  rho=-0.5", lambda: PA.inject_ar1(n, -0.5, lag=8, seed=777)),
        ("AR(1) lag=8  rho=-0.8", lambda: PA.inject_ar1(n, -0.8, lag=8, seed=777)),
        ("AR(1) lag=20 rho=-0.8", lambda: PA.inject_ar1(n, -0.8, lag=20, seed=777)),
        ("对照 纯随机(无注入)", None),
    ]

    for i, (label, fn) in enumerate(cases):
        if fn is None:
            rng = np.random.default_rng(4242)
            rr, bb = RC.gen_random_draws(n, rng)
        else:
            rr, bb = fn()
        r = oot(EV_A, rr, bb, 5000 + i)
        if "_err" in r or r is None:
            print("  %-26s -> ERR %s" % (label, (r or {}).get("_err")))
            continue
        print("  %-26s hit=%.4f  sur=%.4f  p=%.4f  above=%s"
              % (label, r["hit_rate"], r["sur_mean"], r["p_random"], r["above_random"]))
        out_rows.append({"label": label, "hit": r["hit_rate"], "sur": r["sur_mean"],
                         "p": r["p_random"], "above": r["above_random"]})

    json.dump({"ev": EV_A, "k_sur": K_SUR, "rows": out_rows},
              open(paths.p("audit", "gate_control_ev_a.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print("\n已写: %s" % paths.p("audit", "gate_control_ev_a.json"))


if __name__ == "__main__":
    main()
