# -*- coding: utf-8 -*-
"""
firewall.py —— 诚实防火墙（四道物理机制 + 自动合并封死）
==========================================================
把"防火"从口号落成可执行的硬接线。设计对齐 MEMORY.md 护栏契约 #1/#3/#6：

  1) 数据隔离  : 提案者(无论 GA 还是将来的 LLM)只挂载【发现段】，确认段与实盘段
                 物理上不进入它的可读数据——它想偷看未来数据，没有数组可读。
  2) 指标隔离  : 提案者内部迭代用的 fitness(发现段代理指标) ≠ 闸门指标(BH-FDR q / #41 verdict)。
                 闸门只用提案者没摸过的数据裁决，提案者无法把"过闸"当优化目标(Goodhart 红线)。
  3) 审计账本  : 每个候选留痕——来源 / 看过的数据段指纹 / 种子 / 代码版本 / 随机重放 / 裁决 / 是否晋级。
                 任何"幸存者"被质疑都能完整回溯它有没有越界。
  4) 随机重放  : 任何新轴/新公式/新候选，先在同款管线上跑纯随机双色球数据；随机数据上也
                 SURVIVOR → 判 ARTIFACT_BY_CONSTRUCTION 直接降级(已用此抓死 red_recurrence_mean)。

晋级锁死：任何候选想进【生产候选集】必须经人类显式签字(promote 的 human_signoff)；
          本模块提供 promote()，无签字一律拒绝，杜绝自主进化层自动合并。

本文件不向 C 盘写任何东西，全部产物落在 D:\\ssq_evo（与 evidence_ledger.json 同级）。
"""
import os
import json
import subprocess
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_LEDGER = os.path.join(HERE, "audit_ledger.json")
PROD_CANDIDATES = os.path.join(HERE, "production_candidates.json")


# ---------------------------------------------------------------------------
# 0. 代码版本（用于审计留痕；本地 git，不联网）
# ---------------------------------------------------------------------------
def code_version():
    try:
        out = subprocess.run(["git", "-C", HERE, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()[:12]
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# 1. 数据隔离：发现段装载器
# ---------------------------------------------------------------------------
def discovery_split(reds, blues, frac=0.7):
    """按时间顺序把全量切成 (发现段, 确认段)。提案者只拿发现段。"""
    n = int(reds.shape[0])
    d_end = int(n * frac)
    if d_end < 30:
        d_end = max(1, n - 30)
    return reds[:d_end], blues[:d_end], reds[d_end:], blues[d_end:]


def discovery_fingerprint(reds, blues):
    """提案者所见数据段的指纹（写入审计账本，证明它只看发现段）。"""
    try:
        import engine_core as E
        return E.C.data_fingerprint(reds, blues)
    except Exception:
        return None


# 声明：提案者被允许读取的数据清单（物理隔离的清单化表达）。
# 任何试图读取清单外路径的提案者加载器都应被 assert 拦截（见 assert_allowed_path）。
ALLOWED_PROPOSER_PATHS = []  # 留空 = 提案者只许用内存中的发现段数组，不读任何文件


def assert_allowed_path(path):
    """若将来提案者需要从文件读发现段摘要，必须在此白名单内；否则抛错。"""
    p = os.path.abspath(path)
    for a in ALLOWED_PROPOSER_PATHS:
        base = os.path.abspath(a)
        if p == base or p.startswith(base + os.sep):
            return True
    raise PermissionError(
        "防火墙拦截：提案者试图读取未授权路径 %s。提案者只允许使用内存中的发现段数组。" % p)


# ---------------------------------------------------------------------------
# 2. 指标隔离：明确"提案者 fitness ≠ 闸门指标"
# ---------------------------------------------------------------------------
def proposer_fitness_is_not_gate_metric():
    """契约自检：返回防火墙坚持的指标隔离声明。
    实际隔离靠架构保证——提案者的发现段 fitness 只用于'提候选'，
    选拔/晋级只由 evaluator 的 BH-FDR q 与 #41 verdict 决定，二者数据源不同。"""
    return {
        "proposer_fitness_source": "discovery_segment_only",
        "gate_metric_source": "confirmation_segment_never_seen_by_proposer",
        "rule": "proposer fitness is NOT used for promotion; only gate verdict promotes",
    }


# ---------------------------------------------------------------------------
# 3. 审计账本
# ---------------------------------------------------------------------------
def record_candidate(genome, source, disc_fp, seed, random_replay_label=None,
                     random_replay_passed=None, gate_verdict=None, extra=None):
    """把一个候选的来源留痕追加写入不可变审计账本 audit_ledger.json。"""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,                      # "GA" | "LLM" | "manual"
        "genome": {"sig": genome.get("sig"), "test": genome.get("test"),
                   "params": genome.get("params")},
        "discovery_fingerprint": disc_fp,      # 它看过的数据段指纹（证明只看发现段）
        "seed": seed,
        "code_version": code_version(),
        "random_replay": {"label": random_replay_label, "passed": random_replay_passed},
        "gate_verdict": gate_verdict,          # 后续 #41 填；None=待闸
        "promoted": False,
        "promoted_by": None,
    }
    if extra:
        entry.update(extra)
    hist = []
    if os.path.exists(AUDIT_LEDGER):
        try:
            with open(AUDIT_LEDGER, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    hist.append(entry)
    with open(AUDIT_LEDGER, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    return entry


def load_audit_ledger():
    if not os.path.exists(AUDIT_LEDGER):
        return []
    try:
        with open(AUDIT_LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def update_verdict(sig, test, gate_verdict):
    """闸门裁决后回填 gate_verdict（按 (sig,test) 匹配最近一条）。"""
    hist = load_audit_ledger()
    if not hist:
        return
    target = None
    for e in reversed(hist):
        g = e.get("genome") or {}
        if (g.get("sig"), g.get("test")) == (sig, test):
            target = e
            break
    if target is None:
        return
    target["gate_verdict"] = gate_verdict
    with open(AUDIT_LEDGER, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 4. 随机重放（构造伪结构兜底拦截）
# ---------------------------------------------------------------------------
def random_replay_check(sig, test, N, seed, k_sur=60):
    """对任何候选轴/公式，先在纯随机双色球数据上跑同款分层标签。
    返回 (label, passed)：随机数据也 SURVIVOR → 判 ARTIFACT_BY_CONSTRUCTION(passed=False)。"""
    try:
        import run_axes as RA
    except Exception:
        return None, None
    label = RA.random_control_label(sig, [test], N, seed=seed, k_sur=k_sur)
    passed = (label != "SURVIVOR")
    return label, passed


# ---------------------------------------------------------------------------
# 防火墙硬门（构造级强制）：任何候选想进【待闸池】，唯一入口
# ---------------------------------------------------------------------------
def firewall_gate(genome, source, disc_fp, seed, N, k_sur=60):
    """硬门：候选进入待闸池前必须先过 随机重放 + 审计留痕。
    随机数据也 SURVIVOR → 判 ARTIFACT_BY_CONSTRUCTION，直接拒（不进入候选池）。
    这是智能演进层每个候选的唯一入口；绕过此函数即视为越权。
    返回 (passed, label)。"""
    sig = genome.get("sig")
    test = genome.get("test")
    label, passed = random_replay_check(sig, test, N, seed=seed, k_sur=k_sur)
    record_candidate(genome, source, disc_fp, seed,
                     random_replay_label=label, random_replay_passed=passed)
    return passed, label


def verify_data_isolation(disc_r, disc_b, expected_fp):
    """构造级断言：提案者所见发现段数据的指纹必须等于预期发现段指纹。
    若有人把全量/确认段塞进来，指纹对不上 → 抛错。"""
    actual = discovery_fingerprint(disc_r, disc_b)
    if actual != expected_fp:
        raise PermissionError(
            "FIREWALL BREACH: 提案者数据指纹(%s) != 发现段指纹(%s)。"
            "确认/实盘段不得进入提案者。" % (actual, expected_fp))
    return True


# ---------------------------------------------------------------------------
# 晋级锁死：杜绝自主进化自动合并
# ---------------------------------------------------------------------------
def promote(genome, source, human_signoff, signoff_name="human"):
    """把候选晋级进【生产候选集】。无显式人类签字一律拒绝（返回 False）。
    这是诚实护栏的最后一道门：任何公式想参与实盘预测，必须经你签字。"""
    if not human_signoff:
        raise PermissionError(
            "防火墙拒绝自动合并：晋级生产候选集需要人类显式签字(human_signoff=True)。")
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "genome": {"sig": genome.get("sig"), "test": genome.get("test"),
                   "params": genome.get("params")},
        "promoted_by": signoff_name,
        "code_version": code_version(),
    }
    hist = []
    if os.path.exists(PROD_CANDIDATES):
        try:
            with open(PROD_CANDIDATES, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    hist.append(entry)
    with open(PROD_CANDIDATES, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    _mark_promoted(genome, signoff_name)
    return True


def _mark_promoted(genome, signoff_name):
    hist = load_audit_ledger()
    target = None
    for e in reversed(hist):
        g = e.get("genome") or {}
        if (g.get("sig"), g.get("test")) == (genome.get("sig"), genome.get("test")):
            target = e
            break
    if target is None:
        return
    target["promoted"] = True
    target["promoted_by"] = signoff_name
    with open(AUDIT_LEDGER, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print("防火墙模块自检：")
    print("  代码版本:", code_version())
    print("  指标隔离契约:", proposer_fitness_is_not_gate_metric())
    print("  审计账本条目数:", len(load_audit_ledger()))
    print("  晋级锁死: promote() 无签字会抛 PermissionError（已就绪）")
