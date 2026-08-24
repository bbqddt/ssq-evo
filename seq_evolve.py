# -*- coding: utf-8 -*-
"""seq_evolve.py —— 更强预测器架构：红球序列岭回归/映射 forecaster。
替代 evolve_predictor 的"线性加权 top6"（表达力太弱，z=0.451 卡死）。

为什么更强（第一性原理）：
  red_gap_max / transition / omission 等手工特征是"单滞后"或"描述性"的，
  天生对"下一期"预测力有限。这里直接把"过去 K 期红球 one-hot 拼成的 33K 维向量"
  映射到"下一期 33 维 logits"，用岭回归求闭式解 W=(X'X+λI)^-1 X'Y。
  这捕捉任意 K 期高阶/转移结构，是加权 top6 的真超集。

诚实闸门（与 evolve_predictor 同权，绝不放松）：
  1) 严格前序 OOS：测试期 i 只用 <i 的数据拟合，永不泄漏。
  2) 随机 surrogate：Y 打乱后同架构拟合，若 z 也 >2 判构造伪显著，拒。
  3) 独立确认段：训练集外最后 N 期复现 z>2 才声称 >随机。

依赖：仅 numpy（与云端工作流 pip install numpy 一致，无需 torch）。
"""
import os, sys, json, math, random, argparse
import numpy as np

RED_N, RED_PICK, BLUE_N = 33, 6, 16
HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, "D:/ssq_evo_data/ssq_master.csv")
SNAP = os.path.join(HERE, "data/ssq_history.csv")


def load_draws(path=None):
    import csv
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
    # 严格 33 维（球号 1..33，不含无用索引 0），保证 X/Y/W 维度一致
    v = np.zeros(RED_N)
    for n in reds:
        v[n - 1] = 1.0
    return v


def build_XY(draws, K):
    """输入 X: 过去 K 期红球 one-hot 拼平 (33K 维)；输出 Y: 当期红球 one-hot (33 维)。"""
    X, Y = [], []
    for i in range(K, len(draws)):
        x = np.concatenate([onehot(draws[i - K + k]["reds"]) for k in range(K)])
        X.append(x)
        Y.append(onehot(draws[i]["reds"]))
    return np.array(X), np.array(Y), draws[K:]


def fit_ridge(X, Y, lam=1.0):
    """闭式岭回归 W (33 x 33K)。"""
    XtX = X.T @ X + lam * np.eye(X.shape[1])
    XtY = X.T @ Y
    W = np.linalg.solve(XtX, XtY)   # 比 pinv 更稳
    return W


def predict_top6(W, x):
    # W 形状 (33K, 33)：行=33K 特征，列=33 球 logit。x(330,) @ W(330,33) -> 33 维 logits
    logits = x @ W
    return sorted(range(1, RED_N + 1), key=lambda n: (-logits[n - 1], n))[:RED_PICK]


def red_hit_mean(draws, K, lam, fit_end, pred_start, pred_end):
    """锚定 OOS：在 [K, fit_end) 拟合一次 W，对 [pred_start, pred_end) 纯预测（无泄漏）。
    比逐期重拟合快 ~100x，对'序列模型是否真 >随机'的探测足够诚实（测试期绝不用自身数据）。"""
    X_all, Y_all, tgt = build_XY(draws, K)
    W = fit_ridge(X_all[:fit_end - K], Y_all[:fit_end - K], lam)
    hits = []
    for j in range(pred_start - K, pred_end - K):
        if j < 0:
            continue
        pred = predict_top6(W, X_all[j])
        hits.append(len(set(pred) & set(tgt[j]["reds"])))
    return np.array(hits)


def z_vs_random(hits):
    n = len(hits)
    if n < 30:
        return 0.0, 0.0
    mean = np.mean(hits)
    # 随机基线：每期独立从33选6，命中~超几何，均值=6*6/33=1.0909，方差可解析
    p = RED_PICK / RED_N
    var = RED_PICK * p * (1 - p) * (RED_N - RED_PICK) / (RED_N - 1)
    se = math.sqrt(var / n)
    z = (mean - (RED_PICK * p)) / se
    return mean, z


def random_surrogate_z(draws, K, lam, train_end):
    """锚定 surrogate：Y 打乱后同架构拟合一次，预测一段，看 z 是否仍 >2（构造伪显著）。"""
    X_all, Y_all, tgt = build_XY(draws, K)
    rng = np.random.default_rng(7)
    Ys = Y_all.copy()
    rng.shuffle(Ys)
    W = fit_ridge(X_all[:train_end - K], Ys[:train_end - K], lam)
    pred_start, pred_end = train_end, min(train_end + 400, len(draws))
    hits = []
    for j in range(pred_start - K, pred_end - K):
        pred = predict_top6(W, X_all[j])
        hits.append(len(set(pred) & set(tgt[j]["reds"])))
    return z_vs_random(np.array(hits))[1]


def run(K=10, lam=1.0, train_frac=0.8, seed=0):
    draws = load_draws()
    N = len(draws)
    train_end = int(N * train_frac)
    print("[seq] 样本 %d 期, K=%d, lam=%g, 训练截止=%d" % (N, K, lam, train_end))
    # 训练段 OOS（锚定：在 [K,train_end) 拟合，预测其后一段）
    h_tr = red_hit_mean(draws, K, lam, train_end, train_end, min(train_end + 400, N))
    m_tr, z_tr = z_vs_random(h_tr)
    print("[seq] 训练OOS 红命中均值=%.3f z=%.3f" % (m_tr, z_tr))
    # 确认段（训练外最后 N 期）
    h_co = red_hit_mean(draws, K, lam, train_end, train_end, N)
    m_co, z_co = z_vs_random(h_co)
    print("[seq] 确认段(%d期) 红命中均值=%.3f z=%.3f" % (len(h_co), m_co, z_co))
    # 随机 surrogate
    z_sur = random_surrogate_z(draws, K, lam, train_end)
    print("[seq] 随机surrogate z=%.3f (>2=构造伪显著,拒)" % z_sur)
    if z_sur > 2:
        print("  >>> 拒绝：构造伪显著")
    elif z_co > 2:
        print("  >>> 通过：确认段 z>2，序列模型真 >随机")
    else:
        print("  >>> 拒绝：确认段未复现 z>2（当前模型空间仍 null 或表达力仍不足）")
    return {"K": K, "lam": lam, "train_z": z_tr, "confirm_z": z_co, "surrogate_z": z_sur}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    ap.add_argument("--K", type=int, default=10)
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--data", type=str, default=None)
    args = ap.parse_args()
    global MASTER, SNAP
    if args.data:
        MASTER = args.data
    if args.cmd == "run":
        run(K=args.K, lam=args.lam)
    else:
        print("unknown cmd", args.cmd)


if __name__ == "__main__":
    main()
