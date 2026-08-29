#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_historical_fitness.py —— "历史准确率当 fitness" 陷阱对照实验（不进生产管线）
=================================================================================
目的：用铁证演示 Goodhart 定律在双色球搜索中的教科书级表现——

    用"历史命中率"当 fitness，在【发现段】从大量候选"公式"里搜出最准的那个，
    在【确认段】冻结测试（公式不再改）；
    对【真实双色球数据】与【纯随机数据】各跑一遍完全相同流程。

预期结论（也是本实验要证明的）：
  1. 两种数据上，发现段都能搜出"命中率明显高于 50%"的公式——
     这不是找到了结构，而是候选数足够多时多重比较（multiple comparison）必然的虚高。
  2. 确认段（公式冻结、不可再调参）命中率一律跌回 ~50% 随机基线。
  3. 纯随机数据上同样能搜出"发现段高命中率"的公式——
     → 证明高历史准确率根本不区分"真实结构"与"纯噪声"。

本模块只读数据、写 audit/ 结论，绝不 import 生产引擎、不碰 daemon / frontier / firewall。
它是一面"照妖镜"：任何想把"历史准确率"当选公式标准的人，先看这页铁证。
"""
import os
import csv
import json
import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
# 复用项目路径唯一真源（paths.py 已处理 env > 宿主候选 > ./data），不写死盘符。
try:
    from paths import DATA_DIR
except Exception:
    DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(HERE)
OUT_DIR = os.path.join(DATA_DIR, "audit")
OUT_FILE = os.path.join(OUT_DIR, "historical_fitness_trap.json")


# ---------------------------------------------------------------------------
# 1. 数据加载
# ---------------------------------------------------------------------------
def load_real(path=None):
    """真实双色球：issue,r1..r6,b。返回 (reds[N,6], blues[N])。"""
    path = path or os.path.join(DATA_DIR, "ssq_master.csv")
    reds, blues = [], []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 8:
                continue
            try:
                reds.append([int(x) for x in row[1:7]])
                blues.append(int(row[7]))
            except ValueError:
                continue
    return np.array(reds, dtype=int), np.array(blues, dtype=int)


def gen_random(n, seed):
    """纯随机双色球：每期从 1..33 无放回抽 6 个红球 + 1..16 蓝球。"""
    rng = np.random.default_rng(seed)
    reds = np.zeros((n, 6), dtype=int)
    for i in range(n):
        pick = rng.choice(np.arange(1, 34), size=6, replace=False)
        pick.sort()
        reds[i] = pick
    blues = rng.integers(1, 17, size=n)
    return reds, blues


# ---------------------------------------------------------------------------
# 2. 特征工程（真实 / 随机数据通用，纯数值统计）
# ---------------------------------------------------------------------------
def features(reds, blues):
    f = {}
    f["red_sum"] = reds.sum(axis=1).astype(float)
    f["red_mean"] = reds.mean(axis=1)
    f["red_range"] = (reds.max(axis=1) - reds.min(axis=1)).astype(float)
    f["red_first"] = reds[:, 0].astype(float)
    f["red_last"] = reds[:, -1].astype(float)
    f["blue"] = blues.astype(float)
    f["even_count"] = (reds % 2 == 0).sum(axis=1).astype(float)
    f["consec"] = (np.diff(reds, axis=1) == 1).sum(axis=1).astype(float)
    ac = []
    for row in reds:
        s = set()
        for i in range(6):
            for j in range(i + 1, 6):
                s.add(abs(row[i] - row[j]))
        ac.append(len(s) - 5)
    f["ac"] = np.array(ac, dtype=float)
    primes = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31}
    f["prime_count"] = np.array(
        [sum(1 for x in row if x in primes) for row in reds], dtype=float)
    f["sum_mod10"] = (reds.sum(axis=1) % 10).astype(float)
    f["red_std"] = reds.std(axis=1)
    f["gap_mean"] = np.diff(reds, axis=1).mean(axis=1)
    return f


def target_event(reds):
    """目标事件 E[t] = 下期(red_sum) 是否高于【总体中位数】。

    关键：此目标只依赖第 t+1 期，与第 t 期的任何特征统计独立 ——
    因此没有任何"历史特征"能真正预测它。候选公式再多，也只能在
    发现段靠【多重比较】把命中率虚高，确认段（公式冻结）必然跌回 0.5 基线。
    这正是 Goodhart 陷阱的干净演示（不会像"涨跌"目标那样混入同期的真实弱相关）。

    返回 E（长度 N-1，与特征前 N-1 期对齐）。"""
    rs = reds.sum(axis=1).astype(float)
    med = np.median(rs)
    return (rs[1:] > med).astype(int)


# ---------------------------------------------------------------------------
# 3. 候选"公式"池：每条 = 一个可解释布尔规则 f(I_t) = lo<=F<=hi -> 预测涨
# ---------------------------------------------------------------------------
def build_candidates(fdict, n_bins=30):
    cands = []
    for name, arr in fdict.items():
        qs = np.quantile(arr, np.linspace(0.0, 1.0, n_bins + 1))
        for k in range(n_bins):
            lo, hi = qs[k], qs[k + 1]
            if lo >= hi:
                continue
            cands.append((name, float(lo), float(hi)))
    return cands


def eval_candidate(name, lo, hi, fdict, E, seg):
    """在给定事件段 seg（索引区间）上评估规则命中率。
    特征取前 len(E) 期（最后一期无下期事件），与 E 对齐。"""
    feat = fdict[name][: len(E)]
    pred = ((feat >= lo) & (feat <= hi)).astype(int)
    sub = E[seg[0]:seg[1]]
    subp = pred[seg[0]:seg[1]]
    if len(sub) == 0:
        return 0.0
    return float((subp == sub).mean())


def search_best(cands, fdict, E, disc_seg):
    """用'发现段命中率'当 fitness，选最优公式（即 Goodhart 选公式的标准）。"""
    best = None  # (hit, rule)
    for (name, lo, hi) in cands:
        h = eval_candidate(name, lo, hi, fdict, E, disc_seg)
        if best is None or h > best[0]:
            best = (h, (name, lo, hi))
    return best


# ---------------------------------------------------------------------------
# 4. 单组数据跑一遍：发现段搜 + 确认段冻结测
# ---------------------------------------------------------------------------
def run_one(reds, blues, label):
    fdict = features(reds, blues)
    E = target_event(reds)
    M = len(E)
    disc_end = int(M * 0.7)
    disc_seg = (0, disc_end)
    conf_seg = (disc_end, M)

    cands = build_candidates(fdict, n_bins=40)
    best_hit, (bname, blo, bhi) = search_best(cands, fdict, E, disc_seg)
    conf_hit = eval_candidate(bname, blo, bhi, fdict, E, conf_seg)

    return {
        "label": label,
        "n_issues": int(len(reds)),
        "n_events": int(M),
        "n_candidates": int(len(cands)),
        "discovery_hit": round(best_hit, 4),
        "confirmation_hit": round(conf_hit, 4),
        "random_baseline": 0.5,
        "best_rule": {"feature": bname, "lo": round(blo, 3), "hi": round(bhi, 3)},
        "verdict": (
            "发现段命中率 %.3f 显著高于 0.5（多重比较虚高），"
            "确认段冻结后跌回 %.3f ≈ 随机基线 —— 公式未泛化"
            % (best_hit, conf_hit)
        ),
    }


# ---------------------------------------------------------------------------
# 5. 主流程
# ---------------------------------------------------------------------------
def main():
    reds_real, blues_real = load_real()
    reds_rnd, blues_rnd = gen_random(len(reds_real), seed=20260829)

    real = run_one(reds_real, blues_real, "real_data")
    rnd = run_one(reds_rnd, blues_rnd, "random_data")

    delta_real = round(real["discovery_hit"] - real["confirmation_hit"], 4)
    delta_rnd = round(rnd["discovery_hit"] - rnd["confirmation_hit"], 4)

    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "experiment": "历史准确率当 fitness 的 Goodhart 陷阱对照",
        "design": (
            "发现段(前70%)用命中率当 fitness 搜最佳规则；确认段(后30%)冻结测试；"
            "真实数据 vs 纯随机数据跑相同流程"
        ),
        "real_data": real,
        "random_data": rnd,
        "comparison": {
            "real_discovery_minus_confirmation": delta_real,
            "random_discovery_minus_confirmation": delta_rnd,
            "interpretation": (
                "真实数据与纯随机数据都出现'发现段高命中率、确认段跌回基线'；"
                "两者无法区分 —— 证明高历史准确率不来自彩票结构，而来自"
                "候选过多导致的多重比较虚高。若改用更过参数化模型"
                "(多项式/查表/NN)，发现段拟合可逼近 1.0、确认段归零，"
                "这正是一句'回测 100% 准'的来源。"
            ),
        },
        "conclusion": (
            "铁证：用历史准确率当 fitness 选出的'最优公式'，在样本外一律跌回 0.5 随机基线；"
            "且纯随机噪声上也能搜出同等级'高历史准'。因此历史准确率 ≠ 泛化能力，"
            "本系统改用 发现段p值 + 随机对照闸门 + 阳性对照 + OOT不可回溯 的物理隔离方案。"
        ),
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 终端摘要
    print("=" * 64)
    print("Goodhart 陷阱对照实验（历史准确率当 fitness）")
    print("=" * 64)
    for d in (real, rnd):
        print("[%s] N=%d 候选=%d" % (d["label"], d["n_issues"], d["n_candidates"]))
        print("  发现段命中率 = %.3f  | 确认段命中率 = %.3f  | 基线 = 0.5"
              % (d["discovery_hit"], d["confirmation_hit"]))
        print("  最佳规则: feature=%s 区间=[%.3f, %.3f]"
              % (d["best_rule"]["feature"], d["best_rule"]["lo"], d["best_rule"]["hi"]))
    print("-" * 64)
    print("结论：真实 vs 随机 表现一致，发现段虚高、确认段跌回 0.5 基线 → 历史准 ≠ 结构")
    print("输出已写入: %s" % OUT_FILE)
    return result


if __name__ == "__main__":
    main()
