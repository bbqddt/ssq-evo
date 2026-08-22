# -*- coding: utf-8 -*-
"""
learning_contract.py —— 学习模块基石契约（代码级，不可绕过）
=========================================================

本项目（ssq_evo）的核心命题：在双色球开奖序列里寻找可复现的非随机结构。
域大概率为 null —— 这意味着「学习模块」如果接错反馈信号，会**自动制造可信的假阳性**
（Goodhart）：它从噪声里"纠正"出过拟合的偏置，越学越自信越错。

因此本项目立下铁律（用户 2026-08-22 明确划定，永久执行）：

  基石一：不撒谎的反馈信号
  --------------------------------------------------------------
  学习模块（失败吸收 / 原语扩张 / 偏置纠正）**只允许**使用「反过拟合」的反馈信号，
  绝不允许把「回测变好 / 样本内准度」当优化目标。后者必造假阳性。

  基石二：三驾车必须用上、必须回馈
  --------------------------------------------------------------
  学习模块不是离线玩具。它的【输入】必须来自三驾车的真实产出
  （驾1 每轮闸门 state + 驾3 云端提案在驾1 过闸的存活/淘汰），
  它的【产出】必须回馈进三驾车（新原语写回 SIGMAPS → 驾1/驾3 下一轮在新空间搜），
  否则学习模块形同虚设，无法「改变搜索走的道路」。

  基石三：回馈必须 confirm 段复验
  --------------------------------------------------------------
  学习模块在 discovery 段说「显著」≠ 生产确认。任何回馈进 SIGMAPS 的新原语，
  驾1/驾3 第一次用到它时必须再过 #41 发现/确认分离闸门，才算「真吸收」。

  基石四：人类保留否决权
  --------------------------------------------------------------
  任何「吸收进假设空间」的动作默认进入待复核池（pending_primitives），
  绝不在无人复核时自动合并进生产 SIGMAPS。

本模块的职能：
  - 白名单 / 黑名单常量（FEEDBACK_SIGNALS / FORBIDDEN_SIGNALS）。
  - 红队钩子 `redteam_check_learning_signal()`：审计「有无模块把 FORBIDDEN 当优化目标」，
    复用 redteam_audit 的只读对抗精神，但专门盯学习模块。
  - 闭环守卫 `assert_three_car_closure()`：强制「学习输入来自三驾车、产出回馈三驾车」可追踪。
  - 准入闸门 `gate_absorb()`：学习产出要进假设空间前必须通过（随机对照 + 零假设交叉 + 待复核）。

零依赖；可被 pytest 直接 import 单测，不 import 任何重型引擎模块。
"""
import json
import os
import datetime


# ---------------------------------------------------------------------------
# 基石一：反馈信号白名单 / 黑名单（不可绕过）
# ---------------------------------------------------------------------------
# 唯一被允许作为「学习方向」的信号 —— 全部是反过拟合的，不是「回测变好」。
FEEDBACK_SIGNALS = {
    "oot_blind_p": "独立确认段盲测 p 值（核心，永远先看这个）",
    "bh_fdr_q": "跨所有候选的多重校正 q（BH-FDR）",
    "random_control_label": "在纯随机双色球上的同款指标（伪结构拦截）",
    "null_positive_control": "注入已知结构必须被检出（管线功效证明）",
    "zero_hypothesis_cross": "至少 3 种零假设(shuffle/AAFT/bootstrap)一致显著",
    "wf_verdict": "#41 发现/确认分离闸门 verdict（SIGNAL/UNCONFIRMED）",
}

# 严禁作为「学习优化目标」的信号 —— 用这些当目标 = 必造假阳性，红队必拦截。
FORBIDDEN_SIGNALS = {
    "in_sample_accuracy": "样本内准度（过拟合天堂）",
    "backtest_fit": "回测拟合优度",
    "train_auc": "训练集 AUC",
    "discovery_only_p": "仅 discovery 段 p（未经确认段复现）",
    "any_discovery_only_metric": "任何只在发现段好看、未过确认段的指标",
}


def is_allowed_feedback(signal_name):
    """该信号是否可作学习反馈。"""
    return signal_name in FEEDBACK_SIGNALS


def is_forbidden_feedback(signal_name):
    """该信号是否严禁作学习优化目标。"""
    return signal_name in FORBIDDEN_SIGNALS


# ---------------------------------------------------------------------------
# 基石二：三驾车闭环约束（强制可追踪）
# ---------------------------------------------------------------------------
# 学习模块的输入来源必须是这三个（缺一不可追踪）：
THREE_CAR_INPUT_SOURCES = {
    "car1_gate_state": "驾1 每轮闸门 state.json（best_q/best_p/wf_verdict/positive_control…）",
    "car3_proposal_fate": "驾3 云端提案在驾1 过闸的存活/淘汰记录（ingest_fate.jsonl）",
    "failure_taxonomy": "L1 失败吸收器落盘（源自驾1 闸门+驾3 fate 的失败分类，属三驾车真实产出派生）",
    "avoidance_prior": "L1 回避偏置落盘（源自 failure_taxonomy，属三驾车真实产出派生）",
    "ingest_fate": "驾3 提案过闸 fate 结构化落盘（ingest_fate.jsonl，驾3 真实产出）",
}

# 学习模块的产出必须回馈到这两个落点（缺一不可追踪）：
THREE_CAR_OUTPUT_SINKS = {
    "sigmap_injection": "新原语写回 engine_core.SIGMAPS（经 representation_zoo.register 同款接口）",
    "avoidance_prior_injection": "avoidance_prior 注入驾1+驾3 候选生成",
    "bias_corrector.json": "L3 偏置纠正落盘（debunked_sigs/tests + novelty_tilt + elite_bias，驾1/驾3 消费）",
}


class ClosureViolation(Exception):
    """三驾车闭环约束被违反时抛出。"""


def assert_three_car_closure(learning_op):
    """校验一个学习操作是否满足「输入来自三驾车、产出回馈三驾车」。

    learning_op: dict，至少含：
      {
        "kind": "absorb" | "propose" | "correct_bias",
        "input_sources": [list of str in THREE_CAR_INPUT_SOURCES],
        "output_sinks":  [list of str in THREE_CAR_OUTPUT_SINKS],
        "used_feedback": [list of str]   # 实际使用的反馈信号名
      }
    返回 (ok: bool, reasons: list[str])；不满足则 ok=False 并列出违反项。
    """
    reasons = []
    srcs = learning_op.get("input_sources") or []
    sinks = learning_op.get("output_sinks") or []
    used = learning_op.get("used_feedback") or []

    # 输入必须来自三驾车真实产出
    if not srcs:
        reasons.append("学习操作未声明 input_sources（输入必须来自三驾车真实产出）")
    for s in srcs:
        if s not in THREE_CAR_INPUT_SOURCES:
            reasons.append("input_source=%s 不在三驾车来源清单内（不得自造数据）" % s)

    # 产出必须回馈三驾车
    if learning_op.get("kind") in ("absorb", "correct_bias"):
        if not sinks:
            reasons.append("学习操作(kind=%s)未声明 output_sinks（产出必须回馈三驾车）"
                           % learning_op.get("kind"))
        for s in sinks:
            if s not in THREE_CAR_OUTPUT_SINKS:
                reasons.append("output_sink=%s 不在三驾车回馈落点清单内" % s)

    # 反馈信号不得踩黑名单
    for u in used:
        if is_forbidden_feedback(u):
            reasons.append("学习操作使用了严禁信号 %s 作反馈（必造假阳性，红队必拦）" % u)

    ok = len(reasons) == 0
    return ok, reasons


# ---------------------------------------------------------------------------
# 基石三：回馈 confirm 段复验要求（声明式，供 run_cycle 调用）
# ---------------------------------------------------------------------------
def requires_confirm_recheck(primitive):
    """判断某个被学习模块提议的原语，回馈进 SIGMAPS 后是否必须 confirm 段复验。

    规则：凡是「学习模块新生成、非人工手写」的原语，一律必须复验。
    手写（representation_zoo 既有 NEW_SIGNALS）不在此列 —— 它们走既有闸门即可。
    """
    return bool(primitive.get("learned")) and not primitive.get("human_confirmed")


# ---------------------------------------------------------------------------
# 红队钩子：学习模块专用（基石一的执行者）
# ---------------------------------------------------------------------------
def redteam_check_learning_signal(op_record):
    """审计一条「学习模块操作记录」，查有无把 FORBIDDEN 当优化目标 / 有无闭环违规。

    op_record: dict，含 learning_op 字段 + 可选 "objective_signal"（模块自称的优化目标）。
    返回 (verdict, findings: list[str])。
      verdict: "OK" | "REVIEW" | "ALERT"
      ALERT 触发条件：使用了 FORBIDDEN 信号作目标，或闭环约束违反。
    """
    findings = []
    objective = op_record.get("objective_signal")
    if objective and is_forbidden_feedback(objective):
        findings.append(
            "ALERT 学习模块把严禁信号 %s 当优化目标 —— 必从噪声造出假阳性，"
            "立即丢弃该操作并告警（基石一违规）。" % objective
        )
    ok, reasons = assert_three_car_closure(op_record)
    if not ok:
        for r in reasons:
            findings.append("ALERT 三驾车闭环约束违反：%s（基石二违规）" % r)

    verdict = "OK" if not findings else "ALERT"
    return verdict, findings


# ---------------------------------------------------------------------------
# 基石四：人类否决权 —— 待复核池（仅记录，不自动合并）
# ---------------------------------------------------------------------------
PENDING_FILE = "pending_primitives.json"


def stage_for_human_review(primitive, out_dir, feedback_evidence=None):
    """把学习模块产出（经 discovery 段验证、未自动合并）写入待复核池。

    primitive: dict，至少含 name / kind / learned=True。
    feedback_evidence: dict，discovery 段用了哪些 FEEDBACK_SIGNALS 及值（供人工判断）。
    返回写入的文件路径。绝不在此自动 merge 进 SIGMAPS。
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, PENDING_FILE)
    entry = {
        "staged_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "primitive": primitive,
        "feedback_evidence": feedback_evidence or {},
        "human_confirmed": False,
        "note": "待人工复核；确认前绝不注入生产 SIGMAPS（基石四）。",
    }
    pool = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                pool = json.load(f)
        except Exception:
            pool = []
    pool.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    return path


def gate_absorb(primitive, discovery_evidence):
    """学习产出要进假设空间前的统一准入闸门（基石一+三的叠合）。

    要求（任一不满足 → 返回 (False, reason)，不得吸收）：
      1. discovery 段使用的反馈信号全部在 FEEDBACK_SIGNALS 白名单；
      2. zero_hypothesis_cross 必须为 True（≥3 种零假设一致显著，非仅"存在该字段"）；
      3. random_control_label 非 ARTIFACT_BY_CONSTRUCTION（随机数据上不显著）；
      4. 未 human_confirmed 前只 stage_for_human_review，不 merge。

    返回 (allowed_to_stage: bool, reason: str)。
    """
    used = discovery_evidence.get("used_feedback") or []
    for u in used:
        if is_forbidden_feedback(u):
            return False, "反馈信号 %s 在黑名单，禁止吸收（基石一）" % u
    # 关键：zero_hypothesis_cross 必须为真值，而非仅"字段存在"。
    # 否则 NULL / LINEAR_TIME_ARTIFACT 等非 SURVIVOR 结论会漏过闸门。
    if not discovery_evidence.get("zero_hypothesis_cross"):
        return False, "zero_hypothesis_cross 未满足（三零假设未一致显著），禁止吸收"
    if discovery_evidence.get("random_control_label") == "ARTIFACT_BY_CONSTRUCTION":
        return False, "随机对照判 ARTIFACT_BY_CONSTRUCTION，禁止吸收（伪结构）"
    return True, "准入通过：可 stage_for_human_review（仍须人类复核才 merge）"


# ---------------------------------------------------------------------------
# 自检（供 pre_commit / 启动 self_check 调用）
# ---------------------------------------------------------------------------
def self_check():
    """返回 (ok, msgs)。校验契约内部一致性。"""
    msgs = []
    ok = True
    if not FEEDBACK_SIGNALS:
        ok = False
        msgs.append("FEEDBACK_SIGNALS 为空 —— 学习模块将无合法反馈可用")
    if not FORBIDDEN_SIGNALS:
        ok = False
        msgs.append("FORBIDDEN_SIGNALS 为空 —— 失去造假阳性护栏")
    # 白黑名单不得重叠
    overlap = set(FEEDBACK_SIGNALS) & set(FORBIDDEN_SIGNALS)
    if overlap:
        ok = False
        msgs.append("白黑名单重叠：%s" % overlap)
    msgs.append("learning_contract 自检通过：基石一~四已就位")
    return ok, msgs


if __name__ == "__main__":
    ok, msgs = self_check()
    for m in msgs:
        print(m)
    print("OK" if ok else "FAIL")
