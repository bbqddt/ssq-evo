# -*- coding: utf-8 -*-
"""
formula_research_sales.py —— 销售额/奖池 外生轴透镜（突破真杠杆）
============================================================
背景（用户授权"你去完成"）：
  在「仅开奖数字 + 期号」空间内公式研发已系统性穷尽且全 NULL；唯一尚未
  诚实检验的 NEW 外生维度 = 中彩网每期公开的「销售额 / 奖池」运营元数据。
  关键事实：销售额/奖池在停售(20:00)即确定、先于开奖(21:15)，是真正
  *外生于机械摇奖* 的观测，不依赖开奖数字本身 —— 这正是之前找不到的杠杆。

闸门：复用 formula_research_exo 的「球×组 χ² + 置换 null + Kruskal +
BH-FDR + 阳性对照」，保证闸门有功效、绝不自动合并（红线）。

信号构造（同周期对齐：t 期 sales/pool -> t 期 draw；无前瞻泄漏，
因为 sales_t 在 draw_t 之前已确定）：
  - 销售量级 quantile（含对数）
  - 销售动量 mom（t/t-1-1）
  - 销售 z 分数（对滚动 100 期去趋势）
  - 销售滚动分位 rank50（去趋势）
  - 奖池量级 / 动量 / z 分数
  - 销售额/奖池 比值
去趋势特征（mom/z/rank）专门剥离"销量随时间上涨"的混淆，使检验聚焦于
销量*过程*而非单纯时间趋势；置换 null 进一步破坏 期号->开奖 对齐。

诚信预期：按设计摇奖与销量独立，预期全 NULL；但这是 NEW 维度，须经闸门
实跑而非先验否定。阳性对照须 BIAS_DETECTED 证明闸门有功效。
"""
import csv
import numpy as np

import data as D
import formula_research_exo as FX


SALES_CSV = "D:/ssq_evo_data/ssq_sales.csv"


def load_sales(path):
    """code5 -> (sales_int, pool_int)。缺失/非法 -> (nan, nan)。"""
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = r["code5"].strip()
            s = r["sales"].strip()
            p = r["poolmoney"].strip()
            sv = float(s) if s.isdigit() else np.nan
            pv = float(p) if p.isdigit() else np.nan
            out[c] = (sv, pv)
    return out


def bin_safe(arr, nb=10):
    """对非空值等频分箱，NaN 保持 -1。"""
    out = np.full(arr.shape, -1, dtype=int)
    mask = ~np.isnan(arr)
    if mask.sum() < 2:
        return out
    vals = arr[mask]
    qs = np.quantile(vals, np.linspace(0, 1, nb + 1))
    qs[0] -= 1
    qs[-1] += 1
    out[mask] = np.digitize(vals, qs[1:-1]).astype(int)
    return out


def rolling_z(arr, w=100):
    out = np.full(arr.shape, np.nan)
    for i in range(arr.shape[0]):
        win = arr[max(0, i - w):i]
        win = win[~np.isnan(win)]
        if win.size >= 5:
            mu, sd = win.mean(), win.std()
            if sd > 0:
                out[i] = (arr[i] - mu) / sd
    return out


def rolling_rank(arr, w=50):
    out = np.full(arr.shape, np.nan)
    for i in range(arr.shape[0]):
        win = arr[max(0, i - w):i + 1]
        win = win[~np.isnan(win)]
        if win.size >= 2:
            out[i] = np.mean(win <= arr[i])
    return out


def build_sales_signals(sales, pool):
    """sales/pool: float 数组(长度 N, NaN=缺失)。返回 {name: group_array}。"""
    N = sales.shape[0]
    sig = {}
    # 销售量级（含对数）
    sig["sales_q"] = bin_safe(sales, 10)
    log_s = np.full(N, np.nan)
    mpos = sales > 0
    log_s[mpos] = np.log(sales[mpos])
    sig["sales_log_q"] = bin_safe(log_s, 10)
    # 销售动量（去趋势）
    mom = np.full(N, np.nan)
    ok = ~np.isnan(sales)
    s2 = sales.copy()
    mom[ok] = s2[ok] / np.roll(s2, 1)[ok] - 1.0
    mom[0] = np.nan
    sig["sales_mom_q"] = bin_safe(mom, 10)
    # 销售 z（去趋势）
    sig["sales_z100_q"] = bin_safe(rolling_z(sales, 100), 10)
    # 销售滚动分位（去趋势）
    sig["sales_rank50_q"] = bin_safe(rolling_rank(sales, 50), 10)
    # 奖池
    sig["pool_q"] = bin_safe(pool, 10)
    pmom = np.full(N, np.nan)
    okp = ~np.isnan(pool)
    p2 = pool.copy()
    pmom[okp] = p2[okp] / np.roll(p2, 1)[okp] - 1.0
    pmom[0] = np.nan
    sig["pool_mom_q"] = bin_safe(pmom, 10)
    sig["pool_z100_q"] = bin_safe(rolling_z(pool, 100), 10)
    # 销售额/奖池 比值
    ratio = np.where((pool > 0) & (~np.isnan(pool)), sales / pool, np.nan)
    sig["ratio_sp_q"] = bin_safe(ratio, 10)
    return sig


def _subset(reds, groups):
    """只保留 group>=0 且有效的期（与 reds 对齐）。"""
    present = groups >= 0
    return reds[present], groups[present]


def oot_check(reds, groups, frac=0.5, n_perm=200):
    """OOT 稳定性探针：只用后 (1-frac) 时段（按时间切分）重测关联，
    避免 in-sample 过拟合误判。返回 (test_chi2, test_perm_p)。
    若后段 perm_p 仍小 => 关联跨样本稳定（更可信）；若塌缩 => 时间混淆。"""
    n = reds.shape[0]
    k = int(n * frac)
    te, teg = reds[k:], groups[k:]
    mask = teg >= 0
    te, teg = te[mask], teg[mask]
    if te.shape[0] < 100:
        return None, None
    chi, df, _, _ = FX.ball_group_chi2(te, teg)
    p_perm, null_mean, _ = FX.ball_group_chi2_perm_p(te, teg, chi, n_perm=n_perm)
    return float(chi), float(p_perm)


def predictive_oot(reds, groups, frac=0.5):
    """预测型 OOT：前 frac 训练（学各 bin 的 和值>中位 频率），后段预测，
    算 OOS AUC / Brier。AUC~0.5 => 仅有关联性、无可预测力（非可用公式）。"""
    from scipy.stats import rankdata
    n = reds.shape[0]
    k = int(n * frac)
    tr, tg = reds[:k], groups[:k]
    te, teg = reds[k:], groups[k:]
    m1 = tg >= 0
    tr, tg = tr[m1], tg[m1]
    m2 = teg >= 0
    te, teg = te[m2], teg[m2]
    if tr.shape[0] < 100 or te.shape[0] < 50:
        return None
    sum_tr = tr.sum(axis=1).astype(float)
    med = np.median(sum_tr)
    rates = {}
    for g in np.unique(tg):
        sel = tg == g
        rates[int(g)] = float(np.mean(sum_tr[sel] > med))
    y = (te.sum(axis=1).astype(float) > med).astype(int)
    p = np.array([rates.get(int(g), 0.5) for g in teg])
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    order = rankdata(p)
    auc = (order[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    brier = float(np.mean((p - y) ** 2))
    return float(auc), brier


def main():
    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    if not m:
        print("[sales] 未找到主表")
        return
    reds, blues, issues = D.to_arrays(m)
    N = reds.shape[0]
    print("[sales] 载入主表 %d 期" % N)

    sales_map = load_sales(SALES_CSV)
    s_arr = np.full(N, np.nan)
    p_arr = np.full(N, np.nan)
    for i, iss in enumerate(issues):
        if iss in sales_map:
            sv, pv = sales_map[iss]
            s_arr[i] = sv
            p_arr[i] = pv
    cov = int(np.sum(~np.isnan(s_arr)))
    print("[sales] 销售额覆盖 %d/%d 期 (%.1f%%)" % (cov, N, 100.0 * cov / N))
    if cov < 500:
        print("[sales] 覆盖率过低，请先运行 fetch_sales.py 回填历史。")
        return

    sig = build_sales_signals(s_arr, p_arr)
    print("[sales] 外生信号数: %d" % len(sig))

    recs = []
    for name, groups in sig.items():
        rs, g = _subset(reds, groups)
        if rs.shape[0] < 100:
            continue
        chi, df, _, _ = FX.ball_group_chi2(rs, g)
        p_perm, null_mean, null_std = FX.ball_group_chi2_perm_p(rs, g, chi, n_perm=200)
        p_theory = float(__import__("scipy.stats", fromlist=["chi2"]).chi2.sf(chi, df))
        p_sum = FX.draw_prop_kruskal(rs, g, "sum")
        p_odd = FX.draw_prop_kruskal(rs, g, "odd")
        recs.append({
            "sig": name, "n": rs.shape[0], "obs_chi2": round(chi, 1), "df": df,
            "perm_p": p_perm, "theory_p": round(p_theory, 4),
            "null_mean_chi2": round(null_mean, 1),
            "kruskal_sum_p": (round(p_sum, 4) if p_sum is not None else None),
            "kruskal_odd_p": (round(p_odd, 4) if p_odd is not None else None),
        })
        print("  %-16s n=%4d chi2=%.1f df=%d perm_p=%.4g theory_p=%.4g | sum_p=%s odd_p=%s" %
              (name, rs.shape[0], chi, df, p_perm, p_theory,
               ("%.3g" % p_sum) if p_sum is not None else "-",
               ("%.3g" % p_odd) if p_odd is not None else "-"))

    # BH-FDR 校正（perm_p 主判）—— 修正后的正确实现
    FX.apply_bh_fdr(recs)

    pctrl = FX.exo_positive_control()
    print("\n[阳性对照] 注入耦合: %s | obs_chi2=%s df=%s perm_p=%s (期望 BIAS_DETECTED)" %
          (pctrl["verdict"], pctrl["obs_chi2"], pctrl["df"],
           ("%.4g" % pctrl["perm_p"]) if pctrl["perm_p"] is not None else "-"))

    n_surv = sum(1 for r in recs if r["survived"])
    print("\n[sales 汇总] 外生信号=%d  过 BH-FDR 幸存=%d" % (len(recs), n_surv))
    # OOT 稳定性探针 + 预测型 OOT（仅对幸存信号，避免时间混淆误判）
    for r in recs:
        if r["survived"]:
            g = sig[r["sig"]]
            tchi, tp = oot_check(reds, g, frac=0.5, n_perm=200)
            pr = predictive_oot(reds, g, frac=0.5)
            print("  [OOT稳定] %-14s 后50%%时段 chi2=%.1f perm_p=%s %s" %
                  (r["sig"], tchi if tchi is not None else 0,
                   ("%.4g" % tp) if tp is not None else "-",
                   ("=> 跨样本稳定，较可信" if (tp is not None and tp < 0.05)
                    else "=> 后段塌缩，疑时间混淆")))
            if pr is not None:
                auc, brier = pr
                skill = "有微弱预测力" if auc > 0.53 else "无预测力(仅关联)"
                print("  [OOT预测] %-14s OOS AUC=%.3f Brier=%.3f (随机基准 0.50/0.25) => %s" %
                      (r["sig"], auc, brier, skill))
    if n_surv == 0:
        print("[结论] 销售额/奖池外部轴全 NULL —— 销量/奖池与开奖结果无耦合。")
        if pctrl["verdict"] == "BIAS_DETECTED":
            print("        阳性对照 BIAS_DETECTED => 闸门有功效，本 NULL 为真 NULL（非坏测试）。")
        else:
            print("        !! 阳性对照失败 => 闸门无效，所有 NULL 不可信，须排查。")
    else:
        print("[结论] 有 %d 个销售轴信号幸存 —— 可能发现外生耦合，须人工复核后接入 engine_core。" % n_surv)
    return recs, pctrl


if __name__ == "__main__":
    main()
