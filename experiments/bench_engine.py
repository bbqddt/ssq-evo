# -*- coding: utf-8 -*-
"""引擎算力/速度基准 + 进化方向自检（受 __main__ 保护，进程池 spawn 安全）。
用法: python bench_engine.py
"""
import os, time
import numpy as np
import data as D
import engine_core as E

MASTER = os.environ.get("MASTER", "D:/ssq_evo_data/ssq_master.csv")


def _depth_of_comp(cp, d=0):
    if not isinstance(cp, dict):
        return d
    b = cp.get("b")
    if isinstance(b, dict):
        return _depth_of_comp(b, d + 1)
    return d


def max_comp_depth(leaderboard):
    mx = 0
    n_comp = 0
    for e in leaderboard.values():
        if e.get("sig") == "comp":
            n_comp += 1
            cp = (e.get("params") or {}).get("_comp")
            if cp:
                mx = max(mx, _depth_of_comp(cp) + 1)  # gen = 深度+1
    return n_comp, mx


def run_once(n_workers, ep=4, pop=16, kl=12, kh=5):
    master = D.load_master(MASTER)
    reds, blues, issues = D.to_arrays(master)
    rng = np.random.default_rng(20260813 + len(reds) + n_workers)
    t0 = time.time()
    evo = E.Evolution(reds, blues, rng, k_light=kl, k_heavy=kh,
                      epochs=ep, pop=pop, n_workers=n_workers)
    lb, evs = evo.run()
    dt = time.time() - t0
    best = sorted(evs, key=lambda e: e["p_raw"])[0] if evs else None
    n_comp, mx_gen = max_comp_depth(lb)
    print("[bench] n_workers=%-2d  用时 %7.1fs  评估 %4d  唯一 %4d  "
          "comp数=%d 最大gen=%d  best_p=%.4g %s/%s"
          % (n_workers, dt, len(evs), len(lb), n_comp, mx_gen,
             best["p_raw"] if best else float("nan"),
             best["sig"] if best else "-", best["test"] if best else "-"),
          flush=True)
    return dt, len(lb), n_comp, mx_gen


if __name__ == "__main__":
    print("[bench] N=3495 期", flush=True)
    t1, _, c1, g1 = run_once(1)
    t8, _, c8, g8 = run_once(8)
    print("[bench] 进程池加速比 ≈ %.1fx" % (t1 / t8), flush=True)
    print("[bench] comp 候选: 串行=%d 进程池=%d | 最大代数 gen: 串行=%d 进程池=%d"
          % (c1, c8, g1, g8), flush=True)
