# -*- coding: utf-8 -*-
"""changepoint_evolve.py —— 非平稳结构突变（变点）检测。
四引擎一致 null 是基于"全历史平稳"假设。若开奖机制在某历史时段发生过
短暂结构变化（非平稳），全历史平均会把它稀释掉。本脚本用滑动窗口探测：
"是否存在某段历史，其局部可预测性显著 >随机"。

方法（诚实，不泄漏，不放松闸门）：
  对每个候选窗口 [s, e)（长度 W）：
    1) 用 [s, e) 前序训练拟合岭回归（红球序列 K 滞后）；
    2) 在窗口内做严格前序 OOS 评分，得局部 z；
    3) 同窗口随机 surrogate（标签打乱）得 surrogate_z 作对照；
    4) 仅当 局部 z > 2 且 surrogate_z < 2 → 标记"疑似可预测窗口"。
  全历史滑动扫描，输出所有通过闸门的窗口；若无任何窗口通过 → 坐实"连局部都无结构"。

依赖：仅 numpy。
"""
import os
# 限制 BLAS/OpenMP 线程为单线程：沙箱环境下多线程 numpy.linalg 会原生崩溃（exit1 无 traceback）。
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
import csv, math, argparse
import numpy as np

RED_N, RED_PICK = 33, 6
HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, "D:/ssq_evo_data/ssq_master.csv")
SNAP = os.path.join(HERE, "data/ssq_history.csv")


def load_draws(path=None):
    src = path or (MASTER if os.path.exists(MASTER) else SNAP)
    rows = []
    with open(src, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                reds = sorted(int(r["r%d" % i]) for i in range(1, 7))
                blue = int(r["b"])
                int(r["issue"])
            except Exception:
                continue
            rows.append({"issue": r["issue"], "reds": reds, "blue": blue})
    rows.sort(key=lambda d: d["issue"])
    return rows


def onehot(reds):
    v = np.zeros(RED_N)
    for n in reds:
        v[n - 1] = 1.0
    return v


def build_XY(draws, K):
    X, Y, tgt = [], [], []
    for i in range(K, len(draws)):
        x = np.concatenate([onehot(draws[i - K + k]["reds"]) for k in range(K)])
        X.append(x); Y.append(onehot(draws[i]["reds"])); tgt.append(draws[i]["reds"])
    return np.array(X), np.array(Y), tgt


def fit_ridge(X, Y, lam=1.0):
    XtX = X.T @ X + lam * np.eye(X.shape[1])
    return np.linalg.solve(XtX, X.T @ Y)


def predict_top6(W, x):
    logits = x @ W
    return sorted(range(1, RED_N + 1), key=lambda n: (-logits[n - 1], n))[:RED_PICK]


def red_hit_in_window(draws, K, lam, seg_start, seg_end, fit_back):
    """锚定 OOS（与 seq_evolve 同语义，已验证稳定）：在 [seg_start-fit_back, seg_start)
    一次性拟合 W，对 [seg_start, seg_end) 纯滚动预测。窗口内不重拟合 → 无泄漏且省算。"""
    X_all, Y_all, tgt = build_XY(draws, K)
    lo = max(K, seg_start - fit_back)
    if seg_start - lo < 30:
        return np.array([])
    W = fit_ridge(X_all[lo - K:seg_start - K], Y_all[lo - K:seg_start - K], lam)
    hits = []
    for i in range(seg_start, seg_end):
        pred = predict_top6(W, X_all[i - K])
        hits.append(len(set(pred) & set(tgt[i - K])))
    return np.array(hits)


def z_vs_random(hits):
    n = len(hits)
    if n < 30:
        return 0.0, 0.0
    mean = np.mean(hits)
    p = RED_PICK / RED_N
    var = RED_PICK * p * (1 - p) * (RED_N - RED_PICK) / (RED_N - 1)
    se = math.sqrt(var / n)
    return mean, (mean - RED_PICK * p) / se


def surrogate_z_in_window(draws, K, lam, seg_start, seg_end, fit_back):
    X_all, Y_all, tgt = build_XY(draws, K)
    rng = np.random.default_rng(2026)
    Ys = Y_all.copy(); rng.shuffle(Ys)
    lo = max(K, seg_start - fit_back)
    if seg_start - lo < 30:
        return 0.0
    W = fit_ridge(X_all[lo - K:seg_start - K], Ys[lo - K:seg_start - K], lam)
    hits = []
    for i in range(seg_start, seg_end):
        pred = predict_top6(W, X_all[i - K])
        hits.append(len(set(pred) & set(tgt[i - K])))
    return z_vs_random(np.array(hits))[1]


def scan(draws, K=10, lam=1.0, W=300, step=100, fit_back=500, z_thresh=2.0):
    """滑动窗口扫描全历史，返回通过闸门的窗口列表。
    含多重比较校正：扫描 n 个窗口，Bonferroni 校正后阈值 = z_{1-α/n}（α=0.05）。
    单窗口 z>2 不算数，必须过校正阈值才声称局部结构。"""
    N = len(draws)
    hits_windows = []
    n_win = 0
    all_z = []
    for s in range(K + fit_back, N - W, step):
        e = s + W
        h = red_hit_in_window(draws, K, lam, s, e, fit_back)
        m, z = z_vs_random(h)
        sz = surrogate_z_in_window(draws, K, lam, s, e, fit_back)
        n_win += 1
        all_z.append((s, e, m, z, sz))
    # Bonferroni 校正阈值（α=0.05, 双侧）：Φ^{-1}(1 - 0.05/(2n))
    crit = _bonferroni_crit(n_win)
    for s, e, m, z, sz in all_z:
        if z > crit and sz < z_thresh:
            hits_windows.append((s, e, m, z, sz, crit))
    return n_win, crit, hits_windows


def _norm_ppf(p):
    """标准正态分位数（Acklam 近似）。"""
    if p <= 0.0:
        return -1e10
    if p >= 1.0:
        return 1e10
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p <= phigh:
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _bonferroni_crit(n):
    """α=0.05 双侧，n 次比较的 Bonferroni 校正临界 z。"""
    return _norm_ppf(1 - 0.05 / (2 * max(n, 1)))


def erfcinv(x):
    # 备用（避免复数），实际用 _norm_ppf
    return 0.0


def run(K=10, lam=1.0, W=300, step=100, fit_back=500):
    draws = load_draws()
    N = len(draws)
    print("[cp] 样本 %d 期, K=%d, W=%d, step=%d, fit_back=%d" % (N, K, W, step, fit_back))
    n_win, crit, wins = scan(draws, K, lam, W, step, fit_back)
    print("[cp] 扫描窗口数=%d, Bonferroni 校正临界 z=%.3f (α=0.05 双侧)" % (n_win, crit))
    if not wins:
        print("[cp] >>> 无任何窗口通过校正闸门 → 连局部都无稳定结构，坐实全 null（含多重比较校正）")
    else:
        print("[cp] >>> 通过校正闸门窗口（段, 命中均值, 局部z, surrogate_z, 临界）：")
        for s, e, m, z, sz, c in wins:
            print("       [%d-%d) mean=%.3f z=%.3f sur=%.3f (需>%.3f)" % (s, e, m, z, sz, c))
    return {"n_windows": n_win, "crit": crit, "passed": len(wins), "windows": wins}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    ap.add_argument("--K", type=int, default=10)
    ap.add_argument("--W", type=int, default=300)
    ap.add_argument("--step", type=int, default=100)
    ap.add_argument("--fit_back", type=int, default=500)
    ap.add_argument("--data", type=str, default=None)
    args = ap.parse_args()
    global MASTER
    if args.data:
        MASTER = args.data
    if args.cmd == "run":
        run(K=args.K, lam=1.0, W=args.W, step=args.step, fit_back=args.fit_back)
    else:
        print("unknown cmd", args.cmd)


if __name__ == "__main__":
    main()
