# -*- coding: utf-8 -*-
"""方向条件化选号 —— walk-forward 回测（研究用，不碰今晚已登记预测）。

核心假设（来自 accuracy_report.md 真相）：
  - 红球和值 red_mean 存在真实弱惯性，无泄漏样本外方向命中率 0.767（随机 0.487）。
  - 当前 predict_tonight.py 用 red_gap_max（≈随机信号）作态筛选 + 递推边际选号，
    实际 5 期战绩 1.00/6 < 随机 1.40/6 —— 比随机还差，说明管线没用上 red_mean 惯性。

本回测验证：把 red_mean 方向惯性接入"选号"，是否能在严格前序、无泄漏下
稳定优于 (a) 当前方法 (b) 纯递推边际 (c) 随机基线。

方法 direction_conditioned：
  对目标期 t+1，用训练段 red_mean 动量预测方向 d∈{up,down}：
      d = sign(red_mean[t] - red_mean[t-1])   # 惯性：上期升→本期预期升
  仅取训练段中"下期实际方向==d"的历史期子集，统计红球边际频率，取 top6；
  蓝球同理取 top1。方向预测错时该子集含噪声，但正确率 0.767 使条件子集整体更准。

绝不泄漏：train = draws[:idx(tgt)]，目标期信息从不进入训练。
"""
import os, csv, json, random, math
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, "data", "ssq_master.csv")
if not os.path.exists(MASTER):
    MASTER = r"D:/ssq_evo_data/ssq_master.csv"

RED_N, BLUE_N, RED_PICK = 33, 16, 6
WINDOW = 150
DECAY = 0.985


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


def recency_weights(n, decay=DECAY):
    return [decay ** k for k in range(n)]


def red_mean_series(draws):
    return [sum(d["reds"]) / RED_PICK for d in draws]


def predict_recency(train, window=WINDOW, decay=DECAY):
    recent = train[-window:]
    w = recency_weights(len(recent), decay)
    rs = [0.0] * (RED_N + 1); bs = [0.0] * (BLUE_N + 1)
    for d, wt in zip(recent, w):
        for rb in d["reds"]: rs[rb] += wt
        bs[d["blue"]] += wt
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
    return sorted(reds), blue


def predict_direction_conditioned(train, window=WINDOW):
    """用 red_mean 动量预测下一期和值方向，仅取同向历史期子集做边际选号。"""
    rm = red_mean_series(train)
    if len(rm) < 5:
        return predict_recency(train, window)
    d = 1 if rm[-1] >= rm[-2] else -1  # 惯性方向
    # 训练段每期 t 的"下期方向"
    dirs = [1 if rm[i + 1] >= rm[i] else -1 for i in range(len(rm) - 1)]
    # 对齐：train 第 k 期(0-based) 的下期方向 = dirs[k]，其红球 = train[k+1]
    # 我们想挑"下期方向==d"的历史期，用那些期的红球频率
    sel_idx = [k for k in range(len(dirs)) if dirs[k] == d]
    if len(sel_idx) < 20:
        return predict_recency(train, window)
    rs = [0.0] * (RED_N + 1); bs = [0.0] * (BLUE_N + 1)
    for k in sel_idx:
        for rb in train[k + 1]["reds"]: rs[rb] += 1.0
        bs[train[k + 1]["blue"]] += 1.0
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
    return sorted(reds), blue


def predict_signal_regime(train, window=WINDOW, decay=DECAY, tol=1.6, regime_n=30):
    """复刻当前 predict_tonight.signal_regime_red_gap_max，作为对照。"""
    def _rgm(reds):
        s = sorted(reds)
        gaps = [s[i + 1] - s[i] for i in range(RED_PICK - 1)] + [s[0] + RED_N - s[-1]]
        return max(gaps)
    vals = [_rgm(d["reds"]) for d in train]
    regime = sum(vals[-regime_n:]) / min(regime_n, len(vals))
    sel = [d for d, v in zip(train, vals) if abs(v - regime) <= tol]
    if len(sel) < 20: sel = train[-window:]
    w = recency_weights(len(sel), decay)
    rs = [0.0] * (RED_N + 1); bs = [0.0] * (BLUE_N + 1)
    for d, wt in zip(sel, w):
        for rb in d["reds"]: rs[rb] += wt
        bs[d["blue"]] += wt
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
    return sorted(reds), blue


def random_pick(seed):
    rng = random.Random(seed)
    return sorted(rng.sample(range(1, RED_N + 1), RED_PICK)), rng.randint(1, BLUE_N)


def backtest(n_range=500):
    draws = load_draws()
    start = max(len(draws) - n_range, 200)
    targets = draws[start:]
    methods = {
        "direction_conditioned(red_mean)": predict_direction_conditioned,
        "signal_regime(red_gap_max)": lambda tr: predict_signal_regime(tr),
        "recency_marginal": predict_recency,
    }
    agg = {m: {"red": [], "blue": 0} for m in methods}
    rand_red = []; rand_blue = 0
    for tgt in targets:
        idx = draws.index(tgt)
        train = draws[:idx]
        ar = set(tgt["reds"]); ab = tgt["blue"]
        for m, fn in methods.items():
            pr, pb = fn(train)
            agg[m]["red"].append(len(set(pr) & ar))
            agg[m]["blue"] += (1 if pb == ab else 0)
        rr, rb = random_pick(int(tgt["issue"]))
        rand_red.append(len(set(rr) & ar))
        rand_blue += (1 if rb == ab else 0)
    n = len(targets)
    exp_red = RED_PICK * (RED_PICK / RED_N)
    print(f"=== 严格前序 walk-forward 回测（{n} 期目标，尾窗={WINDOW}）===")
    print(f"{'方法':<32}{'红均':>8}{'蓝率':>8}{'vs随机期望':>14}")
    for m in methods:
        rm_ = sum(agg[m]["red"]) / n
        bm_ = agg[m]["blue"] / n
        print(f"{m:<32}{rm_:>8.3f}{bm_:>8.3f}{rm_-exp_red:>+14.3f}")
    rrm_ = sum(rand_red) / n; rbm_ = rand_blue / n
    print(f"{'random_baseline':<32}{rrm_:>8.3f}{rbm_:>8.3f}{'--':>14}")
    print(f"\n随机解析期望: 红/期={exp_red:.3f}, 蓝率={1/BLUE_N:.3f}")
    # 方向预测本身准确率（验证 0.767 惯性在回测段成立）
    rm = red_mean_series(draws[:start])
    correct = 0; total = 0
    for i in range(start, len(draws) - 1):
        d = 1 if rm[-1] >= rm[-2] else -1
        actual = 1 if sum(draws[i+1]["reds"])/RED_PICK >= sum(draws[i]["reds"])/RED_PICK else -1
        correct += (d == actual); total += 1
        rm = rm + [sum(draws[i+1]["reds"])/RED_PICK]
    print(f"方向动量预测准确率(回测段): {correct/total:.3f} (报告 OOS=0.767)")


if __name__ == "__main__":
    backtest(500)
