# physical_prior_bayes.py —— 制造公差能否解释 σ_freq≈3.5%？（次级证据，不进确证链）

# 问题：若红球边际频率候选偏差 σ_freq≈3.5%（方向=注册向量）真实存在，
#       「球体制造公差」（质量/直径/圆度的球间差异）能否在物理上产生它？
# 方法：公差规格 → 球间物理参数相对差异 σ_θ 的两种读法；
#       映射 σ_freq = c·σ_θ（c 无文献来源 ⇒ 假设 c~U[0.5,2]，做敏感性）；
#       蒙特卡洛 P(c·σ_θ ≥ 0.035)。
# 来源（官方，2026-09-01 检索）：
#   [1] 杭州市民政局 2022: 双色球摇奖球 平均重 ~25g，单球误差 ±0.5g；
#       平均直径 ~50mm，单球误差 ±0.5mm（mz.hangzhou.gov.cn）
#   [2] 人民网彩票频道 2016: 规格 25g 允差 ±0.5g / 直径 50mm 允差 ±0.5mm，
#       Ryo-Catteau 制造，中国计量科学研究院检定（caipiao.people.com.cn）
#   [3] 贵州省福彩中心 2022: 单套平均 25±3g，套内各球 ±0.5g；直径 50±0.5mm
#   [4] 重庆晚报(人民网转) 2015: 退役球 24g 允差 ±1g（旧批次，宽松读法参考）

import json
import math
from datetime import datetime

import numpy as np

import paths

OUT_PATH = ("audit", "physical_prior_bayes.json")

MU_M_G, TOL_M_G = 25.0, 0.5      # 克（[1][2][3]）
MU_D_MM, TOL_D_MM = 50.0, 0.5    # 毫米（[1][2][3]）
SIGMA_FREQ = 0.035               # 候选偏差（3 条独立估计 3.2-3.5% 的上限）
N_MC = 2_000_000


def halfnormal(scale, n, rng):
    return abs(rng.normal(0, scale, n))


def main():
    rng = np.random.default_rng(20260901)
    readings = {
        # 公差带读法：±Tol 视为均匀带（对物理解释最宽容）或 3σ 质检读法（最严）
        "uniform_band": {"sigma_mass": TOL_M_G / math.sqrt(3) / MU_M_G,
                          "sigma_diam": TOL_D_MM / math.sqrt(3) / MU_D_MM},
        "three_sigma":  {"sigma_mass": TOL_M_G / 3 / MU_M_G,
                          "sigma_diam": TOL_D_MM / 3 / MU_D_MM},
    }
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "sigma_freq_candidate": SIGMA_FREQ, "sources": [1, 2, 3, 4],
           "c_prior": "U[0.5,2]（无文献来源，纯假设，见 TBD）", "readings": {}}

    print("[prior] σ_freq 候选 = %.1f%%" % (100 * SIGMA_FREQ))
    for name, r in readings.items():
        for k, v in r.items():
            print("[prior] %-14s %s = %.3f%%" % (name, k, 100 * v))
        # 取两参数中较宽容的（质量）作 σ_θ 主尺度；直径仅为其 0.3-0.6 倍
        s_mass = r["sigma_mass"]
        c = rng.uniform(0.5, 2.0, N_MC)
        st = halfnormal(s_mass, N_MC, rng)
        p_mass = float((c * st >= SIGMA_FREQ).mean())
        # 双参数合并（独立贡献平方和，对物理解释同样偏宽容）
        st2 = np.sqrt(halfnormal(s_mass, N_MC, rng) ** 2 +
                      halfnormal(r["sigma_diam"], N_MC, rng) ** 2)
        p_both = float((c * st2 >= SIGMA_FREQ).mean())
        # 反推：要解释候选需要 c 多大（若 σ_θ 取公差上限读数）
        c_need_uniform = SIGMA_FREQ / s_mass
        out["readings"][name] = {
            "sigma_mass_pct": round(100 * s_mass, 3),
            "sigma_diam_pct": round(100 * r["sigma_diam"], 3),
            "P_manufacturing_explains_mass_only": round(p_mass, 5),
            "P_manufacturing_explains_mass_plus_diam": round(p_both, 5),
            "c_needed_if_sigma_theta_at_tolerance_ceiling": round(c_need_uniform, 2),
        }
        print("[prior] %-14s P(制造可解释) 质量单项=%.4f 质量直径合计=%.4f  所需 c>=%.1f"
              % (name, p_mass, p_both, c_need_uniform))

    # 结论性对照：把候选当作真实时，隐含 σ_θ = σ_freq/c，与公差天花板的倍数
    out["implied_sigma_theta_vs_ceiling"] = {
        "c=0.5": round(SIGMA_FREQ / 0.5 / readings["uniform_band"]["sigma_mass"], 1),
        "c=1": round(SIGMA_FREQ / 1.0 / readings["uniform_band"]["sigma_mass"], 1),
        "c=2": round(SIGMA_FREQ / 2.0 / readings["uniform_band"]["sigma_mass"], 1),
        "note": "倍数>1 即超出制造公差天花板（含最宽容的均匀带读法）",
    }
    with open(paths.p(*OUT_PATH), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[prior] 已写 %s" % paths.p(*OUT_PATH))


if __name__ == "__main__":
    main()
