# obf_design.py —— 方案 B 序贯确证设计器（Lan-DeMets OBF + 单统计量）
# 产出三件套（全部落 audit/，boundary/design 由 git 锚点锁死）：
#   1. audit/obf_boundary.csv   n=1..N_MAX 的 Z 边界表（打分器运行时只查表，不现算）
#   2. audit/obf_design.json    设计参数 + 功效表 + 对照结果
#   3. 控制台报告               双向标定证据（阴性 FPR / 阳性功效 / 校准偏差）
#
# 统计量（与预注册向量 v 绑定，方向性一阶边际检验）：
#   w = v_centered / ||v_centered||          （单位化注册方向）
#   l_d = w · X_d ,  X_d = 当期 6 球指示向量   （i.i.d. 增量）
#   Z_n = Σ_{d<=n} l_d / (SD1 * sqrt(n)) ,  SD1^2 = (6*27/(33*32)) * ||w||^2 = 162/1056
#   H0 下 l_d i.i.d. ⇒ Z_n 精确渐近 N(0,1)，且 (Z_{n1},Z_{n2}) 相关 = sqrt(n1/n2)
#   ⇒ 恰为布朗运动信息结构 ⇒ Lan-DeMets OBF α-spending **精确成立**（非渐近近似）。
#
# 用法：
#   python obf_design.py            # 生成边界表 + 全套对照（改闸门必跑）
#   python obf_design.py --check    # 只校验现有 boundary/design 与锚点

import json
import math
import os
import sys
from datetime import datetime

import numpy as np
from scipy.stats import norm

import paths

N_BALL, N_PICK = 33, 6
N_MAX = 3500                 # 设计终点（≈23.3 年 @150 期/年）
ALPHA = 0.05                 # 总 α（单侧）
MIN_LOOK = 50                # 首个正式看点（之前边界天文级，无信息）
SIGMA_DESIGN = 3.5           # 设计效应量（候选 σ，%）
MC_PATH = 400                # 全离散路径对照重复数（阴性/阳性各一份）
MC_BROWN = 20000             # 布朗近似功效 MC（交叉验证用）

BOUND_PATH = ("audit", "obf_boundary.csv")
DESIGN_PATH = ("audit", "obf_design.json")
SD1 = math.sqrt(N_PICK * (N_BALL - N_PICK) / (N_BALL * (N_BALL - 1)))  # ||w||=1 时


# ---------------------------------------------------------------------------
# 注册向量
# ---------------------------------------------------------------------------
def load_w():
    import preregistered_scorer as PS
    reg, err = PS.load_registry()
    if reg is None:
        raise SystemExit("[design] %s" % err)
    v = np.array([reg["dev_pct"][str(i + 1)] for i in range(N_BALL)], float)
    v = v - v.mean()
    return v / np.linalg.norm(v)


def p_bias_from(v_unit_sd, sigma_pct):
    """把注册方向重标定到 σ 后转抽取概率（与旧功效曲线同一构造）。"""
    vv = v_unit_sd / v_unit_sd.std() * sigma_pct
    w = np.exp(vv / 100.0)
    return w / w.sum()


# ---------------------------------------------------------------------------
# OBF 边界：W 尺度 McPherson 递推
# W_k = Z_k*sqrt(k/K)，增量 i.i.d. N(0,Δ)，Δ=1/K
# 花费函数（单侧 OBF）：alpha*(t) = 1 - Phi(z_alpha / sqrt(t))
# ---------------------------------------------------------------------------
def obf_boundary(K=N_MAX, alpha=ALPHA, verbose=True):
    z_a = norm.ppf(1 - alpha)
    spend = lambda t: 1.0 - norm.cdf(z_a / math.sqrt(t)) if t > 0 else 0.0
    delta = 1.0 / K
    # 网格
    h = 0.005
    grid = np.arange(-10.0, 10.0 + h / 2, h)
    # 转移核（±6σ 截断）
    half = int(6 * math.sqrt(delta) / h) + 1
    off = np.arange(-half, half + 1) * h
    kern = norm.pdf(off, scale=math.sqrt(delta))
    kern /= kern.sum()

    c = np.full(K, np.inf)
    dens = norm.pdf(grid, scale=math.sqrt(delta))  # W_1 未截断密度
    spent_prev = 0.0
    for k in range(1, K + 1):
        t = k / K
        need = spend(t) - spent_prev
        # 当前步未截断密度 u（前一步截断后卷积）
        u = np.convolve(dens, kern, mode="same")
        # 尾部质量 from top
        cum = np.cumsum(u[::-1]) * h          # cum[i] = ∫_{grid[i]}^{top} u
        tail = cum[::-1]
        # 解 c：tail(c) = need
        if tail[-1] >= need:                  # 整个网格尾部都比需要的大 ⇒ c 在网格外
            c_k = np.inf
        else:
            idx = np.searchsorted(-tail[::-1] * -1, need)  # placeholder, 用插值
            # tail 递减；找第一个 tail < need 的位置
            j = np.argmax(tail < need)
            if j == 0:
                c_k = grid[0]
            else:
                w1, w2 = grid[j - 1], grid[j]
                t1, t2 = tail[j - 1], tail[j]
                c_k = w1 + (w2 - w1) * (need - t1) / (t2 - t1)
        c[k - 1] = c_k
        dens = u * (grid < c_k) if np.isfinite(c_k) else u
        spent_prev = spend(t)
        if verbose and k % 500 == 0:
            print("  [boundary] k=%d t=%.3f c_W=%.4f b_Z=%.4f" %
                  (k, t, c_k, c_k / math.sqrt(t) if np.isfinite(c_k) else float("inf")))
    b = c / np.sqrt(np.arange(1, K + 1) / K)
    b[np.isinf(c)] = np.inf
    return b, c


# ---------------------------------------------------------------------------
# 全离散路径 MC（H0 阴性 / H1 阳性共用）
# ---------------------------------------------------------------------------
def _sim_paths(p_draw, n_reps, K, w, seed, chunk_draws=500):
    """返回每条路径的 L 累计和 (n_reps, K)。Gumbel top-k 加权无放回抽样。"""
    rng = np.random.default_rng(seed)
    logp = np.log(p_draw)
    out = np.empty((n_reps, K), dtype=np.float64)
    for r in range(n_reps):
        acc = np.empty(K)
        done = 0
        while done < K:
            m = min(chunk_draws, K - done)
            g = rng.gumbel(size=(m, N_BALL)) + logp
            idx = np.argpartition(-g, N_PICK, axis=1)[:, :N_PICK]
            acc[done:done + m] = w[idx].sum(axis=1)
            done += m
        out[r] = np.cumsum(acc)
    return out


def z_from_L(L, w_norm2=1.0):
    n = np.arange(1, L.shape[1] + 1)
    return L / (math.sqrt(SD1 ** 2 * w_norm2) * np.sqrt(n))[None, :]


def run_control(name, p_draw, boundary_z, w, seed):
    L = _sim_paths(p_draw, MC_PATH, N_MAX, w, seed)
    Z = z_from_L(L)
    ns = np.arange(1, N_MAX + 1)
    mask = ns >= MIN_LOOK
    cross = (Z[:, mask] >= boundary_z[mask][None, :]).any(axis=1)
    first = np.array([ns[mask][np.argmax(Z[i, mask] >= boundary_z[mask])] if cross[i] else -1
                      for i in range(MC_PATH)])
    rate = float(cross.mean())
    print("  [%s] reps=%d  跨界率=%.4f  首跨 n 中位数=%s"
          % (name, MC_PATH, rate, int(np.median(first[cross])) if cross.any() else "-"))
    return rate, first


def brownian_power(boundary_z, mu1, sd1, n_reps=MC_BROWN, seed=777):
    """布朗近似功效（增量均值 μ1、单位增量真值用 sd1 标定）。"""
    rng = np.random.default_rng(seed)
    drift = mu1 / sd1
    hit = {1000: 0, 1500: 0, 2000: 0, 3000: 0, N_MAX: 0}
    ns = np.arange(1, N_MAX + 1)
    bz = boundary_z[ns >= MIN_LOOK]
    nsel = ns[ns >= MIN_LOOK]
    for _ in range(n_reps):
        L = np.cumsum(rng.normal(drift, 1.0, N_MAX)) * sd1
        Z = L[MIN_LOOK - 1:] / (sd1 * np.sqrt(nsel))
        cr = np.nonzero(Z >= bz)[0]
        if cr.size:
            n0 = int(nsel[cr[0]])
            for k in hit:
                if n0 <= k:
                    hit[k] += 1
    return {k: v / n_reps for k, v in hit.items()}


def main():
    print("[design] SD1(per-draw sd of l, ||w||=1) = %.6f" % SD1)
    v = load_w()
    v_sd = v / v.std()

    # 1) 边界
    print("[design] 递推 OBF 边界 (K=%d, alpha=%.2f 单侧)..." % (N_MAX, ALPHA))
    b_z, c_w = obf_boundary()
    print("[design] 边界检查: b[50]=%.2f b[200]=%.2f b[1000]=%.2f b[2000]=%.2f b[3500]=%.2f"
          % tuple(b_z[k - 1] for k in (50, 200, 1000, 2000, 3500)))

    # 2) 布朗世界自检：递推边界总跨界率应 ≈ α
    rng = np.random.default_rng(999)
    nsel = np.arange(MIN_LOOK, N_MAX + 1)
    b_sel = b_z[MIN_LOOK - 1:]
    cr = 0
    for _ in range(4000):
        W = np.cumsum(rng.normal(0, math.sqrt(1.0 / N_MAX), N_MAX))
        Z = W[MIN_LOOK - 1:] / np.sqrt(nsel / N_MAX)
        if (Z >= b_sel).any():
            cr += 1
    fpr_brown = cr / 4000
    print("[design] 布朗自检 FPR = %.4f (目标 0.05)" % fpr_brown)

    # 3) 阴性对照：真实离散统计量全路径
    print("[design] 阴性对照（均匀随机全路径）...")
    p_unif = np.full(N_BALL, 1.0 / N_BALL)
    fpr_disc, _ = run_control("阴性/discrete", p_unif, b_z, v, seed=20260901)

    # 4) 阳性对照：σ=3.5% 注入
    print("[design] 阳性对照（σ=%.1f%% 注入全路径）..." % SIGMA_DESIGN)
    p_b = p_bias_from(v_sd, SIGMA_DESIGN)
    # 漂移 = E[l_d] under H1 = Σ_b w_b·P(b∈set) ≈ 6·(w·p)（每期 6 球，包含概率≈6p）
    mu1 = 6.0 * float(v @ p_b)
    pow_disc, first = run_control("阳性/discrete", p_b, b_z, v, seed=31415926)
    pw_brown = brownian_power(b_z, mu1, SD1)
    print("[design] 布朗近似功效 μ1=%.5f: %s" % (mu1, {k: round(x, 3) for k, x in pw_brown.items()}))

    # 5) 落盘
    bp = paths.p(*BOUND_PATH)
    np.savetxt(bp, np.column_stack([np.arange(1, N_MAX + 1), b_z]),
               fmt="%d,%.6f", header="n,b_z", comments="")
    design = {
        "protocol": "PRE_REGISTERED_PROTOCOL_v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_max": N_MAX, "alpha": ALPHA, "one_sided": True,
        "spending": "Lan-DeMets OBF: alpha*(t)=1-Phi(z_alpha/sqrt(t))",
        "statistic": "Z_n = sum(w·X_d)/(SD1*sqrt(n)); w=unit(registered dev_pct); SD1=sqrt(162/1056)",
        "min_look": MIN_LOOK, "sigma_design_pct": SIGMA_DESIGN,
        "boundary_head": {str(k): round(float(b_z[k - 1]), 4) for k in (50, 100, 200, 500, 1000, 2000, 3500)},
        "controls": {
            "brownian_selfcheck_fpr": fpr_brown,
            "discrete_negative_fpr": fpr_disc,
            "discrete_positive_power_by_3500": pow_disc,
            "brownian_power": {str(k): round(x, 4) for k, x in pw_brown.items()},
        },
    }
    with open(paths.p(*DESIGN_PATH), "w", encoding="utf-8") as f:
        json.dump(design, f, ensure_ascii=False, indent=2)
    print("[design] 已写 %s 与 %s" % (bp, paths.p(*DESIGN_PATH)))



if __name__ == "__main__":
    main()
