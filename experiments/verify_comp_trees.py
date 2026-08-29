# -*- coding: utf-8 -*-
"""验证 frontier.comp_elites 的 gen=4 复合公式树能否真正用于选号。
复用 engine_core._build_comp 把树编译成"每期一个标量"的信号序列，
接 predict_from_signal 态筛选+边际频率选号框架，严格前序回测(500期)看红球命中均值。
"""
import sys, csv, json
import numpy as np

sys.path.insert(0, r"D:/ssq_evo")
import engine_core as EC

RED_N, BLUE_N, RED_PICK = 33, 16, 6
MASTER = r"D:/ssq_evo_data/ssq_master.csv"
FRONTIER = r"D:/ssq_evo_data/frontier.json"


def load_draws():
    rows = []
    with open(MASTER, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({"issue": r["issue"],
                             "reds": [int(r["r%d" % i]) for i in range(1, 7)],
                             "blue": int(r["b"])})
            except Exception:
                continue
    rows.sort(key=lambda x: x["issue"])
    return rows


def pred_from_vals(vals, draws, window=150, decay=0.985, tol=1.6, regime_n=30):
    """val = 每期 signal 标量列表(长度=期数)。态筛选+递推边际选 top6+top1。"""
    regime = sum(vals[-regime_n:]) / min(regime_n, len(vals))
    sel_idx = [i for i, v in enumerate(vals) if abs(v - regime) <= tol]
    if len(sel_idx) < 20:
        sel_idx = list(range(max(0, len(vals) - window), len(vals)))
    sel = [draws[i] for i in sel_idx]
    w = [decay ** k for k in range(len(sel))]
    rs, bs = [0.0] * (RED_N + 1), [0.0] * (BLUE_N + 1)
    for d, wt in zip(sel, w):
        for rb in d["reds"]:
            rs[rb] += wt
        bs[d["blue"]] += wt
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
    return reds, blue


def backtest_tree(tree, draws, all_reds, all_blues, n=500):
    x = EC._build_comp(tree, all_reds, all_blues)
    if x is None or not np.all(np.isfinite(x)):
        return None
    vals = list(np.asarray(x, float))
    start = max(len(draws) - n, 200)
    targets = draws[start:]
    hits = []
    for tgt in targets:
        ti = draws.index(tgt)
        pr, _ = pred_from_vals(vals[:ti], draws[:ti])
        hits.append(len(set(pr) & set(tgt["reds"])))
    return sum(hits) / len(hits), len(hits)


if __name__ == "__main__":
    draws = load_draws()
    all_reds = np.array([d["reds"] for d in draws])
    all_blues = np.array([d["blue"] for d in draws])
    fr = json.load(open(FRONTIER))
    ce = fr.get("comp_elites", [])
    print("comp_elites 数量: %d" % len(ce))
    print("随机解析期望(红球命中) = 1.0909\n")
    ok = 0
    best = None
    for i, tree in enumerate(ce):
        r = backtest_tree(tree, draws, all_reds, all_blues)
        if r:
            ok += 1
            print("  树%d: 红命中均值=%.4f (n=%d)" % (i, r[0], r[1]))
            if best is None or r[0] > best[1]:
                best = (i, r[0])
        else:
            print("  树%d: 编译失败/含NaN" % i)
    print("\n可编译树: %d/%d" % (ok, len(ce)))
    if best:
        print("最优树=%d 红命中=%.4f (vs 期望1.0909, 差异 %+.4f)" %
              (best[0], best[1], best[1] - 1.0909))
    else:
        print("所有树均无法编译 -> 公式无法参与计算(需查原因)")
