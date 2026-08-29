# -*- coding: utf-8 -*-
"""反思设计层 (Reflective Designer)
让「智能组件」真正反省：每轮分析自己的搜索行为——失败率、算子利用率、多样性/坍缩、
闸门原始通过率——并生成可审计的「公式设计改进提案」。

提案只表达「下一轮该往搜索空间注入什么候选 / 调什么策略」，绝不含任何绕过统一闸门、
或自动合并的意图。活不活由统一闸门裁定；每条提案写入审计日志（可审计、追加不可篡改）。

这是诚实版「AI 反省怎样设计公式」：它反省的是「引擎自己的搜索结构弱点」，
而不是去噪声残差里挖不存在的信号。
"""
import json
import os
import datetime
from collections import Counter

REFLECTION_LOG = os.path.join(os.environ.get("DATA_DIR", r"D:/ssq_evo_data"),
                              "reflection_log.jsonl")


# ---------------------------------------------------------------------------
# 纯函数：从候选参数里解析使用的算子集合（用于利用率分析，不依赖引擎）
# ---------------------------------------------------------------------------
def _comp_ops_of(genome_params):
    """从 comp 基因组参数里提取使用的算子集合。"""
    cp = (genome_params or {}).get("_comp")
    ops = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        op = node.get("op")
        if op:
            ops.add(op)
        for k in ("a", "b"):
            child = node.get(k)
            if isinstance(child, dict):
                walk(child)
            elif isinstance(child, list):
                for c in child:
                    if isinstance(c, dict):
                        walk(c)

    if isinstance(cp, dict):
        walk(cp)
    return ops


def _variance(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


# ---------------------------------------------------------------------------
# 反省：分析单轮 telemetry，返回结构化报告（不修改任何状态）
# ---------------------------------------------------------------------------
def reflect_epoch(epoch, n_tasks, n_eval, n_cached, n_failed,
                  evals, survivors, leaderboard, novelty_archive,
                  available_ops, available_unary):
    """分析单轮搜索行为。所有输入来自 engine_core 的 Evolution 实例，本函数纯计算。"""
    n_total = max(1, n_tasks)
    failure_rate = n_failed / n_total

    # 幸存者 (sig, test) 利用率
    sig_test = Counter()
    for e in (survivors or []):
        sig_test[(e.get("sig"), e.get("test"))] += 1

    # comp 算子利用率（遍历整个 leaderboard 的 comp 候选）
    comp_ops = Counter()
    for e in (leaderboard or {}).values():
        if e.get("sig") == "comp":
            comp_ops.update(_comp_ops_of(e.get("params")))

    # 多样性
    uniq = len({e["gkey"] for e in (evals or []) if "gkey" in e})
    fitness_vals = [e.get("p_raw", 1.0) for e in (evals or [])]
    var = _variance(fitness_vals)

    # 闸门原始通过率（p_raw<0.05，未 BH 校正，仅作信息信号）
    gate_pass = sum(1 for e in (evals or []) if e.get("p_raw", 1.0) < 0.05)
    gate_rate = gate_pass / max(1, len(evals or []))

    # novelty 存档多样性（若有方法）
    nov_div = None
    try:
        if novelty_archive is not None and hasattr(novelty_archive, "diversity"):
            nov_div = novelty_archive.diversity()
    except Exception:
        nov_div = None

    report = {
        "epoch": epoch,
        "n_tasks": n_tasks, "n_eval": n_eval, "n_cached": n_cached, "n_failed": n_failed,
        "failure_rate": round(failure_rate, 4),
        "unique_genomes": uniq,
        "fitness_var": round(var, 6),
        "gate_pass_raw_rate": round(gate_rate, 4),
        "nov_archive_size": (len(novelty_archive) if novelty_archive is not None else 0),
        "nov_diversity": (round(nov_div, 4) if nov_div is not None else None),
        "sig_test_top": sig_test.most_common(5),
        "comp_op_usage": dict(comp_ops),
        "available_ops": available_ops,
    }

    # 人类可读反省文本
    lines = []
    lines.append(f"EP{epoch}: 评估 {n_eval} 缓存命中 {n_cached} 失败(不可测) {n_failed} "
                 f"(失败率 {failure_rate:.1%})")
    lines.append(f"  多样性: 唯一基因组 {uniq}, fitness方差 {var:.4f}, "
                 f"novelty存档 {report['nov_archive_size']}, 多样性 {nov_div}")
    lines.append(f"  闸门原始通过率(p<0.05)={gate_rate:.1%}; 幸存(sig,test)Top3={sig_test.most_common(3)}")
    lines.append(f"  comp算子利用率={dict(comp_ops)}; 全集算子={available_ops}")
    report["text"] = "\n".join(lines)
    return report


# ---------------------------------------------------------------------------
# 提案：从反省报告生成结构化、可审计的改进提案
# ---------------------------------------------------------------------------
def propose(report, pop, collapse_unique_ratio=0.5, high_fail_thresh=0.3):
    """生成提案列表。每条是结构化 dict（type + 参数 + 人类可读 text）。
    提案只描述「下一轮搜索空间/策略该怎样」，不含任何绕过闸门或自动合并的意图。"""
    props = []
    uniq = report.get("unique_genomes", 0)
    fail = report.get("failure_rate", 0.0)
    var = report.get("fitness_var", 0.0)
    avail_ops = report.get("available_ops", [])
    used_ops = set(report.get("comp_op_usage", {}).keys())

    # 1) 坍缩检测：唯一基因组过少 或 fitness方差≈0 → 加强多样性
    if uniq < max(2, int(pop * collapse_unique_ratio)) or var < 1e-6:
        props.append({
            "type": "novelty_boost",
            "n": max(2, pop // 4),
            "text": (f"检测坍缩(唯一={uniq}, var={var:.2e})→ "
                     f"注入 {max(2, pop // 4)} 个纯随机多样基因组 + 强化 novelty 偏置"),
        })

    # 2) 高失败率：大量公式产出不可测(非有限/退化) → 重新覆盖可测空间
    if fail > high_fail_thresh:
        props.append({
            "type": "inject_diverse",
            "n": max(2, pop // 4),
            "text": (f"失败率 {fail:.1%} 过高 → 注入 {max(2, pop // 4)} 个随机探索基因组，"
                     f"重新覆盖可测公式空间"),
        })

    # 3) 算子利用率缺口：全集算子中从未在 comp 幸存者出现 → 偏置尝试它
    unused = [o for o in avail_ops if o not in used_ops and o not in ("sin", "cos", "abs")]
    for o in unused[:3]:
        props.append({
            "type": "boost_operator",
            "op": o,
            "n": 1,
            "text": (f"算子 '{o}' 从未在幸存 comp 公式出现 → "
                     f"偏置注入 1 个以 '{o}' 为主算子的候选"),
        })

    # 4) 闸门全不过（gate_rate==0）→ 纯探索偏置（结构变异拓宽拓扑）
    if report.get("gate_pass_raw_rate", 0.0) == 0.0 and uniq >= 2:
        props.append({
            "type": "structural_mut",
            "n": max(2, pop // 6),
            "text": (f"本轮无候选过原始闸门 → 注入 {max(2, pop // 6)} 个结构变异"
                     f"(深层 comp)拓宽拓扑"),
        })

    if not props:
        props.append({
            "type": "explore_bias",
            "n": 1,
            "text": "搜索稳健：未触发紧急偏置，维持当前结构与 novelty 配合",
        })
    return props


def log_reflection(report, proposals, path=None):
    """把反省报告 + 提案写入审计日志（可审计、不可篡改追加）。
    path=None 时用模块级 REFLECTION_LOG（可在测试时重定向）。"""
    if path is None:
        path = REFLECTION_LOG
    rec = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "report": report,
        "proposals": proposals,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def summarize_log(path=REFLECTION_LOG, last_n=20):
    """打印最近 N 轮反省摘要（给人看的账本）。"""
    if not os.path.exists(path):
        print("[reflect] 无反省日志。")
        return
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    for r in rows[-last_n:]:
        rep = r.get("report", {})
        print(f"[{r.get('ts')}] EP{rep.get('epoch')} "
              f"失败率={rep.get('failure_rate')} 唯一={rep.get('unique_genomes')} "
              f"gate_raw={rep.get('gate_pass_raw_rate')}")
        for p in r.get("proposals", []):
            print(f"    提案: {p.get('text')}")
