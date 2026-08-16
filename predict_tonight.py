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

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", r"D:/ssq_evo_data")
MASTER = os.path.join(DATA_DIR, "ssq_master.csv")
PRED_FILE = os.path.join(DATA_DIR, "predictions.jsonl")

RED_N, BLUE_N = 33, 16
RED_PICK, BLUE_PICK = 6, 1

# 预测方法默认超参
WINDOW = 150      # 尾窗长度（前序多少期参与）
DECAY = 0.985     # 指数衰减（越近期权重越高）；0.985^150≈0.10


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


# ---------------- 预注册 ----------------
def register(issue, target_date, window=WINDOW, decay=DECAY):
    draws = load_draws()
    # 该 issue 必须尚未开奖（主表里没有），否则不是"预测"而是"回测"
    known = {d["issue"] for d in draws}
    if issue in known:
        print(f"[register] 警告：主表已含 {issue}（已开奖），这不是预测而是回测。仍按已开奖处理。")
    # 用当前全部历史训练
    reds, blue = predict(draws, window, decay)
    seed = int(issue)  # 确定性随机基线
    base_reds, base_blue = random_pick(seed)

    entry = {
        "issue": issue,
        "target_date": target_date,
        "registered_ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "recency_weighted_empirical_marginal",
        "params": {"window": window, "decay": decay},
        "engine_forecast": {"reds": reds, "blue": blue},
        "random_baseline": {"reds": base_reds, "blue": base_blue, "seed": seed},
        "verdict_context": "walk-forward=NULL; spectral=构造伪结构; 非验证模型; null域预注册猜测",
        "scored": False,
    }
    # 不可覆盖：若该 issue 已登记，保留原始时间戳
    if os.path.exists(PRED_FILE):
        for line in open(PRED_FILE, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            if json.loads(line).get("issue") == issue:
                print(f"[register] {issue} 已于本文件登记，保留原始时间戳，不覆盖。")
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
def backtest(n_range=500, window=WINDOW, decay=DECAY, n_random=200):
    draws = load_draws()
    # 目标期：从有足够前序历史的尾部取 n_range 个
    start = max(len(draws) - n_range, 200)
    targets = draws[start:]
    eng_red_hits = []
    eng_blue_hits = 0
    rand_red_hits = []
    rand_blue_hits = 0
    for i, tgt in enumerate(targets):
        train = draws[: draws.index(tgt)]   # 严格前序
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

    print(f"=== 滚动样本外回测（{en} 期目标，尾窗={window}，衰减={decay}）===")
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
def score(issue):
    # 1) 先拉取最新开奖（确保含 issue）
    try:
        sys.path.insert(0, HERE)
        import data as D
        fresh = D.fetch_recent()
        print(f"[score] 已 fetch 最新数据，主表行数={len(fresh) if fresh else '?'}")
    except Exception as e:
        print(f"[score] fetch 失败({e})，尝试仅用本地主表。")
    draws = load_draws()
    actual = next((d for d in draws if d["issue"] == issue), None)
    if actual is None:
        print(f"[score] 主表仍未含 {issue}（可能尚未开奖或未 fetch 成功）。请开奖后重试。")
        return
    preds = load_preds()
    p = next((x for x in preds if x["issue"] == issue), None)
    if p is None:
        print(f"[score] 未找到 {issue} 的预注册预测。无法打分（这本身就说明预测未在开奖前登记）。")
        return
    ef = p["engine_forecast"]
    bf = p["random_baseline"]
    e_red = len(set(ef["reds"]) & set(actual["reds"]))
    e_blue = 1 if ef["blue"] == actual["blue"] else 0
    b_red = len(set(bf["reds"]) & set(actual["reds"]))
    b_blue = 1 if bf["blue"] == actual["blue"] else 0
    # 更新 scored 标记（不改原始预测/时间戳）
    _mark_scored(issue, {"actual_reds": actual["reds"], "actual_blue": actual["blue"],
                         "engine_red_hit": e_red, "engine_blue_hit": e_blue,
                         "baseline_red_hit": b_red, "baseline_blue_hit": b_blue,
                         "scored_ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    print(f"[score] {issue} 实际开奖：红球 {actual['reds']}  蓝球 {actual['blue']}")
    print(f"  引擎预测 {ef['reds']}+{ef['blue']}  => 红球命中 {e_red}/6，蓝球 {'中' if e_blue else '否'}")
    print(f"  随机基线 {bf['reds']}+{bf['blue']}  => 红球命中 {b_red}/6，蓝球 {'中' if b_blue else '否'}")


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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("register")
    r.add_argument("--issue", required=True)
    r.add_argument("--date", required=True)
    r.add_argument("--window", type=int, default=WINDOW)
    r.add_argument("--decay", type=float, default=DECAY)
    b = sub.add_parser("backtest")
    b.add_argument("--range", type=int, default=500)
    b.add_argument("--window", type=int, default=WINDOW)
    b.add_argument("--decay", type=float, default=DECAY)
    s = sub.add_parser("score")
    s.add_argument("--issue", required=True)
    args = ap.parse_args()
    if args.cmd == "register":
        register(args.issue, args.date, args.window, args.decay)
    elif args.cmd == "backtest":
        backtest(args.range, args.window, args.decay)
    elif args.cmd == "score":
        score(args.issue)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
