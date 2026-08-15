"""
tests/nonstationarity_diag.py — 非平稳检测诊断 + 功效阳性对照

真实数据应 (理想情况下) NULL；注入已知漂移/动量后应被检出，证明 gate 有功效。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import data
import nonstationarity as NS


def load():
    m = data.load_master("D:/ssq_evo_data/ssq_master.csv")
    reds, blues, _ = data.to_arrays(m)
    return reds, blues


def summarize(name, res):
    print(f"\n=== {name} ===")
    print(f"  verdict      : {res['verdict']}")
    print(f"  N            : {res['N']}  k_sur={res['k_sur']}")
    print(f"  显著漂移球   : {res['n_sig_drift']}")
    print(f"  显著动量球   : {res['n_sig_mom']}")
    bd = res["best_drift"]
    print(f"  最显著漂移   : {bd[0]}{bd[1]} drift={bd[2]:+.4f} q={res['best_q_drift']:.4g}")
    bm = res["best_mom"]
    print(f"  最显著动量   : {bm[0]}{bm[1]} ac1={bm[4]:+.4f} q={res['best_q_mom']:.4g}")


def main():
    reds, blues = load()
    rng = np.random.default_rng(20260815)

    print("#" * 60)
    print("# 1) 真实数据漂移扫描")
    print("#" * 60)
    res = NS.ball_drift_scan(reds, blues, rng, k_sur=300)
    summarize("真实数据 ball_drift_scan", res)

    print("\n" + "#" * 60)
    print("# 2) 真实数据 walk-forward 近期热门策略验证")
    print("#" * 60)
    wf = NS.walk_forward_validate(reds, blues, rng, train_n=300, step=5)
    print(f"  策略命中率   : {wf['hit_rate']:.4f} (红球命中数/6)")
    print(f"  随机基线率   : {wf['random_rate']:.4f}")
    print(f"  surrogate均值: {wf['sur_mean']:.4f}")
    print(f"  p_random     : {wf['p_random']:.4f}")
    print(f"  高于随机?    : {wf['above_random']}")
    print(f"  样本窗口数   : {wf['n']}")

    print("\n" + "#" * 60)
    print("# 3) 滑动窗口局部结构扫描 (方向2)")
    print("#" * 60)
    rw = NS.rolling_window_scan(reds, blues, rng, window=200, step=50, k_sur=80)
    print(f"  窗口数       : {rw['n_windows']}")
    print(f"  显著窗口数   : {rw['n_sig_windows']}")
    print(f"  显著占比     : {rw['frac_sig']:.2%} (偶然期望 ~{rw['expected_frac']:.0%})")
    print(f"  verdict      : {rw['verdict']}")

    print("\n" + "#" * 60)
    print("# 4) 功效阳性对照：注入已知漂移/动量，应被检出")
    print("#" * 60)
    N = len(reds)
    # 复制真实红球集，但给 ball=7 注入"后期偏热"漂移
    reds_d = reds.copy()
    blues_d = blues.copy()
    # 在后期 40% 期数里，强制把 ball 7 塞进红球集（制造人为漂移）
    inject_start = int(N * 0.6)
    for t in range(inject_start, N):
        if 7 not in reds_d[t]:
            # 替换最后一个球为 7（保持 6 个不重复）
            reds_d[t][-1] = 7
    res_d = NS.ball_drift_scan(reds_d, blues_d, rng, k_sur=300)
    bd = res_d["best_drift"]
    print(f"  注入漂移后最显著: {bd[0]}{bd[1]} drift={bd[2]:+.4f} q={res_d['best_q_drift']:.4g}")
    print(f"  注入漂移 n_sig_drift = {res_d['n_sig_drift']} (应>0 证明 gate 有功效)")

    # 注入动量：让 ball=7 在相邻期高度相关
    reds_m = reds.copy()
    blues_m = blues.copy()
    for t in range(1, N):
        if 7 in reds_m[t - 1] and 7 not in reds_m[t]:
            reds_m[t][-1] = 7
    res_m = NS.ball_drift_scan(reds_m, blues_m, rng, k_sur=300)
    bm = res_m["best_mom"]
    print(f"  注入动量后最显著: {bm[0]}{bm[1]} ac1={bm[4]:+.4f} q={res_m['best_q_mom']:.4g}")
    print(f"  注入动量 n_sig_mom = {res_m['n_sig_mom']} (应>0 证明 gate 有功效)")

    print("\n# 诊断完成。")


if __name__ == "__main__":
    main()
