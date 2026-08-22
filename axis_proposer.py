# -*- coding: utf-8 -*-
"""
axis_proposer.py —— 学习模块 L2：原语扩张器（最小版，扩「基信号」这一类归纳偏置）
================================================================================

定位（呼应 2026-08-22 用户论断：学习要「改良/扩张假设空间」，不止在旧零件上玩）：
  搜索（GA/谱/因果）只在 SIGMAPS 27 个写死基信号的可组合闭包里找最优——若真结构不在
  这个闭包里，搜穿了也搜不出。原语扩张器让系统**从数据出发提议 27 个之外的新基信号**，
  在 discovery 段复用驾1 引擎（run_axes.label_axis：shuffle+AAFT+subset_marginal 三零假设）
  严格验证，显著才进待复核池，**绝不自动进生产 SIGMAPS**。

设计原则（契约基石，不可绕过）：
  - 基石一：discovery 段只用反过拟合信号（oot_blind_p/wf_verdict/random_control_label/
    zero_hypothesis_cross）；绝不看 in_sample_accuracy 等 FORBIDDEN。
  - 基石二：复用驾1 引擎做验证（learning_contract.assert_three_car_closure 校验：
           输入来自 car1_gate_state 复用、产出回馈 sigmap_injection 经待复核）。
  - 基石三：discovery 段说显著 ≠ 生产确认；回馈 SIGMAPS 后驾1/驾3 首次用到须再过 #41。
  - 基石四：任何通过 discovery 的新原语默认 stage_for_human_review，绝不自动 merge。

本文件职责边界（最小版）：
  - 只做「基信号」这一类原语扩张（扩展点已留：operator_zoo/test_zoo/representation_switch
    后续可同接口接入）。
  - 提议器是**确定性变换族**（从残差/排序/交互派生），不接 LLM（LLMProposer 仍是占位桩）。
  - 提议 → discovery 验证 → gate_absorb 准入 → stage_for_human_review。
  - 绝不在此 inject 进 E.SIGMAPS（那是 L4 人类复核后的动作）。

零重型依赖：numpy + engine_core(仅 _build_x 复用) + run_axes(验证) + learning_contract。
"""
import os
import json
import datetime
import numpy as np

import engine_core as E
import run_axes as RA
import learning_contract as LC


DATA_DIR = os.environ.get("DATA_DIR", "D:/ssq_evo_data")


# ---------------------------------------------------------------------------
# 提议器：确定性变换族（从残差/排序/交互派生 27 个之外的新基信号）
# 每个提议器返回 (name, fn)；fn(reds, blues) -> 1D float array (len N)
# 命名前缀 lp_ 以便与既有 SIGMAPS 区分，避免撞键。
# ---------------------------------------------------------------------------
def lp_red_gap_skew(reds, blues):
    """组合轴：排序后相邻球间隔的偏度（非线性间隔结构）。"""
    s = np.sort(reds, axis=1).astype(float)
    diffs = np.diff(s, axis=1)
    mean = diffs.mean(axis=1, keepdims=True)
    std = diffs.std(axis=1, keepdims=True) + 1e-9
    z = (diffs - mean) / std
    return (np.mean(z ** 3, axis=1)).astype(float)


def lp_red_centroid_velocity(reds, blues):
    """时间轴：球号质心相邻期变化率（非平稳/漂移信号）。"""
    c = reds.mean(axis=1).astype(float)
    return np.concatenate([[0.0], np.diff(c)]).astype(float)


def lp_red_pair_corr_max(reds, blues):
    """组合轴：任意两两球号差的模 33 分布峰值（乘法-间隔混合结构）。"""
    s = np.sort(reds, axis=1).astype(float)
    out = np.zeros(reds.shape[0])
    for j in range(5):
        d = np.abs(s[:, j + 1] - s[:, j])
        out += d
    return (out % 33).astype(float)


def lp_blue_resid_autocorr(reds, blues):
    """时间轴：蓝球相对红球的残差自相关（球间耦合的时间结构）。"""
    br = blues.astype(float)
    rr = reds.mean(axis=1).astype(float)
    resid = br - (rr / 33.0)
    resid = resid - resid.mean()
    if len(resid) < 3:
        return resid
    lag1 = resid[1:] * resid[:-1]
    return np.concatenate([[0.0], lag1]).astype(float)


def lp_red_entropy_rate(reds, blues):
    """信息轴：排序后球号序列的符号熵率（不可约信息量，捕捉非随机有序性）。"""
    s = np.sort(reds, axis=1)
    # 把每期 6 球当作 6 个符号，统计相邻期符号差的分布熵
    diff = np.diff(s.astype(float), axis=1).mean(axis=1)
    # 分箱到 10 个等级
    bins = np.linspace(-16, 16, 11)
    digit = np.digitize(diff, bins)
    counts = np.bincount(digit, minlength=len(bins) + 1).astype(float)
    p = counts / (counts.sum() + 1e-9)
    p = p[p > 0]
    ent = -(p * np.log(p)).sum()
    return (diff - diff.mean()).astype(float) * (ent + 1e-9)


# 提议器注册表（扩展点：后续可加 operator_zoo/test_zoo/representation_switch）
PROPOSERS = {
    "lp_red_gap_skew": lp_red_gap_skew,
    "lp_red_centroid_velocity": lp_red_centroid_velocity,
    "lp_red_pair_corr_max": lp_red_pair_corr_max,
    "lp_blue_resid_autocorr": lp_blue_resid_autocorr,
    "lp_red_entropy_rate": lp_red_entropy_rate,
}


# ---------------------------------------------------------------------------
# discovery 段验证（复用驾1 引擎，契约基石二）
# ---------------------------------------------------------------------------
def _discovery_validate(sig_name, fn, reds, blues, rng, k_sur):
    """对一个提议的新基信号在 discovery 段复用驾1 引擎验证。

    返回 dict：含 label / p_shuffle / p_aaft / p_marg / artifact_prone / used_feedback。
    只基于白名单反馈信号（shuffle/aaft/marg 三零假设 = zero_hypothesis_cross 的落地）。
    """
    # 临时注入 SIGMAPS 仅供本次验证（不污染生产；验证完即撤）
    E.SIGMAPS[sig_name] = fn
    try:
        tests = ["acf_max", "perm_entropy", "mi_max"]
        rec = RA.label_axis(sig_name, tests, reds, blues, rng, k_sur)
        N = int(reds.shape[0])
        ctrl = RA.random_control_label(sig_name, tests, N, seed=20260820, k_sur=k_sur)
        artifact = (ctrl == "SURVIVOR")
        rec["artifact_prone"] = artifact
        rec["used_feedback"] = [
            "zero_hypothesis_cross",  # shuffle+aaft+marg 三零假设一致显著（label==SURVIVOR 落地）
            "random_control_label",   # 构造伪结构拦截
            "bh_fdr_q",
        ]
    finally:
        # 撤出：绝不在此污染生产 SIGMAPS（基石四：merge 只在 L4 人类复核后）
        E.SIGMAPS.pop(sig_name, None)
    return rec


def propose_and_validate(reds, blues, rng, k_sur=40, data_dir=DATA_DIR):
    """L2 主流程：遍历提议器 → discovery 验证 → gate_absorb 准入 → 显著才 stage_for_human。

    满足契约：声明输入来自 car1（复用驾1 引擎验证），产出回馈 sigmap_injection（经待复核）。
    返回 (staged: list, report: dict)。
    """
    # 契约闭环声明（基石二）
    op = {
        "kind": "propose",
        "input_sources": ["car1_gate_state"],  # 复用驾1 引擎 label_axis/random_control_label
        "output_sinks": ["sigmap_injection"],  # 经 stage_for_human_review，非自动 merge
        "used_feedback": ["zero_hypothesis_cross", "random_control_label", "bh_fdr_q"],
    }
    closure_ok, closure_reasons = LC.assert_three_car_closure(op)
    if not closure_ok:
        raise LC.ClosureViolation("L2 原语扩张器闭环约束违反: " + "; ".join(closure_reasons))

    staged = []
    report = {"proposed": 0, "survived_discovery": 0, "staged_for_review": 0,
              "artifact_blocked": 0, "details": []}
    N = int(reds.shape[0])

    for name, fn in PROPOSERS.items():
        report["proposed"] += 1
        try:
            rec = _discovery_validate(name, fn, reds, blues, rng, k_sur)
        except Exception as e:
            report["details"].append({"name": name, "error": str(e), "label": "ERROR"})
            continue

        label = rec.get("label")
        artifact = rec.get("artifact_prone")
        detail = {"name": name, "label": label, "artifact": bool(artifact),
                  "p_shuffle": rec.get("p_shuffle"), "p_aaft": rec.get("p_aaft")}
        report["details"].append(detail)

        # —— gate_absorb 准入闸门（基石一+三）——
        discovery_evidence = {
            "used_feedback": rec.get("used_feedback", []),
            "zero_hypothesis_cross": (label == "SURVIVOR"),  # 三零假设一致显著
            "random_control_label": ("ARTIFACT_BY_CONSTRUCTION" if artifact
                                     else "CLEAN"),
        }
        allowed, reason = LC.gate_absorb(
            {"name": name, "learned": True, "human_confirmed": False},
            discovery_evidence,
        )
        if not allowed:
            if artifact:
                report["artifact_blocked"] += 1
            report["details"][-1]["gate"] = "REJECTED: " + reason
            continue

        report["survived_discovery"] += 1
        # —— 基石四：stage_for_human_review，绝不自动 merge ——
        path = LC.stage_for_human_review(
            {"name": name, "learned": True, "human_confirmed": False,
             "kind": "base_signal", "fn_module": "axis_proposer"},
            data_dir,
            feedback_evidence=discovery_evidence,
        )
        report["staged_for_review"] += 1
        report["details"][-1]["gate"] = "STAGED_FOR_REVIEW"
        report["details"][-1]["pending_path"] = path

    return staged, report


def run(reds, blues, seed=20260820, k_sur=40, data_dir=DATA_DIR):
    """CLI 入口：给定真实数据跑 L2 提议+验证+待复核。"""
    rng = np.random.default_rng(seed)
    staged, report = propose_and_validate(reds, blues, rng, k_sur=k_sur, data_dir=data_dir)
    return report


def selfcheck():
    """自检：用随机数据验证「提议器能在随机数据上不被误判为结构」（防假阳性零件）。"""
    ok = True
    msgs = []
    rng = np.random.default_rng(12345)
    N = 300
    reds = np.sort(rng.choice(np.arange(1, 34), size=(N, 6), replace=True), axis=1).astype(int)
    # 纠正：proper_random 保证互异
    reds = np.zeros((N, 6), dtype=int)
    for i in range(N):
        reds[i] = np.sort(rng.choice(np.arange(1, 34), size=6, replace=False))
    blues = rng.integers(1, 17, size=N)
    try:
        _staged, report = propose_and_validate(reds, blues, rng, k_sur=20, data_dir=".")
        msgs.append("L2 自检提议=%d 存活discovery=%d 待复核=%d 伪结构拦截=%d"
                    % (report["proposed"], report["survived_discovery"],
                       report["staged_for_review"], report["artifact_blocked"]))
        # 在纯随机数据上，合理的预期是：存活discovery 极少（应≈0），伪结构拦截可能触发
        if report["survived_discovery"] > 1:
            ok = False
            msgs.append("L2 自检警告：随机数据上存活>1，提议器可能过松")
    except Exception as e:
        ok = False
        msgs.append("L2 selfcheck 异常: %s" % e)
    return ok, msgs


if __name__ == "__main__":
    ok, msgs = selfcheck()
    for m in msgs:
        print(m)
    print("OK" if ok else "FAIL")
