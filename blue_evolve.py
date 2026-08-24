# -*- coding: utf-8 -*-
"""blue_evolve.py —— 蓝球(16 选 1)单独建模。
红球 6/33 命中基线低、噪声大；蓝球仅 16 选 1，单球信号密度更高，
是更可能藏结构的维度。这里用"蓝球自身序列 + 红球聚合特征"建预测，
严格走紧鞘闸门（与 seq_evolve 同权，绝不放松凑 >2）。

特征族（解释性优先，不黑箱）：
  - blue_lag: 过去 K 期蓝球 one-hot (16K 维)
  - red_agg: 当期红球 sum/奇偶比/区间分布 等聚合 (少量)
  - 标签: 下一期蓝球 (16 维 one-hot)
用岭回归闭式解（无需 torch，云端 Actions 也能跑）。

诚实闸门：
  1) 严格前序 OOS：测试期只用 <i 拟合。
  2) 随机 surrogate：标签打乱同架构拟合，z>2 判构造伪显著 → 拒。
  3) 独立确认段：训练外最后 N 期复现 z>2 才声称 >随机。
"""
import os, csv, math, argparse
import numpy as np

RED_N, RED_PICK, BLUE_N = 33, 6, 16
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


def blue_onehot(b):
    v = np.zeros(BLUE_N)
    v[b - 1] = 1.0
    return v


def red_agg(reds):
    """红球聚合特征：和值/奇偶比/三段分布(1-11,12-22,23-33)。"""
    s = sum(reds)
    odd = sum(1 for x in reds if x % 2 == 1) / RED_PICK
    z1 = sum(1 for x in reds if 1 <= x <= 11) / RED_PICK
    z2 = sum(1 for x in reds if 12 <= x <= 22) / RED_PICK
    z3 = sum(1 for x in reds if 23 <= x <= 33) / RED_PICK
    return np.array([s / (RED_N * RED_PICK), odd, z1, z2, z3])


def build_XY(draws, K):
    """X: 过去 K 期蓝球 one-hot (16K) + 当期红球聚合(5)；Y: 下一期蓝球 one-hot(16)。"""
    X, Y, tgt = [], [], []
    for i in range(K, len(draws)):
        xb = np.concatenate([blue_onehot(draws[i - K + k]["blue"]) for k in range(K)])
        xa = red_agg(draws[i]["reds"])
        X.append(np.concatenate([xb, xa]))
        Y.append(blue_onehot(draws[i]["blue"]))
        tgt.append(draws[i]["blue"])
    return np.array(X), np.array(Y), tgt


def fit_ridge(X, Y, lam=1.0):
    XtX = X.T @ X + lam * np.eye(X.shape[1])
    return np.linalg.solve(XtX, X.T @ Y)  # (D, 16)


def predict_top(W, x, top=1):
    logits = x @ W
    return sorted(range(1, BLUE_N + 1), key=lambda b: (-logits[b - 1], b))[:top]


def blue_hit_mean(draws, K, lam, fit_end, pred_start, pred_end, top=1):
    """锚定 OOS：在 [K,fit_end) 拟合一次 W，对 [pred_start,pred_end) 纯预测。"""
    X_all, Y_all, tgt = build_XY(draws, K)
    W = fit_ridge(X_all[:fit_end - K], Y_all[:fit_end - K], lam)
    hits = []
    for j in range(pred_start - K, pred_end - K):
        if j < 0:
            continue
        pred = predict_top(W, X_all[j], top)
        hits.append(1 if tgt[j] in pred else 0)
    return np.array(hits)


def z_vs_random(hits, p_base):
    n = len(hits)
    if n < 30:
        return 0.0, 0.0
    mean = np.mean(hits)
    var = p_base * (1 - p_base)
    se = math.sqrt(var / n)
    z = (mean - p_base) / se
    return mean, z


def random_surrogate_z(draws, K, lam, train_end, top=1):
    X_all, Y_all, tgt = build_XY(draws, K)
    rng = np.random.default_rng(13)
    Ys = Y_all.copy()
    rng.shuffle(Ys)
    W = fit_ridge(X_all[:train_end - K], Ys[:train_end - K], lam)
    pred_start, pred_end = train_end, min(train_end + 400, len(draws))
    hits = []
    for j in range(pred_start - K, pred_end - K):
        pred = predict_top(W, X_all[j], top)
        hits.append(1 if tgt[j] in pred else 0)
    return z_vs_random(np.array(hits), top / BLUE_N)[1]


def run(K=10, lam=1.0, train_frac=0.8, top=1, seed=0):
    draws = load_draws()
    N = len(draws)
    train_end = int(N * train_frac)
    p_base = top / BLUE_N
    print("[blue] 样本 %d 期, K=%d, lam=%g, top=%d, 训练截止=%d" % (N, K, lam, top, train_end))
    h_tr = blue_hit_mean(draws, K, lam, train_end, train_end, min(train_end + 400, N), top)
    m_tr, z_tr = z_vs_random(h_tr, p_base)
    print("[blue] 训练OOS 命中率=%.3f z=%.3f (随机基线=%.3f)" % (m_tr, z_tr, p_base))
    h_co = blue_hit_mean(draws, K, lam, train_end, train_end, N, top)
    m_co, z_co = z_vs_random(h_co, p_base)
    print("[blue] 确认段(%d期) 命中率=%.3f z=%.3f" % (len(h_co), m_co, z_co))
    z_sur = random_surrogate_z(draws, K, lam, train_end, top)
    print("[blue] 随机surrogate z=%.3f (>2=构造伪显著,拒)" % z_sur)
    if z_sur > 2:
        print("  >>> 拒绝：构造伪显著")
    elif z_co > 2:
        print("  >>> 通过：确认段 z>2，蓝球模型真 >随机")
    else:
        print("  >>> 拒绝：确认段未复现 z>2（蓝球当前特征族仍 null 或表达力不足）")
    return {"K": K, "lam": lam, "top": top, "train_z": z_tr, "confirm_z": z_co, "surrogate_z": z_sur}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    ap.add_argument("--K", type=int, default=10)
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=1)
    ap.add_argument("--data", type=str, default=None)
    args = ap.parse_args()
    global MASTER
    if args.data:
        MASTER = args.data
    if args.cmd == "run":
        run(K=args.K, lam=args.lam, top=args.top)
    else:
        print("unknown cmd", args.cmd)


if __name__ == "__main__":
    main()
