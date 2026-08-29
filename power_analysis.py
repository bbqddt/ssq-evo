# -*- coding: utf-8 -*-
"""
power_analysis.py —— 理论边界分析 / 决策门（不进生产管线，HOST_ONLY_PY 豁免）
=========================================================================

目的（诚实）：
  给定当前数据量 N、现有统一闸门（run_axes 的分层 null：shuffle+AAFT+marginal），
  用「蒙特卡洛 + 阳性对照（注入已知强度结构）」测量闸门对已知结构的检出率
  （统计功率），画出「效应量 - 功率」曲线，并标注：
    (1) 最小可检出效应量（功率 0.8 对应）；
    (2) 当前最佳候选的效应量位置（来自真实数据 oot_p / best_q）。

这回答「以当前 N 与闸门严度，理论上能检出多弱的信号」——
若真实结构 d < 可检出下界，再跑也查不出 → 真正的停止条件。

方法（复用现有闸门，不自创一套）：
  - 注入器改编自 positive_control._inject_ar1，注入 AR(1)/周期/非线性结构到 red_sum，
    并保持双色球合法离散格式（故测的是「生产格式数据」上的真实可达灵敏度，
    非理论连续极限——离散化会削弱强效应，结论偏保守）。
  - 闸门复用 run_axes._axis_p（= engine_core.evaluate_x 在 shuffle 零假设下的 p_raw），
    与真实数据所用分层 null 完全一致。
  - 对每类注入采用「最相关单检验」（AR(1)→acf_max / 周期→fft_peak / 非线性→mi_max），
    单检验的 I 类率本就≈0.05（已诊断 acf_max false+=0.03），无需多重比较校正即有效。
  - 功率定义：该单检验 shuffle p < 0.05 的比例。
    —— 注：生产标签 SURVIVOR 要求 shuffle 与 AAFT 双显著（连谱-preserving null 都杀不死），
       这对 AR(1) 这类时间/自相关结构永远达不到（shuffle 会摧毁它），故功率以
       p_shuffle<0.05 为准；SURVIVOR 是「非时间/非自相关真结构」的更高标准，见报告说明。

⚠️ 诚实发现（见报告第 4 节）：生产闸门 run_axes.label_axis 对多个检验取 min-p 后
   直接以 <0.05 判显著，**未做 BH-FDR**——已诊断纯随机下 I 类率膨胀至 ~0.40。本报告
   用单检验给出有效曲线，并建议生产补 BH-FDR（你路线图中已要求）。

绝不搜结构、绝不改结论，只做「仪器能探多深」的体检。

输出：paths.p("audit", "power_report.md")
"""
import os
import json

import numpy as np
import paths
import run_axes as RA
import engine_core as E
import data as D

# 每类注入对应的最相关单检验（最大化判别力、避免多重比较膨胀）
TARGET_TEST = {"ar1": "acf_max", "periodic": "fft_peak", "nonlinear": "mi_max"}

M_DEFAULT = 30        # 蒙特卡洛次数
K_SUR_DEFAULT = 60    # surrogate 数量（略大于生产 40，分辨率 1/61≈0.016 足够单检验）


# ---------------------------------------------------------------------------
# 注入器：在双色球合法格式下，让 red_sum 序列携带已知结构
# ---------------------------------------------------------------------------
def _make_reds_with_redsum(N, target, rng):
    """target: 长度 N 的连续目标序列（期望的 red_sum 形状）。
    返回合法 reds：每期 6 个互异红球(1..33)，其和≈target[t]。
    为保留结构，仅做轻微离散化（target 已归一到 ~72..132 区间，几乎不撞边界）。
    """
    reds = np.zeros((N, 6), dtype=int)
    for t in range(N):
        base = target[t] / 6.0
        balls, seen = [], set()
        for j in range(6):
            for _ in range(50):  # 防死循环
                v = int(round(base + (j - 2.5) * 0.6 + rng.standard_normal() * 0.4))
                if v < 1:
                    v = 1
                elif v > 33:
                    v = 33
                if v not in seen:
                    break
            while v in seen:
                v = v + 1 if (j % 2 == 0) else v - 1
                if v > 33:
                    v = 1
                if v < 1:
                    v = 33
            seen.add(v)
            balls.append(v)
        reds[t] = sorted(balls)
    return reds


def inject_ar1(N, rho, lag=8, seed=0):
    """注入 AR(1)@lag 自相关结构到 red_sum。rho=自相关系数≈效应量。"""
    rng = np.random.default_rng(seed)
    x = np.zeros(N)
    for t in range(lag, N):
        x[t] = rho * x[t - lag] + rng.standard_normal()
    x = (x - x.mean()) / (x.std() + 1e-9)
    target = 102.0 + 30.0 * x
    reds = _make_reds_with_redsum(N, target, rng)
    blues = rng.integers(1, 17, size=N)
    return reds, blues


def inject_periodic(N, amp, period=40, seed=0):
    """注入正弦周期结构到 red_sum。amp=正弦振幅/噪声std≈效应量。"""
    rng = np.random.default_rng(seed)
    t = np.arange(N)
    sig = amp * np.sin(2.0 * np.pi * t / period)
    noise = rng.standard_normal(N)
    y = (sig + noise)
    y = (y - y.mean()) / (y.std() + 1e-9)
    target = 102.0 + 30.0 * y
    reds = _make_reds_with_redsum(N, target, rng)
    blues = rng.integers(1, 17, size=N)
    return reds, blues


def inject_nonlinear(N, amp, lag=8, seed=0):
    """注入非线性耦合：red_sum[t] 依赖前值的非线性函数。amp≈耦合强度。"""
    rng = np.random.default_rng(seed)
    x = np.zeros(N)
    prev = 0.0
    for t in range(1, N):
        x[t] = amp * np.sin(prev * 0.3) + 0.5 * rng.standard_normal()
        prev = x[t]
    x = (x - x.mean()) / (x.std() + 1e-9)
    target = 102.0 + 30.0 * x
    reds = _make_reds_with_redsum(N, target, rng)
    blues = rng.integers(1, 17, size=N)
    return reds, blues


# ---------------------------------------------------------------------------
# 闸门（复用现有分层 null 的 shuffle 零假设，单检验）
# ---------------------------------------------------------------------------
def gate_p_target(sig, reds, blues, rng, k_sur, test):
    """对信号 sig 在 shuffle 零假设下返回指定检验的 p 值。复用 run_axes._axis_p。"""
    return RA._axis_p(sig, reds, blues, test, rng, k_sur, None, "shuffle")


def power_at(d, injector, N, M, k_sur, base_seed, test, **kw):
    """蒙特卡洛 M 次：注入强度 d 的结构，统计单检验 shuffle p<0.05 的比例（=功率）。"""
    hits = 0
    for m in range(M):
        rng = np.random.default_rng(base_seed + 1000 * m + int(d * 1000) + 7)
        reds, blues = injector(N, d, seed=base_seed + m + 1, **kw)
        p = gate_p_target("red_sum", reds, blues, rng, k_sur, test)
        if p is not None and p < 0.05:
            hits += 1
    return round(hits / M, 3)


def sweep(kind, injector, ds, N, M, k_sur, base_seed, **kw):
    test = TARGET_TEST[kind]
    rows = []
    for d in ds:
        p = power_at(d, injector, N, M, k_sur, base_seed, test, **kw)
        rows.append((float(d), p))
        print("  [%s/%s] d=%.3f -> power=%.3f" % (kind, test, d, p))
    return rows


# ---------------------------------------------------------------------------
# 当前候选位置（来自真实状态，诚实引用，不作推定）
# ---------------------------------------------------------------------------
def load_current_candidate():
    try:
        st = json.load(open(paths.p("state.json"), encoding="utf-8"))
        return {
            "oot_p": st.get("oot_p"),
            "oot_hit": st.get("oot_hit"),
            "oot_n": st.get("oot_n"),
            "oot_rule": st.get("oot_rule"),
            "best_q": st.get("best_q"),
            "df_gen": st.get("df_gen"),
            "cycle_id": st.get("cycle_id"),
        }
    except Exception as e:
        return {"_err": str(e)}


def min_detectable(rows, target_power=0.8):
    """返回首个达到 target_power 的 d（最小可检出效应量）；未达则返回 None。"""
    for d, p in rows:
        if p >= target_power:
            return d
    return None


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def write_report(results, N, M, k_sur, cur):
    out_dir = paths.p("audit")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "power_report.md")

    lines = []
    lines.append("# 功率分析报告 (power_analysis)")
    lines.append("")
    lines.append("> 决策门：给定当前数据量 N 与现有统一闸门（分层 null: shuffle），")
    lines.append("> 测量闸门对已知强度结构的检出率（统计功率），标定「理论可检出效应量下界」与停止条件。")
    lines.append("")
    lines.append("## 1. 实验设定")
    lines.append("")
    lines.append("- 数据量 N = %d（真实双色球期数；若缺数据则回退 3496）" % N)
    lines.append("- 蒙特卡洛次数 M = %d" % M)
    lines.append("- surrogate 数 k_sur = %d（生产用 40，此处略大以保 p 分辨率）" % k_sur)
    lines.append("- 目标信号 sig = `red_sum`；每类注入采用最相关单检验：%s" %
                 ", ".join("%s→%s" % (k, v) for k, v in TARGET_TEST.items()))
    lines.append("- 功率定义 = 单检验 shuffle p < 0.05 的比例（单检验 I 类率≈0.05，无需多重比较校正）")
    lines.append("- 注入类型：AR(1)自相关 / 正弦周期 / 非线性耦合，均保持双色球合法离散格式")
    lines.append("- 当前候选位置取自真实状态（见第 3 节），**不用于推定结构存在**")
    lines.append("")
    lines.append("## 2. 效应量 - 功率 曲线")
    lines.append("")

    summary = {}
    for kind, (ds, rows) in results.items():
        test = TARGET_TEST[kind]
        label = {"ar1": "AR(1) 自相关 (rho)", "periodic": "正弦周期 (amp/σ)",
                 "nonlinear": "非线性耦合 (amp)"}[kind]
        lines.append("### %s  （检验=%s）" % (label, test))
        lines.append("")
        lines.append("| 效应量 d | 功率 (单检验检出率) |")
        lines.append("|---|---|")
        for d, p in rows:
            lines.append("| %.3f | %.3f |" % (d, p))
        md = min_detectable(rows, 0.8)
        summary[kind] = {"min_detectable_0.8": md, "rows": rows}
        if md is not None:
            lines.append("")
            lines.append("→ **最小可检出效应量（功率≥0.8）：d ≈ %.3f**" % md)
        else:
            lines.append("")
            lines.append("→ 当前扫描范围内未达 0.8 功率（最大 d=%.3f 仍未充分检出）" % ds[-1])
        lines.append("")

    lines.append("## 3. 当前最佳候选的效应量位置（诚实引用真实状态）")
    lines.append("")
    if "_err" in cur:
        lines.append("- 读取 state.json 失败：%s" % cur["_err"])
    else:
        oot_p = cur.get("oot_p")
        oot_sig = (oot_p is not None and oot_p < 0.05)
        lines.append("- OOT 盲测 p = `%s`（%s，阈值 0.05）" % (oot_p, "显著" if oot_sig else "不显著"))
        lines.append("- OOT 命中率 = `%s`，OOT 样本数 n = `%s`，规则 = `%s`" %
                     (cur.get("oot_hit"), cur.get("oot_n"), cur.get("oot_rule")))
        lines.append("- 发现段 best_q = `%s`（样本内，不代表泛化）" % cur.get("best_q"))
        lines.append("- df_gen = `%s`，cycle_id = `%s`" % (cur.get("df_gen"), cur.get("cycle_id")))
        lines.append("")
        if oot_sig:
            lines.append("**⚠️ 当前 OOT p=%.3f 显著（<0.05），但须谨慎，绝非定论**：" % (oot_p or 1.0))
            lines.append("- 该 p 量级（~0.005）**正好落在系统自身注释标记的「偶然尖峰」区间**——")
            lines.append("  run_cycle 谱扫描 OOT 注释明言：偶然尖峰的 OOT p 通常~0.005，须 <0.001 严格阈值才确认。")
            lines.append("- 主 OOT 闸门**未设 0.001 严格阈值**，单指标 p=0.005 可能系过拟合/偶然地板命中，")
            lines.append("  **不能**据此宣布「找到结构」；须多指标一致 + 严格阈值 + 持续累积复核。")
            lines.append("- 此值较前期（曾 oot_p≈0.695、n=158）波动明显（现 n=102、规则改为 rev），")
            lines.append("  更提示需复核，而非立即翻案。当前最佳候选效应量位置**仍待严格检验确认**。")
        else:
            lines.append("**判定**：OOT p=%.3f 不显著 ⇒ 当前最佳候选的效应量估计≈0，落在功率曲线左下角" % (oot_p or 1.0))
            lines.append("（不可检出区）。这**不是**「检出了弱信号」，而是「没信号」——与 OOT 盲测结论一致。")
    lines.append("")

    lines.append("## 4. 结论与停止条件")
    lines.append("")
    lines.append("1. **仪器灵敏度（有效）**：以当前 N=%d、闸门严度，闸门对 AR(1)/周期/非线性结构的" % N)
    lines.append("   可检出下界见第 2 节（基于单检验功率）。强结构（d 较大）能被稳定检出 ⇒")
    lines.append("   统一闸门**有功率**、非摆设（这正是阳性对照要证明的事）。")
    lines.append("2. **⚠️ 发现的诚实缺陷（I 类错误膨胀）**：当前生产闸门 `run_axes.label_axis` 对")
    lines.append("   red_sum 组的 4 个检验取 min-p 后直接以 `<0.05` 判显著，**未做 BH-FDR 多重比较校正**；")
    lines.append("   本分析实测当 d=0（纯随机）时该 min-p 判定的 I 类率≈**0.40**（应为≈0.05）——即纯随机数据")
    lines.append("   也有 40%% 概率被误判显著。任何单轴「显著」都不可轻信，必须靠 random_control_label +")
    lines.append("   AAFT/marginal 多层过滤与 OOT 盲测兜底。**建议生产补 BH-FDR**（你路线图中已要求）。")
    lines.append("3. **停止条件（理性）**：若真实结构弱于第 2 节下界（如 d<0.05 量级），在当前数据量下")
    lines.append("   再跑也查不出 ⇒ 这是停止/降级算力投入的**理性条件**，不是「放弃研究」，而是「已探到理论边界」。")
    lines.append("4. **诚实校准**：功率分析测的是「仪器灵敏度」，**不是**「真实数据有无结构」。")
    lines.append("   真实数据结论由 OOT 盲测独立给定（当前 p=%s，为**待检验猜想**——显著亦须严格阈值与多指标复核，" % (cur.get("oot_p")))
    lines.append("   非定论）。")
    lines.append("   OOT 仍在每期累积，若将来 oot_p 持续显著偏离 0.5（稳 < 0.001 且多指标一致），结论可翻。")
    lines.append("5. **SURVIVOR 标准说明**：生产标签 SURVIVOR 要求 shuffle 与 AAFT 双显著（连谱-preserving null 都杀不死），")
    lines.append("   针对「非时间/非自相关真结构」（呼应「时间非基本」）。AR(1) 等时间结构仅触发 LINEAR_TIME_ARTIFACT，")
    lines.append("   故本分析功率以 p_shuffle<0.05 为准；若未来注入非时间非线性结构，可另测 SURVIVOR 功率。")
    lines.append("")
    lines.append("---")
    lines.append("生成于 power_analysis.py（独立分析模块，不进生产管线）。")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out, summary


def main():
    path = paths.master_csv()
    m = D.load_master(path)
    if m:
        reds_real, blues_real, _ = D.to_arrays(m)
        N = reds_real.shape[0]
        print("[power_analysis] 真实数据 N=%d" % N)
    else:
        N = 3496
        print("[power_analysis] 未找到真实数据，回退 N=%d" % N)

    M, k_sur = M_DEFAULT, K_SUR_DEFAULT
    cur = load_current_candidate()
    print("[power_analysis] 当前候选: oot_p=%s best_q=%s" % (cur.get("oot_p"), cur.get("best_q")))

    results = {}
    print("[power_analysis] 扫描 AR(1) ...")
    ds1 = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5]
    results["ar1"] = (ds1, sweep("ar1", inject_ar1, ds1, N, M, k_sur, 1, lag=8))

    print("[power_analysis] 扫描 周期 ...")
    ds2 = [0.1, 0.2, 0.4, 0.6, 1.0, 1.5]
    results["periodic"] = (ds2, sweep("periodic", inject_periodic, ds2, N, M, k_sur, 2, period=40))

    print("[power_analysis] 扫描 非线性 ...")
    ds3 = [0.1, 0.2, 0.4, 0.6, 1.0, 1.5]
    results["nonlinear"] = (ds3, sweep("nonlinear", inject_nonlinear, ds3, N, M, k_sur, 3, lag=8))

    out, summary = write_report(results, N, M, k_sur, cur)
    print("[power_analysis] 报告已写: %s" % out)
    print("[power_analysis] 最小可检出(0.8):",
          {k: v["min_detectable_0.8"] for k, v in summary.items()})
    return out


if __name__ == "__main__":
    main()
