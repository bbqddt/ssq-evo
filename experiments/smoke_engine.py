"""smoke_engine.py — 引擎改造验证（速度 / 进化方向 / 闸门连通性）。

仅用于沙箱验证，不进生产。使用 `if __name__ == "__main__"` 守卫避免
Windows spawn 下子进程重跑顶层代码。
"""
import time
import numpy as np

import data as D
import engine_core as E
import formula_composer as FC
import cache as C


def _dfa_ref(x, scales=None):
    y = np.cumsum(x - x.mean())
    n = len(y)
    if scales is None:
        scales = np.unique(np.logspace(np.log10(10), np.log10(max(11, n // 4)), 20).astype(int))
    F, S = [], []
    for s in scales:
        if s >= n:
            break
        num = n // s
        if num < 4:
            continue
        rms = 0.0
        for i in range(num):
            seg = y[i * s:(i + 1) * s]
            t = np.arange(len(seg))
            p = np.polyfit(t, seg, 1)
            rms += np.sqrt(np.mean((seg - np.polyval(p, t)) ** 2))
        F.append(rms / num)
        S.append(s)
    if len(S) < 3:
        return np.nan
    return float(np.polyfit(np.log10(S), np.log10(F), 1)[0])


def test_dfa_equivalence():
    print("[1] dfa_alpha 向量化数值校验 ...")
    maxerr = 0.0
    rng = np.random.RandomState(11)
    for _ in range(8):
        x = rng.randn(2400).astype(float)
        o = _dfa_ref(x)
        nw = E.t_dfa_alpha(x)
        if np.isfinite(o) and np.isfinite(nw):
            maxerr = max(maxerr, abs(o - nw))
    print("    dfa_alpha 最大误差 = %.2e  %s" % (maxerr, "PASS" if maxerr < 1e-6 else "FAIL"))
    return maxerr < 1e-6


def test_depth_capability():
    """直接验证算子能产生深度>1的复合公式（GA 不爬深只是因为全 null 无梯度，非能力缺失）。"""
    print("[2] 复合公式深度能力验证（_random_comp_params 采样 400 次）...")
    rng = np.random.default_rng(7)
    depths = [FC._depth_of(E._random_comp_params(rng)) for _ in range(400)]
    mx = max(depths)
    frac_deep = sum(1 for d in depths if d >= 2) / len(depths)
    print("    最大深度(gen)=%d  深度>=2 占比=%.1f%%  (上限=gen %d)"
          % (mx, 100 * frac_deep, E._MAX_COMP_DEPTH + 1))
    return mx >= 2


def run_cycle(n_workers, pop=24, epochs=6):
    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    r, b, _ = D.to_arrays(m)
    rng = np.random.default_rng(20260826)
    t0 = time.time()
    evo = E.Evolution(r, b, rng, k_light=25, k_heavy=10, epochs=epochs, pop=pop, n_workers=n_workers)
    lb, all_evals = evo.run()
    dt = time.time() - t0
    comp_evals = [e for e in all_evals if e.get("sig") == "comp" and "_comp" in e.get("params", {})]
    depths = [FC._depth_of(e["params"]["_comp"]) for e in comp_evals]
    max_depth = max(depths) if depths else 0
    n_comp = len(comp_evals)
    n_unique = len({e["gkey"] for e in all_evals})
    has_p = any(np.isfinite(e.get("p_raw", np.nan)) for e in all_evals)
    print("    n_workers=%d  用时 %.1fs  唯一=%d  comp=%d  最大深度(gen)=%d  闸门连通=%s"
          % (n_workers, dt, n_unique, n_comp, max_depth, has_p))
    return dt, n_unique, n_comp, max_depth, has_p


def main():
    ok = test_dfa_equivalence()
    cap = test_depth_capability()
    print("[3] 引擎真跑（串行 vs 复用进程池，生产规模 pop=24 epochs=6）...")
    t1, u1, c1, d1, p1 = run_cycle(1)
    t8, u8, c8, d8, p8 = run_cycle(8)
    print("[4] 汇总")
    print("    串行=%.1fs  进程池=%.1fs  加速比=%.2fx" % (t1, t8, t1 / t8 if t8 > 0 else 0))
    print("    最大comp深度(gen): 串行=%d 进程池=%d" % (d1, d8))
    better = (t8 < t1 * 0.8) and cap and p1 and p8 and ok
    print("    结论: %s" % ("PASS — 提速+深度能力+闸门连通" if better else "需复核"))
    C.close_eval_pool()


if __name__ == "__main__":
    main()
