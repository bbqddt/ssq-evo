"""
进展汇报强制闸门（Progress Reporting Gate）
=========================================
根治"daemon.log 一行就下结论→报假好消息→被打脸"的 N 次重复模式。

任何"突破/成功/对齐/演进"类结论，必须同时满足 4 个硬证据才能出口：
  1. 持久化层交叉验证（frontier.json / digest.jsonl）— 不是 daemon.log 内存打印
  2. 时间连续性（至少连续 2 轮稳定，不是单轮 spike）
  3. 来源可追溯（哪个 cycle、哪行代码产生的）
  4. 反面证据已排除（已主动查过"为什么可能是假的"并记录排除理由）

缺任何一个 → 只允许说"观察到 X 现象，待验证"，禁止用"突破/成功/对齐"等词。

用法:
  python3 progress_gate.py              # 全面检查当前状态
  python3 progress_gate.py --claim "df_gen 突破"  # 验证某个具体声明
"""

import json
import os
import sys
import numpy as np
from datetime import datetime

DATA_DIR = os.environ.get("DATA_DIR", r"D:\ssq_evo_data")
DIGEST = os.path.join(DATA_DIR, "daily_digest.jsonl")
FRONTIER = os.path.join(DATA_DIR, "frontier.json")
DAEMON_LOG = os.path.join(DATA_DIR, "daemon.log")

# ─── 禁用词列表：缺证据时不许用 ───
FORBIDDEN_WORDS = [
    "突破", "成功", "对齐", "全线", "确认生效", "真实发生",
    "真正上长", "首次", "里程碑", "落地",
]

ALLOWED_WITH_CAUTION = [
    "观察到", "待验证", "暂未", "需持续观察",
    "单轮显示", "内存中短暂出现", "疑似",
]


def load_digest(n=30):
    """加载最近 n 轮 digest"""
    try:
        lines = [l for l in open(DIGEST) if l.strip()]
        return [json.loads(l) for l in lines[-n:]]
    except Exception:
        return []


def load_frontier():
    """加载 frontier 持久化"""
    try:
        return json.load(open(FRONTIER))
    except Exception:
        return {}


def check_1_persistence(claim_type=""):
    """
    证据1: 持久化层交叉验证
    - frontier.json 是精英持久化真相源
    - digest.jsonl 是每轮结论持久化真相源
    - daemon.log 只是内存打印，不能单独当证据
    """
    findings = []
    score = 0  # 0=FAIL, 0.5=PARTIAL, 1=PASS

    frontier = load_frontier()
    digest = load_digest(5)

    # 1a. frontier 是否可读
    if not frontier:
        findings.append("❌ frontier.json 不可读 → 无法交叉验证持久化")
        score = min(score, 0)
    else:
        findings.append("✅ frontier.json 可读")
        score = max(score, 0.5)

    # 1b. df_gen: 持久化 vs 最近 digest
    f_gen = frontier.get("df_gen")
    if digest:
        d_gen_last = digest[-1].get("df_gen")
        d_gen_prev = digest[-2].get("df_gen") if len(digest) >= 2 else None

        if f_gen is not None:
            findings.append(f"   frontier.df_gen = {f_gen} (持久化真相源)")
            findings.append(f"   digest 最近轮 df_gen = {d_gen_last}")
            if d_gen_prev is not None:
                findings.append(f"   digest 前一轮 df_gen = {d_gen_prev}")

                # 关键检查: digest 是否在两轮间跳变又回落 (transient)
                gens = [d.get("df_gen") for d in digest[-5:] if d.get("df_gen") is not None]
                if len(gens) >= 2 and max(gens) > min(gens) + 1:
                    findings.append("⚠️  df_gen 近 5 轮有跳变+回落 → 可能是 transient artifact")
                    score = min(score, 0.5)
                elif f_gen == d_gen_last:
                    findings.append(f"✅   持久化与最新 digest 一致 ({f_gen})")
                    score = max(score, 1)
        else:
            findings.append("⚠️   frontier 无 df_gen 字段")

    # 1c. comp 精英数: 持久化真相
    elites = frontier.get("elites", [])
    comp_elites = sum(
        1 for e in elites
        if (isinstance(e, dict) and e.get("sig") == "comp") or
           (isinstance(e, dict) and isinstance(e.get("genome"), dict) and e["genome"].get("sig") == "comp")
    )
    findings.append(f"   frontier.comp_精英数 = {comp_elites}/{len(elites)} (持久化)")
    if comp_elites == 0:
        findings.append("⚠️   comp 精英 = 0 → 代际演进未真实发生（无论 daemon.log 打印了什么）")

    return {"name": "证据1: 持久化层交叉验证", "score": score, "findings": findings}


def check_2_continuity():
    """
    证据2: 时间连续性
    - 至少连续 2 轮稳定（不是单轮 spike）
    - 对 df_gen/best_q/pick_p 等关键指标检查稳定性
    """
    findings = []
    score = 1
    digest = load_digest(10)

    if len(digest) < 2:
        findings.append("⚠️ digest 轮数不足 2，无法判断连续性")
        return {"name": "证据2: 时间连续性(≥2轮稳定)", "score": 0.5, "findings": findings}

    # 2a. df_gen 连续性
    gens = [d.get("df_gen") for d in digest[-5:] if d.get("df_gen") is not None]
    if gens:
        if len(set(gens)) == 1:
            findings.append(f"✅ df_gen 连续 5 轮稳定 = {gens[0]}")
        elif gens[-1] > gens[0] and gens[-2] == gens[-1]:
            findings.append(f"✅ df_gen 从 {gens[0]} 稳定上升到 {gens[-1]} (连续 2 轮+)")
        elif gens[-1] != gens[-2]:
            findings.append(f"⚠️ df_gen 不稳定: 最近={gens[-1]}, 前轮={gens[-2]} (非连续)")
            score = min(score, 0.5)

    # 2b. best_q 连续性
    qs = [float(d.get("best_q", 1)) for d in digest[-10:] if d.get("best_q") is not None]
    if len(qs) >= 3:
        cv = np.std(qs) / np.mean(qs) if np.mean(qs) > 0 else float("inf")
        if cv < 0.3:
            findings.append(f"✅ best_q 近 10 轮稳定 (cv={cv:.3f})")
        else:
            findings.append(f"⚠️ best_q 剧烈漂移 (cv={cv:.3f}) → 未收敛")
            score = min(score, 0.5)

    return {"name": "证据2: 时间连续性(≥2轮稳定)", "score": score, "findings": findings}


def check_3_traceability():
    """
    证据3: 来源可追溯
    - 每个 key metric 必须能追溯到 cycle_id + 代码位置
    """
    findings = []
    score = 1
    digest = load_digest(1)

    if not digest:
        findings.append("❌ 无 digest 数据")
        score = 0
    else:
        d = digest[-1]
        has_cycle = d.get("cycle_id") is not None
        has_ts = d.get("ts") is not None
        has_best_sig = d.get("best_sig") is not None

        if has_cycle and has_ts:
            findings.append(f"✅ 可追溯到 cycle {d['cycle_id']} @ {d['ts']}")
        else:
            findings.append("⚠️ 缺少 cycle_id 或时间戳")
            score = 0.5

        if has_best_sig:
            findings.append(f"✅ best_sig 来源: {d.get('best_sig')} / {d.get('best_test')}")
        else:
            findings.append("⚠️ 缺少 best_sig 追溯")

    return {"name": "证据3: 来源可追溯(cycle+代码)", "score": score, "findings": findings}


def check_4_contra_excluded():
    """
    证据4: 反面证据已排除
    - 主动查过"为什么可能是假的"并记录排除理由
    - 包括: transient artifact? 闸门 bug? 种子漂移? 数据污染?
    """
    findings = []
    score = 0.5  # 默认: 没查过反面证据
    digest = load_digest(5)

    # 4a. 阳性对照是否通过（证明闸门没坏）
    pc_pass = False
    for d in digest[-5:]:
        pc = d.get("positive_control")
        if isinstance(pc, dict) and pc.get("verified"):
            pc_pass = True
            findings.append(f"✅ 阳性对照 verified (p={pc.get('conf_p')}) — 闸门功率正常")
            break
    if not pc_pass:
        findings.append("⚠️ 近 5 轮无阳性对照通过记录 — 无法排除闸门误杀/误放")
        score = 0

    # 4b. spectral_verdict 是否有伪结构标记
    for d in digest[-3:]:
        sv = d.get("spectral_verdict")
        if sv and ("伪结构" in str(sv) or "artifact" in str(sv).lower()):
            findings.append(f"⚠️ spectral 标记伪结构/artifact → 排除信号污染")
            # 这其实是好事（排伪在工作），但必须标注

    # 4c. best_q 是否 spike 后回落
    qs = [float(d.get("best_q", 1)) for d in digest[-5:] if d.get("best_q") is not None]
    if len(qs) >= 3:
        if abs(qs[-1] - qs[-2]) > 0.1 or abs(qs[-2] - qs[-3]) > 0.1:
            findings.append("⚠️ best_q 轮间波动 > 0.1 → 可能是种子/数据段敏感，非真改进")
            score = min(score, 0.5)
        else:
            findings.append("✅ best_q 轮间波动平缓 (< 0.1)")

    # 4d. pick_p 是否 NULL
    pick_ps = [d.get("pick_p") for d in digest[-3:] if d.get("pick_p") is not None]
    if pick_ps and all(p >= 0.99 for p in pick_ps):
        findings.append("⚠️ pick_p 全 NULL (≥0.99) → 选号不优于随机，无超额信息")
        score = min(score, 0.5)

    return {"name": "证据4: 反面证据已排除", "score": score, "findings": findings}


def gate_claim(claim_text=""):
    """运行全部 4 项检查，返回是否允许汇报该 claim"""
    checks = [
        check_1_persistence(claim_text),
        check_2_continuity(),
        check_3_traceability(),
        check_4_contra_excluded(),
    ]

    total_score = sum(c["score"] for c in checks) / len(checks)

    print("=" * 60)
    print(f"  进展汇报强制闸门 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if claim_text:
        print(f"  待验证声明: 「{claim_text}」")
    print("=" * 60)

    all_findings = []
    for c in checks:
        print(f"\n[{c['name']}] 得分: {c['score']}/1")
        for f in c["findings"]:
            print(f"  {f}")
            all_findings.append(f)

    print("\n" + "-" * 60)
    print(f"综合得分: {total_score:.2f} / 1.00")

    if total_score >= 0.875:
        print("✅ 闸门通过 — 允许使用\"突破/成功/对齐\"等词汇报")
        verdict = "PASS"
    elif total_score >= 0.5:
        print("⚠️ 闸门部分通过 — 只允许说\"观察到 X 待验证\"，禁用确定性词汇")
        verdict = "PARTIAL"
    else:
        print("❌ 闸门不通过 — 禁止报进展，先修问题再汇报")
        verdict = "FAIL"

    # 检查 claim_text 是否含禁用词
    if claim_text:
        forbidden_found = [w for w in FORBIDDEN_WORDS if w in claim_text]
        if forbidden_found and verdict != "PASS":
            print(f"\n🚨 声明含禁用词 {forbidden_found} 但闸门未通过 → 此声明不许出口！")
            verdict = "BLOCKED"

    print("-" * 60)
    return verdict, checks, total_score


def main():
    import argparse
    parser = argparse.ArgumentParser(description="进展汇报强制闸门")
    parser.add_argument("--claim", default="", help="待验证的进展声明")
    parser.add_argument("--json", action="store_true", help="JSON 输出（供程序调用）")
    args = parser.parse_args()

    verdict, checks, score = gate_claim(args.claim)

    if args.json:
        print(json.dumps({
            "verdict": verdict,
            "score": round(score, 3),
            "timestamp": datetime.now().isoformat(),
            "claim": args.claim,
            "checks": {c["name"]: {"score": c["score"], "findings": c["findings"]} for c in checks},
        }, ensure_ascii=False))

    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
