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

import ssq_log


def load_frontier(DATA_DIR):
    p = os.path.join(DATA_DIR, "frontier.json")
    if os.path.exists(p):
        try:
            f = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            # JSON 损坏（可能被 45min 强杀截断）→ 备份坏文件 + 告警
            bad = p + ".corrupt"
            if not os.path.exists(bad):
                try:
                    import shutil
                    shutil.copy2(p, bad)
                except Exception as _e:
                    ssq_log.log_exception("frontier", _e, "frontier.py:27 silent-except")
            print(f"[frontier] CRITICAL: frontier.json 损坏({e})，备份到 {bad}，从空 frontier 重启")
            f = None
        if f is not None:
            f.setdefault("elites", [])
            f.setdefault("tried", [])
            f.setdefault("best_z_history", [])
            f.setdefault("coverage", 0)
            f.setdefault("footprints", [])   # evolve_predictor 脚印
            f.setdefault("gen", 0)
            f.setdefault("cycles_since_signal", 0)
            return f
    return {"elites": [], "tried": [], "best_z_history": [], "coverage": 0,
            "footprints": [], "gen": 0, "cycles_since_signal": 0}


# 无界增长封顶：tried 每轮追加，不封顶会让 frontier.json 无限膨胀
# （实测 4600+ 条仍在涨），最终拖慢每轮 IO 并撑爆内存/磁盘。
_CAP = {"tried": 20000, "best_z_history": 2000, "acc_history": 2000,
        "footprints": 200, "elites": 200}


def _cap_history(f):
    """把历史类列表截断到上限（保留最近的）——返回是否发生了截断。"""
    trimmed = False
    for key, cap in _CAP.items():
        v = f.get(key)
        if isinstance(v, list) and len(v) > cap:
            f[key] = v[-cap:]
            trimmed = True
    return trimmed


def save_frontier(DATA_DIR, f):
    p = os.path.join(DATA_DIR, "frontier.json")
    tmp = p + ".tmp"
    try:
        _cap_history(f)
    except Exception as _e:
        ssq_log.log_exception("frontier", _e, "frontier.py:66 silent-except")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(f, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        return
    except Exception as e:
        # flush/fsync 必须在 with 内；此处只作极端兜底（如磁盘满）
        ssq_log.error("frontier.save", "atomic write failed, fallback", e)
    try:
        json.dump(f, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        ssq_log.critical("frontier.save", "frontier.json WRITE FAILED (state loss)", e)
        raise
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except Exception as _e:
            ssq_log.log_exception("frontier", _e, "frontier.py:86 silent-except")


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
