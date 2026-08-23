# -*- coding: utf-8 -*-
"""
frontier.py —— 演化搜索前沿的跨轮持久化
========================================
让"演进"真正跨周期累积：每轮跑完把精英算子、已测空间、z 轨迹写入
frontier.json；下一轮读回作为精英种子与去重依据。这样迭代进度可被
客观度量（覆盖度单调增长），而非每轮重新随机撒网。
"""
import json
import os


def load_frontier(DATA_DIR):
    p = os.path.join(DATA_DIR, "frontier.json")
    if os.path.exists(p):
        try:
            f = json.load(open(p, encoding="utf-8"))
            f.setdefault("elites", [])
            f.setdefault("tried", [])
            f.setdefault("best_z_history", [])
            f.setdefault("coverage", 0)
            return f
        except Exception:
            pass
    return {"elites": [], "tried": [], "best_z_history": [], "coverage": 0}


def save_frontier(DATA_DIR, f):
    json.dump(f, open(os.path.join(DATA_DIR, "frontier.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def update_frontier(frontier, leaderboard, tried_set, elite_k=12):
    """用本轮结果刷新 frontier：精英(topK by z) + 去重 tried + z 轨迹 + 覆盖度。

    修复: 精英条目现在携带 q/verdict/z（不再丢弃评估数据），
    以便 breed_from_elites 能区分「真通过闸门的精英」和「仅占 topK 的占位符」。
    """
    # 精英：按 z 降序取 topK 基因组（z 越大越偏离 surrogate，越值得作为下一轮种子）
    items = sorted(leaderboard.values(), key=lambda e: e.get("z", 0.0), reverse=True)
    elites = [{"sig": e["sig"], "test": e["test"],
              "params": e.get("params", {"_sig": {}, "_test": {}}),
              "q": e.get("q"),           # 保留评估结果
              "verdict": e.get("verdict"), # 保留闸门判决
              "z": e.get("z", 0.0)}       # 保留 z 分数
              for e in items[:elite_k]]
    frontier["elites"] = elites

    # 去重 tried：与历史并集
    union = set(frontier.get("tried", [])) | set(tried_set)
    frontier["tried"] = list(union)

    # 覆盖度 = 累计测过的不同基因组数
    frontier["coverage"] = len(union)

    # z 轨迹
    if items:
        frontier["best_z_history"].append(round(float(items[0].get("z", 0.0)), 4))
    return frontier
