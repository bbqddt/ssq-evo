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
# DATA_DIR: 优先用环境变量(Docker 部署传 /app/data)；未设置时默认 D:\ssq_evo_data(本机全在 D 盘，不写 C)
DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    _d = r"D:\ssq_evo_data"
    DATA_DIR = _d if os.path.isdir(_d) else HERE
os.makedirs(DATA_DIR, exist_ok=True)
sys.path.insert(0, HERE)

import engine_core as E
import data as D
import store as S
import frontier as F

MASTER = os.path.join(DATA_DIR, "ssq_master.csv")
DB = os.path.join(DATA_DIR, "ssq_evo.db")
STATE = os.path.join(DATA_DIR, "state.json")

# ---- 可调参数（部署时可改 config.json）----
DEFAULT_CFG = {
    "epochs": 6, "pop": 24, "k_light": 25, "k_heavy": 10,
    "seed": 20260813, "oos_frac": 0.2, "alert_q": 0.01, "alert_oos_p": 0.01,
}


# ============================================================
# 嵌入式自检机制 —— 每轮 cycle 自动运行，主动暴露问题
# 不等用户截图发现。覆盖：OOS 平凡解 / 配置漂移 /
# Dockerfile 漏拷 / best_q 漂移 / state 一致性
# ============================================================
def self_check(state, acc, cfg):
    """返回 dict: {warnings: [str], score: int(0=干净, 越大问题越多)}。
    结果写入 state['self_check'] 并打印到日志。"""
    warns = []
    score = 0

    # 1) OOS 平凡解检测（osc k=1 类 bug 的通用指纹）
    if acc:
        if (acc.get("hit_rate") == 1.0 and acc.get("sur_mean", 0) >= 0.99
                and acc.get("p_random", 1) >= 0.95):
            warns.append(
                "OOS 平凡解: hit_rate=1.0 & sur_mean≈1.0 & p≈1.0"
                " —— 可能是 osc k=1 或类似退化规则导致的伪完美准确率")
            score += 3
        if acc.get("n", 0) < 30:
            warns.append(f"OOS 样本量过小(n={acc['n']}<30)，统计量不足")
            score += 1
        # 新增：osc k=1 直接从 params 里抓
        top_params = (state.get("leaderboard") or [{}])[0].get("params") or {}
        comp = top_params.get("_comp")
        if isinstance(comp, dict) and comp.get("read") == "osc" and int(comp.get("k", 1)) <= 1:
            warns.append("基因组参数异常: read=osc 且 k<=1(平凡解)，已强制 k>=2")
            score += 3

    # 2) 配置一致性（源 vs 运行时）
    src_cfg = os.path.join(HERE, "config.json")
    run_cfg = os.path.join(DATA_DIR, "config.json")
    if os.path.exists(src_cfg) and os.path.exists(run_cfg):
        try:
            sc = json.load(open(src_cfg, encoding="utf-8"))
            rc = json.load(open(run_cfg, encoding="utf-8"))
            for key in ("k_light", "k_heavy", "schedule_hours", "epochs"):
                sv, rv = sc.get(key), rc.get(key)
                if sv != rv:
                    warns.append(f"配置漂移: {key} 源={sv} vs 运行时={rv}(容器可能用了旧配置)")
                    score += 2
        except Exception:
            pass

    # 3) Dockerfile COPY 完整性（只检查本项目 .py 文件是否漏拷）
    df = os.path.join(HERE, "Dockerfile")
    if os.path.exists(df):
        try:
            df_text = open(df, encoding="utf-8").read()
            # 本项目的 .py 文件（排除 __pycache__ / venv / .git）
            proj_py = set()
            for f in os.listdir(HERE):
                fp = os.path.join(HERE, f)
                if f.endswith(".py") and os.path.isfile(fp):
                    # 排除纯本地开发工具（benchmark/smoke_test 等）
                    if f.startswith(("benchmark_", "smoke_", "test_")):
                        continue
                    proj_py.add(f)
            missing_prod = [f for f in proj_py if f not in df_text]
            if missing_prod:
                warns.append(f"Dockerfile 漏拷生产文件: {missing_prod}")
                score += len(missing_prod) * 2
        except Exception:
            pass

    # 4) best_q 漂移检测（读最近快照）
    try:
        snaps = sorted([f for f in os.listdir(DATA_DIR)
                         if f.startswith("state.") and f.endswith(".json")])
        if len(snaps) >= 5:
            recent_qs = []
            for sn in snaps[-5:]:
                sp = json.load(open(os.path.join(DATA_DIR, sn), encoding="utf-8"))
                q = sp.get("best_q")
                if q is not None:
                    recent_qs.append(q)
            if len(recent_qs) >= 3:
                mq = np.mean(recent_qs)
                cq = state.get("best_q", 1)
                if abs(cq - mq) > 0.15:
                    warns.append(
                        f"best_q 剧烈漂移: 当前={cq:.4f}, 近5轮均值={mq:.4f}"
                        f"(搜索前沿未收敛或过拟合)")
                    score += 1
    except Exception:
        pass

    # 5) 结构 vs OOS 矛盾检测
    bq = state.get("best_q", 1)
    cross_ok = state.get("oos_cross_consistent", False)
    if bq < 0.05 and cross_ok and acc and not acc.get("above_random"):
        warns.append(
            "结构显著(best_q<0.05, 交叉一致)但 OOS 不高于随机"
            " —— 可能是训练集过拟合或 OOS 规则退化")
        score += 2

    result = {"warnings": warns, "score": score, "ts": state.get("updated")}
    # 打印摘要
    if warns:
        print(f"[self_check] ⚠️  发现 {len(warns)} 个问题(score={score}):")
        for w in warns:
            print(f"  - {w}")
    else:
        print("[self_check] ✅ 全部通过")
    return result


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

    # 2. 演化（接入跨轮 frontier：精英 seed + 参数 hill-climbing + 去重）
    rng = np.random.default_rng(cfg["seed"] + N)  # 随样本量变化种子，避免每轮完全相同
    fr = F.load_frontier(DATA_DIR)
    elite_seeds = fr.get("elites", [])
    print(f"[cycle] frontier: 历史覆盖度={fr.get('coverage',0)}, 精英种子={len(elite_seeds)}, "
          f"z历史长度={len(fr.get('best_z_history',[]))}")
    evo = E.Evolution(reds, blues, rng, k_light=cfg["k_light"], k_heavy=cfg["k_heavy"],
                      epochs=cfg["epochs"], pop=cfg["pop"],
                      elites=elite_seeds, frontier=fr)
    leaderboard, all_evals = evo.run()
    print(f"[cycle] 评估算子数(含重复): {len(all_evals)}, 唯一基因组: {len(leaderboard)}")

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

    # 5b. 诚实的"高于随机"方向准确率（准确率由公式读取规则决定 + AAFT 替代分布零假设）
    acc = None
    if lb_items:
        acc = E.oos_accuracy(top, reds, blues, rng, frac=cfg["oos_frac"], k_sur=cfg["k_light"])

    # 5c. 多零假设交叉验证（AAFT vs IAAFT）：最优候选是否在两套零假设下都显著？
    cross = None
    if lb_items:
        cross = E.cross_validate_null(top, reds, blues, rng, frac=cfg["oos_frac"], k_sur=cfg["k_light"])

    alert = (best_q < cfg["alert_q"]) and (oos_p is not None) and (oos_p < cfg["alert_oos_p"])
    # 诚实闸门：方向准确率"高于随机"必须先过结构 FDR 显著(best_q<0.05)，否则只是
    # 从大量候选中挑最优再测的"选择性偏差"产物，不能作为结论。
    oos_above = bool(acc and acc["above_random"] and best_q < 0.05)

    # 6. 更新并持久化演化前沿（跨轮累积迭代）
    prev_tried = len(fr.get("tried", []))
    fr = F.update_frontier(fr, leaderboard, evo.tried, elite_k=12)
    if acc:
        hist = fr.setdefault("acc_history", [])
        hist.append({"hr": round(acc["hit_rate"], 4),
                     "sur_mean": round(acc["sur_mean"], 4),
                     "p_random": round(acc["p_random"], 4),
                     "above": bool(acc["above_random"]),
                     "n": int(acc["n"])})
        fr["acc_history"] = hist[-200:]
    F.save_frontier(DATA_DIR, fr)
    z_hist = fr["best_z_history"]
    newly = fr["coverage"] - prev_tried
    print(f"[cycle] 迭代进度: 覆盖度={fr['coverage']} (本论新增 {newly}), "
          f"精英={len(fr['elites'])}, z轨迹末值={z_hist[-1] if z_hist else 'NA'}")

    # 7. 持久化
    con = S.open_db(DB)
    run = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_issues": N, "added": added, "n_eval": len(all_evals),
        "best_q": best_q, "best_sig": best["sig"], "best_test": best["test"],
        "best_p": best["p_raw"], "oos_p": (oos_p if oos_p is not None else -1.0),
        "oos_acc": (round(acc["hit_rate"], 4) if acc else None),
        "oos_acc_sur": (round(acc["sur_mean"], 4) if acc else None),
        "oos_acc_p": (round(acc["p_random"], 4) if acc else None),
        "oos_acc_above": oos_above,
        "oos_acc_n": (acc["n"] if acc else 0),
        "oos_cross_aaft": (round(cross["aaft"], 4) if cross and cross.get("aaft") is not None else None),
        "oos_cross_iaaft": (round(cross["iaaft"], 4) if cross and cross.get("iaaft") is not None else None),
        "oos_cross_consistent": (bool(cross["consistent"]) if cross else False),
        "alert": alert, "coverage": fr["coverage"],
        "note": ("候选结构! 需人工复核" if alert else "无超越随机的可提取结构 (null)"),
    }
    rid = S.insert_run(con, run)
    S.insert_evals(con, rid, all_evals)
    con.close()

    # 8. 写 state.json
    lb_top = sorted(leaderboard.values(), key=lambda e: e["p_raw"])[:20]
    history = S.recent_runs(S.open_db(DB), 200)
    state = {
        "updated": run["ts"], "n_issues": N, "last_issue": issues[-1], "added": added,
        "cycle_id": rid, "best_q": best_q, "best_sig": best["sig"], "best_test": best["test"],
        "best_p": best["p_raw"], "best_stat": best["stat"], "best_z": best["z"],
        "oos_p": (oos_p if oos_p is not None else None),
        "oos_acc": (round(acc["hit_rate"], 4) if acc else None),
        "oos_acc_sur": (round(acc["sur_mean"], 4) if acc else None),
        "oos_acc_p": (round(acc["p_random"], 4) if acc else None),
        "oos_acc_above": oos_above,
        "oos_acc_n": (acc["n"] if acc else 0),
        "oos_cross_aaft": (round(cross["aaft"], 4) if cross and cross.get("aaft") is not None else None),
        "oos_cross_iaaft": (round(cross["iaaft"], 4) if cross and cross.get("iaaft") is not None else None),
        "oos_cross_primary_type": (cross.get("primary_type") if cross else None),
        "oos_cross_primary": (round(cross["primary"], 4) if cross and cross.get("primary") is not None else None),
        "oos_cross_consistent": (bool(cross["consistent"]) if cross else False),
        "alert": bool(alert),
        "n_eval": len(all_evals), "n_unique": len(leaderboard),
        "coverage": fr["coverage"], "elite_count": len(fr["elites"]),
        "best_z_history": z_hist,
        "params": cfg,
        "leaderboard": [
            {"sig": e["sig"], "test": e["test"],
             "params": e.get("params", {"_sig": {}, "_test": {}}),
             "p_raw": e["p_raw"], "q": e.get("q", 1.0),
             "z": e["z"], "stat": e["stat"], "verdict": e["verdict"]} for e in lb_top],
        "history": [{"ts": h[1], "best_q": h[4], "alert": bool(h[9]),
                     "coverage": h[10] if len(h) > 10 else None} for h in history],
    }
    # 7.5 嵌入式自检（主动暴露问题，不等用户发现）
    sc_result = self_check(state, acc, cfg)
    state["self_check"] = {
        "score": sc_result["score"],
        "warnings": sc_result["warnings"],
        "ts": sc_result["ts"],
    }

    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 8b. 滚动快照（防坏 state 丢历史；保留最近 20 个，避免磁盘膨胀）
    import shutil
    snap = os.path.join(DATA_DIR, f"state.{state['cycle_id']}.json")
    shutil.copy2(STATE, snap)
    # 清理旧快照（保留最近 20）
    snaps = sorted([f for f in os.listdir(DATA_DIR) if f.startswith("state.") and f.endswith(".json")])
    for old in snaps[:-20]:
        try:
            os.remove(os.path.join(DATA_DIR, old))
        except OSError:
            pass

    print(f"[cycle] best_q={best_q:.4g}  best={best['sig']}/{best['test']}  "
          f"oos_p={oos_p if oos_p is None else round(oos_p,4)}  alert={alert}")
    if acc:
        if oos_above:
            tag = "高于随机(且结构FDR显著)!"
            note = ""
        elif acc["above_random"]:
            tag = "探索性高于随机"
            note = "（结构未达FDR显著，系从大量候选挑最优再测的选择性偏差，非结论）"
        else:
            tag = "未高于随机(=随机基线)"
            note = ""
        print(f"[cycle] 方向准确率={acc['hit_rate']:.3f}  随机基线={acc['sur_mean']:.3f}  "
              f"p_random={acc['p_random']:.3f}  => {tag}  最优读法={acc.get('best_rule')}{note}")
    if cross:
        print(f"[cycle] 零假设交叉验证: primary({cross.get('primary_type')}) p={cross.get('primary')}  "
              f"AAFT p={cross.get('aaft')}  IAAFT p={cross.get('iaaft')}  "
              f"三零假设一致显著={cross.get('consistent')}")
    print(f"[cycle] state -> {STATE}")

    # 8c. 生成监控看板 (自包含 HTML，供 CloudStudio 部署到腾讯云作为第三辆车的可视化/分享层)
    try:
        import make_dashboard
        make_dashboard.main()
    except Exception as de:
        print(f"[cycle] dashboard 生成失败(不影响主流程): {de}")


if __name__ == "__main__":
    main()
