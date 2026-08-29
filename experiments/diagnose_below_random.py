# -*- coding: utf-8 -*-
"""诊断：为什么引擎预测系统性低于随机？
假设：当前 predict 用"递推加权边际频率(追热号)"，而开奖存在均值回归(刚出的球下期不易再出)，
导致追热策略系统性选到被压低的球 -> 低于随机。
验证：严格前序回测对比 3 种选号策略的红球命中均值 vs 随机期望(6*6/33=1.0909)。
"""
import csv, math, collections

RED_N, BLUE_N, RED_PICK = 33, 16, 6
MASTER = r"D:/ssq_evo_data/ssq_master.csv"


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


def _red_gap_max(reds):
    s = sorted(reds)
    gaps = [s[i + 1] - s[i] for i in range(RED_PICK - 1)] + [s[0] + RED_N - s[-1]]
    return max(gaps)


def recency_pred(draws, window=150, decay=0.985, signal=None):
    """当前线上的两种策略（都追热）。"""
    if signal == "red_gap_max":
        vals = [_red_gap_max(d["reds"]) for d in draws]
        regime = sum(vals[-30:]) / 30
        sel = [d for d, v in zip(draws, vals) if abs(v - regime) <= 1.6]
        if len(sel) < 20:
            sel = draws[-window:]
    else:
        sel = draws[-window:]
    w = [decay ** k for k in range(len(sel))]
    rs, bs = [0.0] * (RED_N + 1), [0.0] * (BLUE_N + 1)
    for d, wt in zip(sel, w):
        for rb in d["reds"]:
            rs[rb] += wt
        bs[d["blue"]] += wt
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
    return reds, blue


def anti_revert_pred(draws, window=150, decay=0.985):
    """反均值回归：惩罚近期出现过的球(追冷而非追热)。"""
    recent = draws[-window:]
    w = [decay ** k for k in range(len(recent))]
    rs, bs = [0.0] * (RED_N + 1), [0.0] * (BLUE_N + 1)
    for d, wt in zip(recent, w):
        for rb in d["reds"]:
            rs[rb] -= wt  # 近期出过的球降权
        bs[d["blue"]] -= wt
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
    return reds, blue


def global_pred(draws):
    """不追热：纯全局边际频率。"""
    rs, bs = [0.0] * (RED_N + 1), [0.0] * (BLUE_N + 1)
    for d in draws:
        for rb in d["reds"]:
            rs[rb] += 1
        bs[d["blue"]] += 1
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
    return reds, blue


def backtest(method, draws, n=500):
    start = max(len(draws) - n, 200)
    targets = draws[start:]
    hits = []
    for tgt in targets:
        train = draws[: draws.index(tgt)]
        if method == "recency":
            pr, _ = recency_pred(train)
        elif method == "signal":
            pr, _ = recency_pred(train, signal="red_gap_max")
        elif method == "anti":
            pr, _ = anti_revert_pred(train)
        elif method == "global":
            pr, _ = global_pred(train)
        hits.append(len(set(pr) & set(tgt["reds"])))
    mean = sum(hits) / len(hits)
    dist = dict(sorted(collections.Counter(hits).items()))
    return mean, dist


if __name__ == "__main__":
    draws = load_draws()
    exp = RED_PICK * (RED_PICK / RED_N)
    print("随机解析期望(红球命中) = %.4f\n" % exp)
    for m, label in [("recency", "递推加权边际(追热, 朴素)"),
                     ("signal", "red_gap_max态筛选(追热, 当前26098方法)"),
                     ("anti", "反均值回归(追冷)"),
                     ("global", "全局边际频率(不追热)")]:
        mean, dist = backtest(m, draws)
        print(f"[{label}]")
        print(f"  红球命中均值 = {mean:.4f}  (vs 期望 {exp:.4f}, 差异 {mean-exp:+.4f})")
        print(f"  命中分布 = {dist}\n")
