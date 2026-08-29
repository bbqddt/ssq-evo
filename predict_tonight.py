# -*- coding: utf-8 -*-
"""今晚开奖预注册 + 历史滚动样本外回测 + 开奖后打分。

立场（务必读）：ssq_evo 是 null 域结构搜索引擎。引擎最新结论：
  - walk-forward #41 = NULL（最佳候选 red_gap_max 未过确认分离）
  - 谱扫描 = 构造伪结构[随机对照闸门]
  - 排行榜第一 red_recurrence_mean 已被判构造伪结构（禁用）
=> 诚实结论：未发现经确认的可复现结构。

因此本模块的"预测"不是验证过的模型，而是 **null 域下的一次预注册猜测**：
用近期结构跟随启发式（红球/蓝球边际频率的递推加权）生成一组号码，
开奖前登记（时间戳不可篡改），开奖后打分，并看它是否优于随机基线。
预期：不显著优于随机 —— 这本身就是对 NULL 结论的一次独立外样本检验。

用法：
  python predict_tonight.py register --issue 26094 --date 2026-08-16
  python predict_tonight.py backtest  --range 500
  python predict_tonight.py score    --issue 26094   # 开奖后：先 fetch 再打分
"""
import os, sys, csv, json, random, math, argparse, datetime
import paths
import ssq_log

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = paths.DATA_DIR
MASTER = os.path.join(DATA_DIR, "ssq_master.csv")
PRED_FILE = os.path.join(DATA_DIR, "predictions.jsonl")

RED_N, BLUE_N = 33, 16
RED_PICK, BLUE_PICK = 6, 1

# 预测方法默认超参
WINDOW = 150      # 尾窗长度（前序多少期参与）
DECAY = 0.985     # 指数衰减（越近期权重越高）；0.985^150≈0.10

# ---------------- 公式驱动（来自引擎进化存活的复合公式树）----------------
# 这些树是 formula_evolution 的产出（frontier.comp_elites），gen=4 复合结构。
# 我们用 engine_core._build_comp 把它们编译成"每期一个标量"的信号序列，
# 再接态筛选+边际投票选号框架——让公式演进成果真正参与计算。
FRONTIER_PATH = os.path.join(DATA_DIR, "frontier.json")
try:
    import numpy as _np
    sys.path.insert(0, HERE)
    import engine_core as _EC
    _EC_OK = True
except Exception as _e:
    _EC_OK = False
    _EC_ERR = _e

_MATH_COMB = math.comb


def load_comp_trees():
    """读 frontier.json 的 comp_elites（gen=4 复合公式树）。返回可编译的树列表。"""
    if not _EC_OK:
        return []
    try:
        fr = json.load(open(FRONTIER_PATH, encoding="utf-8"))
        return fr.get("comp_elites", []) or []
    except Exception:
        return []


def _build_comp_signals(trees, draws):
    """把每棵复合树编译成全长度信号序列（因果：x[j] 只用 ≤j 期数据）。
    返回 dict: idx -> np.array(长度=len(draws))。编译失败的树被跳过。"""
    if not trees:
        return {}
    all_reds = _np.array([d["reds"] for d in draws], dtype=float)
    all_blues = _np.array([d["blue"] for d in draws], dtype=float)
    out = {}
    for i, t in enumerate(trees):
        try:
            x = _EC._build_comp(t, all_reds, all_blues)
            x = _np.asarray(x, dtype=float)
            if x.shape[0] != len(draws) or not _np.all(_np.isfinite(x)):
                continue
            out[i] = x
        except Exception:
            continue
    return out


def predict_from_comp_ensemble(trees, draws, window=WINDOW, decay=DECAY,
                               tol=1.6, regime_n=30):
    """复合公式树集成投票选号：每棵树独立做态筛选+边际选 top6+top1，
    再对 33 红球/16 蓝球做跨树投票，取 top6+top1。
    返回 (reds, blue, info)。退化为单树或朴素边际当无树可用。"""
    sig = _build_comp_signals(trees, draws)
    if not sig:
        reds, blue = predict(draws, window, decay)
        return sorted(reds), blue, {"method": "fallback_recency", "n_trees": 0}
    red_vote = [0.0] * (RED_N + 1)
    blue_vote = [0.0] * (BLUE_N + 1)
    for ti in (None,):  # 占位，实际在 backtest 里按目标期切片
        pass
    # 注意：集成用于"已训练好全部历史的当前预测"（ti=len(draws)），
    # 严格 walk-forward 回测另见 backtest_comp()。
    ti = len(draws)
    for idx, x in sig.items():
        vals = list(x[:ti])
        regime = sum(vals[-regime_n:]) / min(regime_n, len(vals))
        sel_idx = [j for j, v in enumerate(vals) if abs(v - regime) <= tol]
        if len(sel_idx) < 20:
            sel_idx = list(range(max(0, ti - window), ti))
        sel = [draws[j] for j in sel_idx]
        w = recency_weights(len(sel), decay)
        rs, bs = [0.0] * (RED_N + 1), [0.0] * (BLUE_N + 1)
        for d, wt in zip(sel, w):
            for rb in d["reds"]:
                rs[rb] += wt
            bs[d["blue"]] += wt
        top_reds = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
        top_blue = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
        for rb in top_reds:
            red_vote[rb] += 1.0
        blue_vote[top_blue] += 1.0
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-red_vote[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (blue_vote[x], -x))
    return sorted(reds), blue, {"method": "comp_ensemble", "n_trees": len(sig)}


def backtest_comp(n_range=500, window=WINDOW, decay=DECAY, tol=1.6, regime_n=30):
    """严格 walk-forward 回测复合公式集成：每个目标期只用其前序历史编译+选号。"""
    draws = load_draws()
    trees = load_comp_trees()
    if not trees:
        print("[backtest_comp] 无可用复合树（引擎未产出 comp_elites），跳过。")
        return None
    sig = _build_comp_signals(trees, draws)
    if not sig:
        print("[backtest_comp] 所有树编译失败，跳过。")
        return None
    start = max(len(draws) - n_range, 200)
    targets = draws[start:]
    eng_red, eng_blue = [], 0
    rand_red, rand_blue = [], 0
    for tgt in targets:
        ti = draws.index(tgt)
        red_vote = [0.0] * (RED_N + 1)
        blue_vote = [0.0] * (BLUE_N + 1)
        for idx, x in sig.items():
            vals = list(x[:ti])
            regime = sum(vals[-regime_n:]) / min(regime_n, len(vals))
            sel_idx = [j for j, v in enumerate(vals) if abs(v - regime) <= tol]
            if len(sel_idx) < 20:
                sel_idx = list(range(max(0, ti - window), ti))
            sel = [draws[j] for j in sel_idx]
            w = recency_weights(len(sel), decay)
            rs, bs = [0.0] * (RED_N + 1), [0.0] * (BLUE_N + 1)
            for d, wt in zip(sel, w):
                for rb in d["reds"]:
                    rs[rb] += wt
                bs[d["blue"]] += wt
            tr = sorted(range(1, RED_N + 1), key=lambda x: (-rs[x], x))[:RED_PICK]
            tb = max(range(1, BLUE_N + 1), key=lambda x: (bs[x], -x))
            for rb in tr:
                red_vote[rb] += 1.0
            blue_vote[tb] += 1.0
        pr = sorted(range(1, RED_N + 1), key=lambda x: (-red_vote[x], x))[:RED_PICK]
        pb = max(range(1, BLUE_N + 1), key=lambda x: (blue_vote[x], -x))
        ar, ab = set(tgt["reds"]), tgt["blue"]
        eng_red.append(len(set(pr) & ar))
        eng_blue += (1 if pb == ab else 0)
        rr, rb = random_pick(int(tgt["issue"]))
        rand_red.append(len(set(rr) & ar))
        rand_blue += (1 if rb == ab else 0)
    n = len(targets)
    em, rm = sum(eng_red) / n, sum(rand_red) / n
    ebr, rbr = eng_blue / n, rand_blue / n
    exp_red = RED_PICK * (RED_PICK / RED_N)
    print(f"=== 复合公式集成 严格 walk-forward 回测（{n} 期，{len(sig)} 棵树）===")
    print(f"  引擎(公式集成) 红球命中均值 = {em:.4f}   蓝球命中率 = {ebr:.4f}")
    print(f"  随机基线        红球命中均值 = {rm:.4f}   蓝球命中率 = {rbr:.4f}")
    print(f"  随机解析期望    红球命中 = {exp_red:.4f}   蓝球命中率 = {1.0/BLUE_N:.4f}")
    print(f"  公式集成 - 随机期望(红) = {em-exp_red:+.4f}")
    var = sum((x - rm) ** 2 for x in rand_red) / max(1, len(rand_red) - 1)
    se = math.sqrt(var / n)
    z = (em - exp_red) / se if se > 0 else 0
    print(f"  相对随机基线噪声：z≈{z:.2f}（|z|<2 视为不显著）")
    return {"engine_red_mean": em, "rand_red_mean": rm, "exp_red": exp_red,
            "engine_blue_rate": ebr, "rand_blue_rate": rbr, "n": n,
            "n_trees": len(sig), "z": z}



def load_draws():
    rows = []
    with open(MASTER, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                reds = [int(r["r%d" % i]) for i in range(1, 7)]
                blue = int(r["b"])
                rows.append({"issue": r["issue"], "reds": reds, "blue": blue})
            except Exception:
                continue
    rows.sort(key=lambda x: x["issue"])
    return rows


def recency_weights(n, decay=DECAY):
    """第 0 个（最新）权重 1，第 k 个权重 decay^k。"""
    return [decay ** k for k in range(n)]


def predict(train, window=WINDOW, decay=DECAY):
    """用 train（list of draw dict）生成 top6 红球 + top1 蓝球。
    train 已按时间升序；取最后 window 个，递推加权边际频率。
    """
    recent = train[-window:] if window else train
    w = recency_weights(len(recent), decay)
    red_score = [0.0] * (RED_N + 1)
    blue_score = [0.0] * (BLUE_N + 1)
    for d, wt in zip(recent, w):
        for rb in d["reds"]:
            red_score[rb] += wt
        blue_score[d["blue"]] += wt
    # 取 top6 红球；并列时取较小号码（确定性）
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-red_score[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (blue_score[x], -x))
    return sorted(reds), blue


def random_pick(seed):
    """确定性随机基线：均匀抽 6 个不同红球 + 1 蓝球。"""
    rng = random.Random(seed)
    reds = sorted(rng.sample(range(1, RED_N + 1), RED_PICK))
    blue = rng.randint(1, BLUE_N)
    return reds, blue


# ---------------- 公式驱动预测（来自引擎进化出的存活信号）----------------
def _red_gap_max(reds):
    """引擎 best_sig=red_gap_max：排序红球相邻最大间隙（含 33->1 环绕）。"""
    s = sorted(reds)
    gaps = [s[i + 1] - s[i] for i in range(RED_PICK - 1)] + [s[0] + RED_N - s[-1]]
    return max(gaps)


SIGNAL_FUNCS = {"red_gap_max": _red_gap_max}


def predict_from_signal(train, signal="red_gap_max", window=WINDOW, decay=DECAY,
                        tol=1.6, regime_n=30):
    """用引擎进化出的最佳存活信号(red_gap_max)作'态筛选器'：
    取近期 signal 值均值作当前态(regime)，只聚合历史上落在该态 ±tol 的期，
    再做递推加权边际取 top6+top1。这比裸全局边际更'由公式驱动'：
    是信号决定了哪些历史期与当前相关，而非无差别全历史平均。"""
    if signal not in SIGNAL_FUNCS:
        raise ValueError(f"未知信号 {signal}")
    f = SIGNAL_FUNCS[signal]
    vals = [f(d["reds"]) for d in train]
    regime = sum(vals[-regime_n:]) / min(regime_n, len(vals))
    sel = [d for d, v in zip(train, vals) if abs(v - regime) <= tol]
    if len(sel) < 20:
        sel = train[-window:]  # 兜底
    w = recency_weights(len(sel), decay)
    red_score = [0.0] * (RED_N + 1)
    blue_score = [0.0] * (BLUE_N + 1)
    for d, wt in zip(sel, w):
        for rb in d["reds"]:
            red_score[rb] += wt
        blue_score[d["blue"]] += wt
    reds = sorted(range(1, RED_N + 1), key=lambda x: (-red_score[x], x))[:RED_PICK]
    blue = max(range(1, BLUE_N + 1), key=lambda x: (blue_score[x], -x))
    return sorted(reds), blue, {"signal": signal, "regime": round(regime, 3),
                                "n_selected": len(sel), "tol": tol}


# ---------------- 预注册 ----------------
def register(issue, target_date, window=WINDOW, decay=DECAY, signal=None, method=None):
    draws = load_draws()
    # 该 issue 必须尚未开奖（主表里没有），否则不是"预测"而是"回测"
    known = {d["issue"] for d in draws}
    if issue in known:
        print(f"[register] 警告：主表已含 {issue}（已开奖），这不是预测而是回测。仍按已开奖处理。")
    # 方法优先级：显式 signal > 显式 method="comp" > 有存活复合树则默认公式驱动 > 朴素边际
    trees_active = bool(load_comp_trees())
    use_comp = (method == "comp") or (method is None and signal is None and trees_active)
    if use_comp:
        trees = load_comp_trees()
        reds, blue, info = predict_from_comp_ensemble(trees, draws, window, decay)
        method_name = f"comp_ensemble_{info.get('n_trees')}trees"
        params = {"window": window, "decay": decay, "formula_driven": True,
                  "n_trees": info.get("n_trees"), "fallback": info.get("method")}
        verdict_ctx = ("formula_evolution comp_elites(gen=4 复合树)集成投票驱动; "
                       "walk-forward=NULL 但公式成果已接入选号; null域预注册猜测")
    elif signal:
        reds, blue, info = predict_from_signal(draws, signal, window, decay)
        method_name = f"signal_regime_{signal}"
        params = {"window": window, "decay": decay, "signal": signal, "regime_info": info}
        verdict_ctx = "walk-forward=NULL; 公式引擎best_sig=%s驱动; null域预注册猜测" % signal
    else:
        reds, blue = predict(draws, window, decay)
        method_name = "recency_weighted_empirical_marginal"
        params = {"window": window, "decay": decay}
        verdict_ctx = "walk-forward=NULL; 朴素递推边际; null域预注册猜测"
    seed = int(issue)  # 确定性随机基线
    base_reds, base_blue = random_pick(seed)

    entry = {
        "issue": issue,
        "target_date": target_date,
        "registered_ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": method_name,
        "params": params,
        "engine_forecast": {"reds": reds, "blue": blue},
        "random_baseline": {"reds": base_reds, "blue": base_blue, "seed": seed},
        "verdict_context": verdict_ctx,
        "scored": False,
    }
    # 不可覆盖：若该 issue + 该方法已登记，保留原始时间戳（同 issue 允许多种方法并存）
    if os.path.exists(PRED_FILE):
        for line in open(PRED_FILE, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("issue") == issue and d.get("method") == method:
                print(f"[register] {issue} / {method} 已于本文件登记，保留原始时间戳，不覆盖。")
                return
    with open(PRED_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[register] 已登记 {issue}（开奖前，timestamp={entry['registered_ts']}）")
    print(f"  引擎预测 : 红球 {reds}  蓝球 {blue}")
    print(f"  随机基线 : 红球 {base_reds}  蓝球 {base_blue}  (seed={seed})")


def load_preds():
    if not os.path.exists(PRED_FILE):
        return []
    return [json.loads(l) for l in open(PRED_FILE, encoding="utf-8") if l.strip()]


# ---------------- 历史滚动样本外回测 ----------------
def backtest(n_range=500, window=WINDOW, decay=DECAY, signal=None, n_random=200):
    draws = load_draws()
    method_name = f"signal_regime_{signal}" if signal else "recency_marginal"
    # 目标期：从有足够前序历史的尾部取 n_range 个
    start = max(len(draws) - n_range, 200)
    targets = draws[start:]
    eng_red_hits = []
    eng_blue_hits = 0
    rand_red_hits = []
    rand_blue_hits = 0
    for i, tgt in enumerate(targets):
        train = draws[: draws.index(tgt)]   # 严格前序
        if signal:
            pr, pb, _ = predict_from_signal(train, signal, window, decay)
        else:
            pr, pb = predict(train, window, decay)
        ar, ab = set(tgt["reds"]), tgt["blue"]
        rh = len(set(pr) & ar)
        eng_red_hits.append(rh)
        eng_blue_hits += (1 if pb == ab else 0)
        # 随机基线（同训练规模，用目标期号做种子，确定性）
        rr, rb = random_pick(int(tgt["issue"]))
        rand_red_hits.append(len(set(rr) & ar))
        rand_blue_hits += (1 if rb == ab else 0)

    def stats(lst):
        n = len(lst)
        mean = sum(lst) / n
        # 分布
        from collections import Counter
        dist = dict(sorted(Counter(lst).items()))
        return n, mean, dist

    en, em, ed = stats(eng_red_hits)
    rn, rm, rd = stats(rand_red_hits)
    # 随机解析期望：每期红球命中 = 6*(6/33) ≈ 1.0909，蓝球命中率 = 1/16
    exp_red = RED_PICK * (RED_PICK / RED_N)
    exp_blue = 1.0 / BLUE_N

    print(f"=== 滚动样本外回测 [{method_name}]（{en} 期目标，尾窗={window}，衰减={decay}）===")
    print(f"  引擎方法  红球命中均值 = {em:.4f}   蓝球命中率 = {eng_blue_hits/en:.4f}")
    print(f"  随机基线  红球命中均值 = {rm:.4f}   蓝球命中率 = {rand_blue_hits/rn:.4f}")
    print(f"  随机解析期望          红球命中 = {exp_red:.4f}   蓝球命中率 = {exp_blue:.4f}")
    print(f"  引擎红球命中分布: {ed}")
    print(f"  随机红球命中分布: {rd}")
    diff = em - exp_red
    print(f"  引擎 - 随机期望(红球) = {diff:+.4f}  （接近 0 即无优势，佐证 NULL）")
    # 粗略显著性：用随机基线的样本标准差估计噪声
    if len(rand_red_hits) > 1:
        var = sum((x - rm) ** 2 for x in rand_red_hits) / (len(rand_red_hits) - 1)
        se = math.sqrt(var / len(rand_red_hits))
        z = diff / se if se > 0 else 0
        print(f"  相对随机基线噪声：差异 {diff:+.4f}，SE≈{se:.4f}，z≈{z:.2f}（|z|<2 视为不显著）")
    return {"engine_red_mean": em, "rand_red_mean": rm, "exp_red": exp_red,
            "engine_blue_rate": eng_blue_hits / en, "rand_blue_rate": rand_blue_hits / rn,
            "exp_blue": exp_blue, "n": en}


# ---------------- 开奖后打分 ----------------
def hypergeo_p(reds_pred, reds_actual, blue_pred, blue_actual):
    """null 域精确检验：在真随机假设下，预测命中是否显著优于随机？
    红球：X ~ Hypergeometric(N=33, K=6 中奖, n=6 预测)；p_red = P(X >= k_red) 单侧。
    蓝球：p_blue = P(命中) = 1/16（单侧，命中即小概率事件）。
    合并 p 用 Fisher 法。返回 dict(p_red, p_blue, p_combined)。"""
    k = len(set(reds_pred) & set(reds_actual))
    total = _MATH_COMB(RED_N, RED_PICK)
    # P(X >= k) = sum_{j=k}^{min(6,K)} C(K,j)*C(N-K, n-j) / C(N,n)
    p_red = 0.0
    lo = max(k, 0)
    hi = min(RED_PICK, len(reds_actual))
    for j in range(lo, hi + 1):
        p_red += _MATH_COMB(len(reds_actual), j) * _MATH_COMB(RED_N - len(reds_actual), RED_PICK - j)
    p_red = p_red / total if total else 1.0
    p_blue = (1.0 / BLUE_N) if blue_pred == blue_actual else ((BLUE_N - 1.0) / BLUE_N)
    # Fisher 合并：p_comb = exp(sum ln(p)) 经 -2*sum(ln p) ~ chi2(4df)；用直接乘积更稳健
    try:
        from math import log
        chi2 = -2.0 * (log(p_red) + log(p_blue))
        p_comb = math.exp(-chi2 / 2.0)  # 粗略；精确用 chi2.sf
        try:
            from statistics import NormalDist  # 仅占位，实际用近似
        except Exception as _e:
            ssq_log.log_exception("predict_tonight", _e, "predict_tonight.py:416 silent-except")
        # 用 scipy 若可用，否则近似正态
        try:
            import scipy.stats as _st
            p_comb = _st.chi2.sf(chi2, 4)
        except Exception:
            # 大 chi2 时近似正态（4df）：E=4, Var=8 -> z=(chi2-4)/sqrt(8)
            z = (chi2 - 4.0) / math.sqrt(8.0)
            p_comb = 0.5 * math.erfc(z / math.sqrt(2.0))
    except Exception:
        p_comb = p_red * p_blue
    return {"p_red": p_red, "p_blue": p_blue, "p_combined": p_comb, "k_red": k}


def score(issue):
    # 1) 先拉取最新开奖并合并进主表（确保含 issue）
    try:
        sys.path.insert(0, HERE)
        import data as D
        fresh = D.fetch_recent()
        if fresh:
            master = D.load_master(MASTER)
            master, added = D.update_master(master, fresh)
            D.save_master(master, MASTER)
            print(f"[score] 已 fetch 并合并 {added} 期，主表行数={len(master)}")
        else:
            print("[score] fetch 返回空，尝试仅用本地主表。")
    except Exception as e:
        print(f"[score] fetch/合并失败({e})，尝试仅用本地主表。")
    draws = load_draws()
    actual = next((d for d in draws if d["issue"] == issue), None)
    if actual is None:
        print(f"[score] 主表仍未含 {issue}（可能尚未开奖或未 fetch 成功）。请开奖后重试。")
        return
    preds = load_preds()
    # 同 issue 可能登记多种方法（朴素/公式驱动/...），逐条独立打分，互不污染
    matched = [p for p in preds if p["issue"] == issue]
    if not matched:
        print(f"[score] 未找到 {issue} 的预注册预测。无法打分（这本身就说明预测未在开奖前登记）。")
        return
    print(f"[score] {issue} 实际开奖：红球 {actual['reds']}  蓝球 {actual['blue']}")
    for p in matched:
        ef = p["engine_forecast"]
        bf = p["random_baseline"]
        e_red = len(set(ef["reds"]) & set(actual["reds"]))
        e_blue = 1 if ef["blue"] == actual["blue"] else 0
        b_red = len(set(bf["reds"]) & set(actual["reds"]))
        b_blue = 1 if bf["blue"] == actual["blue"] else 0
        # 超几何 null 检验：在真随机下，本命中数是否小概率？
        hg = hypergeo_p(ef["reds"], actual["reds"], ef["blue"], actual["blue"])
        # 逐条独立写回 result（不改原始预测/时间戳）
        p["scored"] = True
        p["result"] = {"actual_reds": actual["reds"], "actual_blue": actual["blue"],
                       "engine_red_hit": e_red, "engine_blue_hit": e_blue,
                       "baseline_red_hit": b_red, "baseline_blue_hit": b_blue,
                       "hypergeo_p_red": round(hg["p_red"], 4),
                       "hypergeo_p_combined": round(hg["p_combined"], 4),
                       "scored_ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        sig_tag = " [显著优于随机!]" if hg["p_combined"] < 0.05 else ""
        print(f"  [{p['method']}] 引擎 {ef['reds']}+{ef['blue']} => 红 {e_red}/6 蓝{'中' if e_blue else '否'}；"
              f"随机基线 {bf['reds']}+{bf['blue']} => 红 {b_red}/6 蓝{'中' if b_blue else '否'}；"
              f"null检验 p_combined={hg['p_combined']:.4f}{sig_tag}")
    # 整体写回（保留其它 issue 不变）
    with open(PRED_FILE, "w", encoding="utf-8") as f:
        for p in preds:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    _summarize_scored()


def _mark_scored(issue, result):
    out = []
    for line in open(PRED_FILE, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d["issue"] == issue:
            d["scored"] = True
            d["result"] = result
        out.append(json.dumps(d, ensure_ascii=False))
    with open(PRED_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def _summarize_scored():
    """累计汇总：让长期观察公式驱动 vs 随机基线变得直观。"""
    preds = load_preds()
    sc = [p for p in preds if p.get("scored") and p.get("result")]
    if not sc:
        return
    e_red = sum(p["result"].get("engine_red_hit", 0) for p in sc)
    b_red = sum(p["result"].get("baseline_red_hit", 0) for p in sc)
    e_blue = sum(p["result"].get("engine_blue_hit", 0) for p in sc)
    b_blue = sum(p["result"].get("baseline_blue_hit", 0) for p in sc)
    eng_win = sum(1 for p in sc
                  if (p["result"].get("engine_red_hit", 0) + p["result"].get("engine_blue_hit", 0))
                  >= (p["result"].get("baseline_red_hit", 0) + p["result"].get("baseline_blue_hit", 0)))
    n = len(sc)
    print(f"[汇总] 已打分 {n} 期：引擎红球总命中 {e_red}（均值 {e_red/n:.2f}） / 随机基线 {b_red}（{b_red/n:.2f}）；"
          f"蓝球 {e_blue} / {b_blue}；引擎不劣于基线 {eng_win}/{n} 期。")


def auto(phase="both", signal="red_gap_max", window=WINDOW, decay=DECAY, method=None):
    """开奖日自动流程（供周期性自动化调用）：
      register: 今天若为开奖日(周二/四/日)且下一期尚未登记，则公式驱动预注册(开奖前，时间戳不可篡改)。
      score:    对所有'已登记未打分且已开奖'的期 fetch+打分，并打印累计汇总。
    默认 method=comp：优先用引擎进化出的复合公式树集成投票驱动选号（公式参与计算）。"""
    draws = load_draws()
    known = {d["issue"] for d in draws}
    today = datetime.date.today().strftime("%Y-%m-%d")
    wd = datetime.date.today().weekday()
    is_draw_day = wd in (1, 3, 6)  # 周一=0 … 周日=6 → 周二/四/日

    if phase in ("both", "register"):
        if not is_draw_day:
            print(f"[auto:register] {today} 非开奖日(周二/四/日)，跳过预注册。")
        else:
            last = max(int(d["issue"]) for d in draws)
            nxt = f"{last + 1:05d}"
            if nxt in known:
                print(f"[auto:register] {nxt} 已在主表(已开奖)，无需预注册。")
            elif any(p["issue"] == nxt for p in load_preds()):
                print(f"[auto:register] {nxt} 已预注册，保留原时间戳，不覆盖。")
            else:
                register(nxt, today, window, decay, signal=signal, method=method)
                tag = f"公式驱动复合树集成" if method == "comp" or (method is None and load_comp_trees()) else f"公式驱动 {signal}"
                print(f"[auto:register] 已为 {today}(开奖日) 预注册 {nxt}（{tag} + 随机基线）。")

    if phase in ("both", "score"):
        # 对所有'已登记未打分'的期调 score()；score() 内部先 fetch 最新开奖并合并进主表，
        # 再判断该期是否已开奖。不依赖 auto() 开头加载的 known，避免刚开奖、主表尚未含该期时被漏掉。
        undecided = [p for p in load_preds() if not p.get("scored")]
        if not undecided:
            print(f"[auto:score] 无待打分条目。")
        else:
            for p in undecided:
                score(p["issue"])
        _summarize_scored()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("register")
    r.add_argument("--issue", required=True)
    r.add_argument("--date", required=True)
    r.add_argument("--window", type=int, default=WINDOW)
    r.add_argument("--decay", type=float, default=DECAY)
    r.add_argument("--signal", default=None,
                   help="用引擎进化出的存活信号作态筛选器(如 red_gap_max)")
    r.add_argument("--method", default=None, choices=[None, "comp"],
                   help="comp=用引擎复合公式树集成驱动(默认有树则自动启用)")
    b = sub.add_parser("backtest")
    b.add_argument("--range", type=int, default=500)
    b.add_argument("--window", type=int, default=WINDOW)
    b.add_argument("--decay", type=float, default=DECAY)
    b.add_argument("--signal", default=None,
                   help="回测公式驱动方法(如 red_gap_max)；不填=朴素递推边际")
    bc = sub.add_parser("backtest_comp")
    bc.add_argument("--range", type=int, default=500)
    bc.add_argument("--window", type=int, default=WINDOW)
    bc.add_argument("--decay", type=float, default=DECAY)
    s = sub.add_parser("score")
    s.add_argument("--issue", required=True)
    a = sub.add_parser("auto")
    a.add_argument("--phase", default="both", choices=["both", "register", "score"])
    a.add_argument("--signal", default="red_gap_max",
                   help="公式驱动信号(默认 red_gap_max，来自引擎进化最佳存活信号)")
    a.add_argument("--method", default=None, choices=[None, "comp"],
                   help="comp=用引擎复合公式树集成驱动(默认有树则自动启用)")
    a.add_argument("--window", type=int, default=WINDOW)
    a.add_argument("--decay", type=float, default=DECAY)
    args = ap.parse_args()
    if args.cmd == "register":
        register(args.issue, args.date, args.window, args.decay, signal=args.signal, method=args.method)
    elif args.cmd == "backtest":
        backtest(args.range, args.window, args.decay, signal=args.signal)
    elif args.cmd == "backtest_comp":
        backtest_comp(args.range, args.window, args.decay)
    elif args.cmd == "score":
        score(args.issue)
    elif args.cmd == "auto":
        auto(args.phase, args.signal, args.window, args.decay, method=args.method)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
