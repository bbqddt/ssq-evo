"""静态边际偏倚探针（宿主专用审计，不进容器 / 不改生产判定）。

为什么需要这个模块
------------------
整个闸门家族（13 个检验，**零假设 100% 是 shuffle / AAFT / 期序打乱**）
全部是**重排类零假设**——它们**精确保留边际分布**。
因此无论多强的**静态边际偏倚**（某个球系统性偏多/偏少），
shuffle 替代序列都带着一模一样的偏倚 ⇒ 差异恒为 0 ⇒ **所有闸门一律报 NULL**。

而物理上最可能存在的偏倚恰恰是静态的：
球重差异、磨损、机器偏好、球体批次——这些**不依赖期序**。
也就是说：在本探针之前，系统对一个大区**完全失明**，
此前任何"穷尽了搜索空间 / 已证 null"的陈述都不成立。

本探针的回答方式：不跟替代比，直接跟**理论均匀分布**比，
并用蒙特卡洛秩 p 校准（遵守铁律：不用正态近似 p）。

验证协议（与本项目其他闸门同严）
------------------------------
1. 幅度检验：卡方 vs 均匀分布（蒙特卡洛秩 p）
2. 持续性检验：分段偏差向量的段间相关（判"是否跨时段稳定"）
3. K 鲁棒性：K=4..24 扫描，看效应是否依赖分割数（研究者自由度）
4. 连续 vs 随机分段：判"真·时不变偏倚"还是"数据分块假象"
5. 发现/确认分离：前 70% 估偏差向量 → 预测后 30%（真样本外）
6. 阳性对照：注入已知偏倚，验证探针能检出（否则是瞎探针）
7. 功率分析：最小可检出的持续偏倚幅度
8. 预注册：把偏差向量落盘，供未来开奖前瞻打分
"""

import itertools
import json
import os
from datetime import datetime

import numpy as np

import data as D
import paths

N_BALL = 33
N_PICK = 6
N_BLUE = 16


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def load_real():
    master = D.load_master(paths.master_csv())
    reds, blues, issues = D.to_arrays(master)
    return np.asarray(reds), np.asarray(blues)


def gen_uniform(n, rng):
    """均匀随机开奖：每期 6 个互异红球(1..33) + 1 蓝球(1..16)。"""
    r = np.sort(rng.random((n, N_BALL)).argsort(axis=1)[:, :N_PICK] + 1, axis=1)
    b = rng.integers(1, N_BLUE + 1, size=n)
    return r, b


def gen_biased(n, rng, sigma_pct, seed=0):
    """注入**静态**边际偏倚：每个球一个固定的对数权重 ~ N(0, sigma_pct)。

    这是本探针唯一能检出的那类结构——注意它**不含任何期序信息**，
    所以重排类零假设（shuffle/AAFT）对它 100% 失明。
    """
    w = np.exp(np.random.default_rng(seed).normal(0, sigma_pct / 100.0, N_BALL))
    p = w / w.sum()
    out = np.zeros((n, N_PICK), dtype=np.int64)
    for i in range(n):
        # 无放回按权重抽样（简单拒绝/轮询实现，n 不大时够用）
        cs = np.cumsum(p)
        u = rng.random(N_PICK)
        picked = []
        for x in u:
            j = int(np.searchsorted(cs, x))
            while j in picked or j >= N_BALL:
                j = (j + 1) % N_BALL
            picked.append(j)
        out[i] = np.sort(np.array(picked) + 1)
    return out


def dev_red(r):
    """红球边际频率相对均匀的百分比偏差向量（长度 33），约束：求和=0。"""
    c = np.bincount(np.asarray(r).ravel(), minlength=N_BALL + 1)[1:N_BALL + 1]
    e = len(r) * N_PICK / N_BALL
    return (c - e) / e * 100.0


def dev_blue(b):
    c = np.bincount(np.asarray(b), minlength=N_BLUE + 1)[1:N_BLUE + 1]
    e = len(b) / N_BLUE
    return (c - e) / e * 100.0


def chi2_red(r):
    d = dev_red(r)
    e = len(r) * N_PICK / N_BALL
    return float(((d / 100.0 * e) ** 2 / e).sum())


def rank_p(obs, null):
    null = np.asarray(null, float)
    if null.size == 0:
        return 1.0
    return float(min(1.0, max(0.0, (null >= obs).mean() + 0.5 / null.size)))


def seg_stat(r, K, idx=None):
    """分段偏差向量的段间平均相关（持续性统计量）。"""
    n = len(r)
    if idx is None:
        segs = np.array_split(np.arange(n), K)
    else:
        segs = np.array_split(idx, K)
    Dm = np.array([dev_red(r[s]) for s in segs])
    pairs = list(itertools.combinations(range(K), 2))
    return float(np.mean([np.corrcoef(Dm[i], Dm[j])[0, 1] for i, j in pairs]))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(m_mc=400, k_scan=(4, 6, 8, 12, 16, 24), pos_sigma=(2.0, 3.6, 6.0, 10.0)):
    reds, blues = load_real()
    n = len(reds)
    rng = np.random.default_rng(20260829)
    rep = {"n": n, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "m_mc": m_mc}

    print("=" * 68)
    print("静态边际偏倚探针   N=%d   M(蒙特卡洛)=%d" % (n, m_mc))
    print("=" * 68)

    # ---------- 1. 幅度检验 ----------
    obs = chi2_red(reds)
    null = [chi2_red(gen_uniform(n, rng)[0]) for _ in range(m_mc)]
    p1 = rank_p(obs, null)
    print("\n[1] 幅度: 红球边际 chi2 = %.1f   零假设 %.1f±%.1f   秩 p = %.4f"
          % (obs, np.mean(null), np.std(null), p1))
    obs_b = float(((dev_blue(blues) / 100.0 * (n / N_BLUE)) ** 2 / (n / N_BLUE)).sum())
    null_b = [float(((dev_blue(gen_uniform(n, rng)[1]) / 100.0 * (n / N_BLUE)) ** 2
                     / (n / N_BLUE)).sum()) for _ in range(m_mc)]
    p1b = rank_p(obs_b, null_b)
    print("    蓝球边际 chi2 = %.1f   秩 p = %.4f" % (obs_b, p1b))
    rep["chi2"] = {"red": obs, "p_red": p1, "blue": obs_b, "p_blue": p1b,
                   "null_mean": float(np.mean(null)), "null_sd": float(np.std(null))}

    # ---------- 2/3. 持续性 + K 鲁棒性 ----------
    print("\n[2/3] 持续性(段间偏差向量平均相关) + K 鲁棒性:")
    print("      K     真实r      零假设均值±sd      秩p")
    krows, kps = [], []
    for K in k_scan:
        o = seg_stat(reds, K)
        nl = np.array([seg_stat(gen_uniform(n, rng)[0], K) for _ in range(m_mc)])
        p = rank_p(o, nl)
        krows.append((K, o, float(nl.mean()), float(nl.std()), p))
        kps.append(p)
        print("      %-5d %+.4f    %+.4f±%.4f     %.4f %s"
              % (K, o, nl.mean(), nl.std(), p, "显著" if p < 0.05 else ""))
    rep["persistence"] = [{"K": k, "r": r_, "null_mean": nm, "null_sd": ns, "p": p}
                          for k, r_, nm, ns, p in krows]
    kps = np.array(kps)
    n_sig = int((kps < 0.05).sum())
    try:
        from scipy.stats import chi2 as _c2
        fisher = float(_c2.sf(-2 * np.sum(np.log(np.maximum(kps, 1e-9))), 2 * len(kps)))
    except Exception:
        fisher = float("nan")
    print("      ⇒ %d/%d 档显著；Fisher 合并 p = %.3g" % (n_sig, len(kps), fisher))
    rep["k_robust"] = {"n_sig": n_sig, "n_total": len(kps), "fisher_p": fisher}

    # ---------- 4. 连续 vs 随机分段 ----------
    K = 8
    oc = seg_stat(reds, K)
    perm = rng.permutation(n)
    ornd = seg_stat(reds, K, idx=perm)
    nl4 = np.array([seg_stat(gen_uniform(n, rng)[0], K, idx=rng.permutation(n))
                    for _ in range(m_mc)])
    pc, prnd = rank_p(oc, nl4), rank_p(ornd, nl4)
    print("\n[4] 连续分段 r=%+.4f (p=%.4f)  vs  随机分段 r=%+.4f (p=%.4f)"
          % (oc, pc, ornd, prnd))
    print("    ⇒ 两者相近 ⇒ 时不变偏倚；连续>>随机 ⇒ 数据分块假象")
    rep["contiguous_vs_random"] = {"contiguous": oc, "random": ornd,
                                   "p_contiguous": pc, "p_random": prnd}

    # ---------- 5. 发现/确认分离（真样本外） ----------
    cut = int(n * 0.7)
    v, u = dev_red(reds[:cut]), dev_red(reds[cut:])
    r_oot = float(np.corrcoef(v, u)[0, 1])
    nl5 = np.array([float(np.corrcoef(dev_red(gen_uniform(cut, rng)[0]),
                                      dev_red(gen_uniform(n - cut, rng)[0]))[0, 1])
                    for _ in range(m_mc)])
    p5 = rank_p(r_oot, nl5)
    hit = float(np.mean(np.sign(v) == np.sign(u)))
    nh = np.array([np.mean(np.sign(dev_red(gen_uniform(cut, rng)[0])) ==
                           np.sign(dev_red(gen_uniform(n - cut, rng)[0]))) for _ in range(m_mc)])
    p5h = rank_p(hit, nh)
    print("\n[5] 发现/确认分离: 前70%%(n=%d) 估偏差 → 预测后30%%(n=%d)" % (cut, n - cut))
    print("    r = %+.4f  零假设 %+.4f±%.4f  秩 p = %.4f %s"
          % (r_oot, nl5.mean(), nl5.std(), p5, "显著" if p5 < 0.05 else "未达显著(功效不足)"))
    print("    方向命中率 = %.1f%%  零假设 %.1f%%±%.1f%%  秩 p = %.4f"
          % (hit * 100, nh.mean() * 100, nh.std() * 100, p5h))
    rep["oot_split"] = {"cut": cut, "r": r_oot, "p": p5, "null_sd": float(nl5.std()),
                        "dir_hit": hit, "p_dir": p5h}

    # ---------- 6. 阳性对照 + 7. 功率 ----------
    print("\n[6/7] 阳性对照 + 功率: 注入静态偏倚 σ (每球频率的 1sd 百分比)")
    print("      σ(%%    检出率(发现/确认检验 p<0.05)   平均 r")
    power = []
    m_pos = 60
    for sg in pos_sigma:
        rs, ps = [], []
        for m in range(m_pos):
            rr = gen_biased(n, rng, sg, seed=1000 + m)
            vv, uu = dev_red(rr[:cut]), dev_red(rr[cut:])
            rv = float(np.corrcoef(vv, uu)[0, 1])
            rs.append(rv)
            ps.append(rank_p(rv, nl5))
        ps = np.array(ps)
        power.append({"sigma_pct": sg, "detect_rate": float((ps < 0.05).mean()),
                      "mean_r": float(np.mean(rs))})
        print("      %-6.1f %.0f%%                        %+.4f"
              % (sg, 100 * (ps < 0.05).mean(), np.mean(rs)))
    rep["power"] = power
    # 观测 r 对应的隐含 σ
    try:
        sigs = [p["sigma_pct"] for p in power]
        rrs = [p["mean_r"] for p in power]
        rep["implied_sigma_pct"] = float(np.interp(r_oot, rrs, sigs))
        print("\n    ⇒ 实测 r=%+.4f 对应的隐含持续偏倚 σ ≈ %.2f%%"
              % (r_oot, rep["implied_sigma_pct"]))
    except Exception:
        rep["implied_sigma_pct"] = None

    # ---------- 8. 预注册 ----------
    full = dev_red(reds)
    reg = {
        "registered_at": rep["ts"],
        "n_basis": n,
        "prediction": ("以下球在此后开奖中，边际频率将继续沿同一方向偏离均匀期望 "
                       "(球号 1-based，值为相对均匀的百分比偏差)"),
        "dev_pct": {str(i + 1): round(float(full[i]), 3) for i in range(N_BALL)},
        "scoring_rule": ("未来每累积 K 期(建议 K>=300)，计算该窗口的偏差向量，"
                         "与本预注册向量求相关 r 与方向命中率；"
                         "零分布用同期数的均匀随机模拟。判显著阈值 p<0.05。"),
        "status": "CANDIDATE — 未确认，禁止作为结论使用",
    }
    reg_path = paths.p("audit", "marginal_bias_preregistered.json")
    json.dump(reg, open(reg_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[8] 预注册已写: %s" % reg_path)
    rep["preregistered_path"] = reg_path

    out = paths.p("audit", "marginal_bias_probe.json")
    json.dump(rep, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[marginal_bias_probe] 结果: %s" % out)
    return out


if __name__ == "__main__":
    m = int(os.environ.get("MBP_MC", "400"))
    main(m_mc=m)
