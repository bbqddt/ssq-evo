# -*- coding: utf-8 -*-
"""
formula_research.py —— 原创公式研发扩展（突破刀口）
=================================================
用户定位：前人公式一定不可行，重点放在「对公式的研发、突破」上。

本模块不借任何前人公式结论，只引入 **原创数学基元** 与 **非时序结构类**，
全部汇入 engine_core 同一套诚实闸门（shuffle + AAFT + subset_marginal + 随机对照 + BH-FDR）。
绝不绕过统一闸门（头号红线）：优化器绝不以「过闸」为目标自动合并候选。

为什么这是「突破」而非「堆算力」：
  公平不放回抽签在「时序」上必然独立 → 所有基于「用过去预测下一期」的时序结构搜索
  在数学上注定 null（我们已反复验证）。结构唯一可能藏身之处是 **机器/算法偏倚**：
  边际频率或两两共现频率偏离理论超几何分布。所以 B 块（机器偏倚公式）才是真正的
  突破杠杆；A 块（原创基元）是把更大的、前人没用过的字母表交给 GA 去发明组合公式。

两部分：
  A. 原创基元字母表扩张（注册进 SIGMAPS/BASE_SIGNALS，自动被 formula_composer 组合演进）：
       - red_digit_sum      数位和（每球十进制数位求和后再对6球求和）
       - red_digit_root9    数位根（数位和模9，经典数论不变量）
       - red_qr_count       二次剩余 mod 33 计数（组合-数论）
       - red_fib_count      斐波那契数计数（数论-组合）
       - red_pairwise_prod  两两积和 e2（非线性对称不变量，非 sum 能表达）
       - red_gap_var        排序间距方差（组合间隔二阶矩）
       - red_lz_complexity  出现位串的 Lempel-Ziv76 复杂度（信息论）
  B. 非时序结构类（突破杠杆）：machine-bias / 共现偏倚公式。
       直接对「理论超几何分布」做偏差检验 + walk-forward 预测闸门，并含阳性对照。
"""
import numpy as np

import engine_core as E
import run_axes as RA
import data as D


# ---------------------------------------------------------------------------
# A. 原创基元（每期 -> 1D 序列，纯函数、无前瞻，可直接进 surrogate 闸门）
# ---------------------------------------------------------------------------
def _digit_sum(n):
    return sum(int(c) for c in str(int(n)))


_vec_ds = np.vectorize(_digit_sum, otypes=[np.int64])


def red_digit_sum(reds, blues):
    """数位和：每球十进制数位求和，再对 6 球相加（范围 0..54）。"""
    return _vec_ds(reds).sum(axis=1).astype(float)


def red_digit_root9(reds, blues):
    """数位根：数位和模 9（经典数论不变量，对 9 的循环结构）。"""
    return (_vec_ds(reds).sum(axis=1) % 9).astype(float)


_QR33 = frozenset((i * i) % 33 for i in range(1, 34))


def red_qr_count(reds, blues):
    """二次剩余 mod 33 计数：6 球中有几个是模 33 的二次剩余。"""
    return np.array([sum(1 for x in row if int(x) in _QR33) for row in reds], dtype=float)


_FIB = frozenset([1, 2, 3, 5, 8, 13, 21])


def red_fib_count(reds, blues):
    """斐波那契数计数：6 球中有几个落在 {1,2,3,5,8,13,21}。"""
    return np.array([sum(1 for x in row if int(x) in _FIB) for row in reds], dtype=float)


def red_pairwise_prod(reds, blues):
    """两两积和 e2 = ((Σr)² − Σr²)/2（非线性对称不变量，sum 无法表达的乘积结构）。"""
    s = reds.sum(axis=1).astype(float)
    s2 = (reds.astype(float) ** 2).sum(axis=1)
    return ((s ** 2 - s2) / 2.0)


def red_gap_var(reds, blues):
    """排序后相邻间距的方差（组合间隔二阶矩，比 gap_mean/std 更细）。"""
    g = np.diff(np.sort(reds, axis=1), axis=1)
    return g.var(axis=1).astype(float)


def _lz76(bits):
    """Lempel-Ziv 76 复杂度（位串短语数）。"""
    words = set()
    w = ""
    c = 1
    for b in bits:
        wc = w + str(int(b))
        if wc in words:
            w = wc
        else:
            words.add(wc)
            w = ""
            c += 1
    return c


def red_lz_complexity(reds, blues):
    """出现位串的 LZ76 复杂度：把每期 33 位出现向量压成信息论复杂度。"""
    out = np.empty(reds.shape[0], dtype=float)
    for t in range(reds.shape[0]):
        bits = [1 if (i + 1) in reds[t] else 0 for i in range(33)]
        out[t] = _lz76(bits)
    return out


# ---------------------------------------------------------------------------
# A2. 集合论基元（把红球当「多重集」，换集合论/模运算透镜；前人公式几乎只挖
#     线性/时序统计量，集合论组合——自信息量/Jaccard/集合和模/GCD/奇偶类是真空区）
# ---------------------------------------------------------------------------
def red_info_content(reds, blues):
    """每期红球组合的「自信息量」：用全样本经验频率 p(x)，该期信息量 = -Σ ln p(x_i)。
    高值=罕见球组合，低值=常见球组合（集合论-信息论不变量）。"""
    cnt = np.zeros(34)
    for row in reds:
        for x in row:
            cnt[int(x)] += 1
    p = cnt[1:34] / cnt[1:34].sum()
    return np.array([float(sum(-np.log(p[int(x)]) for x in row)) for row in reds], dtype=float)


def red_jaccard_prev(reds, blues):
    """与上一期红球集合的 Jaccard 相似度（lag-1，无前瞻）。序列集合相似度不变量。"""
    out = np.zeros(reds.shape[0], dtype=float)
    for t in range(1, reds.shape[0]):
        a = set(int(x) for x in reds[t - 1])
        b = set(int(x) for x in reds[t])
        out[t] = len(a & b) / max(1, len(a | b))
    return out


def red_set_sum_mod33(reds, blues):
    """红球集合和 mod 33（数论-集合组合不变量；区别于现有 red_sum 非模版本）。"""
    return (reds.sum(axis=1) % 33).astype(float)


def red_gap_gcd(reds, blues):
    """排序后相邻间距的 GCD（组合间隔代数不变量，比方差更代数化）。"""
    g = np.diff(np.sort(reds, axis=1), axis=1).astype(int)
    return np.array([float(np.gcd.reduce(g[t])) for t in range(g.shape[0])], dtype=float)


def red_even_count(reds, blues):
    """偶数红球计数（奇偶组合类不变量）。"""
    return (reds % 2 == 0).sum(axis=1).astype(float)


NEW_RESEARCH_SIGNALS = {
    "red_digit_sum": red_digit_sum,
    "red_digit_root9": red_digit_root9,
    "red_qr_count": red_qr_count,
    "red_fib_count": red_fib_count,
    "red_pairwise_prod": red_pairwise_prod,
    "red_gap_var": red_gap_var,
    "red_lz_complexity": red_lz_complexity,
    "red_info_content": red_info_content,
    "red_jaccard_prev": red_jaccard_prev,
    "red_set_sum_mod33": red_set_sum_mod33,
    "red_gap_gcd": red_gap_gcd,
    "red_even_count": red_even_count,
}


def register():
    """注册原创基元：先确保既有 representation_zoo 基元已注入，再追加本模块基元，
    并刷新 BASE_SIGNALS（使 formula_composer 自动把新字母表纳入组合演进）。
    幂等：已存在则跳过。"""
    RA.RZ.register()
    for name, fn in NEW_RESEARCH_SIGNALS.items():
        if name not in E.SIGMAPS:
            E.SIGMAPS[name] = fn
    E.BASE_SIGNALS = list(E.SIGMAPS.keys())


# 研究轴表（复用 run_axes 的诚实分层 null 闸门扫描）
RESEARCH_AXES = [
    {"group": "research_number_theory", "sig": "red_digit_sum", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "数位和(数论原创)"},
    {"group": "research_number_theory", "sig": "red_digit_root9", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "数位根模9(数论原创)"},
    {"group": "research_number_theory", "sig": "red_qr_count", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "二次剩余mod33计数(数论原创)"},
    {"group": "research_number_theory", "sig": "red_fib_count", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "斐波那契数计数(数论原创)"},
    {"group": "research_combinatorial", "sig": "red_pairwise_prod", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "两两积和e2(非线性对称不变量)"},
    {"group": "research_combinatorial", "sig": "red_gap_var", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "排序间距方差(组合二阶矩)"},
    {"group": "research_info_theory", "sig": "red_lz_complexity", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "出现位串LZ76复杂度(信息论)"},
    {"group": "research_set_theory", "sig": "red_info_content", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "红球组合自信息量(集合论-信息论)"},
    {"group": "research_set_theory", "sig": "red_jaccard_prev", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "与上期Jaccard相似度(lag-1集合相似)"},
    {"group": "research_set_theory", "sig": "red_set_sum_mod33", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "集合和mod33(数论-集合)"},
    {"group": "research_set_theory", "sig": "red_gap_gcd", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "排序间距GCD(组合间隔代数)"},
    {"group": "research_set_theory", "sig": "red_even_count", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "偶数红球计数(奇偶组合类)"},
]


def scan(reds, blues, seed=20260826, k_sur=40):
    """用 run_axes 同一套分层 null + 随机对照闸门扫描 A 块新基元。返回记录列表。"""
    rng = np.random.default_rng(seed)
    N = reds.shape[0]
    recs = []
    for ax in RESEARCH_AXES:
        if ax["sig"] not in E.SIGMAPS:
            continue
        rec = RA.label_axis(ax["sig"], ax["tests"], reds, blues, rng, k_sur)
        rec["group"] = ax["group"]
        rec["note"] = ax["note"]
        # —— 随机数据对照闸门：纯随机也 SURVIVOR => 构造伪结构，降级 ——
        ctrl = RA.random_control_label(ax["sig"], ax["tests"], N, seed=seed, k_sur=60)
        if ctrl == "SURVIVOR":
            rec["artifact_prone"] = True
            rec["label"] = "ARTIFACT_BY_CONSTRUCTION"
            rec["note"] += " [随机对照闸门: 纯随机也SURVIVOR => 构造伪结构, 已降级]"
        recs.append(rec)
    return recs


# ---------------------------------------------------------------------------
# B. 非时序结构类：machine-bias / 共现偏倚公式（突破杠杆）
# ---------------------------------------------------------------------------
def _proper_random(N, rng):
    r = np.zeros((N, 6), dtype=int)
    for i in range(N):
        r[i] = np.sort(rng.choice(np.arange(1, 34), size=6, replace=False))
    b = rng.integers(1, 17, size=N)
    return r, b


def bias_formula_test(reds, discovery_frac=0.7, K=8, alternative="two-sided"):
    """共现偏倚公式（walk-forward，无泄露）：

    发现段估 33 球的经验边际频率 p_hat；取偏离理论 6/33 最甚的 top-K 球。
    确认段用二项检验：6 球中落入 top-K 的总数是否显著偏离 Bin(6n, K/33)。
    这是「公式算开奖」该攻的方向——若机器/算法有偏，top-K 会在未来超随机出现。
    时序搜索注定 null，偏倚搜索才可能挖到真结构。
    返回诚实结论 dict。
    """
    from scipy.stats import binomtest
    N = reds.shape[0]
    base = int(N * discovery_frac)
    if base < 200 or (N - base) < 60:
        return {"verdict": "INSUFFICIENT_DATA", "p": None}
    disc = reds[:base]
    conf = reds[base:]

    cnt = np.zeros(34, dtype=np.int64)
    for row in disc:
        for x in row:
            cnt[int(x)] += 1
    n_disc = len(disc)
    p_hat = cnt[1:34] / n_disc
    theo = 6.0 / 33.0
    dev = p_hat - theo
    order = np.argsort(-dev)              # 降序：最超代表在前
    topK = set((order[:K] + 1).tolist())  # 球号 1..33

    obs = np.array([sum(1 for x in row if int(x) in topK) for row in conf])
    total_hits = int(obs.sum())
    n_conf = len(conf)
    p_k = K / 33.0
    res = binomtest(total_hits, n_conf * 6, p_k, alternative=alternative)
    p = float(res.pvalue)
    exp_hits = n_conf * 6.0 * p_k
    var = n_conf * 6.0 * p_k * (1 - p_k)
    z = (total_hits - exp_hits) / np.sqrt(var + 1e-9)
    verdict = "BIAS_DETECTED" if p < 0.05 else "NULL"
    return {
        "method": "marginal_bias_topK", "K": K,
        "obs_hits": total_hits, "exp_hits": round(exp_hits, 1),
        "z": round(float(z), 2), "p": p, "verdict": verdict,
    }


def bias_positive_control(seed=7, N=3000, K=8):
    """阳性对照：人为让球 1..8 超代表，验证 bias_formula_test 有功效（须检出 BIAS）。
    若阳性对照失败 => 测试本身无效，所有 NULL 结论不可信。"""
    rng = np.random.default_rng(seed)
    # 构造：每期以 0.5 概率从 {1..8} 取 3 球、剩余 3 球从 {9..33} 取（使 1..8 远超 6/33≈0.18 的边际）
    r = np.zeros((N, 6), dtype=int)
    for i in range(N):
        a = rng.choice(np.arange(1, 9), size=3, replace=False)
        b = rng.choice(np.arange(9, 34), size=3, replace=False)
        r[i] = np.sort(np.concatenate([a, b]))
    res = bias_formula_test(r, K=K)
    res["is_positive_control"] = True
    return res


def bias_pair_test(reds, discovery_frac=0.7, topM=12):
    """两两共现偏倚（walk-forward，无泄露）：

    发现段估 495 个两两组合的共现频率；取偏离理论 C(31,4)/C(33,6) 最甚的 top-M 对。
    确认段检验这 M 个固定对的总共现数是否超随机——捕捉「边际不偏、但交互偏」的机器偏倚
    （单球频率正常，却特定两球被一起抽出更频繁）。这是边际偏倚检验挖不到的角度。
    返回诚实结论 dict。
    """
    from scipy.stats import binomtest
    from math import comb
    N = reds.shape[0]
    base = int(N * discovery_frac)
    if base < 200 or (N - base) < 60:
        return {"verdict": "INSUFFICIENT_DATA", "p": None}
    disc = reds[:base]
    conf = reds[base:]
    theo_pair = comb(31, 4) / comb(33, 6)   # 单期含某特定对的理论概率

    pair_cnt = {}
    for row in disc:
        rs = sorted(int(x) for x in row)
        for a in range(6):
            for b in range(a + 1, 6):
                k = (rs[a], rs[b])
                pair_cnt[k] = pair_cnt.get(k, 0) + 1
    n_disc = len(disc)
    devs = sorted(((c / n_disc - theo_pair, k) for k, c in pair_cnt.items()), reverse=True)
    top_pairs = set(k for _, k in devs[:topM])

    total = 0
    for row in conf:
        rs = set(int(x) for x in row)
        for (a, b) in top_pairs:
            if a in rs and b in rs:
                total += 1
    n_conf = len(conf)
    # 固定 M 对、每对出现概率 theo_pair、每期相互独立近似 => 总出现 ~ Bin(n_conf*M, theo_pair)
    exp = n_conf * topM * theo_pair
    var = n_conf * topM * theo_pair * (1 - theo_pair)
    z = (total - exp) / np.sqrt(var + 1e-9)
    res = binomtest(total, n_conf * topM, theo_pair, alternative="two-sided")
    p = float(res.pvalue)
    verdict = "BIAS_DETECTED" if p < 0.05 else "NULL"
    return {"method": "pairwise_bias_topM", "topM": topM, "obs": total,
            "exp": round(exp, 1), "z": round(float(z), 2), "p": p, "verdict": verdict}


def bias_pair_positive_control(seed=11, N=3000, topM=12):
    """阳性对照（两两）：人为让对 (1,2) 超共现，验证 bias_pair_test 有功效。"""
    rng = np.random.default_rng(seed)
    r = np.zeros((N, 6), dtype=int)
    for i in range(N):
        if rng.random() < 0.5:
            # 强注入 (1,2) 共现：固定包含 1,2，其余 4 球随机
            rest = rng.choice(np.arange(3, 34), size=4, replace=False)
            r[i] = np.sort(np.concatenate([[1, 2], rest]))
        else:
            r[i] = np.sort(rng.choice(np.arange(1, 34), size=6, replace=False))
    res = bias_pair_test(r, topM=topM)
    res["is_positive_control"] = True
    return res


# ---------------------------------------------------------------------------
# 主入口：载入真实数据 -> A 扫描 + B 偏倚 + 阳性对照 -> 诚实输出
# ---------------------------------------------------------------------------
def _print(recs, bias, pctrl, bias_pair, pctrl_pair):
    print("\n================ A. 原创基元 · 分层 null 闸门 ================")
    print("%-20s %-10s %-9s %-9s %-9s %s" %
          ("sig", "label", "p_shuf", "p_aaft", "p_marg", "note"))
    print("-" * 110)
    for r in recs:
        fmt = lambda v: ("%.4g" % v) if isinstance(v, float) else "-"
        print("%-20s %-10s %-9s %-9s %-9s %s" %
              (str(r.get("sig", "-")), r.get("label", "-"),
               fmt(r.get("p_shuffle")), fmt(r.get("p_aaft")), fmt(r.get("p_marg")),
               r.get("note", "")))
    n_surv = sum(1 for r in recs if r.get("label") == "SURVIVOR")
    n_art = sum(1 for r in recs if r.get("label") == "ARTIFACT_BY_CONSTRUCTION")
    print("A 汇总: SURVIVOR=%d  ARTIFACT=%d  (其余NULL)" % (n_surv, n_art))

    print("\n================ B. 机器偏倚公式 · walk-forward 闸门 ================")
    print("[边际] 真实数据: %s | obs=%s exp=%s z=%s p=%s" %
          (bias.get("verdict"), bias.get("obs_hits"), bias.get("exp_hits"),
           bias.get("z"), ("%.4g" % bias["p"]) if bias.get("p") is not None else "-"))
    print("[边际] 阳性对照(注入偏倚): %s | p=%s  (期望 BIAS_DETECTED 证明闸门有功效)" %
          (pctrl.get("verdict"), ("%.4g" % pctrl["p"]) if pctrl.get("p") is not None else "-"))
    print("[两两] 真实数据: %s | obs=%s exp=%s z=%s p=%s" %
          (bias_pair.get("verdict"), bias_pair.get("obs"), bias_pair.get("exp"),
           bias_pair.get("z"), ("%.4g" % bias_pair["p"]) if bias_pair.get("p") is not None else "-"))
    print("[两两] 阳性对照(注入共现偏倚): %s | p=%s  (期望 BIAS_DETECTED 证明闸门有功效)" %
          (pctrl_pair.get("verdict"), ("%.4g" % pctrl_pair["p"]) if pctrl_pair.get("p") is not None else "-"))


def main():
    path = "D:/ssq_evo_data/ssq_master.csv"
    m = D.load_master(path)
    if not m:
        print("[formula_research] 未找到真实数据，使用合成 null 演示")
        rng = np.random.default_rng(0)
        reds, blues = _proper_random(2000, rng)
    else:
        reds, blues, _ = D.to_arrays(m)
        print("[formula_research] 载入真实数据 %d 期" % len(reds))

    register()  # 注入原创基元 + 刷新 BASE_SIGNALS（composer 自动可用）
    recs = scan(reds, blues)
    bias = bias_formula_test(reds)
    pctrl = bias_positive_control()
    bias_pair = bias_pair_test(reds)
    pctrl_pair = bias_pair_positive_control()
    _print(recs, bias, pctrl, bias_pair, pctrl_pair)
    return recs, bias, pctrl, bias_pair, pctrl_pair


if __name__ == "__main__":
    main()
