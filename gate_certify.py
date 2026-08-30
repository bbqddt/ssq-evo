"""闸门认证器（Gate Certifier）—— 把"必须跑对照"从纪律变成代码。

诞生背景（2026-08-29/30 三次踩坑）
---------------------------------
1. 生产 OOT 闸门：结构齐全(BH-FDR+多零假设+OOT)，从未对随机数据标零
   ⇒ 纯随机开奖上 **100% 假阳性**
2. prequential 置换检验：看起来在测"次序信息"
   ⇒ 注入 σ=15% 换球时代 **0/10 检出（零功效）**
3. Fisher 合并三条"独立"证据
   ⇒ 实测零假设下 r=+0.997，**合并非法**

共同模式：**闸门看起来严谨，但从未被验证过。**
每次都是事后被对照实验"碰巧"抓到。本模块把验证变成**事前的强制前置条件**：

    任何新统计量/闸门，在结论可被引用之前，必须取得本模块签发的证书。
    无证书 ⇒ 结论不得出口。

认证四件套（缺一不可）
----------------------
1. **阴性对照（下标定）**：纯随机数据上假显著率须回名义水平。
   判据用**二项检验**（不用硬阈值——M 小时观测 2~3 次可能只是噪声）。
2. **阳性对照（上标定）**：注入已知效应，检出率须**单调上升**；
   且效应量清单**必须包含目标效应量**——否则测的是"能检出多大的假偏倚"，
   不是"能检出我们关心的那个"。
3. **双向标定声明**：若提供理论上限（theoretical_ceiling），显式检查
   判决阈值是否低于备择成立时的可达信号——高于它即零功效。
4. **联合相关性声明**：若与其他已认证闸门联合使用，须报告零假设下的
   相关矩阵；r>0.5 的组合强制改用联合 min-p 蒙特卡洛。

用法
----
    import gate_certify as GC
    cert = GC.certify(
        name="边际χ²",
        stat_fn=lambda data: ...,          # 返回统计量，越大越"显著"
        null_gen=lambda rng: ...,         # 零假设数据生成器
        injector_fn=lambda rng, sg: ...,  # 注入已知效应 sg
        effect_sizes=[2.0, 3.5, 6.0],     # 必须含目标效应量
        target_effect=3.5,
    )
    GC.must_pass(cert)   # 未通过则抛异常，阻断结论出口
"""

import json
import os
from math import comb

import numpy as np

import honesty_footer as HF
import paths


# ---------------------------------------------------------------------------
# 二项检验：观察到的假阳性数是否显著超过名义水平（不用硬阈值）
# ---------------------------------------------------------------------------
def binom_sf(k, n, p):
    """P(X >= k), X ~ Binomial(n, p)。"""
    if n <= 0:
        return 1.0
    k = max(0, int(k))
    return float(sum(comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k, n + 1)))


# ---------------------------------------------------------------------------
# 认证主函数
# ---------------------------------------------------------------------------
def certify(name, stat_fn, null_gen, injector_fn, effect_sizes,
            target_effect=None, m_null=40, m_pos=40, m_mc=400,
            alpha=0.05, theoretical_ceiling=None, threshold=None,
            seed=20260830, verbose=True):
    """对一个闸门跑完整认证，返回证书 dict。

    stat_fn(data) -> float      越大越"显著"
    null_gen(rng) -> data       零假设数据
    injector_fn(rng, sigma) -> data   注入强度 sigma 的已知效应
    target_effect               目标效应量（必须在 effect_sizes 里）
    theoretical_ceiling         （可选）备择完全真实时统计量的理论上限
    threshold                   （可选）若闸门有显式判决阈值，则做上标定检查
    """
    rng = np.random.default_rng(seed)
    checks = {}

    # ---- 1. 零分布与阈值 ----
    null_stats = np.array([stat_fn(null_gen(rng)) for _ in range(m_mc)])
    thr = float(np.quantile(null_stats, 1 - alpha)) if threshold is None else float(threshold)

    # ---- 2. 阴性对照（下标定）----
    fp_hits = sum(1 for _ in range(m_null) if stat_fn(null_gen(rng)) >= thr)
    fpr = fp_hits / m_null
    p_fpr = binom_sf(fp_hits, m_null, alpha)
    ok_fpr = bool(p_fpr > 0.05)
    checks["negative_control"] = {
        "fpr": fpr, "k": fp_hits, "n": m_null, "binom_p": round(p_fpr, 4),
        "pass": ok_fpr,
        "note": "假显著率与名义水平无显著差异（二项检验）" if ok_fpr
                else "FPR 显著超过名义水平——闸门系统性假阳性，禁止使用",
    }

    # ---- 3. 阳性对照（上标定 + 单调性 + 目标效应量覆盖）----
    if target_effect is not None and target_effect not in effect_sizes:
        effect_sizes = sorted(set(list(effect_sizes) + [target_effect]))
    power = []
    for sg in effect_sizes:
        hits = sum(1 for _ in range(m_pos) if stat_fn(injector_fn(rng, sg)) >= thr)
        power.append({"sigma": sg, "detect_rate": hits / m_pos})
    rates = [q["detect_rate"] for q in power]
    ok_mono = bool(all(rates[i] <= rates[i + 1] + 1e-9 for i in range(len(rates) - 1)))
    ok_target = None
    if target_effect is not None:
        tgt = [q for q in power if q["sigma"] == target_effect][0]
        # 目标效应量下至少要有可辩护的功效（>=30%），否则闸门对该效应近似失明
        ok_target = bool(tgt["detect_rate"] >= 0.30)
    checks["positive_control"] = {
        "power": power, "monotone": ok_mono, "target": target_effect,
        "target_detect_rate": (tgt["detect_rate"] if target_effect is not None else None),
        "pass": bool(ok_mono and (ok_target is not False)),
        "note": ("检出率单调且目标效应量下功效 >= 30%%" if (ok_mono and ok_target is not False)
                 else "检出率非单调或目标效应量下功效不足(<30%%)——对关心的效应近似失明"),
    }

    # ---- 4. 双向标定（若给了理论上限与显式阈值）----
    if theoretical_ceiling is not None and threshold is not None:
        ok_ceiling = bool(threshold < theoretical_ceiling)
        checks["ceiling"] = {
            "threshold": threshold, "theoretical_ceiling": theoretical_ceiling,
            "pass": ok_ceiling,
            "note": ("阈值低于备择成立时的理论上限" if ok_ceiling
                     else "阈值高于理论上限——零功效：备择成立也永远通不过"),
        }

    # ---- 5. 判定 ----
    all_pass = all(c["pass"] for c in checks.values())
    cert = {
        "gate": name, "alpha": alpha, "m_mc": m_mc, "m_null": m_null, "m_pos": m_pos,
        "threshold": thr, "null_mean": float(null_stats.mean()),
        "null_sd": float(null_stats.std()),
        "checks": checks,
        "certified": bool(all_pass),
        "verdict": ("CERTIFIED" if all_pass else "REJECTED"),
        "footer": HF.HONESTY_FOOTER,
        "joint_warning": ("与其他闸门联合引用前，必须报告零假设下的相关矩阵；"
                          "r>0.5 的组合强制改用联合 min-p 蒙特卡洛，禁止 Fisher。"),
    }
    if verbose:
        _print_cert(cert)
    return cert


def _print_cert(c):
    print("=" * 64)
    print("闸门认证: %s  ⇒  %s" % (c["gate"], c["verdict"]))
    print("=" * 64)
    print("  零分布: %.3f ± %.3f   判决阈值(α=%.2f): %.3f"
          % (c["null_mean"], c["null_sd"], c["alpha"], c["threshold"]))
    nc = c["checks"]["negative_control"]
    print("  [阴性] FPR = %.1f%% (%d/%d)  二项检验 p = %.3f  %s"
          % (100 * nc["fpr"], nc["k"], nc["n"], nc["binom_p"],
             "✓" if nc["pass"] else "✗"))
    pc = c["checks"]["positive_control"]
    line = "  [阳性] "
    for q in pc["power"]:
        line += "σ=%.1f%%→%.0f%%  " % (q["sigma"], 100 * q["detect_rate"])
    print(line)
    print("         单调=%s  目标σ=%.1f%% 功效=%.0f%%  %s"
          % (pc["monotone"], pc["target"] or -1,
             100 * (pc["target_detect_rate"] or 0), "✓" if pc["pass"] else "✗"))
    if "ceiling" in c["checks"]:
        cc = c["checks"]["ceiling"]
        print("  [上限] 阈值 %.4g vs 理论上限 %.4g  %s"
              % (cc["threshold"], cc["theoretical_ceiling"], "✓" if cc["pass"] else "✗"))
    print("  %s" % c["joint_warning"])


def must_pass(cert):
    """未通过 ⇒ 抛异常，阻断结论出口。"""
    if not cert.get("certified"):
        raise AssertionError(
            "[gate_certify] 闸门 '%s' 未通过认证(%s)，其结论不得出口。"
            % (cert.get("gate"), cert.get("verdict")))
    return cert


def save(cert, fname=None):
    fname = fname or ("gate_cert_%s.json" % cert["gate"].replace("/", "_"))
    out = paths.p("audit", fname)
    json.dump(cert, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    return out


# ---------------------------------------------------------------------------
# 演示：认证当前在用的两个真实闸门
# ---------------------------------------------------------------------------
def demo():
    import exchangeability_order_probe as EO
    from exchangeable_probe import N_BALL, N_PICK, ball_counts, gen_uniform

    n = 3496

    def chi2_of(r):
        c = ball_counts(r)
        e = c.sum() / N_BALL
        return float(((c - e) ** 2 / e).sum())

    def inject_static(rng, sg):
        return EO.gen_eras(n, rng, sg, n_eras=1, seed=int(rng.integers(1e6)))

    def inject_era(rng, sg):
        return EO.gen_eras(n, rng, sg, n_eras=2, seed=int(rng.integers(1e6)))

    certs = []
    print("\n【认证 1】边际 χ²（静态偏倚闸门，目标 σ=3.5%）")
    c1 = certify("边际χ²", chi2_of,
                 lambda rng: gen_uniform(n, rng),
                 inject_static,
                 effect_sizes=[2.0, 3.5, 6.0], target_effect=3.5,
                 m_mc=400, m_null=40, m_pos=40)
    certs.append(("marginal_chi2", c1))

    print("\n【认证 2】分段同质性 χ²（状态结构闸门，目标 σ=8%）")
    c2 = certify("同质性χ²", lambda r: EO.homogeneity_test(r, n_seg=4, m_mc=1)["chi2_obs"]
                  if False else _homog_stat(r),
                  lambda rng: gen_uniform(n, rng),
                  inject_era,
                  effect_sizes=[3.5, 8.0, 15.0], target_effect=8.0,
                  m_mc=400, m_null=40, m_pos=40)
    certs.append(("homogeneity_chi2", c2))

    outs = [save(c, "%s.json" % k) for k, c in certs]
    print("\n证书已写: %s" % "; ".join(outs))
    return certs


def _homog_stat(r):
    """分段同质性 χ²（轻量版，供认证器调用）。"""
    import numpy as np
    from exchangeable_probe import N_BALL
    segs = np.array_split(np.arange(len(r)), 4)
    tab = np.array([np.bincount(r[s].ravel(), minlength=N_BALL + 1)[1:N_BALL + 1]
                    .astype(float) for s in segs])
    row = tab.sum(axis=1, keepdims=True)
    col = tab.sum(axis=0, keepdims=True)
    E = row @ col / tab.sum()
    return float(((tab - E) ** 2 / np.maximum(E, 1e-9)).sum())


if __name__ == "__main__":
    demo()
