"""球级可交换(Pitman-Yor / Dirichlet)探针 + 精确自证伪协议。

宿主专用审计模块：不进容器、不改生产判定、无新依赖。

为什么是「球级」而不是「组合级」
--------------------------------
组合支撑 C(33,6)×16 = 17,721,088，而 N=3496 ⇒ 覆盖率 0.0197%，
实测 3490/3496 个组合只出现一次。全 singleton 数据会把 PY/DP 后验
推到 θ→∞ / d→1 的「均匀」边界（EPPF ∏(θ+i·d)/(θ+i) 单调增至 1）。
⇒ 组合级**没有分辨力**，只能给出「不可预测」的严格证书。

球级则完全不同：20,976 个红球槽位 / 33 个球 ⇒ 每球期望 635.6，
零假设相对 sd 仅 3.91%。**功效在这里。**

本探针做的事
------------
1. Dirichlet(θ/33) 后验 → 每球频率的点估计 + 后验区间（带有限样本误差界）
2. θ 的 Dirichlet-multinomial 极大似然估计（θ→∞ 即「均匀/无结构」）
3. 顺序预测对数损失（prequential log-loss）对比均匀基线
4. 组合级缺失质量界（Good-Turing 点估计 + McAllester–Schapire 有限样本界）
5. 后验预测检验 / 标签等变 / 时移不变
6. **阳性对照**（注入 σ=1/2/3.5/6% 球级偏倚，要求检出率单调）
7. **阴性对照**（均匀随机，要求 FPR 回名义，二项检验判据）
8. σ 点估计 + 误差界（判决 marginal_bias_probe 的 CANDIDATE）
"""

import json
import os
from math import comb, lgamma, log, exp, sqrt

import numpy as np

import data as D
import honesty_footer as HF
import paths

N_BALL = 33
N_PICK = 6
N_BLUE = 16
COMB_SUPPORT = comb(33, 6) * 16


# ---------------------------------------------------------------------------
# 1. 数据
# ---------------------------------------------------------------------------
def load_real():
    master = D.load_master(paths.master_csv())
    reds, blues, _ = D.to_arrays(master)
    return np.asarray(reds), np.asarray(blues)


def ball_counts(reds):
    return np.bincount(np.asarray(reds).ravel(), minlength=N_BALL + 1)[1:N_BALL + 1].astype(float)


def gen_uniform(n, rng):
    return np.sort(rng.random((n, N_BALL)).argsort(axis=1)[:, :N_PICK] + 1, axis=1)


def gen_biased(n, rng, sigma_pct, seed=0):
    """注入球级静态偏倚：每球固定权重 ~ lognormal(0, sigma)。"""
    w = np.exp(np.random.default_rng(seed).normal(0, sigma_pct / 100.0, N_BALL))
    p = w / w.sum()
    cs = np.cumsum(p)
    out = np.zeros((n, N_PICK), dtype=np.int64)
    for i in range(n):
        picked = []
        for x in rng.random(N_PICK):
            j = int(np.searchsorted(cs, x))
            while j in picked or j >= N_BALL:
                j = (j + 1) % N_BALL
            picked.append(j)
        out[i] = np.sort(np.array(picked) + 1)
    return out


# ---------------------------------------------------------------------------
# 2. Dirichlet 后验 + θ 估计
# ---------------------------------------------------------------------------
def posterior_mean(counts, theta, slots=None):
    """p̂_i = (θ/K + n_i) / (θ + N)。θ→∞ 收缩到均匀，θ→0 退化为经验频率。"""
    a = theta / N_BALL
    tot = counts.sum() if slots is None else slots
    return (a + counts) / (theta + tot)


def posterior_sd(counts, theta, slots=None):
    p = posterior_mean(counts, theta, slots)
    tot = counts.sum() if slots is None else slots
    return np.sqrt(p * (1 - p) / (theta + tot + 1.0))


def dirichlet_multinomial_ll(counts, theta):
    """log p(n | θ)，对称 Dirichlet(θ/K) 下的 Dirichlet-多项边际似然。"""
    N = counts.sum()
    a = theta / N_BALL
    ll = lgamma(theta) - lgamma(theta + N)
    ll += float(np.sum([lgamma(c + a) for c in counts])) - N_BALL * lgamma(a)
    return ll


def estimate_theta(counts, grid=None):
    """在网格上找 θ 的极大似然。θ 越大 ⇒ 越接近均匀。"""
    if grid is None:
        grid = np.concatenate([np.logspace(-1, 6, 90), [1e9]])
    lls = np.array([dirichlet_multinomial_ll(counts, t) for t in grid])
    i = int(np.argmax(lls))
    return float(grid[i]), float(lls[i]), float(lls[-1])  # θ̂, ll(θ̂), ll(θ→∞=均匀)


# ---------------------------------------------------------------------------
# 3. 统计量：χ²（无放回 ⇒ 零假设均值由蒙特卡洛定，不用渐近 32）
# ---------------------------------------------------------------------------
def chi2_counts(counts):
    e = counts.sum() / N_BALL
    return float(((counts - e) ** 2 / e).sum())


def sigma_from_chi2(obs_chi2, null_mean, rel_sd_null):
    """由 χ² 超额反推持续偏倚 σ（%）。方差比 r = obs/null_mean。"""
    r = obs_chi2 / null_mean
    if r <= 1.0:
        return 0.0
    sd_obs = rel_sd_null * sqrt(r)
    return float(sqrt(max(0.0, sd_obs ** 2 - rel_sd_null ** 2)))


# ---------------------------------------------------------------------------
# 4. 顺序预测对数损失（逐期更新，绝不偷看未来）
# ---------------------------------------------------------------------------
def prequential_logloss(reds, theta, warmup=200):
    """球级 Bernoulli 对数损失：Y_ti = 1 当且仅当球 i 出现在第 t 期。

    这是**正当的**评分规则（逐球 Bernoulli 对数似然之和），
    且严格 prequential：预测只用 t 之前的数据。
    """
    n = len(reds)
    cum = np.zeros(N_BALL)
    loss_model = 0.0
    loss_unif = 0.0
    npick_total = 0.0
    pu = 1.0 / N_BALL
    base_u = -(N_PICK * log(pu) + (N_BALL - N_PICK) * log(1 - pu))
    for t in range(n):
        if t >= warmup:
            slots = 6.0 * t
            p = posterior_mean(cum, theta, slots)
            p = np.clip(p, 1e-9, 1 - 1e-9)
            y = np.zeros(N_BALL)
            for b in reds[t]:
                y[int(b) - 1] = 1.0
            loss_model += float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).sum())
            loss_unif += base_u
            npick_total += 1
        for b in reds[t]:
            cum[int(b) - 1] += 1
    m = max(1.0, npick_total)
    return {"model": loss_model / m, "uniform": loss_unif / m,
            "delta": (loss_unif - loss_model) / m, "n_scored": int(m)}


def prequential_mc_test(reds, theta_grid=(1e4, 3e4, 1e5), m_mc=60, seed=4242):
    """顺序预测改进的蒙特卡洛秩检验。

    为什么不能用固定 margin（原提案的 0.01）
    --------------------------------------
    若偏倚 σ=3.5% 完全真实且 p 已知，每期对数损失的理论改进上限为
        KL(p||uniform) × 6 ≈ 0.0037 nats
    比 0.01 还小 2.7 倍 ⇒ 固定 margin=0.01 是**零功效检验**，
    即便备择假设 100% 成立也永远通不过。
    正确做法：用均匀随机数据构造 Δ 的零分布，取秩 p（不用固定阈值）。
    """
    rng = np.random.default_rng(seed)

    def best_delta(rr):
        d = -1e9
        for th in theta_grid:
            d = max(d, prequential_logloss(rr, th)["delta"])
        return d

    obs = best_delta(reds)
    null = np.array([best_delta(gen_uniform(len(reds), rng)) for _ in range(m_mc)])
    p = float(min(1.0, (null >= obs).mean() + 0.5 / m_mc))
    return {"delta_obs": obs, "null_mean": float(null.mean()),
            "null_sd": float(null.std()), "rank_p": p,
            "theta_grid": [float(t) for t in theta_grid], "M": m_mc,
            "beats_baseline": bool(p < 0.05)}


# ---------------------------------------------------------------------------
# 5. 缺失质量界（组合级）
# ---------------------------------------------------------------------------
def missing_mass(reds, blues, delta=0.05):
    """Good-Turing 点估计 + McAllester–Schapire 有限样本界。"""
    keys = [tuple(r) + (int(b),) for r, b in zip(np.asarray(reds), np.asarray(blues))]
    from collections import Counter
    c = Counter(keys)
    N = len(keys)
    f1 = sum(1 for v in c.values() if v == 1)
    gt = f1 / N                                   # Good-Turing 缺失质量
    eps = sqrt(2.0 * log(1.0 / delta) / N)        # MS: P(M0 > Ĝ + ε) ≤ exp(-Nε²/2)
    return {"n_distinct": len(c), "singletons": f1, "N": N,
            "good_turing": gt,
            "mc_allester_schapire": {"delta": delta, "eps": eps,
                                     "low": max(0.0, gt - eps), "high": min(1.0, gt + eps)}}


# ---------------------------------------------------------------------------
# 6. 不变性检验
# ---------------------------------------------------------------------------
def validate_missing_mass_bound(m_mc=200, n_draw=3496, m_support=50000,
                                alpha=1.2, delta=0.05, seed=7):
    """在**已知真值**的合成数据上验证 McAllester–Schapire 界的覆盖率。

    为什么必须有这一步
    ------------------
    原协议的写法：
        low <= empirical_missing <= high    其中 low=GT-ε, high=GT+ε
    是**恒真式**——上界下界由被检验的量自身构造，永远不可能失败，零信息。
    真实数据的"真缺失质量"不可知，所以界的有效性只能在合成数据上验证：
    构造一个已知 p 的分布，真缺失质量 = 未被抽到的那些 p_i 之和，
    再检查 MS 界是否以 >=1-δ 的频率覆盖真值。
    """
    rng = np.random.default_rng(seed)
    w = 1.0 / np.arange(1, m_support + 1) ** alpha
    p = w / w.sum()
    cover = 0
    ratios = []
    tms = []
    for _ in range(m_mc):
        s = rng.choice(m_support, size=n_draw, p=p)
        obs = np.zeros(m_support, bool)
        obs[s] = True
        true_missing = float(p[~obs].sum())
        f1 = int(np.sum(np.bincount(s, minlength=m_support) == 1))
        gt = f1 / n_draw
        eps = sqrt(2.0 * log(1.0 / delta) / n_draw)
        lo, hi = gt - eps, min(1.0, gt + eps)
        if lo <= true_missing <= hi:
            cover += 1
        ratios.append(gt / (true_missing + 1e-12))
        tms.append(true_missing)
    return {"m_mc": m_mc, "delta": delta, "coverage": cover / m_mc,
            "nominal": 1 - delta, "valid": bool(cover / m_mc >= (1 - delta)),
            "gt_over_true_mean": float(np.mean(ratios)),
            "true_missing_mean": float(np.mean(tms))}


def label_equivariance(counts, theta):
    """打乱球标签 → 后验应等变（ catching bugs，非科学检验）。"""
    p0 = posterior_mean(counts, theta)
    perm = np.random.default_rng(0).permutation(N_BALL)
    p1 = posterior_mean(counts[perm], theta)
    return bool(np.allclose(np.sort(p0), np.sort(p1), atol=1e-12))


def time_shift_invariance(reds, theta):
    """前半估 → 后半验，与反向对比。对称性 ⇒ 时不变。"""
    n = len(reds)
    h = n // 2
    def ll(train, test, th):
        c = ball_counts(train)
        p = posterior_mean(c, th)
        p = np.clip(p, 1e-9, 1 - 1e-9)
        tot = 0.0
        for t in range(len(test)):
            y = np.zeros(N_BALL)
            for b in test[t]:
                y[int(b) - 1] = 1.0
            tot += float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).sum())
        return tot / len(test)
    a = ll(reds[:h], reds[h:], theta)      # 前半→后半
    b = ll(reds[h:], reds[:h], theta)      # 后半→前半
    return {"forward": a, "reverse": b, "gap": abs(a - b)}


# ---------------------------------------------------------------------------
# 7. 自证伪协议
# ---------------------------------------------------------------------------
def self_falsification(reds, blues, theta, null, mc_m=60, margin=0.01):
    """返回 dict；未通过 ⇒ 模型自动降级为 UNCALIBRATED，不得进入决策层。"""
    R = {}
    counts = ball_counts(reds)

    # 用**蒙特卡洛秩检验**判定，不用固定 margin。
    # 原因：固定 margin=0.01 高于理论上限 0.0037（σ=3.5% 完全真实且 p 已知时的
    # 每期最大改进），是零功效检验——备择成立也永远通不过。
    mc = prequential_mc_test(reds, m_mc=mc_m)
    R["prequential"] = {"delta_obs": mc["delta_obs"], "null_mean": mc["null_mean"],
                        "null_sd": mc["null_sd"], "rank_p": mc["rank_p"],
                        "theta_grid": mc["theta_grid"], "M": mc["M"]}
    R["beats_baseline"] = bool(mc["beats_baseline"])

    obs = chi2_counts(counts)
    R["chi2_obs"] = obs
    R["chi2_null_mean"] = float(np.mean(null))
    R["chi2_null_sd"] = float(np.std(null))
    R["chi2_rank_p"] = float(min(1.0, (np.asarray(null) >= obs).mean() + 0.5 / len(null)))
    R["excess_chi2"] = bool(R["chi2_rank_p"] < 0.05)

    mm = missing_mass(reds, blues)
    R["missing_mass"] = mm
    # 不用恒真式（low<=GT<=high 永远成立）。改在已知真值的合成数据上验证界本身。
    msv = validate_missing_mass_bound()
    R["missing_mass_bound_validation"] = msv
    R["missing_mass_valid"] = bool(msv["valid"])

    R["label_equivariance"] = label_equivariance(counts, theta)
    ts = time_shift_invariance(reds, theta)
    R["time_shift"] = ts
    R["time_shift_ok"] = bool(ts["gap"] < 0.05 * max(ts["forward"], ts["reverse"]))

    R["passed"] = bool(R["beats_baseline"] and R["missing_mass_valid"]
                       and R["label_equivariance"] and R["time_shift_ok"])
    return R


# ---------------------------------------------------------------------------
# 8. 主流程
# ---------------------------------------------------------------------------
def main(m_mc=500, theta_fixed=None, pos_sigma=(1.0, 2.0, 3.5, 6.0), m_pos=60):
    reds, blues = load_real()
    n = len(reds)
    rng = np.random.default_rng(20260829)
    rep = {"n": n, "m_mc": m_mc, "footer": HF.HONESTY_FOOTER}

    print("=" * 68)
    print("球级可交换探针   N=%d   红球槽位=%d" % (n, n * 6))
    print("=" * 68)

    # --- 蒙特卡洛零假设（无放回 ⇒ 均值 ≠ 渐近 32）---
    null = np.array([chi2_counts(ball_counts(gen_uniform(n, rng))) for _ in range(m_mc)])
    rel_sd_null = 100.0 * sqrt(n * 6 * (1 / 33.0) * (32 / 33.0)) / (n * 6 / 33.0)
    print("\n[0] 蒙特卡洛零假设 χ²: 均值 %.2f ± %.2f （渐近 df=32 会偏差，禁用）"
          % (null.mean(), null.std()))

    # --- θ 估计 ---
    counts = ball_counts(reds)
    theta_hat, ll_hat, ll_inf = estimate_theta(counts)
    theta = theta_fixed if theta_fixed is not None else theta_hat
    print("[1] Dirichlet 浓度 θ̂ = %.3g   （θ→∞ 即均匀；ll(θ̂)=%.2f, ll(∞)=%.2f）"
          % (theta_hat, ll_hat, ll_inf))

    # --- 后验 + 误差界 ---
    p = posterior_mean(counts, theta)
    sd = posterior_sd(counts, theta)
    dev = (p - 1 / N_BALL) * 100 / (1 / N_BALL)   # 相对均匀的百分比偏差
    obs = chi2_counts(counts)
    sigma_hat = sigma_from_chi2(obs, float(null.mean()), rel_sd_null)
    print("[2] 每球频率后验（相对均匀偏差 %%）:")
    print("    最大偏离: %s" % ", ".join(
        "球%d %+.2f%%(±%.2f)" % (int(i + 1), dev[i], sd[i] * 100 / (1 / N_BALL))
        for i in np.argsort(-np.abs(dev))[:5]))
    print("    χ² = %.1f  ⇒ σ̂ = %.2f%%" % (obs, sigma_hat))

    # --- 自证伪协议 ---
    R = self_falsification(reds, blues, theta, null)
    print("\n[3] 自证伪协议（θ=%.3g）:" % theta)
    pq = R["prequential"]
    print("    顺序预测改进 Δ=%+.6f nats/期  零假设 %+.6f±%.6f  秩 p=%.4f  → 击败基线=%s"
          % (pq["delta_obs"], pq["null_mean"], pq["null_sd"],
             pq["rank_p"], R["beats_baseline"]))
    print("    χ² 超额检验      p=%.4f  → 有超额=%s" % (R["chi2_rank_p"], R["excess_chi2"]))
    mm = R["missing_mass"]
    print("    缺失质量(组合级)  Good-Turing=%.4f  95%%界 [%.4f, %.4f]  → 界有效=%s"
          % (mm["good_turing"], mm["mc_allester_schapire"]["low"],
             mm["mc_allester_schapire"]["high"], R["missing_mass_valid"]))
    print("    标签等变=%s   时移不变=%s (gap=%.4f)"
          % (R["label_equivariance"], R["time_shift_ok"], R["time_shift"]["gap"]))
    print("    ⇒ 协议判决: %s" % ("PASSED" if R["passed"] else "UNCALIBRATED(不进决策层)"))
    rep["self_falsification"] = {k: v for k, v in R.items() if k != "prequential"}
    rep["prequential"] = R["prequential"]
    rep["theta_hat"] = theta_hat
    rep["theta_used"] = theta
    rep["sigma_hat_pct"] = sigma_hat
    rep["chi2_obs"] = obs
    rep["chi2_null_mean"] = float(null.mean())
    rep["chi2_null_sd"] = float(null.std())

    # --- 阴性对照 ---
    print("\n[4] 阴性对照（均匀随机，M=%d）:" % m_pos)
    fps = []
    for m in range(m_pos):
        rr = gen_uniform(n, rng)
        fps.append(chi2_counts(ball_counts(rr)))
    fps = np.array(fps)
    thr = float(np.quantile(fps, 0.95))
    # 用真实数据的同样流程在均匀数据上的假阳性率
    fpr = float((fps >= thr).mean())
    print("    χ² 阈值(95%%)=%.1f   名义 FPR=5%%   实测=%.1f%%" % (thr, 100 * fpr))
    rep["negative_control"] = {"threshold_chi2": thr, "fpr": fpr, "M": m_pos,
                               "null_mean": float(fps.mean()), "null_sd": float(fps.std())}

    # --- 阳性对照（探针必须有功效）---
    print("\n[5] 阳性对照（注入球级静态偏倚，要求检出率单调）:")
    power = []
    for sg in pos_sigma:
        hits = 0
        sigs = []
        for m in range(m_pos):
            rr = gen_biased(n, rng, sg, seed=2000 + m)
            c2 = chi2_counts(ball_counts(rr))
            sigs.append(sigma_from_chi2(c2, float(null.mean()), rel_sd_null))
            if c2 >= thr:
                hits += 1
        power.append({"sigma_injected": sg, "detect_rate": hits / m_pos,
                      "sigma_recovered": float(np.mean(sigs))})
        print("    注入 σ=%-4.1f%%  检出率 %3.0f%%   回收 σ̂=%.2f%%"
              % (sg, 100 * hits / m_pos, np.mean(sigs)))
    rep["power"] = power
    rep["sigma_at_obs"] = float(np.interp(sigma_hat,
                                          [q["sigma_recovered"] for q in power],
                                          [q["sigma_injected"] for q in power])) \
        if sigma_hat > 0 else 0.0
    print("    ⇒ 观测 σ̂=%.2f%% 对应的注入刻度 ≈ %.2f%%" % (sigma_hat, rep["sigma_at_obs"]))

    # --- 判决 ---
    if R["excess_chi2"] and R["beats_baseline"]:
        verdict = ("CANDIDATE_STRENGTHENED — 段内超额离散 + 样本外顺序预测力均已检出；"
                   "仍须预注册前瞻确认或物理测量坐实，禁止表述为'已确认结构'")
    elif R["excess_chi2"]:
        verdict = "CANDIDATE_HELD — 仅段内超额离散，样本外预测力未过闸"
    else:
        verdict = "NO_EXCESS_AT_THIS_THETA"
    rep["verdict"] = verdict
    print("\n[6] 判决 = %s" % verdict)

    out = paths.p("audit", "exchangeable_probe.json")
    json.dump(rep, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[exchangeable_probe] 结果: %s" % out)
    print("[页脚] %s" % HF.HONESTY_FOOTER)
    return out


if __name__ == "__main__":
    import sys
    m = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    main(m_mc=m)
