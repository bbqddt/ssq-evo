"""redteam_audit.py — 诚实版自主进化层的「红队自审」组件（只读对抗审计器）。

定位（见 MEMORY.md 护栏契约 #5）：本模块是「诚实的守护」，不是「自主进化」的危险面。
它**不搜索结构、不自动合并候选、不改代码、不碰生产 state**——只读取 cycle 产出，
主动挑自己结论的毛病，逼系统别自欺。

审计项：
  1) 多重比较通胀：在 null 下，跑 n_eval 个检验，最小 p 期望≈1/n_eval。若观测 best_p
     落在 null 随机波动范围内，直接点破「显著」只是噪声（对抗"普通方法不出结果"陷阱）。
  2) 效应量/极端值 sanity：z 值荒谬（如 >1e6，分母近零的退化统计）必须报警。
  3) 确认段完整性：声称 SIGNAL 必须有过 #41 发现/确认分离（wf_n_confirm 证据），否则报警。
  4) 过度声称扫描：对给定的结论文本做红队措辞审查，绝对化措辞（"发现结构/可预测"）且无
     任何对冲词（"可能/未发现/待验证"）即报警。
  5) 阳性对照完整性：若 state 未记录阳性对照验证证据，提示补做（不自跑重型注入，避免破坏只读）。

输出：audit/report.json + audit/report.md，含 verdict(OK/REVIEW/ALERT) 与带严重度的 findings。

用法：
  python redteam_audit.py --state D:/ssq_evo_data/state.json --out D:/ssq_evo_data/audit
                          [--summary "本次结论文本..."]
也可在 run_cycle 中以 redteam_audit_enabled:true 钩子调用（默认关闭，保持只读、不扰生产）。
"""

import os
import re
import json
import math
import datetime
import argparse


# ---- 过度声称红队词典 ----
RISKY_PHRASES = [
    "发现结构", "找到规律", "找到结构", "存在规律", "存在结构",
    "可预测", "预测准确", "证明存在", "确凿", "稳赚", "必中", "包中", "破解",
]
HEDGE_PHRASES = [
    "可能", "疑似", "也许", "未确认", "待独立验证", "待确认", "未发现",
    "null", "无显著", "目前没有证据", "尚不能", "不足以", "边界", "谨慎",
]


def scan_overclaim(text):
    """返回 list[str]：检出的过度声称。无文本则返回空。"""
    if not text:
        return []
    findings = []
    lowered = text.lower()
    hit_risky = [p for p in RISKY_PHRASES if p.lower() in lowered]
    if not hit_risky:
        return []
    has_hedge = any(h.lower() in lowered for h in HEDGE_PHRASES)
    if not has_hedge:
        findings.append(
            "结论文本含绝对化措辞 %s 但无任何对冲词（可能/未发现/待验证等）；"
            "在 null 域下易构成过度声称。" % ("、".join(hit_risky))
        )
    else:
        # 有对冲但仍提示：确认对冲是否紧邻风险措辞（简单起见只提示人工复核）
        findings.append(
            "结论文本含绝对化措辞 %s，虽带对冲词但建议人工确认对冲是否真正削弱了声称。"
            % ("、".join(hit_risky))
        )
    return findings


def multiple_comparison_check(state):
    """null 下多重比较体检：观测 best_p 是否只是噪声。返回 (findings, info)。"""
    findings = []
    info = {}
    n = state.get("n_eval")
    fdr_q = state.get("fdr_q", 0.05)
    best_q = state.get("best_q")
    best_p = state.get("best_p")
    info = {"n_eval": n, "fdr_q": fdr_q, "best_q": best_q, "best_p": best_p}
    if not n or best_p is None:
        return findings, info
    # null 下最小 p 的期望约 1/(n+1)；P(min_p <= x) = 1-(1-x)^n
    expected_min_p = 1.0 / (n + 1)
    p_min_le_obs = 1.0 - (1.0 - min(best_p, 0.999)) ** n
    info["expected_min_p_null"] = expected_min_p
    info["p_obs_le_best_under_null"] = p_min_le_obs
    # 连 FDR 都没过 => 结论是 null，这是诚实的正确结果，不算问题（不报警）。
    # 审计器只 scrutinize「声称显著」的情形；无声称则无毛病可挑。
    if best_q is not None and best_q >= fdr_q:
        return findings, info
    # 过了 FDR 但 best_p 落在 null 期望最小 p 的量级 => 仍可能是噪声
    if best_p > expected_min_p:
        findings.append(
            "best_p=%.4g 但 n_eval=%d，null 下最小 p 期望≈%.4g；观测值大于 null 期望，"
            "完全落在随机波动范围内，不能作为存在结构的证据（多重比较噪声）。"
            % (best_p, n, expected_min_p)
        )
    elif p_min_le_obs > 0.5:
        findings.append(
            "best_p=%.4g 在 n_eval=%d 下，null 有 %.0f%% 概率产生≤此的最小 p；"
            "显著性不足以区分信号与噪声，须靠独立确认段复现。" % (best_p, n, p_min_le_obs * 100)
        )
    return findings, info


def extremity_sanity(state):
    """效应量/极端值体检：荒谬 z 值报警。"""
    findings = []
    z = state.get("best_z")
    if z is not None and (abs(z) > 1e6 or math.isinf(z) or math.isnan(z)):
        findings.append(
            "best_z=%.3g 荒谬（分母近零的退化统计或数值溢出），该极值不可信，"
            "对应 best_stat/best_p 须人工核查。" % z
        )
    # 历史序列里有无同样荒谬的离群
    hist = state.get("best_z_history") or []
    for v in hist:
        if isinstance(v, (int, float)) and (abs(v) > 1e6 or math.isinf(v) or math.isnan(v)):
            findings.append(
                "best_z_history 含荒谬离群值 %.3g（退化统计），提示该 cycle 的某检验 stat 分母近零，"
                "其显著性不可信，应从候选池剔除或修正。" % v
            )
            break
    return findings


def holdout_integrity(state):
    """确认段完整性：声称 SIGNAL 须有 #41 发现/确认分离证据。"""
    findings = []
    alert = state.get("alert")
    best_q = state.get("best_q")
    fdr_q = state.get("fdr_q", 0.05)
    claims_signal = (best_q is not None and best_q < fdr_q) or bool(alert)
    wf_n_confirm = state.get("wf_n_confirm")
    wf_verdict = state.get("wf_verdict")
    if claims_signal:
        if wf_verdict is None:
            findings.append(
                "存在 SIGNAL 级 claim（best_q=%.4g/alert=%s）但未记录 #41 发现/确认分离 verdict；"
                "无独立确认段复现证据，不得宣称为结构。" % (best_q, alert)
            )
        elif wf_verdict != "SIGNAL":
            findings.append(
                "存在 SIGNAL 级 claim 但 #41 确认闸门 verdict=%s（非 SIGNAL）；"
                "属 UNCONFIRMED，确认段未复现，闸门已拦截过拟合。" % wf_verdict
            )
        elif wf_n_confirm is not None and wf_n_confirm < 2:
            findings.append(
                "#41 确认闸门仅 %d 折确认（n_confirm=%d）；单折确认脆弱，须 ≥2 折独立复现。"
                % (wf_n_confirm, wf_n_confirm)
            )
    return findings


def positive_control_note(state):
    """持续阳性对照结果体检：run_cycle 每 K 轮真实注入已知结构并跑 #41 闸门。
    若闸门对已知结构判不出 SIGNAL，说明闸门功率退化，直接 ALERT。
    """
    findings = []
    pc = state.get("positive_control")
    if pc is None:
        # 本轮未跑（positive_control_every>1 且非本轮次），不报警，等其节奏
        return findings
    if pc.get("verified") is not True:
        verdict = pc.get("verdict")
        conf_p = pc.get("conf_p")
        findings.append(
            "ALERT 持续阳性对照失败：注入已知 AR(1) 结构后 #41 闸门 verdict=%s "
            "(conf_p=%s)。闸门功率疑似退化——真实结构已检不出，"
            "管线/参数很可能已漂移，须立即人工核查（否则所有 null 结论的可信度都受损）。"
            % (verdict, conf_p)
        )
    return findings


def audit_cycle(state, summary_text=None):
    """对单个 cycle 的 state 做红队审计，返回报告 dict。"""
    findings = []
    info = {}
    f1, info["mc"] = multiple_comparison_check(state)
    findings += f1
    findings += extremity_sanity(state)
    findings += holdout_integrity(state)
    findings += positive_control_note(state)
    findings += scan_overclaim(summary_text)

    # 严重度：过度声称 / 退化统计 / 持续阳性对照失败 => ALERT（闸门坏了最严重）；其余 => REVIEW；无 => OK
    sev_alert = any(("荒谬" in f or "绝对化措辞" in f or "持续阳性对照失败" in f)
                    for f in findings)
    verdict = "OK" if not findings else ("ALERT" if sev_alert else "REVIEW")
    report = {
        "audited_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cycle_id": state.get("cycle_id"),
        "verdict": verdict,
        "n_findings": len(findings),
        "findings": findings,
        "info": info,
    }
    return report


def write_report(report, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "report.json")
    mp = os.path.join(out_dir, "report.md")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    lines = ["# 红队自审报告", "", "- 审计时间：%s" % report["audited_at"],
             "- cycle_id：%s" % report["cycle_id"], "- 结论：**%s**" % report["verdict"],
             "- 发现数：%d" % report["n_findings"], ""]
    if report["findings"]:
        lines.append("## 发现")
        for i, f in enumerate(report["findings"], 1):
            lines.append("%d. %s" % (i, f))
    else:
        lines.append("## 发现")
        lines.append("无。本次 cycle 未触发红队警报（仍须依赖独立确认段与阳性对照持续把关）。")
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return jp, mp


def main():
    ap = argparse.ArgumentParser(description="ssq_evo 红队自审（只读对抗审计）")
    ap.add_argument("--state", required=True, help="state.json 路径")
    ap.add_argument("--out", default="audit", help="报告输出目录")
    ap.add_argument("--summary", default=None, help="本次结论文本（供过度声称扫描）")
    a = ap.parse_args()
    with open(a.state, "r", encoding="utf-8") as f:
        state = json.load(f)
    report = audit_cycle(state, a.summary)
    jp, mp = write_report(report, a.out)
    print("红队自审 verdict=%s, findings=%d" % (report["verdict"], report["n_findings"]))
    print("报告: %s | %s" % (jp, mp))
    for i, f in enumerate(report["findings"], 1):
        print("  %d. %s" % (i, f))


if __name__ == "__main__":
    main()
