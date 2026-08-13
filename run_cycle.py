# -*- coding: utf-8 -*-
"""
run_cycle.py —— 一次完整演化周期
流程：抓取/合并数据 -> 跑演化搜索若干代 -> BH-FDR 校正 -> 样本外验证
      -> 写 SQLite + state.json（供看板读取）
用法：python run_cycle.py            # 抓取最新数据并跑一轮
      python run_cycle.py --no-fetch # 不联网，用本地主表重跑（断网/离线也持续产出）
"""
import os, sys, json, argparse, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", HERE)   # Docker 部署时通过环境变量指向 /app/data (= D 盘卷)
os.makedirs(DATA_DIR, exist_ok=True)
sys.path.insert(0, HERE)

import engine_core as E
import data as D
import store as S

MASTER = os.path.join(DATA_DIR, "ssq_master.csv")
DB = os.path.join(DATA_DIR, "ssq_evo.db")
STATE = os.path.join(DATA_DIR, "state.json")

# ---- 可调参数（部署时可改 config.json）----
DEFAULT_CFG = {
    "epochs": 6, "pop": 24, "k_light": 25, "k_heavy": 10,
    "seed": 20260813, "oos_frac": 0.2, "alert_q": 0.01, "alert_oos_p": 0.01,
}


def load_cfg():
    p = os.path.join(DATA_DIR, "config.json")
    if not os.path.exists(p):
        p = os.path.join(HERE, "config.json")   # 回退到代码目录
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(p):
        cfg.update(json.load(open(p, encoding="utf-8")))
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    cfg = load_cfg()

    # 1. 数据
    master = D.load_master(MASTER)
    added = 0
    if not args.no_fetch:
        fresh = D.fetch_recent()
        if fresh is None:
            print("[cycle] 抓取失败，使用本地主表继续。")
        else:
            master, added = D.update_master(master, fresh)
            D.save_master(master, MASTER)
    else:
        if master:
            pass
    if not master:
        print("[cycle] 无数据，退出。"); return
    reds, blues, issues = D.to_arrays(master)
    N = len(reds)
    print(f"[cycle] N={N} 期, 新增 {added}, 末期 {issues[-1]}")

    # 2. 演化
    rng = np.random.default_rng(cfg["seed"] + N)  # 随样本量变化种子，避免每轮完全相同
    evo = E.Evolution(reds, blues, rng, k_light=cfg["k_light"], k_heavy=cfg["k_heavy"],
                      epochs=cfg["epochs"], pop=cfg["pop"])
    leaderboard, all_evals = evo.run()
    print(f"[cycle] 评估算子数(含重复去重前): {len(all_evals)}, 唯一算子: {len(leaderboard)}")

    # 3. FDR (跨全部评估)
    pvals = np.array([e["p_raw"] for e in all_evals])
    qs = E.bh_fdr(pvals)
    for e, q in zip(all_evals, qs):
        e["q"] = float(q)
        if q < 0.05:
            e["verdict"] = "显著(<0.05)"
        elif q < 0.2:
            e["verdict"] = "边缘"
        else:
            e["verdict"] = "随机区间"

    # 4. 本论最优（按 q）
    order = sorted(all_evals, key=lambda e: e["q"])
    best = order[0]
    best_q = best["q"]

    # 5. 样本外验证（针对唯一 leaderboard 最优）
    oos_p = None
    lb_items = sorted(leaderboard.values(), key=lambda e: e["p_raw"])
    if lb_items:
        top = lb_items[0]
        oos = E.out_of_sample(top, reds, blues, rng, frac=cfg["oos_frac"], k_sur=cfg["k_light"])
        oos_p = oos

    alert = (best_q < cfg["alert_q"]) and (oos_p is not None) and (oos_p < cfg["alert_oos_p"])

    # 6. 持久化
    con = S.open_db(DB)
    run = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_issues": N, "added": added, "n_eval": len(all_evals),
        "best_q": best_q, "best_sig": best["sig"], "best_test": best["test"],
        "best_p": best["p_raw"], "oos_p": (oos_p if oos_p is not None else -1.0),
        "alert": alert,
        "note": ("候选结构! 需人工复核" if alert else "无超越随机的可提取结构 (null)"),
    }
    rid = S.insert_run(con, run)
    S.insert_evals(con, rid, all_evals)
    con.close()

    # 7. 写 state.json
    lb_top = sorted(leaderboard.values(), key=lambda e: e["p_raw"])[:20]
    history = S.recent_runs(S.open_db(DB), 200)
    state = {
        "updated": run["ts"], "n_issues": N, "last_issue": issues[-1], "added": added,
        "cycle_id": rid, "best_q": best_q, "best_sig": best["sig"], "best_test": best["test"],
        "best_p": best["p_raw"], "best_stat": best["stat"], "best_z": best["z"],
        "oos_p": (oos_p if oos_p is not None else None), "alert": bool(alert),
        "n_eval": len(all_evals), "n_unique": len(leaderboard),
        "params": cfg,
        "leaderboard": [
            {"sig": e["sig"], "test": e["test"], "p_raw": e["p_raw"], "q": e.get("q", 1.0),
             "z": e["z"], "stat": e["stat"], "verdict": e["verdict"]} for e in lb_top],
        "history": [{"ts": h[1], "best_q": h[4], "alert": bool(h[9])} for h in history],
    }
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"[cycle] best_q={best_q:.4g}  best={best['sig']}/{best['test']}  "
          f"oos_p={oos_p if oos_p is None else round(oos_p,4)}  alert={alert}")
    print(f"[cycle] state -> {STATE}")


if __name__ == "__main__":
    main()
