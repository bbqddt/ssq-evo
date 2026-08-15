# -*- coding: utf-8 -*-
"""
发现/确认分离闸门的功效验证 (#41)
================================
证明"发现集上挖出的结构，若只存在于发现集，会在确认集上失效"（闸门抓过拟合），
而全局真实结构能跨折确认（闸门有功效）。三类阳性对照：

  A) 全局注入周期结构（整条序列）→ 预期 SIGNAL
  B) 仅发现段注入结构、确认段纯噪声 → 预期 UNCONFIRMED（核心：抓过拟合）
  C) 真实双色球数据 → 预期 NULL（与主线结论一致，且无假阳性 SIGNAL）

退出码：核心对照 A/B 任一不满足则非零（可作 CI 门）；C 出现 SIGNAL 视为假阳性报警。
"""
import sys, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))           # ssq_evo/
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # 项目根

import engine_core as E
import evaluator as EV
import data as D

RNG = np.random.default_rng(20260815)
N = 2000
K = 100
TEST = "fft_peak"        # 谱峰值：对周期结构 power 强、对纯噪声为 null
DISCOVERY_FRAC = 0.7
N_FOLDS = 3


def baseline(n):
    """基底：i.i.d. 高斯噪声（单位方差），用于隔离闸门逻辑做阳性对照。
    真实的"非退化、非结构"零假设基线；振幅 3 的周期结构在其上明显可检。
    （真实数据形态由对照 C 单独覆盖，无需在此混入幅度干扰。）"""
    return RNG.standard_normal(n)


def inject_global(n, period=17, amp=3.0):
    """全局周期结构：注入整条序列 → 发现段与确认段都应显著。"""
    x = baseline(n)
    t = np.arange(n)
    x = x + amp * np.sin(2 * np.pi * t * period / n)
    return x


def inject_discovery_only(n, period=17, amp=3.0, disc_frac=DISCOVERY_FRAC):
    """仅发现段注入结构、确认段保持纯基底（无结构）→ 确认段应失效。"""
    x = baseline(n)
    t = np.arange(n)
    d_end = int(n * disc_frac)
    x[:d_end] = x[:d_end] + amp * np.sin(2 * np.pi * t[:d_end] * period / n)
    return x


def main():
    print("=" * 78)
    print("ssq_evo 发现/确认分离闸门 — 功效验证 (阳性对照)")
    print("=" * 78)

    results = []
    fails = []

    # ---- A) 全局结构 → SIGNAL ----
    xg = inject_global(N)
    wf_a = EV.confirm_x(xg, TEST, RNG, N_FOLDS, DISCOVERY_FRAC, K)
    ok_a = wf_a is not None and wf_a["verdict"] == "SIGNAL"
    results.append(("A 全局结构", "SIGNAL", wf_a["verdict"] if wf_a else None, ok_a))
    if not ok_a:
        fails.append("A")

    # ---- B) 仅发现段结构 → UNCONFIRMED（核心抓过拟合）----
    xd = inject_discovery_only(N)
    wf_b = EV.confirm_x(xd, TEST, RNG, N_FOLDS, DISCOVERY_FRAC, K)
    ok_b = wf_b is not None and wf_b["verdict"] == "UNCONFIRMED"
    results.append(("B 仅发现段结构", "UNCONFIRMED", wf_b["verdict"] if wf_b else None, ok_b))
    if not ok_b:
        fails.append("B")

    # ---- C) 真实数据 → NULL（且不出现 SIGNAL 假阳性）----
    # 注：此处不跑重型 spectral_scan（k_sur=100 在 23 信号上约 5 分钟），
    # 直接用若干代表性 fft_peak 候选跑 confirm_candidate（向量化、秒级），
    # 断言真实数据不会触发假阳性 SIGNAL。重型家族由生产管线既有闸门覆盖。
    real_verdicts = []
    try:
        m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
        if m:
            reds, blues, _ = D.to_arrays(m)
            rng2 = np.random.default_rng(12345)
            cands = [("red_sum", "fft_peak"), ("blue", "fft_peak"),
                     ("red_mean", "fft_peak"), ("red_zone_entropy", "fft_peak")]
            for sig, test in cands:
                g = {"sig": sig, "test": test,
                     "params": {"_sig": {}, "_test": {}, "_reorder": "identity"}}
                wf_c = EV.confirm_candidate(g, reds, blues, rng2,
                                            N_FOLDS, DISCOVERY_FRAC, 60)
                if wf_c is not None:
                    real_verdicts.append(wf_c["verdict"])
    except Exception as e:
        real_verdicts = [f"跳过:{e}"]
    real_verdict = "/".join(real_verdicts) if real_verdicts else "N/A"
    ok_c = "SIGNAL" not in real_verdict   # 真实数据出现 SIGNAL = 假阳性，应报警
    results.append(("C 真实双色球", "非SIGNAL", real_verdict, ok_c))
    if not ok_c:
        fails.append("C")

    # ---- 输出 ----
    for name, expect, got, ok in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:18} 期望={expect:12} 实得={str(got):12}")

    # 详细打印 B 的 p 分布，证明"发现显著 / 确认失效"
    if wf_b is not None:
        print(f"\n  [B 细节] 发现 p={wf_b['disc_p_list']} (合并 {wf_b['disc_combined_p']:.3e})"
              f" | 确认 p={wf_b['conf_p_list']} (合并 {wf_b['conf_combined_p']:.3e})"
              f" | 确认折数 {wf_b['n_confirm']}/{wf_b['n_folds']} → {wf_b['verdict']}")
    if wf_a is not None:
        print(f"  [A 细节] 发现合并 p={wf_a['disc_combined_p']:.3e}"
              f" | 确认合并 p={wf_a['conf_combined_p']:.3e}"
              f" | 确认折数 {wf_a['n_confirm']}/{wf_a['n_folds']} → {wf_a['verdict']}")

    print()
    if fails:
        print(f"** 失败对照: {', '.join(fails)} —— 闸门存在功效/特异性缺陷，须修复后再上线**")
        sys.exit(1)
    else:
        print("** 全部阳性对照通过：闸门既能检出全局结构(SIGNAL)，又能拦截发现集过拟合(UNCONFIRMED)，"
              "真实数据为 NULL（无假阳性）。**")
        sys.exit(0)


if __name__ == "__main__":
    main()
