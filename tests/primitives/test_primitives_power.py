# -*- coding: utf-8 -*-
"""
原语消融 / 功效验证 harness —— ssq_evo 的"准入闸门"基础设施。

设计原则（第一性原理 + 诚实 + 小心）：
  任何检验原语进入 TESTS 注册表前，必须在此证明：
    (1) POWER : 对注入的已知结构，shuffle 零假设下 surrogate p < 0.05 能检出
                （证明它不是"瞎的"——否则真实数据的 null 结论可能是漏检假阴性）
    (2) SPEC  : 对纯随机 null 序列，不应稳定误报（特异性）
    (3) REAL  : 在真实双色球数据上应落在随机区间（与主线结论一致，sanity）

本 harness **直接遍历 engine_core.TESTS 注册表**（注册表增删原语会自动同步），
对每个原语用与其 direction 匹配的注入结构做阳性对照，验证逻辑与生产中 evaluate() 一致。

用法:
    python tests/primitives/test_primitives_power.py
退出码: 任一已注册原语 POWER 失败则非零（可作 CI 门）。
"""
import sys, os, math, json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import engine_core as E
import data as D

N = 1200
RNG = np.random.default_rng(20260815)

# ---------------------------------------------------------------------------
# 结构化注入信号生成器
# ---------------------------------------------------------------------------
def sig_random(n=N, rng=RNG):
    return rng.standard_normal(n)

def sig_periodic(n=N, rng=RNG):
    """周期结构：同时抬高谱/自相关类统计量、压低复杂度/熵类统计量。"""
    t = np.arange(n)
    x = np.sin(2 * np.pi * t * 7.0 / n)
    x += 0.05 * rng.standard_normal(n)
    return x

def sig_autocorr(n=N, rng=RNG):
    x = np.zeros(n)
    x[0] = rng.standard_normal()
    for i in range(1, n):
        x[i] = 0.88 * x[i - 1] + 0.12 * rng.standard_normal()
    return x

def sig_block(n=N, rng=RNG):
    """强结构分块序列：确定性重复模式，排列/样本/多尺度熵极低（远低于随机）。"""
    pat = np.arange(8, dtype=float)
    x = np.tile(pat, n // 8 + 1)[:n]
    x += 0.01 * rng.standard_normal(n)
    return x

def bivar_inject(n=N, rng=RNG):
    """双变量因果注入：target = 0.7*source_lag + 噪声 (X->Y 信息流)。"""
    src = rng.standard_normal(n)
    tgt = np.zeros(n)
    tgt[1:] = 0.7 * src[:-1] + 0.3 * rng.standard_normal(n - 1)
    return src, tgt

# 默认注入（按 direction 选）：high 原语用周期(抬高谱/自相关)，
# low 原语(复杂度/熵类)用强结构分块序列(确定性压低熵，比正弦更 unambiguous)
DEFAULT_INJ = {"high": sig_periodic, "low": sig_block}
K_SUR = {"light": 80, "heavy": 25}

def sur_p_uni(x, func, k_sur, direction, rng, **kw):
    real = func(x, **kw)
    if not math.isfinite(real):
        return real, 1.0
    svals = []
    for _ in range(k_sur):
        sx = rng.permutation(x)
        sv = func(sx, **kw)
        if math.isfinite(sv):
            svals.append(sv)
    svals = np.array(svals)
    if svals.size == 0:
        return real, 1.0
    if direction == "high":
        p = (1.0 + np.sum(svals >= real)) / (1.0 + svals.size)
    else:
        p = (1.0 + np.sum(svals <= real)) / (1.0 + svals.size)
    return real, float(p)

def sur_p_bi(x, y, func, k_sur, rng, **kw):
    real = func(x, y, **kw)
    if not math.isfinite(real):
        return real, 1.0
    svals = []
    for _ in range(k_sur):
        idx = rng.permutation(len(x))
        sv = func(x[idx], y[idx], **kw)
        if math.isfinite(sv):
            svals.append(sv)
    svals = np.array(svals)
    if svals.size == 0:
        return real, 1.0
    p = (1.0 + np.sum(svals >= real)) / (1.0 + svals.size)
    return real, float(p)

def main():
    rng = RNG
    rows, fails = [], []

    print("=" * 78)
    print("ssq_evo 原语功效验证 (阳性对照) — 遍历 TESTS 注册表, 复用生产 shuffle 零假设")
    print("=" * 78)

    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    reds, blues, _ = D.to_arrays(m)

    for name in sorted(E.TESTS):
        func, direction, tier = E.TESTS[name]
        is_bivar = name in E.BIVARIATE_TESTS
        k_sur = K_SUR.get(tier, 25)

        if is_bivar:
            src, tgt = bivar_inject(rng=rng)
            _, p_str = sur_p_bi(src, tgt, func, k_sur, rng)
            src0, tgt0 = rng.standard_normal(N), rng.standard_normal(N)
            _, p_null = sur_p_bi(src0, tgt0, func, k_sur, rng)
            p_real = float("nan")
        else:
            inj = DEFAULT_INJ.get(direction, sig_periodic)
            x_str = inj(rng=rng)
            _, p_str = sur_p_uni(x_str, func, k_sur, direction, rng)
            x_null = sig_random(rng=rng)
            _, p_null = sur_p_uni(x_null, func, k_sur, direction, rng)
            x_real = E._build_x("red_sum", reds, blues, None)
            _, p_real = sur_p_uni(x_real, func, k_sur, direction, rng)

        power_ok = p_str < 0.05
        if not power_ok:
            fails.append(name)
        typ = "bi" if is_bivar else "uni"
        prs = "—" if math.isnan(p_real) else f"{p_real:.3f}"
        rows.append((name, typ, k_sur, p_null, p_str, prs, power_ok))
        print(f"[{'PASS' if power_ok else 'FAIL'}] {name:16} {typ:3} k={k_sur:2} "
              f"null_p={p_null:6.3f} struct_p={p_str:7.4f} real_p={prs}")

    md = ["# 原语功效验证报告", "",
          "| 原语 | 类型 | k_sur | null_p | struct_p | real_p | 功效 |",
          "|------|------|-------|--------|----------|--------|------|"]
    for name, typ, k, pn, ps, pr, ok in rows:
        md.append(f"| {name} | {typ} | {k} | {pn:.3f} | {ps:.4f} | {pr} | {'✅' if ok else '❌'} |")
    md += [""]
    if fails:
        md.append(f"**结论**: {len(rows)-len(fails)}/{len(rows)} 原语通过功效验证。")
        md.append(f"**待 prune / 重修**: {', '.join(fails)}（注入已知结构仍无法检出，说明该原语在当前零假设下失明）")
    else:
        md.append(f"**结论**: {len(rows)}/{len(rows)} 已注册原语全部通过功效验证（含方向正确性）。")

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("\n" + "\n".join(md[2:]))
    print(f"\n报告已写入 tests/primitives/REPORT.md")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
