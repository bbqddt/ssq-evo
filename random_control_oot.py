"""OOT 随机数据对照闸门（宿主专用审计模块，不进容器 / 不改生产管线）。

动机（诚实红线 #4：随机数据对照）
---------------------------------
生产 OOT（`engine_core.out_of_time`）是终审闸门：候选来自训练段、规则冻结、
在真正未来段盲打，用替代分布校准 p_random。

但存在一类**构造性伪信号**：命中率的高分不来自"数据有结构"，而来自
**信号构造方式本身**（如 ewm 平滑 → 增量强负相关 → `rev` 反转规则天然高分）。
若此时零假设是 shuffle（彻底打乱时间次序），替代序列会**失去平滑性**，
于是"真实 ~0.84 vs 替代 ~0.67"会给出 p≈0.005 的假显著——而这套高分在
**纯随机开奖数据上会一模一样地出现**。

判别方法（本模块）
-----------------
用**纯随机开奖**（6/33 无重复红球 + 1/16 蓝球，与真实同边际约束、同期数）
跑**完全相同的 OOT 管线**（同一 ev 基因组、同一 k_sur、同一随机种子协议），
得到命中率与 p_random 的零分布：

- 若随机数据的 OOT 命中率分布与真实数据相当，且随机数据 p<0.05 的比例
  远超名义 5% → 判 **ARTIFACT_BY_CONSTRUCTION**（构造性伪信号）。
- 若随机数据命中率显著低于真实、且 p<0.05 比例 ≈5% → 闸门有效，
  真实数据的显著性才值得继续追查。

本模块只做审计，输出 audit/random_control_oot.md + .json，**不修改任何生产状态**。
"""

import json
import os
import sys

import numpy as np

import data as D
import engine_core as E
import paths

RED_MAX = 33
N_RED = 6
BLUE_MAX = 16

OUT_MD = None  # 由 main 写入


# ---------------------------------------------------------------------------
# 1. 数据
# ---------------------------------------------------------------------------
def load_real():
    master = D.load_master(paths.master_csv())
    reds, blues, issues = D.to_arrays(master)
    return np.asarray(reds), np.asarray(blues)


def gen_random_draws(n, rng):
    """纯随机开奖：每期 6 个互异红球(1..33，与真实同边际) + 1 个蓝球(1..16)。

    注意：这里刻意**保留真实的边际分布特征**（离散、无重复、有界），
    只摧毁"任何跨期结构"。若构造性伪信号来自变换的平滑/离散化，
    它在这份数据上会照样出现——这正是要检测的。
    """
    r = np.zeros((n, N_RED), dtype=reds_dtype())
    for i in range(n):
        r[i] = np.sort(rng.choice(RED_MAX, size=N_RED, replace=False) + 1)
    b = rng.integers(1, BLUE_MAX + 1, size=n)
    return r, b


def reds_dtype():
    return np.int64


# ---------------------------------------------------------------------------
# 2. 候选基因组（取自生产 state.json，保证审计对象=生产对象）
# ---------------------------------------------------------------------------
def load_top_ev():
    st_path = paths.p("state.json")
    if not os.path.exists(st_path):
        return None, "state.json 不存在: %s" % st_path
    try:
        st = json.load(open(st_path, encoding="utf-8"))
    except Exception as e:
        return None, "state.json 读取失败: %s" % e
    lb = st.get("leaderboard") or []
    if not lb:
        return None, "state.leaderboard 为空"
    ev = lb[0]
    return {
        "sig": ev.get("sig"),
        "test": ev.get("test"),
        "params": ev.get("params"),
        "tier": ev.get("tier", "light"),
    }, None


# ---------------------------------------------------------------------------
# 3. 单次 OOT（与生产 run_cycle 5d 完全同参）
# ---------------------------------------------------------------------------
def run_oot(ev, reds, blues, seed, k_sur=100, train_frac=0.85):
    rng = np.random.default_rng(seed)
    try:
        return E.out_of_time(ev, reds, blues, rng, train_frac=train_frac, k_sur=k_sur)
    except Exception as e:
        return {"_err": str(e)}


# ---------------------------------------------------------------------------
# 4. 主实验
# ---------------------------------------------------------------------------
def main(m_random=20, k_sur=100, base_seed=20260829):
    ev, err = load_top_ev()
    if ev is None:
        print("[random_control_oot] 无法加载候选: %s" % err)
        return None

    print("[random_control_oot] 审计对象 ev = %s" % json.dumps(ev, ensure_ascii=False))
    print("  零假设类型 = %s" % E.TEST_SUR_TYPE.get(ev["test"], "aaft"))

    reds, blues = load_real()
    n = len(reds)
    print("  真实数据 N=%d" % n)

    # 4a. 真实数据 OOT（复现生产数字）
    real = run_oot(ev, reds, blues, seed=base_seed, k_sur=k_sur)
    if not real or "_err" in real:
        print("  真实 OOT 失败: %s" % (real or {}).get("_err"))
        return None
    print("  真实 OOT: hit=%.4f p=%.4f n=%d rule=%s"
          % (real["hit_rate"], real["p_random"], real["n"], real.get("best_rule")))

    # 4b. 纯随机数据 OOT 零分布
    hits, ps, rules = [], [], {}
    for m in range(m_random):
        rng = np.random.default_rng(base_seed + 1000 + m)
        rr, bb = gen_random_draws(n, rng)
        res = run_oot(ev, rr, bb, seed=base_seed + 5000 + m, k_sur=k_sur)
        if not res or "_err" in res:
            print("  [随机#%d] OOT 失败: %s" % (m, res.get("_err") if res else "None"))
            continue
        hits.append(res["hit_rate"])
        ps.append(res["p_random"])
        rk = res.get("best_rule")
        rules[rk] = rules.get(rk, 0) + 1
        print("  [随机#%02d] hit=%.4f p=%.4f n=%d rule=%s"
              % (m, res["hit_rate"], res["p_random"], res["n"], rk))

    if not hits:
        print("  随机对照全部失败，无法判定")
        return None

    hits = np.array(hits)
    ps = np.array(ps)
    fpr = float((ps < 0.05).mean())
    # 真实命中率在随机零分布中的百分位（>= 表示真实不低于随机的比例）
    pct_ge = float((hits >= real["hit_rate"]).mean())

    # 4c. 判定（用统计检验，不用硬阈值——否则判据本身又会制造假警报）
    #     二项检验：观察到的假阳性数是否显著超过名义 5%？
    k_fp = int((ps < 0.05).sum())
    p_fpr = _binom_sf(k_fp, int(ps.size), 0.05)
    real_sig = real["p_random"] < 0.05

    if p_fpr <= 0.05:
        verdict = "FPR_INFLATED"
        reason = ("随机数据假显著率 %.1f%%（%d/%d）显著超过名义 5%%（二项检验 p=%.3f）"
                  "⇒ 闸门系统性假阳性。" % (fpr * 100, k_fp, ps.size, p_fpr))
        if real_sig and pct_ge >= 0.05:
            reason += (" 且真实命中率 %.4f 落在随机零分布内（%.0f%% 的随机样本不低于它）"
                       "⇒ 判定 ARTIFACT_BY_CONSTRUCTION：高分来自信号构造本身。"
                       % (real["hit_rate"], pct_ge * 100))
    elif real_sig and pct_ge >= 0.05:
        verdict = "ARTIFACT_BY_CONSTRUCTION"
        reason = ("真实数据 p=%.4f 看似显著，但随机数据命中率同量级（随机均值 %.4f，"
                  "%.0f%% 的随机样本不低于真实 %.4f）⇒ 该显性是信号构造的产物，"
                  "不是数据可预测性。" % (real["p_random"], hits.mean(),
                                   pct_ge * 100, real["hit_rate"]))
    elif real_sig and pct_ge < 0.05:
        verdict = "SIGNAL_CANDIDATE"
        reason = ("真实数据 p=%.4f 显著，且真实命中率 %.4f 高于 %.0f%% 的随机样本"
                  "（随机均值 %.4f），随机假显著率 %.1f%% 与名义一致"
                  "⇒ 值得进入独立确认段复核（仍不是定论）。"
                  % (real["p_random"], real["hit_rate"], (1 - pct_ge) * 100,
                     hits.mean(), fpr * 100))
    else:
        verdict = "GATE_VALID_NULL_ON_REAL"
        reason = ("真实数据 p=%.4f 不显著；随机数据假显著率 %.1f%%（%d/%d）与名义 5%% "
                  "无显著差异（二项检验 p=%.3f）⇒ 闸门校准正常，当前候选无样本外预测力。"
                  % (real["p_random"], fpr * 100, k_fp, ps.size, p_fpr))

    summary = {
        "ev": ev,
        "sur_type": E.TEST_SUR_TYPE.get(ev["test"], "aaft"),
        "n_real": n,
        "m_random": int(len(hits)),
        "k_sur": k_sur,
        "real": {k: real.get(k) for k in
                 ("hit_rate", "p_random", "above_random", "n", "best_rule", "sur_mean", "sur_std")},
        "random_hit_mean": float(hits.mean()),
        "random_hit_std": float(hits.std(ddof=0)),
        "random_hit_min": float(hits.min()),
        "random_hit_max": float(hits.max()),
        "random_p_mean": float(ps.mean()),
        "random_fpr_at_0.05": fpr,
        "random_fpr_k": k_fp,
        "random_fpr_binom_p": p_fpr,
        "real_pct_in_random": pct_ge,
        "raw": {"random_hits": [round(float(v), 4) for v in hits],
                "random_ps": [round(float(v), 4) for v in ps]},
        "random_rule_dist": rules,
        "verdict": verdict,
        "reason": reason,
    }

    out_md = write_report(summary, hits, ps)
    json.dump(summary, open(paths.p("audit", "random_control_oot.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print("\n[random_control_oot] 判定 = %s" % verdict)
    print("[random_control_oot] %s" % reason)
    print("[random_control_oot] 报告: %s" % out_md)
    return out_md


def _binom_sf(k, n, p):
    """P(X >= k)，X~Binomial(n,p)。用于检验假显著率是否超过名义水平。"""
    from math import comb
    if n <= 0:
        return 1.0
    k = max(0, int(k))
    return float(sum(comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k, n + 1)))


def write_report(s, hits, ps):
    L = []
    L.append("# OOT 随机数据对照闸门报告")
    L.append("")
    L.append("> 生成时间：%s" % _now())
    L.append("> 性质：宿主审计，不改生产状态、不进容器。")
    L.append("")
    L.append("## 1. 审计对象")
    L.append("")
    L.append("- 候选基因组：`%s`" % json.dumps(s["ev"], ensure_ascii=False))
    L.append("- 零假设类型：`%s`（`TEST_SUR_TYPE` 路由）" % s["sur_type"])
    L.append("- 真实数据量 N = %d，随机对照 M = %d，k_sur = %d" % (s["n_real"], s["m_random"], s["k_sur"]))
    L.append("")
    L.append("## 2. 结果")
    L.append("")
    L.append("| 指标 | 真实数据 | 纯随机数据 |")
    L.append("|---|---|---|")
    L.append("| OOT 命中率 | %.4f | 均值 %.4f (sd %.4f, 区间 %.4f~%.4f) |"
             % (s["real"]["hit_rate"], s["random_hit_mean"], s["random_hit_std"],
                s["random_hit_min"], s["random_hit_max"]))
    L.append("| OOT p_random | %.4f | 均值 %.4f |" % (s["real"]["p_random"], s["random_p_mean"]))
    L.append("| 命中样本数 n | %s | — |" % s["real"]["n"])
    L.append("| 冻结读取规则 | %s | %s |" % (s["real"]["best_rule"], json.dumps(s["random_rule_dist"], ensure_ascii=False)))
    L.append("")
    L.append("- 随机数据 p<0.05 比例（名义应为 5%%）：**%.1f%%**（%d/%d）"
             % (s["random_fpr_at_0.05"] * 100, s["random_fpr_k"], s["m_random"]))
    L.append("- 该比例是否显著超过名义 5%%：二项检验 **p = %.3f**（>0.05 即与名义无显著差异）"
             % s["random_fpr_binom_p"])
    L.append("- 真实命中率在随机分布中的百分位（随机 ≥ 真实 的比例）：**%.0f%%**"
             % (s["real_pct_in_random"] * 100))
    L.append("")
    L.append("## 3. 判定")
    L.append("")
    L.append("**%s**" % s["verdict"])
    L.append("")
    L.append(s["reason"])
    L.append("")
    L.append("## 4. 诚实校准（不越界解读）")
    L.append("")
    L.append("1. 本判定只针对**该候选的 OOT 评分口径**是否可信，**不是**对"
             "「双色球有无结构」的结论。")
    L.append("2. 若判 ARTIFACT_BY_CONSTRUCTION，含义是：当前 OOT 的显著性由"
             "**信号构造 + 零假设错配**产生，不能作为「战胜随机」的证据。")
    L.append("3. 真实数据是否可预测仍由**修正后的闸门**判定；在修正前，"
             "该候选的 `above_random` 标志应视为**无效**。")
    L.append("4. 修复方向：让替代分布与信号构造**同变换**（对平滑序列应使用"
             "保留线性结构的 AAFT/IAAFT，而非 shuffle），或用"
             "**随机数据零分布**直接替代 surrogate 校准。")
    L.append("")

    out = paths.p("audit", "random_control_oot.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L))
    return out


def _now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "--report-only":
        # 用缓存的原始数组重生成报告（不重跑昂贵的 OOT）
        cached = json.load(open(paths.p("audit", "random_control_oot.json"), encoding="utf-8"))
        raw = cached.get("raw") or {}
        hits = np.array(raw.get("random_hits") or [])
        ps = np.array(raw.get("random_ps") or [])
        print("[random_control_oot] 报告重生成: %s" % write_report(cached, hits, ps))
    else:
        m = int(args[0]) if args else 20
        main(m_random=m)
