"""物理约束下的偏倚模型 + 红蓝联合功效分析（宿主专用，零风险）。

解决两个此前悬空的问题
----------------------
1. **σ 的物理含义未定义**：此前"σ≈3.5%"只是一个描述性统计量（球频率相对 sd），
   既没有落在单纯形上，也没有与任何可测量的物理量挂钩。这里把它映射为
   `σ_freq = |c| · σ_θ`，其中 c 是物理灵敏度、σ_θ 是球体物理参数的相对离散度。
   ⇒ 实验室一旦测出 σ_θ，就能立即反推所需灵敏度 c，或反之。

2. **蓝球单独功效仅 12%，联合建模能买回多少**：若红蓝偏倚由**共享物理机制**
   （同一台机器的气流/振动/静电）驱动，联合检验可池化证据；若是两套球组
   各自独立的属性，则联合可能反而稀释。这里把两种情形都算出来。
"""

import json

import numpy as np

import honesty_footer as HF
import paths
from exchangeable_probe import N_BALL, N_PICK, N_BLUE, gen_uniform, load_real


# ---------------------------------------------------------------------------
# 1. 物理映射：σ_freq = |c| · σ_θ
# ---------------------------------------------------------------------------
def physical_mapping(sigma_freq_pct, c_list=(0.1, 0.3, 1.0, 3.0)):
    """给定观测到的频率离散度，反推各灵敏度 c 下所需的物理参数离散度 σ_θ。

    模型：p_i = p_0 (1 + c · δθ_i)，δθ_i 为归一化物理参数偏差（sd = σ_θ）
        ⇒ Var(p_i) = p_0² c² σ_θ²
        ⇒ σ_freq ≡ std(p)/mean(p) = |c| · σ_θ
    """
    rows = []
    for c in c_list:
        rows.append({"sensitivity_c": c,
                     "required_sigma_theta_pct": sigma_freq_pct / c,
                     "interpretation": _interp(c)})
    return {"sigma_freq_pct": sigma_freq_pct,
            "relation": "sigma_freq = |c| * sigma_theta",
            "rows": rows}


def _interp(c):
    if c >= 3:
        return "极高灵敏：微小物理差异即造成显著频率偏倚（现实中罕见）"
    if c >= 1:
        return "高灵敏：1% 物理差异 ⇒ 1% 频率差异"
    if c >= 0.3:
        return "中灵敏：需约 10% 级的物理离散度才能解释观测"
    return "低灵敏：需极大物理离散度（>35%），与精密制造球组不符"


# ---------------------------------------------------------------------------
# 2. 红蓝联合功效
# ---------------------------------------------------------------------------
def _chi2(counts):
    e = counts.sum() / len(counts)
    return float(((counts - e) ** 2 / e).sum())


def _gen_biased_counts(n_draw, k_ball, per_draw, sigma_pct, rng, seed):
    w = np.exp(np.random.default_rng(seed).normal(0, sigma_pct / 100.0, k_ball))
    p = w / w.sum()
    cs = np.cumsum(p)
    out = np.zeros((n_draw, per_draw), dtype=np.int64)
    for i in range(n_draw):
        picked = []
        for x in rng.random(per_draw):
            j = int(np.searchsorted(cs, x))
            while j in picked or j >= k_ball:
                j = (j + 1) % k_ball
            picked.append(j)
        out[i] = np.sort(np.array(picked) + 1)
    return out, np.bincount(out.ravel(), minlength=k_ball + 1)[1:k_ball + 1].astype(float)


def joint_power(n=3496, m_mc=400, m_pos=200, sigma_list=(3.5, 6.0), seed=4242):
    """红球单独 / 蓝球单独 / 红蓝联合 的检出功效（min-p 联合，蒙特卡洛校准）。"""
    rng = np.random.default_rng(seed)

    # 零分布
    null_r, null_b = [], []
    for _ in range(m_mc):
        rr = gen_uniform(n, rng)
        null_r.append(_chi2(np.bincount(rr.ravel(), minlength=N_BALL + 1)[1:N_BALL + 1].astype(float)))
        null_b.append(_chi2(np.bincount(rng.integers(1, N_BLUE + 1, size=n),
                                        minlength=N_BLUE + 1)[1:N_BLUE + 1].astype(float)))
    null_r = np.array(null_r)
    null_b = np.array(null_b)

    def rp(obs, null):
        return float(min(1.0, (null >= obs).mean() + 0.5 / len(null)))

    # min-p 联合的零分布（同时生成红蓝）
    jn = []
    for _ in range(m_mc):
        rr = gen_uniform(n, rng)
        a = _chi2(np.bincount(rr.ravel(), minlength=N_BALL + 1)[1:N_BALL + 1].astype(float))
        b = _chi2(np.bincount(rng.integers(1, N_BLUE + 1, size=n),
                              minlength=N_BLUE + 1)[1:N_BLUE + 1].astype(float))
        jn.append(min(rp(a, null_r), rp(b, null_b)))
    jn = np.array(jn)
    thr_j = float(np.quantile(jn, 0.05))

    thr_r = float(np.quantile(null_r, 0.95))
    thr_b = float(np.quantile(null_b, 0.95))

    out = []
    for sg in sigma_list:
        hr = hb = hj = 0
        for m in range(m_pos):
            # 共享机制：红蓝承受**同一强度**的相对偏倚（不同球组独立抽样）
            _, cr = _gen_biased_counts(n, N_BALL, N_PICK, sg, rng, 10000 + m)
            _, cb = _gen_biased_counts(n, N_BLUE, 1, sg, rng, 20000 + m)
            a, b = _chi2(cr), _chi2(cb)
            if a >= thr_r:
                hr += 1
            if b >= thr_b:
                hb += 1
            if min(rp(a, null_r), rp(b, null_b)) <= thr_j:
                hj += 1
        out.append({"sigma_pct": sg, "red_only": hr / m_pos,
                    "blue_only": hb / m_pos, "joint_min_p": hj / m_pos})
    return {"m_mc": m_mc, "m_pos": m_pos,
            "null_red": {"mean": float(null_r.mean()), "sd": float(null_r.std()), "thr95": thr_r},
            "null_blue": {"mean": float(null_b.mean()), "sd": float(null_b.std()), "thr95": thr_b},
            "power": out,
            "note": ("共享机制假设：红蓝承受同一**强度**的相对偏倚(σ)，"
                     "但具体球号偏差独立抽样(两套球组)。联合用 min-p，蒙特卡洛校准。")}


# ---------------------------------------------------------------------------
# 3. 主流程
# ---------------------------------------------------------------------------
def main(m_mc=400, m_pos=200):
    rep = {"footer": HF.HONESTY_FOOTER}
    print("=" * 68)
    print("物理约束偏倚模型 + 红蓝联合功效")
    print("=" * 68)

    print("\n[1] σ 的物理映射（σ_freq = |c| · σ_θ）")
    pm = physical_mapping(3.5)
    print("    观测 σ_freq = %.1f%%" % pm["sigma_freq_pct"])
    print("    %-12s %-22s %s" % ("灵敏度 c", "所需 σ_θ", "判读"))
    for r in pm["rows"]:
        print("    %-12.1f %-22.1f%% %s" % (r["sensitivity_c"],
                                            r["required_sigma_theta_pct"], r["interpretation"]))
    rep["physical_mapping"] = pm

    print("\n[2] 红球单独 / 蓝球单独 / 红蓝联合 的检出功效")
    jp = joint_power(m_mc=m_mc, m_pos=m_pos)
    print("    零假设: 红 χ² %.2f±%.2f (阈%.1f)   蓝 χ² %.2f±%.2f (阈%.1f)"
          % (jp["null_red"]["mean"], jp["null_red"]["sd"], jp["null_red"]["thr95"],
             jp["null_blue"]["mean"], jp["null_blue"]["sd"], jp["null_blue"]["thr95"]))
    print("    %-10s %-12s %-12s %s" % ("σ", "红球单独", "蓝球单独", "联合 min-p"))
    for q in jp["power"]:
        print("    %-10.1f%% %-12.0f%% %-12.0f%% %.0f%%"
              % (q["sigma_pct"], 100 * q["red_only"], 100 * q["blue_only"], 100 * q["joint_min_p"]))
    rep["joint_power"] = jp

    out = paths.p("audit", "physical_bias_model.json")
    json.dump(rep, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[physical_bias_model] 结果: %s" % out)
    print("[页脚] %s" % HF.HONESTY_FOOTER)
    return out


if __name__ == "__main__":
    main()
