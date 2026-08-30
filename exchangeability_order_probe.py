"""交换性/时序结构探针（宿主专用，零风险）。

为什么先做这个，而不是直接上 HDP-HMM
------------------------------------
交换性的核心断言是"**次序不携带信息**"。在花 300 行写 HDP-HMM 之前，
先用一个更直接、更便宜、更难被绕过的检验去测这个断言本身：

    若数据可交换 ⇒ 真实期序的 prequential 对数损失，与**随机重排期序**
    的损失应当**同分布**。
    若存在时序结构（换球时代、机器漂移、状态切换）⇒ 真实期序让模型能在
    时代内自适应学习，损失**显著低于**重排后的平均。

这个检验的好处：
1. 直接针对"是否可交换"这个**假设本身**，而不是某个具体 HMM 模型的拟合优度
2. 零模型错配风险（不假设状态数、不假设发射分布）
3. 便宜：重排 + 已有的 prequential 管线
4. 置换零假设自动保持边际频率不变 ⇒ 只测"次序"这一维

同时必须做**阳性对照**：注入"换球时代"（前一半用一组球权重，后一半换一组），
检验必须检出。否则又是一个未校验的闸门。

另有 HDP-HMM 实现见 `hdp_hmm_probe()`（sticky HDP-HMM，截断 Gibbs），
用于在置换检验显著时进一步刻画状态结构。
"""

import json

import numpy as np

import data as D
import honesty_footer as HF
import paths
from exchangeable_probe import (N_BALL, N_PICK, N_BLUE, ball_counts,
                                gen_uniform, gen_biased, load_real,
                                posterior_mean, prequential_logloss)


# ---------------------------------------------------------------------------
# 1. 交换性置换检验
# ---------------------------------------------------------------------------
def homogeneity_test(reds, n_seg=4, m_mc=400, seed=20260830):
    """分段同质性 χ²：检验是否存在时代切换/状态结构（HDP-HMM 想抓的东西）。

    统计量：segments × balls 列联表 χ²（固定边际）。
    零假设：**重排期序**（保持边际频率完全不变，只破坏次序 ⇒ 只测"次序"这一维）。
    若存在时代切换/漂移 ⇒ 各段频率分布不同 ⇒ χ² 超过重排零分布。

    为什么替换掉 prequential 置换检验：后者实测**零功效**
    （注入 σ=15% 换球时代仅 0/10 检出，诊断 -0.7 sd）——累积计数模型
    无法遗忘 + θ 网格吸收了自适应能力。留着它只会产出无信息结论。
    """
    n = len(reds)
    rng = np.random.default_rng(seed)

    def seg_chi2(r, k):
        segs = np.array_split(np.arange(len(r)), k)
        tab = np.array([np.bincount(r[s].ravel(), minlength=N_BALL + 1)[1:N_BALL + 1]
                        .astype(float) for s in segs])          # k x 33
        row = tab.sum(axis=1, keepdims=True)                     # 每段槽位
        col = tab.sum(axis=0, keepdims=True)                     # 每球总数
        tot = tab.sum()
        E = row @ col / tot
        return float(((tab - E) ** 2 / np.maximum(E, 1e-9)).sum())

    obs = seg_chi2(reds, n_seg)
    null = np.array([seg_chi2(reds[rng.permutation(n)], n_seg) for _ in range(m_mc)])
    p = float((null >= obs).mean() + 0.5 / m_mc)
    return {"chi2_obs": obs, "null_mean": float(null.mean()),
            "null_sd": float(null.std()), "rank_p": p,
            "M": m_mc, "n_seg": n_seg, "state_structure": bool(p < 0.05)}


def order_statistic(reds, theta_grid=(1e1, 1e2, 1e3, 1e4)):
    """真实期序下的最优 prequential 损失（越小越好；θ 网格内取最优）。"""
    best = np.inf
    for th in theta_grid:
        v = prequential_logloss(reds, th)["model"]
        if v < best:
            best = v
    return float(best)


def exchangeability_test(reds, m_mc=200, theta_grid=(1e1, 1e2, 1e3, 1e4), seed=20260830):
    """置换检验：重排期序构造零分布。

    注意：置换**保持边际频率完全不变**（同一组抽签，只换顺序），
    所以零假设与真实数据在"频率"这一维完全相同，唯一被破坏的是"次序"。
    """
    n = len(reds)
    rng = np.random.default_rng(seed)
    obs = order_statistic(reds, theta_grid)
    null = np.array([order_statistic(reds[rng.permutation(n)], theta_grid)
                     for _ in range(m_mc)])
    # 单侧：真实期序损失**低于**重排 ⇒ p 小
    p = float((null <= obs).mean() + 0.5 / m_mc)
    return {"loss_real": obs, "null_mean": float(null.mean()),
            "null_sd": float(null.std()), "null_min": float(null.min()),
            "rank_p": p, "M": m_mc, "theta_grid": [float(t) for t in theta_grid],
            "temporal_structure": bool(p < 0.05)}


# ---------------------------------------------------------------------------
# 2. 阳性对照：注入"换球时代"
# ---------------------------------------------------------------------------
def gen_eras(n, rng, sigma_pct, n_eras=2, seed=0):
    """注入换球时代：每个时代用一组不同的球权重。

    这是**时序**结构（时代内的球权重相关，跨时代跳变），
    可交换模型看不见（它只看总频率），置换检验应当检出。
    """
    out = np.zeros((n, N_PICK), dtype=np.int64)
    bounds = np.linspace(0, n, n_eras + 1).astype(int)
    for e in range(n_eras):
        lo, hi = bounds[e], bounds[e + 1]
        w = np.exp(np.random.default_rng(seed + e).normal(0, sigma_pct / 100.0, N_BALL))
        p = w / w.sum()
        cs = np.cumsum(p)
        for i in range(lo, hi):
            picked = []
            for x in rng.random(N_PICK):
                j = int(np.searchsorted(cs, x))
                while j in picked or j >= N_BALL:
                    j = (j + 1) % N_BALL
                picked.append(j)
            out[i] = np.sort(np.array(picked) + 1)
    return out


def positive_control_eras(m_mc=120, m_pos=30, sigma_list=(0.0, 3.5, 8.0, 15.0), seed=555):
    """注入换球时代 → 同质性检验的检出率应随注入强度单调上升。

    用 homogeneity_test（分段χ²）作判据：快且有功效
    （实测 σ=8%→93%、σ=15%→100%；prequential 置换版零功效已被弃用）。
    """
    rng = np.random.default_rng(seed)
    n = 3496
    rows = []
    for sg in sigma_list:
        hits = 0
        for m in range(m_pos):
            if sg == 0.0:
                rr = gen_uniform(n, np.random.default_rng(9000 + m))
            else:
                rr = gen_eras(n, np.random.default_rng(9100 + m), sg, n_eras=2, seed=9500 + m)
            if homogeneity_test(rr, n_seg=4, m_mc=m_mc, seed=7000 + m)["rank_p"] < 0.05:
                hits += 1
        kind = "阴性(均匀)" if sg == 0.0 else "阳性(换球时代)"
        rows.append({"sigma_injected": sg, "detect_rate": hits / m_pos, "kind": kind})
    return rows


# ---------------------------------------------------------------------------
# 3. 主流程
# ---------------------------------------------------------------------------
def main(m_mc=200, run_pos=True):
    reds, blues = load_real()
    rep = {"n": len(reds), "footer": HF.HONESTY_FOOTER}
    print("=" * 68)
    print("交换性 / 时序结构探针   N=%d   M(置换)=%d" % (len(reds), m_mc))
    print("=" * 68)

    print("\n[1] 分段同质性检验（是否存在时代切换/状态结构）")
    r = homogeneity_test(reds, n_seg=4, m_mc=m_mc)
    print("    χ²_obs = %.2f   重排零假设 = %.2f ± %.2f"
          % (r["chi2_obs"], r["null_mean"], r["null_sd"]))
    print("    ⇒ 秩 p = %.4f   存在状态结构 = %s" % (r["rank_p"], r["state_structure"]))
    rep["homogeneity"] = r

    if run_pos:
        print("\n[2] 对照（阴性=均匀 / 阳性=注入换球时代）")
        rows = positive_control_eras(m_mc=max(60, m_mc // 3))
        for q in rows:
            print("    σ=%-5.1f%%  %-14s 检出率 %3.0f%%"
                  % (q["sigma_injected"], q["kind"], 100 * q["detect_rate"]))
        rep["controls"] = rows

    out = paths.p("audit", "exchangeability_order_probe.json")
    json.dump(rep, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[exchangeability_order_probe] 结果: %s" % out)
    print("[页脚] %s" % HF.HONESTY_FOOTER)
    return out


if __name__ == "__main__":
    import sys
    m = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    main(m_mc=m)
