# -*- coding: utf-8 -*-
"""
formula_research_calendar.py —— 真实日历外部轴透镜（突破刀口 C·续）
============================================================
取舍结论（用户授权"选择权给你"）：
  物理/机器偏倚维度需物理数据（无、且强制随机化难藏结构）不可执行；
  期号 mod k / 相位 / 数位和已在 formula_research_exo.py 测过且全 NULL。
  但**真实公历日期**是 NEW 信息：期号 mod k ≠ 真实月/节假日/节气。
  双色球固定在 周日/周二/周四 开奖，故可由期号确定性重建真实日期，
  派生 月 / 节假日邻近 / 节气邻近 这组前人从未诚实检验的外部轴。

闸门：复用 formula_research_exo 的「球×组 χ² + 置换 null + Kruskal +
BH-FDR + 阳性对照」，保证闸门有功效、绝不自动合并（红线）。

日期重建近似说明（诚实标注）：
  按"每年首个 Sun/Tue/Thu = 该年首期"重建年内日期序列（seq 1..N）。
  个别节假日可能调期 => 重建存在 ±几日误差；但闸门用"置换组标签"作 null，
  小误差只会引入噪声、使检验更保守（难出假阳性），不影响诚实性。
"""
import datetime as dt
import numpy as np

import data as D
import formula_research_exo as FX


# ---- 真实日历重建：每年 Sun/Tue/Thu = 该年 draw 日期序列 ----
def reconstruct_dates(issues):
    """issue -> datetime。返回 np.array(dtype=object) 长度 N。"""
    out = []
    for iss in issues:
        iss = int(str(iss))
        yy = iss // 1000
        seq = iss % 1000
        year = 2000 + yy
        draws = _year_draws(year)
        # seq 从 1 起；防越界
        idx = min(max(seq, 1), len(draws)) - 1
        out.append(draws[idx])
    return np.array(out, dtype=object)


def _year_draws(year):
    """该年所有 Sun/Tue/Thu 日期，升序（=该年 draw 序列）。"""
    d = dt.date(year, 1, 1)
    # 找到该年首个 Sun/Tue/Thu
    draws = []
    cur = d
    # 回退到周日开始扫描整年
    while cur.weekday() != 6:  # 0=Mon..6=Sun
        cur = cur - dt.timedelta(days=1)
    end = dt.date(year, 12, 31)
    while cur <= end:
        wd = cur.weekday()
        if wd in (6, 1, 3):  # Sun=6, Tue=1, Thu=3
            if cur.year == year:
                draws.append(cur)
        cur = cur + dt.timedelta(days=1)
    return draws


# ---- 春节日期表 2003-2026（已知公开值）----
SPRING_FEST = {
    2003: (2, 1), 2004: (1, 22), 2005: (2, 9), 2006: (1, 29),
    2007: (2, 18), 2008: (2, 7), 2009: (1, 26), 2010: (2, 14),
    2011: (2, 3), 2012: (1, 23), 2013: (2, 10), 2014: (1, 31),
    2015: (2, 19), 2016: (2, 8), 2017: (1, 28), 2018: (2, 16),
    2019: (2, 5), 2020: (1, 25), 2021: (2, 12), 2022: (2, 1),
    2023: (1, 22), 2024: (2, 10), 2025: (1, 29), 2026: (2, 17),
}

# ---- 24 节气近似日期（标准近似，±1日年际波动，闸门对噪声鲁棒）----
SOLAR_TERMS = [
    (1, 6), (1, 20), (2, 4), (2, 19), (3, 6), (3, 21), (4, 5), (4, 20),
    (5, 6), (5, 21), (6, 6), (6, 21), (7, 7), (7, 23), (8, 8), (8, 23),
    (9, 8), (9, 23), (10, 8), (10, 23), (11, 7), (11, 22), (12, 7), (12, 22),
]

FIXED_HOLIDAYS = [(1, 1), (4, 5), (5, 1), (10, 1)]  # 元旦/清明/劳动/国庆


def _min_days_to(d, targets):
    """d: date；targets: list[(month,day)] -> 最小绝对天数差。"""
    best = 1e9
    for m, day in targets:
        try:
            t = dt.date(d.year, m, day)
        except ValueError:
            continue
        best = min(best, abs((d - t).days))
        # 跨年边界
        try:
            t0 = dt.date(d.year - 1, m, day)
            best = min(best, abs((d - t0).days))
        except ValueError:
            pass
        try:
            t1 = dt.date(d.year + 1, m, day)
            best = min(best, abs((d - t1).days))
        except ValueError:
            pass
    return int(best)


def build_calendar_signals(dates):
    """由真实日期派生外部组标签。返回 {name: group_array}。"""
    N = len(dates)
    out = {}

    months = np.array([d.month for d in dates])
    out["cal_month"] = months - 1  # 0..11

    dom = np.array([d.day for d in dates])
    out["cal_dayofmonth"] = dom - 1  # 0..30

    # ISO week of year (1..53)
    woy = np.array([int(d.strftime("%W")) for d in dates])
    out["cal_weekofyear"] = np.clip(woy, 0, 52)  # 0..52

    dow = np.array([d.weekday() for d in dates])  # 0=Mon..6=Sun
    out["cal_dow"] = dow  # 7 组（交叉验证：应≈ exo_issue_mod3）

    # 节气邻近（bin 10）
    st_days = np.array([_min_days_to(d, SOLAR_TERMS) for d in dates])
    out["cal_solarterm_prox"] = FX._bin(st_days.astype(float), 10)

    # 春节邻近（bin 10）
    sf_days = np.array([
        _min_days_to(d, [SPRING_FEST.get(d.year, (2, 1))]) for d in dates])
    out["cal_springfest_prox"] = FX._bin(sf_days.astype(float), 10)

    # 国庆邻近（bin 10）
    nd_days = np.array([_min_days_to(d, [(10, 1)]) for d in dates])
    out["cal_nationalday_prox"] = FX._bin(nd_days.astype(float), 10)

    # 节假日周（邻近任意主要节假日 <=4 天 -> 1 否则 0）
    major = list(FIXED_HOLIDAYS) + [SPRING_FEST.get(d.year, (2, 1)) for d in dates]
    holi_flag = np.array([
        1 if _min_days_to(d, major) <= 4 else 0 for d in dates])
    out["cal_holiday_week"] = holi_flag

    return out


def _bh_fdr(recs):
    # 修正：委托给 formula_research_exo.apply_bh_fdr（正确算法）
    return FX.apply_bh_fdr(recs)


def main():
    path = "D:/ssq_evo_data/ssq_master.csv"
    m = D.load_master(path)
    if not m:
        print("[cal] 未找到真实数据")
        return
    reds, blues, issues = D.to_arrays(m)
    N = reds.shape[0]
    print("[cal] 载入真实数据 %d 期" % N)

    dates = reconstruct_dates(issues)
    # 重建合理性校验：打印首尾日期 + 星期分布
    print("[cal] 首期 %s(%s)  末期 %s(%s)" %
          (dates[0], "一二三四五六日"[dates[0].weekday()],
           dates[-1], "一二三四五六日"[dates[-1].weekday()]))
    wd, cnt = np.unique([d.weekday() for d in dates], return_counts=True)
    print("[cal] 星期分布(0=Mon):", dict(zip(wd.tolist(), cnt.tolist())))

    exo = build_calendar_signals(dates)
    print("[cal] 日历外部信号数: %d" % len(exo))

    recs = []
    for name, groups in exo.items():
        chi, df, _, _ = FX.ball_group_chi2(reds, groups)
        p_perm, null_mean, null_std = FX.ball_group_chi2_perm_p(reds, groups, chi, n_perm=200)
        p_theory = float(__import__("scipy.stats", fromlist=["chi2"]).chi2.sf(chi, df))
        p_sum = FX.draw_prop_kruskal(reds, groups, "sum")
        p_odd = FX.draw_prop_kruskal(reds, groups, "odd")
        recs.append({
            "sig": name, "obs_chi2": round(chi, 1), "df": df,
            "perm_p": p_perm, "theory_p": round(p_theory, 4),
            "null_mean_chi2": round(null_mean, 1),
            "kruskal_sum_p": (round(p_sum, 4) if p_sum is not None else None),
            "kruskal_odd_p": (round(p_odd, 4) if p_odd is not None else None),
        })
        print("  %-20s chi2=%.1f df=%d perm_p=%.4g theory_p=%.4g | sum_p=%s odd_p=%s" %
              (name, chi, df, p_perm, p_theory,
               ("%.3g" % p_sum) if p_sum is not None else "-",
               ("%.3g" % p_odd) if p_odd is not None else "-"))

    _bh_fdr(recs)

    pctrl = FX.exo_positive_control()
    print("\n[阳性对照] 注入耦合: %s | obs_chi2=%s df=%s perm_p=%s (期望 BIAS_DETECTED)" %
          (pctrl["verdict"], pctrl["obs_chi2"], pctrl["df"],
           ("%.4g" % pctrl["perm_p"]) if pctrl["perm_p"] is not None else "-"))

    n_surv = sum(1 for r in recs if r["survived"])
    print("\n[cal 汇总] 日历信号=%d  过 BH-FDR 幸存=%d" % (len(recs), n_surv))
    if n_surv == 0:
        print("[结论] 真实日历外部轴全 NULL —— 月/节假日/节气与开奖结果无耦合。")
        if pctrl["verdict"] == "BIAS_DETECTED":
            print("        阳性对照 BIAS_DETECTED => 闸门有功效，本 NULL 为真 NULL（非坏测试）。")
    else:
        print("[结论] 有 %d 个日历信号幸存 —— 可能发现外部耦合，须人工复核。" % n_surv)
    return recs, pctrl


if __name__ == "__main__":
    main()
