# -*- coding: utf-8 -*-
"""gru_evolve.py —— 纯 numpy 实现的 GRU 非线性序列模型（红球）。
不依赖 torch（云端 Actions 装 torch 体积大、代理易断）；GRU 数学闭式可手写。
GRU 比岭回归强在：隐状态非线性递归，能捕捉"红球序列中长期依赖/非线性转移"，
是岭回归的真超集（岭回归 = 无递归、无激活的线性特例）。

网络：输入 x_t (33 维红球 one-hot) → GRU 隐状态 h (dim H) → 线性头 → 33 维 logits → softmax。
训练：严格前序滚动（期 i 的预测只用 <i 的参数）。为诚实且不泄漏，采用
"滑动窗口再训练"：每预测一期后用真值做一步梯度更新（在线学习），但确认段禁止更新（learn=False）。

诚实闸门（与 seq_evolve 同权，绝不放松）：
  1) 严格前序 OOS：测试期 i 只用 <i 的数据/参数，永不泄漏。
  2) 随机 surrogate：Y 打乱同架构训练，z>2 判构造伪显著 → 拒。
  3) 独立确认段：训练外最后 N 期复现 z>2 才声称 >随机。
"""
import os, csv, math, argparse
import numpy as np
import paths

RED_N, RED_PICK, BLUE_N = 33, 6, 16
HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = paths.DATA_DIR
MASTER = os.path.join(_DATA_DIR, "ssq_master.csv")
SNAP = os.path.join(_DATA_DIR, "ssq_history.csv")


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


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


class GRU:
    """最小 GRU：输入 dim=RED_N，隐藏 dim=H，输出 dim=RED_N。"""

    def __init__(self, H=32, lr=0.05, seed=0):
        self.H = H
        self.lr = lr
        rng = np.random.default_rng(seed)
        s = math.sqrt(1.0 / H)
        # 更新门 z、重置门 r、候选 n 的参数（输入+隐 → H）
        self.W_z = rng.normal(0, s, (RED_N, H))
        self.U_z = rng.normal(0, s, (H, H))
        self.b_z = np.zeros(H)
        self.W_r = rng.normal(0, s, (RED_N, H))
        self.U_r = rng.normal(0, s, (H, H))
        self.b_r = np.zeros(H)
        self.W_n = rng.normal(0, s, (RED_N, H))
        self.U_n = rng.normal(0, s, (H, H))
        self.b_n = np.zeros(H)
        # 输出头：H → RED_N
        self.W_o = rng.normal(0, s, (H, RED_N))
        self.b_o = np.zeros(RED_N)
        self.h = np.zeros(H)

    def reset(self):
        self.h = np.zeros(self.H)

    def step(self, x):
        """单步前向：x (RED_N,) → 更新 h，返回 logits (RED_N,)。"""
        z = sigmoid(x @ self.W_z + self.h @ self.U_z + self.b_z)
        r = sigmoid(x @ self.W_r + self.h @ self.U_r + self.b_r)
        n = np.tanh(x @ self.W_n + (r * self.h) @ self.U_n + self.b_n)
        self.h = (1 - z) * self.h + z * n
        return self.h @ self.W_o + self.b_o

    def logits(self, x):
        return self.step(x)

    def update(self, x, y_true):
        """一步在线学习：用真值 y_true (RED_N one-hot) 做梯度下降更新参数。"""
        # 前向 + 反向（手工，仅对当前步）
        z = sigmoid(x @ self.W_z + self.h @ self.U_z + self.b_z)
        r = sigmoid(x @ self.W_r + self.h @ self.U_r + self.b_r)
        n = np.tanh(x @ self.W_n + (r * self.h) @ self.U_n + self.b_n)
        h_new = (1 - z) * self.h + z * n
        logits = h_new @ self.W_o + self.b_o
        # softmax + 交叉熵梯度
        e = np.exp(logits - logits.max())
        p = e / e.sum()
        d_out = (p - y_true)  # (RED_N,)
        # 输出头梯度
        dW_o = np.outer(h_new, d_out)
        db_o = d_out
        dh = self.W_o @ d_out  # (H,)
        # 反向到 h_new
        dh_new = dh.copy()
        # h_new = (1-z)*h + z*n
        dz = (-self.h + n) * dh_new * z * (1 - z)
        dn = z * dh_new * (1 - n ** 2)
        dr = (self.h @ self.U_n) * dn * r * (1 - r)
        # 参数梯度
        dW_z = np.outer(x, dz); dU_z = np.outer(self.h, dz); db_z = dz
        dW_r = np.outer(x, dr); dU_r = np.outer(self.h, dr); db_r = dr
        dW_n = np.outer(x, dn); dU_n = np.outer(r * self.h, dn); db_n = dn
        # 应用
        lr = self.lr
        for p, g in [(self.W_z, dW_z), (self.U_z, dU_z), (self.b_z, db_z),
                     (self.W_r, dW_r), (self.U_r, dU_r), (self.b_r, db_r),
                     (self.W_n, dW_n), (self.U_n, dU_n), (self.b_n, db_n),
                     (self.W_o, dW_o), (self.b_o, db_o)]:
            p -= lr * g
        self.h = h_new  # 状态已前推


def predict_top6(gru, x):
    logits = gru.logits(x)
    return sorted(range(1, RED_N + 1), key=lambda n: (-logits[n - 1], n))[:RED_PICK]


def red_hit_series(draws, H, lr, seed, fit_end, pred_start, pred_end, learn=True):
    """严格前序：在 [K=1, fit_end) 预训练 GRU，对 [pred_start,pred_end) 预测；
    learn=True 时每期用真值在线更新（仅训练/验证用），确认段 learn=False 防泄漏。"""
    gru = GRU(H=H, lr=lr, seed=seed)
    # 预训练：用 [1, fit_end) 滚动在线学习
    for i in range(1, fit_end):
        x = onehot(draws[i - 1]["reds"])
        y = onehot(draws[i]["reds"])
        gru.logits(x)
        gru.update(x, y)
    # 预测段
    hits = []
    for i in range(pred_start, pred_end):
        x = onehot(draws[i - 1]["reds"])
        pred = predict_top6(gru, x)
        hits.append(len(set(pred) & set(draws[i]["reds"])))
        if learn:
            gru.update(x, onehot(draws[i]["reds"]))
    return np.array(hits)


def z_vs_random(hits):
    n = len(hits)
    if n < 30:
        return 0.0, 0.0
    mean = np.mean(hits)
    p = RED_PICK / RED_N
    var = RED_PICK * p * (1 - p) * (RED_N - RED_PICK) / (RED_N - 1)
    se = math.sqrt(var / n)
    z = (mean - RED_PICK * p) / se
    return mean, z


def random_surrogate_z(draws, H, lr, seed, train_end):
    """Y 打乱：把每期红球标签随机重排，同架构训练预测，看 z 是否仍 >2。"""
    n = len(draws)
    draws_s = [dict(d) for d in draws]
    rng = np.random.default_rng(seed + 99)
    reds = [d["reds"] for d in draws_s]
    perm = rng.permutation(n)
    for i in range(n):
        draws_s[i]["reds"] = reds[perm[i]]
    gru = GRU(H=H, lr=lr, seed=seed)
    for i in range(1, train_end):
        x = onehot(draws_s[i - 1]["reds"]); y = onehot(draws_s[i]["reds"])
        gru.logits(x); gru.update(x, y)
    hits = []
    for i in range(train_end, min(train_end + 400, n)):
        x = onehot(draws_s[i - 1]["reds"])
        pred = predict_top6(gru, x)
        hits.append(len(set(pred) & set(draws[i]["reds"])))
    return z_vs_random(np.array(hits))[1]


def run(H=32, lr=0.05, train_frac=0.8, seed=0):
    draws = load_draws()
    N = len(draws)
    train_end = int(N * train_frac)
    print("[gru] 样本 %d 期, H=%d, lr=%g, 训练截止=%d" % (N, H, lr, train_end))
    h_tr = red_hit_series(draws, H, lr, seed, train_end, train_end, min(train_end + 400, N), learn=True)
    m_tr, z_tr = z_vs_random(h_tr)
    print("[gru] 训练OOS 红命中均值=%.3f z=%.3f" % (m_tr, z_tr))
    h_co = red_hit_series(draws, H, lr, seed, train_end, train_end, N, learn=False)
    m_co, z_co = z_vs_random(h_co)
    print("[gru] 确认段(%d期) 红命中均值=%.3f z=%.3f" % (len(h_co), m_co, z_co))
    z_sur = random_surrogate_z(draws, H, lr, seed, train_end)
    print("[gru] 随机surrogate z=%.3f (>2=构造伪显著,拒)" % z_sur)
    if z_sur > 2:
        print("  >>> 拒绝：构造伪显著")
    elif z_co > 2:
        print("  >>> 通过：确认段 z>2，GRU 真 >随机")
    else:
        print("  >>> 拒绝：确认段未复现 z>2（GRU 当前仍 null 或表达力仍不足）")
    return {"H": H, "lr": lr, "train_z": z_tr, "confirm_z": z_co, "surrogate_z": z_sur}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    ap.add_argument("--H", type=int, default=32)
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--data", type=str, default=None)
    args = ap.parse_args()
    global MASTER
    if args.data:
        MASTER = args.data
    if args.cmd == "run":
        run(H=args.H, lr=args.lr)
    else:
        print("unknown cmd", args.cmd)


if __name__ == "__main__":
    main()
