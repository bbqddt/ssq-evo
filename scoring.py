# -*- coding: utf-8 -*-
"""
scoring.py —— 正确评分规则(proper scoring rule) + 实盘 live 排行榜
===============================================================
替代"红球命中数"这种高噪声选拔指标，给"公式 vs 随机基线"一个诚实、可统计的结论。

诚实前提：predict_tonight 产出的是【点预测】(6 红球 + 1 蓝球)，不是校准概率分布。
因此严格 proper scoring rule 只能以"点预测 = 把所有概率质量压在所选结果上"解释——
这会得到一个【诚实但保守】的结论：点预测在 log-loss 上必然不如均匀 null 模型
(因为过度自信)。所以我们同时提供两类工具：

  (1) proper_logloss_gap：模型 vs 均匀 null 的信息损失差（proper；点预测天然吃亏，
      正好说明"点公式预测无法在 log-loss 上战胜 null"，是正确且警醒的结论）。
  (2) bernoulli_edge + Wilson 置信区间：把每期"是否命中"当伯努利试验，用频率学派
      方法比较 引擎 vs 随机基线 的命中率差异，给出是否显著超出噪声。
      ——这才是 null 域里我们能【诚实比较】实盘战绩的工具。

实盘战绩(predictions.jsonl 累积)是"真实开奖纪录"这一不可作弊裁判的载体，
按用户约定：历史回测(高噪声、可反复翻看)只筛除不选拔，实盘命中率才是主选拔信号。
"""
import json
import math
import os

COMB33_6 = math.comb(33, 6)        # 1,107,568
LOG_COMB33_6 = math.log(COMB33_6)
LOG16 = math.log(16.0)


# ---------------------------------------------------------------------------
# Proper scoring rule: log-loss（点预测解释为退化分布）
# ---------------------------------------------------------------------------
def null_logloss_reds():
    return LOG_COMB33_6              # 均匀随机选 6 红球，预期 log-loss = 熵

def null_logloss_blue():
    return LOG16


def model_logloss_reds(actual_reds, pred_reds):
    """点预测把所有质量压在 pred_reds 上；actual ⊆ pred 才得 0，否则 = 均匀熵。"""
    if actual_reds and set(actual_reds).issubset(set(pred_reds or [])):
        return 0.0
    return LOG_COMB33_6


def model_logloss_blue(actual_blue, pred_blue):
    return 0.0 if (actual_blue is not None and actual_blue == pred_blue) else LOG16


# ---------------------------------------------------------------------------
# 频率学派比较：两比例差的 Wilson 置信区间 + z
# ---------------------------------------------------------------------------
def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def bernoulli_edge(engine_hits, baseline_hits, n):
    """比较两个伯努利过程(引擎 vs 随机基线)的命中率差异 + Wilson CI + z(双尾)。
    注意：engine_hits/baseline_hits 必须是【二元命中计数】(每期 0/1，如"至少中1红")，
    不能是 0..6 的红球命中计数——否则 pp 会超 1 触发 sqrt 定义域错误。
    返回可序列化 dict；n=0 时返回空。"""
    if n == 0:
        return {"n": 0, "edge": None, "ci": (None, None), "z": None, "p_two": None}
    pe = engine_hits / n
    pb = baseline_hits / n
    edge = pe - pb
    pp = min(1.0, max(0.0, (engine_hits + baseline_hits) / (2.0 * n)))
    se = math.sqrt(pp * (1.0 - pp) * (1.0 / n + 1.0 / n))
    if se > 1e-12:
        z = edge / se
        p_two = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))
    else:
        z, p_two = 0.0, 1.0
    ci_e = wilson_ci(engine_hits, n)
    ci_b = wilson_ci(baseline_hits, n)
    return {"n": n, "engine_rate": pe, "baseline_rate": pb, "edge": edge,
            "edge_ci_wilson": (ci_e[0] - ci_b[0], ci_e[1] - ci_b[1]),
            "z": z, "p_two": p_two}


# ---------------------------------------------------------------------------
# live 排行榜：读 predictions.jsonl，按 method 汇总实盘战绩，与随机基线比较
# ---------------------------------------------------------------------------
def load_predictions(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def live_leaderboard(data_dir):
    """读取 predictions.jsonl，按【期】聚合实盘战绩，并与随机基线比较。
    返回 dict(可序列化)。
    关键修正：随机基线是"每期一个确定性结果"，应从该期的 random 条目取一次，
    不可把每条记录的 baseline_* 都累加（会重复计数导致 pp>1）。
    比较口径：
      - 计数 edge：每期平均红球命中差（engine - baseline），诚实表达"每期多中几个"。
      - 二元 edge：每期"至少中1红"当作伯努利，比较命中率差 + Wilson CI（安全，pp<=1）。
      - proper log-loss：点预测 vs 均匀 null 的信息损失差（点预测天然吃亏，正确结论）。
    """
    path = os.path.join(data_dir, "predictions.jsonl")
    preds = load_predictions(path)
    scored = [p for p in preds if p.get("result")]
    if not scored:
        return {"n_issues": 0, "baseline": None, "methods": {}}
    # 按 issue 分组：issue -> {method: entry}
    issues = {}
    for p in scored:
        issues.setdefault(p["issue"], {})[p.get("method")] = p

    methods = {}
    base_red_per_issue = []
    base_blue_per_issue = []
    base_red1_per_issue = []   # 二元：随机基线该期是否至少中1红
    base_blue1_per_issue = []
    n_issues = 0
    for issue, mentries in issues.items():
        n_issues += 1
        # 该期基线：优先取 random 条目的引擎命中；否则取任一记录的 baseline_*
        base_e = mentries.get("random", next(iter(mentries.values())))
        br = base_e["result"].get("baseline_red_hit", base_e["result"].get("engine_red_hit", 0))
        bb = base_e["result"].get("baseline_blue_hit", base_e["result"].get("engine_blue_hit", 0))
        base_red_per_issue.append(br)
        base_blue_per_issue.append(bb)
        base_red1_per_issue.append(1 if br >= 1 else 0)
        base_blue1_per_issue.append(1 if bb >= 1 else 0)
        for m, p in mentries.items():
            if m == "random":
                continue
            r = p["result"]
            ef = p.get("engine_forecast", {}) or {}
            d = methods.setdefault(m, {"n": 0, "red_hit": 0, "blue_hit": 0,
                                       "red_ll": 0.0, "blue_ll": 0.0,
                                       "red1": 0, "blue1": 0})
            d["n"] += 1
            er = r.get("engine_red_hit", 0)
            eb = r.get("engine_blue_hit", 0)
            d["red_hit"] += er
            d["blue_hit"] += eb
            d["red1"] += (1 if er >= 1 else 0)
            d["blue1"] += (1 if eb >= 1 else 0)
            d["red_ll"] += model_logloss_reds(r.get("actual_reds", []), ef.get("reds", []))
            d["blue_ll"] += model_logloss_blue(r.get("actual_blue"), ef.get("blue"))

    base_red = sum(base_red_per_issue)
    base_blue = sum(base_blue_per_issue)
    base_red1 = sum(base_red1_per_issue)
    base_blue1 = sum(base_blue1_per_issue)
    out = {"n_issues": n_issues,
           "baseline": {"red_hit_sum": base_red, "blue_hit_sum": base_blue,
                        "avg_red_hit": base_red / n_issues if n_issues else 0.0,
                        "blue_rate": base_blue / n_issues if n_issues else 0.0,
                        "avg_red_logloss": null_logloss_reds(),
                        "avg_blue_logloss": null_logloss_blue()},
           "methods": {}}
    for m, d in methods.items():
        avg_red = d["red_hit"] / d["n"] if d["n"] else 0.0
        avg_blue = d["blue_hit"] / d["n"] if d["n"] else 0.0
        out["methods"][m] = {
            "n": d["n"],
            "red_hit_sum": d["red_hit"], "blue_hit_sum": d["blue_hit"],
            "avg_red_hit": avg_red, "blue_rate": avg_blue,
            "avg_red_logloss": d["red_ll"] / d["n"] if d["n"] else None,
            "avg_blue_logloss": d["blue_ll"] / d["n"] if d["n"] else None,
            # 计数 edge（每期平均多中几个红球 / 蓝球命中率差）
            "edge_red_count": (avg_red - (base_red / n_issues)) if n_issues else None,
            "edge_blue_rate": (avg_blue - (base_blue / n_issues)) if n_issues else None,
            # 二元 edge（至少中1红/蓝球命中率差 + Wilson CI），n>=2 才有意义
            "edge_red1_binary": bernoulli_edge(d["red1"], base_red1, n_issues),
            "edge_blue1_binary": bernoulli_edge(d["blue1"], base_blue1, n_issues),
        }
    return out


if __name__ == "__main__":
    import sys
    _dd = sys.argv[1] if len(sys.argv) > 1 else r"D:\ssq_evo_data"
    print(json.dumps(live_leaderboard(_dd), ensure_ascii=False, indent=2))
