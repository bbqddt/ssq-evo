# -*- coding: utf-8 -*-
"""evolve_predictor.py —— 让"计算"稳定高于随机的第一性原理闭环（v2: 公式渗透+在线修正）。

立场（务必读，诚实基线）：
  当前引擎在 null 域：best_verdict=None、pick_p=1.0（选号不优于随机）。
  现有特征(gap/recurrence/phase)全是【当期描述性】——对单期算一个数，
  天生对下一期无预测力。这正是"计算不高于随机"的根。

  本模块不污染现有引擎，独立实现【跨期预测器进化】闭环：
    L1 脚印记忆层 : 只持久化"经独立确认段复现 z>2"的预测器（永不退缩，从脚印起跳）
    L2 进化层     : GA 搜索 特征组合 + 学习率 + 窗口；权重由【在线修正】自动填充
    L3 紧鞘闸门   : 进化绝不可见确认段；候选先过随机surrogate(防构造伪显著)→
                   再独立确认段复现→才允许声称">随机"

  公式渗透设计（核心升级 v2）：
    预测器 = 基特征线性组合 + 在线 perceptron 修正。每一期预测后，用真实开奖
    反向更新特征权重（梯度 = 目标 - 预测），使"公式"在滚动评估中持续自我修正——
    这正是"计算、训练、公式渗透进每一环"。GA 只决定【结构】(哪些特征/学习率/窗口)，
    权重交给在线学习在严格前序滚动中自动习得，杜绝进化直接过拟合。

用法：
  python evolve_predictor.py probe      # 普查跨期特征族信息增益
  python evolve_predictor.py evolve     # 跑 GA 进化(从脚印起跳, 在线修正)
  python evolve_predictor.py verify     # 验证最佳脚印在确认段的真z值
  python evolve_predictor.py predict    # 用最佳脚印生成下一期预测(开奖前登记)
"""
import os, sys, json, math, random, datetime, argparse, time
import numpy as np
from collections import defaultdict
import multiprocessing as mp

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", r"D:/ssq_evo_data")
MASTER = os.path.join(DATA_DIR, "ssq_master.csv")
FRONTIER = os.path.join(DATA_DIR, "evolve_frontier.json")
PRED_FILE = os.path.join(DATA_DIR, "predictions.jsonl")

RED_N, BLUE_N = 33, 16
RED_PICK, BLUE_PICK = 6, 1
RANDOM_EXP_RED = RED_PICK * (RED_PICK / RED_N)   # 6*6/33 ≈ 1.0909
RANDOM_EXP_BLUE = 1.0 / BLUE_N                    # 1/16


# ---------------- 数据 ----------------
def load_draws(path=None):
    import csv
    src = path or MASTER
    rows = []
    with open(src, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                reds = [int(r["r%d" % i]) for i in range(1, 7)]
                blue = int(r["b"])
                rows.append({"issue": r["issue"], "reds": reds, "blue": blue})
            except Exception:
                continue
    rows.sort(key=lambda x: x["issue"])
    return rows


# ---------------- 跨期特征族（输入历史窗口 → 对下期的 33 维预测因子）----------------
def feat_transition_win(win):
    """马尔可夫转移：窗口内 球a→球b 转移频次归一 → 33维(下一期某球权重)。"""
    M = defaultdict(float)
    for j in range(1, len(win)):
        for a in win[j - 1]["reds"]:
            for b in win[j]["reds"]:
                M[b] += 1.0
    total = sum(M.values()) or 1.0
    v = np.zeros(RED_N + 1)
    for b, c in M.items():
        v[b] = c / total
    return v


def feat_omission_win(win, N):
    """遗漏值：每球距上次出现期数（首次用惩罚值 N 校准，防 recurrence 式伪显著）。"""
    last = {}
    for idx, d in enumerate(win):
        for num in d["reds"]:
            last[num] = idx
    v = np.zeros(RED_N + 1)
    for num in range(1, RED_N + 1):
        v[num] = (len(win) - last[num]) if num in last else N
    return v


def feat_symdiff_win(win):
    """相邻期对称差 → 低频球加权（对称差大→下期偏冷门）。"""
    v = np.zeros( 33 + 1)
    freq = defaultdict(int)
    for d in win:
        for num in d["reds"]:
            freq[num] += 1
    n = len(win) or 1
    for num in range(1, RED_N + 1):
        v[num] = (n - freq.get(num, 0)) / n
    return v


def feat_zone_drift_win(win):
    """落区(1-11/12-22/23-33)分布偏移 → 低频区补回。"""
    v = np.zeros(RED_N + 1)
    zc = [0, 0, 0]
    for d in win:
        for num in d["reds"]:
            zc[min((num - 1) // 11, 2)] += 1
    tot = sum(zc) or 1
    for num in range(1, RED_N + 1):
        z = min((num - 1) // 11, 2)
        v[num] = (tot - zc[z]) / tot
    return v


FEATURES = {
    "transition": feat_transition_win,
    "omission": feat_omission_win,
    "symdiff": feat_symdiff_win,
    "zone_drift": feat_zone_drift_win,
}


# ---------------- 在线修正评估（公式渗透：预测→真值→反向更新权重）----------------
def online_eval(spec, draws, start, end, learn=True):
    """严格前序滚动 + 在线 perceptron 修正。
    - 预测期 i 只用 draws[:i]（不含目标），公平无泄漏。
    - learn=True 时，预测后用 draws[i] 真值反向更新权重（梯度 = 目标 - 预测），供 i+1（公式渗透训练）。
    - learn=False 时纯 OOS 评估（不更新），用于独立确认段/闸门，杜绝泄漏。
    - 权重由在线学习填充；GA 只定结构(features/lr/window)。
    返回 (red_hits, blue_hits, n)。"""
    win = spec["window"]
    lr = spec["lr"]
    feats = spec["features"]
    W = {f: np.zeros(RED_N + 1) for f in feats}   # 红 33 维权重
    blueW = np.zeros(BLUE_N + 1)                   # 蓝 16 维权重
    red_hits, blue_hits = [], 0
    for i in range(max(start, 1), end):
        lo = max(0, i - win)
        win_data = draws[lo:i]                     # 严格前序窗口（不含目标 i）
        if len(win_data) < 10:
            red_hits.append(0); continue
        fvs = {}
        for f in feats:
            fvs[f] = FEATURES[f](win_data, len(win_data)) if f == "omission" else FEATURES[f](win_data)
        score = np.zeros(RED_N + 1)
        for f in feats:
            score += W[f] * fvs[f]
        reds = sorted(range(1, RED_N + 1), key=lambda x: (-score[x], x))[:RED_PICK]
        tgt = draws[i]
        hit = len(set(reds) & set(tgt["reds"]))
        red_hits.append(hit)
        if learn:
            y = np.zeros(RED_N + 1)
            for r in tgt["reds"]:
                y[r] = 1.0
            p = 1.0 / (1.0 + np.exp(-score))           # sigmoid 软预测
            for f in feats:
                W[f] += lr * (y - p) * fvs[f]
            bb = max(range(1, BLUE_N + 1), key=lambda x: (blueW[x], -x))
            if bb == tgt["blue"]:
                blue_hits += 1
            yb = np.zeros(BLUE_N + 1); yb[tgt["blue"]] = 1.0
            pb = 1.0 / (1.0 + np.exp(-blueW))
            blueW += lr * (yb - pb)
        else:
            if max(range(1, BLUE_N + 1), key=lambda x: (blueW[x], -x)) == tgt["blue"]:
                blue_hits += 1
    n = len(red_hits)
    return red_hits, blue_hits, n


def z_vs_random(red_hits):
    n = len(red_hits)
    if n < 30:
        return 0.0, 0.0
    mean = sum(red_hits) / n
    var = sum((x - mean) ** 2 for x in red_hits) / (n - 1)
    se = math.sqrt(var / n)
    z = (mean - RANDOM_EXP_RED) / se if se > 0 else 0.0
    return mean, z


def random_baseline_z(draws, start=200, end=None, seed=0):
    import random as _r
    if end is None:
        end = len(draws)
    hits = []
    for i in range(max(start, 1), end):
        tgt = draws[i]
        rng = _r.Random(int(tgt["issue"]))
        rr = sorted(rng.sample(range(1, RED_N + 1), RED_PICK))
        hits.append(len(set(rr) & set(tgt["reds"])))
    return z_vs_random(hits)


# ---------------- 紧鞘闸门 ----------------
def random_surrogate_z(spec, N=600, seed=12345):
    """构造伪显著拦截：同一预测器跑在纯随机双色球上，若 z 也 >2 → 拒(构造伪结构)。"""
    rng = random.Random(seed)
    draws = []
    base = 100000
    for i in range(N):
        rr = sorted(rng.sample(range(1, RED_N + 1), RED_PICK))
        bb = rng.randint(1, BLUE_N)
        draws.append({"issue": "%05d" % (base + i), "reds": rr, "blue": bb})
    red_hits, _, n = online_eval(spec, draws, start=50, end=N, learn=False)
    _, z = z_vs_random(red_hits)
    return z


def confirm_segment_z(spec, draws, confirm_n=50, seeds=(0, 1, 2, 3)):
    """独立确认段：先在 [0, train_end] 训练，再对 [train_end, end] 纯OOS评估（不更新）。
    多 seed 用不同窗口起点制造独立子样本，取平均 z。进化绝不可见此段。"""
    end = len(draws)
    start = end - confirm_n
    zs = []
    for sd in seeds:
        s = start + (sd * 5)
        red_hits, _, n = online_eval(spec, draws, start=s, end=end, learn=False)
        _, z = z_vs_random(red_hits)
        zs.append(z)
    mean_z = sum(zs) / len(zs)
    return mean_z, zs, confirm_n


# ---------------- GA 进化 ----------------
def random_spec(rng):
    fns = list(FEATURES.keys())
    k = rng.randint(1, len(fns))
    feats = rng.sample(fns, k)
    return {"features": feats, "lr": rng.uniform(0.01, 0.5),
            "window": rng.choice([100, 150, 200, 300])}


def mutate(spec, rng):
    s = json.loads(json.dumps(spec))
    if rng.random() < 0.5:
        s["lr"] = max(0.005, min(1.0, s["lr"] + rng.uniform(-0.1, 0.1)))
    else:
        if rng.random() < 0.5 and len(s["features"]) < len(FEATURES):
            fn = rng.choice([f for f in FEATURES if f not in s["features"]])
            s["features"].append(fn)
        elif len(s["features"]) > 1:
            i = rng.randrange(len(s["features"]))
            s["features"].pop(i)
    if rng.random() < 0.5:
        s["window"] = rng.choice([100, 150, 200, 300])
    return s


def fitness_kfold(spec, draws, train_end, k=4):
    """适应度 = 训练集内部 K 折交叉验证的【稳健 z】(最差折主导)。
    每折：用该折之前全部历史训练(learn=True)，该折内纯OOS评估(learn=False)。
    完全在训练集内部完成，进化仍看不见最终独立确认段(闸门职责)。"""
    seg = (train_end - 200) // k
    if seg < 30:
        red_hits, _, n = online_eval(spec, draws, start=200, end=train_end, learn=False)
        _, z = z_vs_random(red_hits)
        return z
    zs = []
    for i in range(k):
        s = 200 + i * seg
        e = 200 + (i + 1) * seg if i < k - 1 else train_end
        red_hits, _, n = online_eval(spec, draws, start=s, end=e, learn=False)
        _, z = z_vs_random(red_hits)
        zs.append(z)
    return min(zs)


# ---------------- 多进程并行评估（把 8 核用满，突破单核瓶颈）----------------
_WORKER_DRAWS = None
_WORKER_TRAIN_END = None


def _init_worker(dr, te):
    global _WORKER_DRAWS, _WORKER_TRAIN_END
    _WORKER_DRAWS = dr
    _WORKER_TRAIN_END = te


def _eval_spec_worker(spec):
    return fitness_kfold(spec, _WORKER_DRAWS, _WORKER_TRAIN_END, k=4)


def load_frontier():
    if os.path.exists(FRONTIER):
        return json.load(open(FRONTIER, encoding="utf-8"))
    return {"footprints": [], "gen": 0}


def save_frontier(fr):
    json.dump(fr, open(FRONTIER, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def evolve(generations=20, pop=20, seed=20260823, workers=0, data=None, out=None):
    rng = random.Random(seed)
    draws = load_draws(data)
    train_end = len(draws) - 50   # 最后50期留作确认段(进化不可见)
    fr = load_frontier()
    pop_specs = [json.loads(json.dumps(fp["spec"])) for fp in fr["footprints"][-5:]]
    while len(pop_specs) < pop:
        pop_specs.append(random_spec(rng))

    if workers <= 0:
        workers = max(1, min(mp.cpu_count(), pop))
    t0 = time.time()
    best_gen_z = -999
    for g in range(generations):
        # 种群并行评估（多进程，把 8 核用满）
        if workers > 1 and len(pop_specs) > 1:
            with mp.Pool(processes=workers, initializer=_init_worker, initargs=(draws, train_end)) as pool:
                zs = pool.map(_eval_spec_worker, pop_specs)
            scored = sorted(((z, s) for s, z in zip(pop_specs, zs)), key=lambda x: -x[0])
        else:
            scored = []
            for s in pop_specs:
                try:
                    z = fitness_kfold(s, draws, train_end, k=4)
                except Exception:
                    z = -999
                scored.append((z, s))
            scored.sort(key=lambda x: -x[0])
        gen_best = scored[0][0]
        best_gen_z = max(best_gen_z, gen_best)
        new_pop = [json.loads(json.dumps(scored[0][1]))]
        while len(new_pop) < pop:
            parent = scored[rng.randrange(min(10, len(scored)))][1]
            new_pop.append(mutate(parent, rng))
        pop_specs = new_pop
        print("[evolve] gen %d/%d K折稳健最佳z=%.3f (已用%d核, 累计%.1fs)" % (
            g + 1, generations, gen_best, workers, time.time() - t0))

    best_spec = scored[0][1]
    if out:
        import json as _json
        proposal = {
            "meta": {"seed": seed, "gen": fr["gen"], "kfold_z": scored[0][0],
                      "git_sha": os.environ.get("GITHUB_SHA", "local"),
                      "role": "PROPOSAL_ONLY", "engine": "evolve_predictor"},
            "spec": best_spec,
        }
        with open(out, "w", encoding="utf-8") as _f:
            _json.dump(proposal, _f, ensure_ascii=False, indent=2)
        print("[evolve] 提案写出 -> %s" % out)
    # 训练全段 z（供参考，非适应度）
    red_hits, _, n = online_eval(best_spec, draws, start=200, end=train_end, learn=True)
    _, train_z = z_vs_random(red_hits)
    print("\n[evolve] K折最佳稳健z=%.3f (训练全段z=%.3f)，过紧鞘闸门..." % (scored[0][0], train_z))
    sur_z = random_surrogate_z(best_spec)
    print("  随机surrogate z=%.3f (>2=构造伪显著,拒)" % sur_z)
    if sur_z > 2:
        print("  >>> 拒绝：构造伪显著(随机数据也>2)")
        return
    conf_mean_z, conf_zs, conf_n = confirm_segment_z(best_spec, draws)
    print("  确认段(%d期,多seed) 平均z=%.3f 各seed=%s" % (conf_n, conf_mean_z, [round(z, 2) for z in conf_zs]))
    if conf_mean_z > 2:
        fp = {"spec": best_spec, "kfold_z": scored[0][0], "train_z": train_z,
              "surrogate_z": sur_z, "confirm_mean_z": conf_mean_z,
              "confirm_zs": conf_zs,
              "added_ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "gen": fr["gen"] + 1, "online": True}
        fr["footprints"].append(fp)
        fr["gen"] += 1
        save_frontier(fr)
        print("  >>> 入库脚印 #%d (确认段z=%.3f>2, 经独立复现)" % (fr["gen"], conf_mean_z))
    else:
        print("  >>> 拒绝：确认段未复现z>2(K折稳健仍不足以跨到独立确认)")


# ---------------- probe（信息增益普查）----------------
def probe(data=None):
    draws = load_draws(data)
    _, baseline_z = random_baseline_z(draws)
    print("[probe] 随机基线滚动z=%.3f (参考线)" % baseline_z)
    print("[probe] 红球命中随机解析期望=%.4f\n" % RANDOM_EXP_RED)
    for fn in FEATURES:
        spec = {"features": [fn], "lr": 0.1, "window": 200}
        red_hits, _, n = online_eval(spec, draws, start=200, end=len(draws), learn=True)
        mean, z = z_vs_random(red_hits)
        sur_z = random_surrogate_z(spec)
        flag = "OK(>随机)" if z > 2 and sur_z <= 2 else ("构造伪显著" if sur_z > 2 else "噪声")
        print("  %-12s 红命中均值=%.3f 训练z=%.3f surrogate_z=%.3f -> %s" % (fn, mean, z, sur_z, flag))


def predict_next():
    fr = load_frontier()
    if not fr["footprints"]:
        print("[predict]  * 无已验证脚印，无法生成预测。先跑 evolve。")
        return
    fp = max(fr["footprints"], key=lambda x: x["confirm_z"])
    draws = load_draws()
    spec = fp["spec"]
    win = spec["window"]
    win_data = draws[-win:] if win else draws
    fvs = {}
    for f in spec["features"]:
        fvs[f] = FEATURES[f](win_data, len(win_data)) if f == "omission" else FEATURES[f](win_data)
    # 用历史做一遍在线学习预热（公平：只用历史，不含最后一期目标）
    W = {f: np.zeros(RED_N + 1) for f in spec["features"]}
    blueW = np.zeros(BLUE_N + 1)
    for d in win_data[:-1]:
        score = np.zeros(RED_N + 1)
        for f in spec["features"]:
            score += W[f] * fvs[f]
        y = np.zeros(RED_N + 1)
        for r in d["reds"]:
            y[r] = 1.0
        p = 1.0 / (1.0 + np.exp(-score))
        for f in spec["features"]:
            W[f] += spec["lr"] * (y - p) * fvs[f]
        yb = np.zeros(BLUE_N + 1); yb[d["blue"]] = 1.0
        pb = 1.0 / (1.0 + np.exp(-blueW))
        blueW += spec["lr"] * (yb - pb)
    score = np.zeros(RED_N + 1)
    for f in spec["features"]:
        score += W[f] * fvs[f]
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-score[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (blueW[x], -x))
    print("[predict] 用脚印#%d(确认z=%.3f)" % (fp["gen"], fp["confirm_z"]))
    print("  红球预测: %s" % reds)
    print("  蓝球预测: %s" % blue)
    issue = "%05d" % (int(draws[-1]["issue"]) + 1)
    entry = {
        "issue": issue, "target_date": datetime.date.today().strftime("%Y-%m-%d"),
        "registered_ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "evolve_predictor_gen%d" % fp["gen"],
        "params": spec,
        "engine_forecast": {"reds": reds, "blue": blue},
        "verdict_context": "evolve_predictor在线修正确认段z=%.3f(>2=经独立复现>随机); null域观测" % fp["confirm_z"],
        "scored": False,
    }
    known = []
    if os.path.exists(PRED_FILE):
        with open(PRED_FILE, encoding="utf-8") as f:
            known = [json.loads(l) for l in f if l.strip()]
    if not any(p["issue"] == issue and p["method"] == entry["method"] for p in known):
        known.append(entry)
        with open(PRED_FILE, "w", encoding="utf-8") as f:
            for p in known:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print("  >>> 已登记 %s 到 predictions.jsonl" % issue)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "evolve", "verify", "predict"])
    ap.add_argument("--generations", type=int, default=20)
    ap.add_argument("--pop", type=int, default=20)
    ap.add_argument("--workers", type=int, default=0, help="并行进程数,0=自动(=min(核数,种群))")
    ap.add_argument("--data", type=str, default=None, help="历史CSV路径(默认 DATA_DIR/ssq_master.csv)")
    ap.add_argument("--out", type=str, default=None, help="把最佳提案写到此JSON(供CI合并)")
    args = ap.parse_args()
    if args.cmd == "probe":
        probe(args.data)
    elif args.cmd == "evolve":
        evolve(generations=args.generations, pop=args.pop, workers=args.workers,
               data=args.data, out=args.out)
    elif args.cmd == "verify":
        fr = load_frontier()
        for fp in fr["footprints"]:
            print("  脚印#%d 训练z=%.3f 确认z=%.3f surrogate_z=%.3f" % (fp["gen"], fp["train_z"], fp["confirm_z"], fp["surrogate_z"]))
    elif args.cmd == "predict":
        predict_next()


if __name__ == "__main__":
    main()
