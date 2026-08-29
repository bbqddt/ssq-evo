# -*- coding: utf-8 -*-
"""
formula_research_exo.py —— 外生因子耦合透镜（突破刀口 C）
=====================================================
用户取舍结论：物理/机器偏倚维度需物理数据（当前无、且强制随机化极难藏结构）
不可执行；唯一可打的新轴是「外生因子耦合」——把开奖的**外部元数据**
（期号 / 开奖序号 / 周期相位）当作耦合变量，测试其是否与开奖结果相关。

前人公式几乎只挖「开奖数字自身」的时序/数论结构；把「开奖序号元数据」
作为预测变量这个真空区从未被诚实检验过。

诚实闸门的关键设计（必须特殊，不能复用 run_axes 的 shuffle/AAFT）：
  外生信号只依赖期号、与红球数组无关。若用 run_axes 的 null（打乱 reds 重算信号），
  信号不变 -> null 退化成错误结论。正确做法：
    1. 把「信号与开奖的对齐关系」整体置换 = 随机重排每期所属组 -> 正确的 null。
    2. 主检验：「球(33) × 组(G)」列联表独立性 χ²（直接测「期号元数据是否决定哪个球被抽」）。
    3. 置换 null：重排组分配 200 次，得经验 p = P(perm_χ² >= obs_χ²)。
    4. 次检验：开奖级属性（和值 / 奇数个数）跨组的 Kruskal-Wallis 非参检验。
    5. BH-FDR 多候选校正（12 个外生信号 × 多次检验）。
    6. 阳性对照：人为注入「期号 mod 3 == 0 时球1超代表」-> 闸门必须检出（否则测试无效）。

绝不自动合并进演进（红线）。若幸存，再规划接入 engine_core._build_x 接受 issues。
"""
import numpy as np
from scipy.stats import chi2, kruskal

import data as D


# ---------------------------------------------------------------------------
# 1. 外生信号工厂（只依赖 issue，无前瞻）
# ---------------------------------------------------------------------------
def build_exo_signals(issues, N):
    """由期号派生外生信号。返回 {name: (group_assignment_array, is_mod)}。
    - mod 类：group = issue_int mod k，离散 G=k 组。
    - 周期相位类：把相位 bin 成 10 组（连续 -> 离散，才能做列联表）。
    - 序号类：rank/N 连续 -> bin 10 组。
    全部是「每期一个标量/组标签」，编码了开奖的外部元数据。"""
    issue_int = np.array([int(str(x)) for x in issues], dtype=np.int64)
    rank = np.arange(N)
    out = {}

    # --- 模剩余类（期号 mod k）---
    for k in (2, 3, 5, 7, 11, 16, 33):
        g = (issue_int % k).astype(int)
        out[f"exo_issue_mod{k}"] = (g, True)

    # --- 序号归一化（开奖序列位置）---
    idx = rank / max(1, N - 1)
    out["exo_draw_index"] = (_bin(idx, 10), True)

    # --- 周期相位（每周≈3期 -> 频率3；每年≈156期 -> 频率156；月≈13期 -> 13；半年≈78）---
    for freq, name in ((3, "exo_phase_wk"), (13, "exo_phase_mo"),
                       (78, "exo_phase_half"), (156, "exo_phase_yr")):
        phase = np.sin(2 * np.pi * freq * idx)
        out[name] = (_bin(phase, 10), True)

    # --- 期号数位和（元数据自身的数论角度）---
    ds = np.array([sum(int(c) for c in str(x)) for x in issue_int], dtype=int)
    out["exo_issue_digitsum"] = (ds % 9, True)

    return out


def _bin(x, nb):
    """把连续数组分 nb 个等频 bin，返回 0..nb-1 整数组标签。"""
    qs = np.quantile(x, np.linspace(0, 1, nb + 1))
    qs[0] -= 1
    qs[-1] += 1
    return np.digitize(x, qs[1:-1]).astype(int)


# ---------------------------------------------------------------------------
# 2. 诚实闸门：球 × 组 独立性 χ² + 置换 null
# ---------------------------------------------------------------------------
def ball_group_chi2(reds, groups):
    """列联表：行=33 球(1..33)，列=G 组。O[g][i]=球i在组g出现次数。
    返回 (chi2_stat, df, perm_p, obs_stat)。"""
    N = reds.shape[0]
    G = int(np.max(groups)) + 1
    O = np.zeros((G, 33), dtype=np.int64)   # 仅球 1..33（第0列恒0，排除避免退化）
    for t in range(N):
        g = int(groups[t])
        for x in reds[t]:
            O[g, int(x) - 1] += 1
    col_marg = O.sum(axis=1)                # 每组总抽球数 = 6*n_g
    row_marg = O.sum(axis=0)                # 每球总出现（33 个，均>0）
    total = int(col_marg.sum())
    if total == 0 or (col_marg == 0).any():
        return 0.0, (33 - 1) * (G - 1), 1.0, 0.0
    E = np.outer(col_marg, row_marg) / total
    with np.errstate(divide="ignore", invalid="ignore"):
        chi = np.nansum((O - E) ** 2 / E)
    df = (33 - 1) * (G - 1)
    return float(chi), int(df), None, float(chi)


def ball_group_chi2_perm_p(reds, groups, obs_chi, n_perm=200, seed=0):
    """置换 null：重排每期所属组（保留组大小），重算 χ²。
    经验 p = P(perm_χ² >= obs_χ²)。正确破坏「期号->开奖」的真实耦合。"""
    rng = np.random.default_rng(seed)
    N = reds.shape[0]
    base = groups.copy()
    ge = np.zeros(n_perm)
    for p in range(n_perm):
        perm = rng.permutation(N)
        gperm = base[perm]                  # 重排组分配
        c, _, _, _ = ball_group_chi2(reds, gperm)
        ge[p] = c
    p = float(np.mean(ge >= obs_chi))
    return p, float(ge.mean()), float(ge.std())


def draw_prop_kruskal(reds, groups, prop):
    """开奖级属性（和值/奇数个数）跨组的 Kruskal-Wallis 非参检验。
    prop in {'sum','odd'}。返回 p 值（None 若组不足）。"""
    N = reds.shape[0]
    G = int(np.max(groups)) + 1
    if prop == "sum":
        vals = reds.sum(axis=1).astype(float)
    elif prop == "odd":
        vals = (reds % 2 == 1).sum(axis=1).astype(float)
    else:
        return None
    samples = [vals[groups == g] for g in range(G) if np.sum(groups == g) > 0]
    if len(samples) < 2:
        return None
    try:
        h = kruskal(*samples)
        return float(h.pvalue)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3. 阳性对照：注入「期号 mod 3 == 0 时球1超代表」
# ---------------------------------------------------------------------------
def exo_positive_control(N=3000, seed=7):
    """验证 ball_group_chi2 有功效：人为让 issue mod 3 == 0 的期，
    球1以 0.5 概率出现（理论 6/33≈0.18）。闸门须检出 BIAS（p~0）。"""
    rng = np.random.default_rng(seed)
    r = np.zeros((N, 6), dtype=int)
    issues = np.arange(N)
    for i in range(N):
        if (issues[i] % 3 == 0) and rng.random() < 0.5:
            rest = rng.choice(np.arange(2, 34), size=5, replace=False)
            r[i] = np.sort(np.concatenate([[1], rest]))
        else:
            r[i] = np.sort(rng.choice(np.arange(1, 34), size=6, replace=False))
    g = (issues % 3).astype(int)
    chi, df, _, _ = ball_group_chi2(r, g)
    p, null_mean, null_std = ball_group_chi2_perm_p(r, g, chi, n_perm=200, seed=seed)
    return {"verdict": "BIAS_DETECTED" if p < 0.05 else "NULL",
            "obs_chi2": round(chi, 1), "df": df, "perm_p": p,
            "null_mean_chi2": round(null_mean, 1),
            "is_positive_control": True}


# ---------------------------------------------------------------------------
# 3b. 正确的 BH-FDR 校正（与 engine_core.bh_fdr 同算法：从大到小步进）
#     注意：早期探索脚本里"从小到大取 min"的写法是 BUG——一旦最小 p≈0 会把
#     所有 q 值传染成 0，虚假全盘幸存。此处修正。
# ---------------------------------------------------------------------------
def apply_bh_fdr(recs):
    """对 recs 的 perm_p 做 BH-FDR，写入 bh_q / survived。返回 recs。"""
    pvals = np.array([r["perm_p"] for r in recs], dtype=float)
    n = pvals.size
    order = np.argsort(pvals)
    ranked = pvals[order]
    q = ranked * n / (np.arange(1, n + 1))
    q = np.minimum.accumulate(q[::-1])[::-1]   # 从大到小步进，保证单调
    q = np.clip(q, 0, 1)
    out = np.empty(n)
    out[order] = q
    for i, r in enumerate(recs):
        r["bh_q"] = round(float(out[i]), 4)
        r["survived"] = bool(out[i] < 0.05)
    return recs


# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------
def main():
    path = "D:/ssq_evo_data/ssq_master.csv"
    m = D.load_master(path)
    if not m:
        print("[exo] 未找到真实数据")
        return
    reds, blues, issues = D.to_arrays(m)
    N = reds.shape[0]
    print("[exo] 载入真实数据 %d 期" % N)

    exo = build_exo_signals(issues, N)
    print("[exo] 外生信号数: %d" % len(exo))

    recs = []
    for name, (groups, _is_mod) in exo.items():
        chi, df, _, _ = ball_group_chi2(reds, groups)
        p_perm, null_mean, null_std = ball_group_chi2_perm_p(reds, groups, chi, n_perm=200)
        p_theory = float(chi2.sf(chi, df))
        # 次检验：开奖级属性跨组
        p_sum = draw_prop_kruskal(reds, groups, "sum")
        p_odd = draw_prop_kruskal(reds, groups, "odd")
        recs.append({
            "sig": name, "obs_chi2": round(chi, 1), "df": df,
            "perm_p": p_perm, "theory_p": round(p_theory, 4),
            "null_mean_chi2": round(null_mean, 1),
            "kruskal_sum_p": (round(p_sum, 4) if p_sum is not None else None),
            "kruskal_odd_p": (round(p_odd, 4) if p_odd is not None else None),
        })
        print("  %-18s chi2=%.1f df=%d perm_p=%.4g theory_p=%.4g | sum_p=%s odd_p=%s" %
              (name, chi, df, p_perm, p_theory,
               ("%.3g" % p_sum) if p_sum is not None else "-",
               ("%.3g" % p_odd) if p_odd is not None else "-"))

    # BH-FDR 校正（以 perm_p 为主）—— 用修正后的正确实现
    apply_bh_fdr(recs)

    # 阳性对照
    pctrl = exo_positive_control()
    print("\n[阳性对照] 注入耦合: %s | obs_chi2=%s df=%s perm_p=%s (期望 BIAS_DETECTED)" %
          (pctrl["verdict"], pctrl["obs_chi2"], pctrl["df"],
           ("%.4g" % pctrl["perm_p"]) if pctrl["perm_p"] is not None else "-"))

    n_surv = sum(1 for r in recs if r["survived"])
    print("\n[exo 汇总] 外生信号=%d  过 BH-FDR 幸存=%d  (perm_p 主判)" % (len(recs), n_surv))
    if n_surv == 0:
        print("[结论] 外生因子耦合在真实数据上全 NULL —— 开奖序号元数据与开奖结果无耦合。")
        if pctrl["verdict"] == "BIAS_DETECTED":
            print("        阳性对照 BIAS_DETECTED => 闸门有功效，本 NULL 为真 NULL（非坏测试）。")
        else:
            print("        !! 阳性对照失败 => 闸门无效，所有 NULL 不可信，须排查。")
    else:
        print("[结论] 有 %d 个外生信号幸存 —— 可能发现期号元数据耦合，须人工复核后接入 engine_core。" % n_surv)

    return recs, pctrl


if __name__ == "__main__":
    main()
