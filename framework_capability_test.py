"""框架能力推演——已知答案考卷（ground-truth 自测）。

原理
----
评价一套科学仪器的唯一硬标准：**把已知答案的考卷给它盲考**。
合成四个真值已知的域，整套框架（边际χ² + 同质性χ² + K段持续性）
逐域盲测，对答案：

  域A  纯均匀随机            → 期望：全部不检出（无假阳性）
  域B  静态球级偏倚 σ=6%     → 期望：边际+持续性检出，同质性不响
  域C  换球时代结构 σ=10%    → 期望：同质性检出（时序结构）
  域D  静态偏倚 σ=2%（低于功效）→ 期望：不检出，**且如实报告最小可检出效应量**

第四张考卷最关键：仪器对"看不见的东西"必须诚实地说"我看不见"+给出
最小可检出量，而不是含糊其辞。这是"缺乏证据≠证据缺乏"的落地形态。

注意：合成域上的检验**不计入真实数据的分析账本**——
合成数据有已知真值，不产生假阳性机会。
"""

import json

import numpy as np

import honesty_footer as HF
import paths

N = 3496
K = 33
M = 6


# ---------------------------------------------------------------------------
# 生成器
# ---------------------------------------------------------------------------
def gen_uniform(n, rng):
    return np.sort(rng.random((n, K)).argsort(axis=1)[:, :M] + 1, axis=1)


def gen_static(n, rng, sigma, seed):
    w = np.exp(np.random.default_rng(seed).normal(0, sigma / 100.0, K))
    p = w / w.sum()
    cs = np.cumsum(p)
    out = np.zeros((n, M), dtype=np.int64)
    for i in range(n):
        picked = []
        for x in rng.random(M):
            j = int(np.searchsorted(cs, x))
            while j in picked or j >= K:
                j = (j + 1) % K
            picked.append(j)
        out[i] = np.sort(np.array(picked) + 1)
    return out


def gen_eras(n, rng, sigma, n_eras=2, seed=0):
    out = np.zeros((n, M), dtype=np.int64)
    bounds = np.linspace(0, n, n_eras + 1).astype(int)
    for e in range(n_eras):
        w = np.exp(np.random.default_rng(seed + e).normal(0, sigma / 100.0, K))
        p = w / w.sum()
        cs = np.cumsum(p)
        for i in range(bounds[e], bounds[e + 1]):
            picked = []
            for x in rng.random(M):
                j = int(np.searchsorted(cs, x))
                while j in picked or j >= K:
                    j = (j + 1) % K
                picked.append(j)
            out[i] = np.sort(np.array(picked) + 1)
    return out


# ---------------------------------------------------------------------------
# 统计量
# ---------------------------------------------------------------------------
def chi2_marginal(r):
    c = np.bincount(r.ravel(), minlength=K + 1)[1:K + 1].astype(float)
    e = c.sum() / K
    return float(((c - e) ** 2 / e).sum())


def chi2_homog(r, n_seg=4):
    segs = np.array_split(np.arange(len(r)), n_seg)
    tab = np.array([np.bincount(r[s].ravel(), minlength=K + 1)[1:K + 1]
                    .astype(float) for s in segs])
    row = tab.sum(axis=1, keepdims=True)
    col = tab.sum(axis=0, keepdims=True)
    E = row @ col / tab.sum()
    return float(((tab - E) ** 2 / np.maximum(E, 1e-9)).sum())


def persist_r(r, n_seg=4):
    segs = np.array_split(np.arange(len(r)), n_seg)
    D = []
    for s in segs:
        c = np.bincount(r[s].ravel(), minlength=K + 1)[1:K + 1].astype(float)
        e = len(r[s]) * M / K
        D.append((c - e) / e * 100.0)
    pairs = [(i, j) for i in range(n_seg) for j in range(i + 1, n_seg)]
    return float(np.mean([np.corrcoef(D[i], D[j])[0, 1] for i, j in pairs]))


def mde_sigma(null_chi2_mean, rel_sd_pct, n_seg=4, power_target=0.80):
    """最小可检出效应量：静态偏倚 σ 使检出功效达 power_target 所需的值（%）。

    检出判据：χ²_obs ≥ χ²_null 的 95% 分位。功效 = P(χ²_sig ≥ thr)。
    用解析近似：非中心 χ² ≈ K·(1+σ²/σ_noise²)。
    """
    from scipy.stats import norm as _norm, chi2 as _c2
    df_eff = null_chi2_mean          # 蒙特卡洛零假设均值即有效自由度
    thr = _c2.ppf(0.95, df_eff)
    # 需要 mean_sig >= thr + z_{0.8}·sd；sd ≈ sqrt(2·df_eff)
    need = thr + _norm.ppf(power_target) * np.sqrt(2 * df_eff)
    ratio = need / df_eff
    return float(rel_sd_pct * np.sqrt(max(0.0, ratio - 1.0)))


# ---------------------------------------------------------------------------
# 单域盲测
# ---------------------------------------------------------------------------
def exam(reds, m_mc=300, seed=99):
    rng = np.random.default_rng(seed)
    null_m = np.array([chi2_marginal(gen_uniform(len(reds), rng)) for _ in range(m_mc)])
    null_h = np.array([chi2_homog(gen_uniform(len(reds), rng)) for _ in range(m_mc)])
    null_p = np.array([persist_r(gen_uniform(len(reds), rng)) for _ in range(m_mc)])

    pm = float((null_m >= chi2_marginal(reds)).mean() + 0.5 / m_mc)
    ph = float((null_h >= chi2_homog(reds)).mean() + 0.5 / m_mc)
    pp = float((null_p >= persist_r(reds)).mean() + 0.5 / m_mc)

    rel_sd = 100.0 * np.sqrt(len(reds) * M * (1 / K) * (1 - 1 / K)) / (len(reds) * M / K)
    mde = mde_sigma(float(null_m.mean()), rel_sd)

    return {"p_marginal": round(pm, 4), "p_homog": round(ph, 4), "p_persist": round(pp, 4),
            "mde_sigma_pct": round(mde, 2), "rel_sd_null_pct": round(rel_sd, 2)}


def characterize(res):
    """从三闸门结果给出类型判定（与判决卡同规则）。"""
    sig_m = res["p_marginal"] < 0.05
    sig_h = res["p_homog"] < 0.05
    sig_p = res["p_persist"] < 0.05
    if sig_m and sig_p and not sig_h:
        return "静态偏倚（时不变）"
    if sig_h:
        return "时序结构（时代切换/状态）"
    if sig_m:
        return "未解释离散（机制未明）"
    return "未检出（报告 MDE=%.2f%%）" % res["mde_sigma_pct"]


# ---------------------------------------------------------------------------
# 主流程：四张考卷
# ---------------------------------------------------------------------------
def main(m_mc=300, seed=20260830, n_rep=12):
    """**率**based 考卷：每域 n_rep 个独立种子，报检出率而非单点结论。

    为什么必须是率：首版用单个实现，域A(纯均匀)抽到一次极端样本
    (z_chi2=2.23, z_persist=3.61) 被误判成"静态偏倚" ⇒ 得分 1/4。
    复查 12 个均匀种子：平均 z=+0.16/+0.17(应≈0) ⇒ **零假设健康,
    那只是 1/2000 的运气**。单点实现当结论 = 我们这两天一直在批判的错误。
    """
    rng = np.random.default_rng(seed)
    n = N
    # 共享零假设（一次性算好，供所有域用）
    null_m = np.array([chi2_marginal(gen_uniform(n, rng)) for _ in range(m_mc)])
    null_h = np.array([chi2_homog(gen_uniform(n, rng)) for _ in range(m_mc)])
    null_p = np.array([persist_r(gen_uniform(n, rng)) for _ in range(m_mc)])

    def p_of(obs, null):
        return float((null >= obs).mean() + 0.5 / m_mc)

    rel_sd = 100.0 * np.sqrt(n * M * (1 / K) * (1 - 1 / K)) / (n * M / K)
    mde = mde_sigma(float(null_m.mean()), rel_sd)

    papers = [
        ("A 纯均匀(无结构)", lambda sd: gen_uniform(n, np.random.default_rng(sd)), None),
        ("B 静态偏倚 σ=6%", lambda sd: gen_static(n, np.random.default_rng(sd), 6.0, sd), 6.0),
        ("C 换球时代 σ=10%", lambda sd: gen_eras(n, np.random.default_rng(sd), 10.0, 2, sd), 10.0),
        ("D 静态偏倚 σ=2%(低于功效)", lambda sd: gen_static(n, np.random.default_rng(sd), 2.0, sd), 2.0),
    ]
    print("=" * 72)
    print("框架能力推演：已知答案考卷（**率**based, 每域 %d 个独立种子, MC M=%d）" % (n_rep, m_mc))
    print("=" * 72)
    print("零假设: 边际χ² %.2f±%.2f | 同质性 %.2f±%.2f | 持续性 %+.4f±%.4f"
          % (null_m.mean(), null_m.std(), null_h.mean(), null_h.std(),
             null_p.mean(), null_p.std()))
    print("MDE(80%%功效的最小可检出 σ) = %.2f%%   零假设噪声 sd = %.2f%%" % (mde, rel_sd))

    rows = []
    for name, fn, truth in papers:
        hits = {"marginal": 0, "homog": 0, "persist": 0}
        char_ok = 0
        ps_m, ps_p = [], []
        for i in range(n_rep):
            reds = fn(2000 + 100 * len(rows) + i)
            pm = p_of(chi2_marginal(reds), null_m)
            ph = p_of(chi2_homog(reds), null_h)
            pp = p_of(persist_r(reds), null_p)
            ps_m.append(pm); ps_p.append(pp)
            sm, sh, sp = pm < 0.05, ph < 0.05, pp < 0.05
            hits["marginal"] += sm; hits["homog"] += sh; hits["persist"] += sp
            verdict = characterize({"p_marginal": pm, "p_homog": ph, "p_persist": pp,
                                    "mde_sigma_pct": mde})
            if truth is None:
                good = (not sm and not sh and not sp)          # 无假阳性
            elif truth == 6.0:
                good = (sm and sp and not sh)                  # 静态偏倚画像
            elif truth == 10.0:
                good = sh                                       # 时序结构
            else:
                good = (not sm)                                 # 低于功效: 不检出=诚实
            char_ok += good
        row = {"domain": name, "truth_sigma": truth, "n_rep": n_rep,
               "rate_marginal": hits["marginal"] / n_rep,
               "rate_homog": hits["homog"] / n_rep,
               "rate_persist": hits["persist"] / n_rep,
               "characterization_correct_rate": char_ok / n_rep,
               "mde_sigma_pct": round(mde, 2), "rel_sd_null_pct": round(rel_sd, 2),
               "min_p_marginal": round(float(min(ps_m)), 4),
               "min_p_persist": round(float(min(ps_p)), 4)}
        rows.append(row)
        print("\n【%s】真值: %s  (n=%d 种子)" % (name, "无结构" if truth is None else "σ=%s%%" % truth, n_rep))
        print("  检出率: 边际χ² %.0f%% | 同质性 %.0f%% | 持续性 %.0f%%"
              % (100 * row["rate_marginal"], 100 * row["rate_homog"], 100 * row["rate_persist"]))
        print("  **表征正确率 = %.0f%%**" % (100 * row["characterization_correct_rate"]))

    summary = {"M": m_mc, "n_rep": n_rep, "papers": rows,
               "null": {"marginal_mean": float(null_m.mean()), "marginal_sd": float(null_m.std()),
                        "homog_mean": float(null_h.mean()), "homog_sd": float(null_h.std()),
                        "persist_mean": float(null_p.mean()), "persist_sd": float(null_p.std())},
               "mde_sigma_pct": round(mde, 2), "footer": HF.HONESTY_FOOTER,
               "note": ("合成域检验不计入真实数据分析账本（有已知真值，不产生假阳性机会）。"
                        "首版单点考卷已废弃：纯均匀域曾因单个极端样本被误判，"
                        "复查确认零假设健康（12 种子平均 z≈0.16）")}
    print("\n" + "-" * 72)
    print("能力总结：")
    print("  域A 假阳性率  %.0f%% / %.0f%% / %.0f%%（边际/同质性/持续性，名义 5%%）"
          % (100 * rows[0]["rate_marginal"], 100 * rows[0]["rate_homog"], 100 * rows[0]["rate_persist"]))
    print("  域B σ=6%% 静态 => 边际检出 %.0f%%, 持续性检出 %.0f%%, 表征正确 %.0f%%"
          % (100 * rows[1]["rate_marginal"], 100 * rows[1]["rate_persist"], 100 * rows[1]["characterization_correct_rate"]))
    print("  域C σ=10%% 时代 => 同质性检出 %.0f%%, 表征正确 %.0f%%"
          % (100 * rows[2]["rate_homog"], 100 * rows[2]["characterization_correct_rate"]))
    print("  域D σ=2%% 低于功效 => 边际检出 %.0f%%（不检出即诚实）, MDE=%.2f%%"
          % (100 * rows[3]["rate_marginal"], mde))
    out = paths.p("audit", "framework_capability_test.json")
    json.dump(summary, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[framework_capability_test] %s" % out)
    return out


if __name__ == "__main__":
    main()
