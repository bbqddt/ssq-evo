# -*- coding: utf-8 -*-
"""
run_axes.py —— 轴驱动器 + 证据账本（A+B 收口到实践闸门）
=====================================================
对每个轴通道跑现有 engine_core 检验，并按分层 null 打标签：

  SURVIVOR            : 真集在「shuffle（摧毁时间序）」与「AAFT（保留频谱/自相关）」两套
                       零假设下都显著(p<0.05) => 结构不依赖时间序/自相关，呼应「时间非基本」，
                       是唯一值得追的候选（连谱-preserving null 都杀不死）。
  LINEAR_TIME_ARTIFACT: shuffle 下显著、AAFT 下消失(p_aaft>=0.05)
                       => 结构只是线性自相关/时间依赖，被严格 null 正确识别为「时间在做功」
                       （暴露 temptation：看似结构，实则序列自身的自相关）。
  NULL                : 两套 null 下都不显著。

并附加 subset_marginal（摧毁每期组合结构）零假设 p_marg：
  - SURVIVOR + composition_robust : 连组合重抽都杀不死 => 最强候选。
  - SURVIVOR + composition_linked : 组合重抽后消失 => 结构偏每期组合属性。

所有结果写入不可变证据账本 evidence_ledger.json（追加，保留历史），
逼近「全域 null」边界——这正是「把错误方向缩小」的落地。

公式轴(comp)由 diff_formula 进化后同样过分层 null，把用户寄望的「公式的进化」
纳入同一诚实框架，不另立标准。
"""
import os
import json
from datetime import datetime, timezone

import numpy as np
import engine_core as E
import evaluator as EV  # noqa: F401  (确认闸门在 diff_formula 内部复用)
import diff_formula as DF
import representation_zoo as RZ
import layered_null as LN

RZ.register()

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence_ledger.json")


def _axis_p(sig, reds, blues, test, rng, k_sur, params, sur_type):
    """在该数据集/该 surrogate 类型下构造 x 并算 p_raw；失败返回 None。"""
    try:
        x = E._build_x(sig, reds, blues, params)
    except Exception:
        return None
    if x is None or x.shape[0] < 8:
        return None
    ev = E.evaluate_x(x, test, rng, k_sur, sur_type=sur_type,
                      test_params=(params or {}).get("_test") if params else None)
    return ev["p_raw"] if ev else None


def _min_p(sig, tests, reds, blues, rng, k_sur, params, sur_type):
    ps = [_axis_p(sig, reds, blues, t, rng, k_sur, params, sur_type) for t in tests]
    ps = [p for p in ps if p is not None]
    return min(ps) if ps else None


def proper_random(N, rng):
    """正确模拟双色球：每期 6 个互异红球(1..33)排序 + 1 蓝球(1..16)。
    用于「随机数据对照闸门」——检验某轴的显著是否由信号构造本身产生（而非真实结构）。"""
    r = np.zeros((N, 6), dtype=int)
    for i in range(N):
        r[i] = np.sort(rng.choice(np.arange(1, 34), size=6, replace=False))
    b = rng.integers(1, 17, size=N)
    return r, b


def random_control_label(sig, tests, N, seed, k_sur=60):
    """对该轴在「纯随机数据」上跑分层标签。返回 label。
    若随机数据也 SURVIVOR => 该轴的显著是构造伪结构（如 recurrence_mean 的 N 惩罚尖峰），
    必须在发现阶段降级，绝不计入真实候选。这是「连自己的信号构造都当嫌疑」的自动落地。"""
    rng = np.random.default_rng(seed)
    rr, bb = proper_random(N, rng)
    rec = label_axis(sig, tests, rr, bb, rng, k_sur)
    return rec["label"]


def label_axis(sig, tests, reds, blues, rng, k_sur=40, params=None):
    """对一个轴（取多检验最小 p 最显著）做分层 null 标签。

    shuffle = 摧毁时间序的硬零假设；AAFT = 保留频谱/自相关的零假设。
    """
    p_shuffle = _min_p(sig, tests, reds, blues, rng, k_sur, params, "shuffle")   # 摧毁时间序
    p_aaft = _min_p(sig, tests, reds, blues, rng, k_sur, params, "aaft")         # 保留频谱/自相关
    r_m, b_m = LN.subset_marginal(reds, blues, rng)                              # 摧毁组合结构
    p_marg = _min_p(sig, tests, r_m, b_m, rng, k_sur, params, "shuffle")
    if p_shuffle is None:
        return {"sig": sig, "p_shuffle": None, "p_aaft": p_aaft, "p_marg": p_marg, "label": "NULL"}
    if p_shuffle < 0.05:
        if p_aaft is not None and p_aaft < 0.05:
            # 连谱-preserving null 都杀不死 => 非时间/非自相关结构（呼应「时间非基本」）
            flag = "composition_robust" if (p_marg is not None and p_marg < 0.05) else "composition_linked"
            return {"sig": sig, "p_shuffle": p_shuffle, "p_aaft": p_aaft, "p_marg": p_marg,
                    "label": "SURVIVOR", "flag": flag}
        # 仅 shuffle 下显著、AAFT 下消失 => 纯线性自相关（时间在做功）
        return {"sig": sig, "p_shuffle": p_shuffle, "p_aaft": p_aaft, "p_marg": p_marg,
                "label": "LINEAR_TIME_ARTIFACT"}
    return {"sig": sig, "p_shuffle": p_shuffle, "p_aaft": p_aaft, "p_marg": p_marg, "label": "NULL"}


def run(reds, blues, seed=20260815, k_sur=40):
    """扫描所有轴 + 公式轴，返回证据记录列表。

    每轴附带「随机数据对照闸门」：若同轴在纯随机数据上仍 SURVIVOR，
    则该轴显著系信号构造本身所致（构造伪结构），降级为 ARTIFACT_BY_CONSTRUCTION，
    绝不计入真实候选。这把「连自己的信号构造都当嫌疑对象」写进例行流水线。
    """
    rng = np.random.default_rng(seed)
    N = reds.shape[0]
    recs = []
    for ax in RZ.AXES:
        if ax["sig"] not in E.SIGMAPS:
            continue
        rec = label_axis(ax["sig"], ax["tests"], reds, blues, rng, k_sur)
        rec["group"] = ax["group"]
        rec["note"] = ax["note"]
        # —— 随机数据对照闸门（构造伪结构拦截）——
        ctrl = random_control_label(ax["sig"], ax["tests"], N, seed=seed, k_sur=60)
        if ctrl == "SURVIVOR":
            rec["artifact_prone"] = True
            rec["label"] = "ARTIFACT_BY_CONSTRUCTION"
            rec["note"] += " [随机对照闸门: 纯随机也SURVIVOR => 构造伪结构, 已降级]"
        recs.append(rec)
    # 公式轴（用户寄望的公式进化）：diff_formula 进化候选，取最优基因组过分层 null
    try:
        res = DF.run_diff_search(reds, blues, rng, n_candidates=4, confirm=False,
                                 discovery_frac=0.7, k_sur_opt=k_sur, n_steps=8)
        if res:
            best = min(res, key=lambda r: (r["disc_p"] if r["disc_p"] is not None else 1.0))
            lr = label_axis(best["sig"], [best["test"]], reds, blues, rng, k_sur, params=best["params"])
            lr["group"] = "formula(comp)"
            lr["note"] = "可微 Formula 进化候选（公式轴）"
            lr["disc_p"] = best["disc_p"]
            recs.append(lr)
    except Exception as e:  # 公式轴失败不应拖垮整轮
        recs.append({"group": "formula(comp)", "sig": None, "label": "ERROR",
                     "note": "diff_formula 失败: %s" % e})
    return recs


def _now():
    return datetime.now(timezone.utc).isoformat()


def write_ledger(recs, path=LEDGER):
    """追加写入不可变证据账本（保留历史，逼近全域 null 边界）。"""
    entry = {"ts": _now(), "recs": recs}
    hist = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    hist.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    return entry


def _print_table(recs):
    print("\n%-18s %-16s %-10s %-9s %-9s %-9s %s" %
          ("group", "sig", "label", "p_shuf", "p_aaft", "p_marg", "note"))
    print("-" * 110)
    for r in recs:
        ps = r.get("p_shuffle"); pa = r.get("p_aaft"); pm = r.get("p_marg")
        fmt = lambda v: ("%.4g" % v) if isinstance(v, float) else "-"
        extra = ("[" + r.get("flag", "") + "]") if r.get("flag") else ""
        print("%-18s %-16s %-10s %-9s %-9s %-9s %s" %
              (r.get("group", "-"), str(r.get("sig", "-")), r.get("label", "-"),
               fmt(ps), fmt(pa), fmt(pm), r.get("note", "") + extra))


def main():
    import data as D
    path = "D:/ssq_evo_data/ssq_master.csv"
    m = D.load_master(path)
    if not m:
        N = 2000
        rng0 = np.random.default_rng(0)
        reds = np.sort(rng0.integers(1, 34, size=(N, 6)), axis=1)
        blues = rng0.integers(1, 17, size=(N,))
        print("[run_axes] 未找到真实数据，使用合成 null 数据演示")
    else:
        reds, blues, _ = D.to_arrays(m)
        print("[run_axes] 载入真实数据 %d 期" % len(reds))
    recs = run(reds, blues, k_sur=40)
    write_ledger(recs)
    _print_table(recs)
    n_surv = sum(1 for r in recs if r.get("label") == "SURVIVOR")
    n_time = sum(1 for r in recs if r.get("label") == "LINEAR_TIME_ARTIFACT")
    n_art = sum(1 for r in recs if r.get("label") == "ARTIFACT_BY_CONSTRUCTION")
    print("\n汇总: SURVIVOR=%d  LINEAR_TIME_ARTIFACT=%d  ARTIFACT_BY_CONSTRUCTION=%d  (证据已写入 %s)"
          % (n_surv, n_time, n_art, LEDGER))
    return recs


if __name__ == "__main__":
    main()
