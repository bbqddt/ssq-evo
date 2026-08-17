# -*- coding: utf-8 -*-
"""速度基准：分别用 1 进程 与 全核 跑一轮演化，对比耗时。只读数据，不写。
用法: python benchmark_speed.py
"""
import os, time
import numpy as np
import data as D
import engine_core as E

MASTER = os.environ.get("MASTER", "D:/ssq_evo_data/ssq_master.csv")
master = D.load_master(MASTER)
reds, blues, issues = D.to_arrays(master)
N = len(reds)
print(f"[bench] N={N} 期, 末期 {issues[-1]}", flush=True)

EP, POP, KL, KH = 5, 18, 18, 6

def run_once(n_workers):
    rng = np.random.default_rng(20260813 + N)
    t0 = time.time()
    evo = E.Evolution(reds, blues, rng, k_light=KL, k_heavy=KH,
                      epochs=EP, pop=POP, n_workers=n_workers)
    lb, evs = evo.run()
    dt = time.time() - t0
    best = sorted(evs, key=lambda e: e["p_raw"])[0] if evs else None
    print(f"[bench] n_workers={n_workers:>2}  用时 {dt:7.1f}s  评估 {len(evs):>3} 次  "
          f"唯一 {len(lb):>3}  best_p={best['p_raw']:.4g} {best['sig']}/{best['test']}",
          flush=True)
    return dt

print("[bench] === 串行 (1 进程) ===", flush=True)
t1 = run_once(1)
print("[bench] === 全核并行 ===", flush=True)
t8 = run_once(8)
print(f"[bench] 加速比 ≈ {t1 / t8:.1f}x", flush=True)
