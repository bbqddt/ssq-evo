# -*- coding: utf-8 -*-
"""
formula_research_block.py —— 块状宇宙(eternalism)命题的顺序不变性检验
================================================================================
爱因斯坦式命题的操作化：开奖序列是单一静态数学对象，时间轴只是坐标参数，
"过去/现在/未来"无本体论优先性。可证伪预言：

    **若时间为幻觉，则序列的"时间顺序"不承载任何特殊结构。**
    → 对时序敏感的全部统计量，在"期次行随机打乱"后应保持不变；
    → 若真实序列是打乱分布的离群点，则时序本身承载因果结构，反而证伪纯块状宇宙观。

本检验与既有"预测性 OOT/surrogate"检验是**正交的两根杠杆**：
  - surrogate(AAFT/shuffle) 检验的是"某期能否由邻近期推算"(因果/时序预测力) → 此前全 NULL；
  - 本检验(meta-order) 检验的是"时间顺序本身是否特殊"(顺序不变性) → 全新维度。

严格复用同一诚实底线：不把任何中间进度翻译成"域定性结论"；
阳性对照 = 在真实序列里注入一个已知时序周期信号，应被本检验捕获(证闸门有功效)。

实现要点：
  - null 构造：对 (reds,blues) 联合**行置换**(permute rows)，保留每期 6+1 球完整组合、
    保留所有边际分布，只打乱"哪一期中奖"——这正是"时间是否特殊"的精确 null。
  - 统计量电池(全部对顺序敏感)：
      * autocorr： lags 1..20 的最大 |ACF|（时序相关）
      * perm_entropy： Bandt-Pompe 排列熵 m=3（序列复杂度的顺序敏感度量）
      * t_dfa_alpha： 去趋势波动分析 Hurst 指数（长程时序相关）
      * t_corr_dim_slope： 相关维斜率（时序吸引子维数）
  - 对每个统计量算 p_order = P(|S_shuf - μ_shuf| >= |S_real - μ_shuf|) (two-sided)，
    及 z = (S_real - μ_shuf)/σ_shuf。
"""
import numpy as np
import data as D
import engine_core as E


# ---------------------------------------------------------------------------
# 顺序敏感统计量
# ---------------------------------------------------------------------------
def autocorr_maxabs(x, lags=(1, 2, 3, 5, 8, 13, 20)):
    x = np.asarray(x, float)
    x = x - x.mean()
    s = x.std() + 1e-12
    best = 0.0
    for L in lags:
        if len(x) <= L:
            return 0.0
        c = np.mean(x[:-L] * x[L:]) / (s * s)
        best = max(best, abs(c))
    return float(best)


def perm_entropy(x, m=3, tau=1):
    """Bandt-Pombe 排列熵（顺序敏感）。ties 用稳定排序保证确定性。"""
    x = np.asarray(x, float)
    n = len(x)
    if n < m * tau + 1:
        return np.nan
    counts = {}
    for i in range(n - m * tau):
        window = x[i:i + m * tau:tau]
        order = tuple(np.argsort(window, kind="stable"))
        counts[order] = counts.get(order, 0) + 1
    probs = np.array(list(counts.values()), dtype=float)
    probs /= probs.sum()
    return float(-np.sum(probs * np.log2(probs)))


def _series_battery(reds, blues):
    """从 (reds,blues) 构造一组对顺序敏感的标量序列。"""
    sig = E._base_signals(reds, blues)
    out = {}
    out["red_sum"] = sig.get("red_sum")
    out["red_sum_z"] = (sig.get("red_sum") - np.nanmean(sig.get("red_sum"))) / (np.nanstd(sig.get("red_sum")) + 1e-12)
    out["blue"] = blues.astype(float)
    # digit_root9 / qr_count 等来自 formula_research 基元（若已注册进 BASE_SIGNALS）
    for nm in ("red_digit_root9", "red_qr_count", "red_fib_count", "red_gap_var"):
        if nm in sig:
            out[nm] = sig[nm]
    # 清洗 NaN
    for k in list(out):
        if out[k] is None or np.all(np.isnan(out[k])):
            del out[k]
    return out


def _stat_on_series(x):
    """对单条标量序列计算全部顺序敏感统计量。"""
    return {
        "autocorr_max": autocorr_maxabs(x),
        "perm_entropy": perm_entropy(x, m=3, tau=1),
        "dfa_alpha": E.t_dfa_alpha(x),
        "corr_dim_slope": E.t_corr_dim_slope(x, m_max=7, tau=1),
    }


def _permute_rows(reds, blues, rng):
    p = rng.permutation(reds.shape[0])
    return reds[p], blues[p]


def meta_order_test(reds, blues, k_cheap=500, k_heavy=50, seed=20260827):
    """主检验：对每条序列、每个统计量，比较真实 vs 行置换 null。"""
    rng = np.random.default_rng(seed)
    reals = _series_battery(reds, blues)
    results = {}
    for sname, x in reals.items():
        stats_real = _stat_on_series(x)
        # 行置换 null 分布（每条序列一次性生成 k 个打乱矩阵并重算全部统计量）
        null = {st: [] for st in stats_real}
        k = k_heavy  # heavy 统计主导耗时，统一用 k_heavy（cheap 统计免费获得同样 null）
        for _ in range(k):
            pr, pb = _permute_rows(reds, blues, rng)
            sx = _series_battery(pr, pb)[sname]
            for st, v in _stat_on_series(sx).items():
                if np.isfinite(v):
                    null[st].append(v)
        rec = {}
        for st, vreal in stats_real.items():
            arr = np.array(null[st], dtype=float)
            if arr.size < 10 or not np.isfinite(vreal):
                rec[st] = dict(real=vreal, p_order=np.nan, z=np.nan, n_null=arr.size)
                continue
            mu, sd = arr.mean(), arr.std() + 1e-12
            # two-sided p：|S_shuf - μ| >= |S_real - μ|
            p_order = np.mean(np.abs(arr - mu) >= abs(vreal - mu))
            z = (vreal - mu) / sd
            rec[st] = dict(real=float(vreal), p_order=float(p_order),
                           z=float(z), mu=float(mu), sd=float(sd), n_null=int(arr.size))
        results[sname] = rec
    return results


# ---------------------------------------------------------------------------
# 阳性对照：注入已知时序周期信号，应被顺序敏感统计捕获
# ---------------------------------------------------------------------------
def positive_control(reds, blues, period=37, amp=8.0, seed=20260827):
    rng = np.random.default_rng(seed)
    n = reds.shape[0]
    # 在 red_sum 上叠加确定性周期(正弦)，真实"时间顺序"中可见
    rs = E._base_signals(reds, blues)["red_sum"].copy()
    rs = rs - np.nanmean(rs)
    injected = rs + amp * np.sin(2 * np.pi * np.arange(n) / period)
    # 真实序列统计
    s_real = _stat_on_series(injected)
    # 行置换 null
    null = {st: [] for st in s_real}
    for _ in range(50):
        p = rng.permutation(n)
        sx = injected[p]
        for st, v in _stat_on_series(sx).items():
            if np.isfinite(v):
                null[st].append(v)
    out = {}
    for st, vreal in s_real.items():
        arr = np.array(null[st], dtype=float)
        if arr.size < 10:
            out[st] = dict(real=vreal, p_order=np.nan)
            continue
        mu, sd = arr.mean(), arr.std() + 1e-12
        p_order = np.mean(np.abs(arr - mu) >= abs(vreal - mu))
        out[st] = dict(real=float(vreal), p_order=float(p_order),
                       z=float((vreal - mu) / sd), n_null=int(arr.size))
    return out


def _fmt_p(p):
    return "%.4g" % p if np.isfinite(p) else "nan"


if __name__ == "__main__":
    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    reds, blues, issues = D.to_arrays(m)
    print("[block] 载入 %d 期  reds=%s blues=%s" % (reds.shape[0], reds.shape, blues.shape))

    print("\n===== 阳性对照（注入周期信号，应被捕获）=====")
    pc = positive_control(reds, blues, period=37, amp=8.0)
    for st, r in pc.items():
        flag = "  <-- 检出(闸门有效)" if (np.isfinite(r["p_order"]) and r["p_order"] < 0.05) else ""
        print("  %-16s real=%.4f  p_order=%s  z=%.2f%s" %
              (st, r["real"], _fmt_p(r["p_order"]), r.get("z", float("nan")), flag))

    print("\n===== 顺序不变性检验（块状宇宙命题）=====")
    res = meta_order_test(reds, blues, k_cheap=500, k_heavy=50)
    any_struct = False
    for sname, rec in res.items():
        print("\n  -- 序列 %s --" % sname)
        for st, r in rec.items():
            sig = (np.isfinite(r["p_order"]) and r["p_order"] < 0.01)
            any_struct = any_struct or sig
            tag = "  *** 时序顺序显著(证伪块状宇宙/现真实杠杆) ***" if sig else ""
            print("    %-16s real=%.4f  μ_shuf=%.4f  z=%.2f  p_order=%s%s" %
                  (st, r["real"], r.get("mu", float("nan")), r.get("z", float("nan")),
                   _fmt_p(r["p_order"]), tag))

    print("\n[block 结论] ", end="")
    if any_struct:
        print("存在对时序顺序敏感的统计量(p<0.01) → 时间轴承载真实结构，纯块状宇宙观不成立，"
              "且这是此前未触及的预测杠杆。")
    else:
        print("全部顺序敏感统计量在行置换下不变(p_order 均不显著) → 与块状宇宙命题一致"
              "(时间顺序无特殊结构)，同时再证一道'无时序结构'。")
