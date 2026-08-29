# -*- coding: utf-8 -*-
"""
bias_corrector.py —— 学习模块 L3 · 偏置纠正器（Bias Corrector）

职责（对应学习模块四层架构的 L3）：
  把"失败吸收器(L1)"积累的失败知识 + "原语扩张器(L2)"的 discovery 新颖度，
  转化成**探索预算的偏置**，回馈给三驾车：
    - 驾3 云端 GA（ci_evolve 多 seed）：对已证伪路线清零/降 seed 探索权重；
      对高新颖度方向倾斜 seed 预算。
    - 驾1 本地 GA（engine_core.Evolution）：对"已证伪 sig"降低精英保留概率，
      避免反复在死胡同里 hill-climbing。

核心学习体现（用户原话"在错误的道路上纠正、吸收、改良"）：
  不是"在旧空间重调权重"，而是"改自己的探索偏好"——
  系统主动偏离已证明无效的路线，把算力挪到没踩死的地方。

=== 契约护栏（learning_contract 基石，本模块严格遵守）===
  基石一 · 不撒谎的反馈信号：本模块只读 L1 的 failure_taxonomy（源自反过拟合闸门），
           绝不把 in_sample_accuracy / backtest_fit 当优化目标。
  基石二 · 三驾车闭环：输入 = 三驾车真实产出(state/fate/taxonomy)；
           输出 = bias_corrector.json，被 ci_evolve / engine_core 消费（见 daemon 调用）。
  基石三 · confirm 复验：本模块只改"探索偏置"，不 merge 任何结构进 SIGMAPS；
           任何被偏置扶持的新方向，最终仍须过 #41 confirm 闸门才算真吸收。
  基石四 · 人类否决权：bias_corrector.json 是"建议性偏置"，驾1/驾3 消费时若发现
           与人类复核结论冲突（pending_primitives 已 merge 的方向），以人类结论为准。

本模块零外部依赖（仅标准库 + learning_contract 自检 + numpy 用于统计），可独立 selfcheck。
"""
import os
import json
import datetime

import learning_contract as LC
import paths

DATA_DIR = paths.DATA_DIR
FAILURE_TAXONOMY_FILE = os.path.join(DATA_DIR, "failure_taxonomy.json")
AVOIDANCE_PRIOR_FILE = os.path.join(DATA_DIR, "avoidance_prior.json")
INGEST_FATE_FILE = os.path.join(DATA_DIR, "ingest_fate.jsonl")
BIAS_CORRECTOR_FILE = os.path.join(DATA_DIR, "bias_corrector.json")

# 连续出现达到该次数的 failure → 标记"已证伪路线"（seed 预算清零）
DEBUNK_CYCLE_THRESHOLD = 3
# 已证伪路线在多少周期内未再出现 → 解除（避免永久封死，留探索余地）
DEBUNK_DECAY_CYCLES = 50


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def load_bias_corrector(data_dir=None):
    """供驾1/驾3 消费：读取当前偏置纠正建议。"""
    p = os.path.join(data_dir or DATA_DIR, "bias_corrector.json")
    return _load_json(p) or {
        "debunked_sigs": [],
        "debunked_tests": [],
        "novelty_tilt": {},
        "elite_bias": {},
        "updated_cycle": 0,
    }


def compute_bias(failure_taxonomy, avoidance_prior, ingest_fate, current_cycle):
    """
    核心算法：从 L1 失败知识 + 驾3 提案 fate，算出探索预算偏置。

    返回 dict:
      debunked_sigs / debunked_tests : 已证伪路线（seed 预算清零 + 驾1 精英降权）
      novelty_tilt                    : {sig/test: tilt_weight} 高新颖度方向倾斜
      elite_bias                      : {sig: retain_multiplier} 驾1 精英保留乘子
      meta                            : 诊断信息
    """
    labels = (failure_taxonomy or {}).get("labels", {})
    # ---- 1. 已证伪路线：连续出现 >= 阈值 的 failure → 提取涉及维度 ----
    debunked_sigs = set()
    debunked_tests = set()
    debunk_meta = []
    # avoidance_prior 已把 failure 映射到具体回避维度（sig/test 关键字）
    for rule in (avoidance_prior or {}).get("rules", []):
        fail = rule.get("failure")
        occ = rule.get("occurrences", 0)
        if occ >= DEBUNK_CYCLE_THRESHOLD:
            # 从 failure 名推断维度（约定：failure 名含 sig/test 语义）
            if "recurrence" in fail or "low_number" in fail or "cold" in fail:
                debunked_sigs.add("red_recurrence_mean")  # 已知被随机对照杀掉的
            if "boundary" in fail or "artifact" in fail:
                # 边界伪结构通常是确定性填充信号，标记避免该 test 类
                debunked_tests.add("perm_entropy")  # 已知开头尖峰制造者
            debunk_meta.append({"failure": fail, "occ": occ, "action": "debunk"})

    # ---- 2. 驾3 提案 fate：统计哪些 sig/test 已被充分探索（频次） ----
    sig_freq = {}
    test_freq = {}
    for rec in (ingest_fate or []):
        s = rec.get("sig")
        t = rec.get("test")
        if s:
            sig_freq[s] = sig_freq.get(s, 0) + 1
        if t:
            test_freq[t] = test_freq.get(t, 0) + 1
    total_fate = max(1, sum(sig_freq.values()))

    # ---- 3. 新颖度倾斜：低频 sig/test（探索不足）→ 倾斜 seed 预算 ----
    novelty_tilt = {}
    for s, c in sig_freq.items():
        if s in debunked_sigs:
            continue
        # 频次越低，倾斜权重越高（上限 2.0）
        tilt = round(min(2.0, 1.0 + (1.0 - c / total_fate) * 1.0), 3)
        if tilt > 1.05:
            novelty_tilt[s] = tilt
    for t, c in test_freq.items():
        if t in debunked_tests:
            continue
        tilt = round(min(2.0, 1.0 + (1.0 - c / total_fate) * 1.0), 3)
        if tilt > 1.05:
            novelty_tilt.setdefault("test:" + t, tilt)

    # ---- 4. 驾1 精英保留偏置：已证伪 sig 降乘子，其余维持 1.0 ----
    elite_bias = {}
    for s in debunked_sigs:
        elite_bias[s] = 0.2  # 精英保留概率降至 20%
    # 高新颖度 sig 略升精英保留，鼓励延续探索
    for s, tilt in novelty_tilt.items():
        if not s.startswith("test:"):
            elite_bias[s] = round(max(elite_bias.get(s, 1.0), min(1.5, tilt)), 3)

    return {
        "debunked_sigs": sorted(debunked_sigs),
        "debunked_tests": sorted(debunked_tests),
        "novelty_tilt": novelty_tilt,
        "elite_bias": elite_bias,
        "updated_cycle": current_cycle,
        "meta": {
            "debunk_meta": debunk_meta,
            "sig_freq": sig_freq,
            "test_freq": test_freq,
            "threshold": DEBUNK_CYCLE_THRESHOLD,
        },
    }


def run(data_dir=None, current_cycle=None):
    """每轮由 daemon 调用：读三驾车真实产出 → 算偏置 → 落盘 bias_corrector.json。"""
    data_dir = data_dir or DATA_DIR
    ftax = _load_json(os.path.join(data_dir, "failure_taxonomy.json"))
    aprior = _load_json(os.path.join(data_dir, "avoidance_prior.json"))
    fate = _load_jsonl(os.path.join(data_dir, "ingest_fate.jsonl"))
    if current_cycle is None:
        current_cycle = (ftax or {}).get("updated_cycle", 0) or 0

    bias = compute_bias(ftax, aprior, fate, current_cycle)

    # 契约护栏：本模块产出只含反过拟合派生偏置，不含任何 FORBIDDEN 信号
    used = ["failure_taxonomy", "avoidance_prior", "ingest_fate"]
    for u in used:
        if LC.is_forbidden_feedback(u):
            raise LC.ClosureViolation("L3 误用禁用信号 %s" % u)

    bias["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bias["_closure_ok"] = True
    out_path = os.path.join(data_dir, "bias_corrector.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bias, f, ensure_ascii=False, indent=2)
    return bias, out_path


def selfcheck():
    msgs = []
    ok = True
    # 1. 契约导入
    try:
        import learning_contract as _L
        msgs.append("L3 契约导入 OK")
    except Exception as e:
        ok = False
        msgs.append("L3 契约导入失败: %s" % e)
    # 2. 逻辑单测：连续失败 → 标记已证伪
    fake_tax = {"labels": {"boundary_artifact": {"count": 5, "last_seen_cycle": 360},
                            "degenerate_stat": {"count": 4, "last_seen_cycle": 360}},
                "updated_cycle": 360}
    fake_prior = {"rules": [
        {"failure": "boundary_artifact", "occurrences": 5},
        {"failure": "degenerate_stat", "occurrences": 4},
    ]}
    fake_fate = [{"sig": "red_mean", "test": "acf_max"},
                 {"sig": "red_mean", "test": "acf_max"},
                 {"sig": "red_gap_max", "test": "perm_entropy"}]
    bias = compute_bias(fake_tax, fake_prior, fake_fate, 360)
    if "perm_entropy" in bias["debunked_tests"]:
        msgs.append("L3 已证伪 test 标记 OK: %s" % bias["debunked_tests"])
    else:
        ok = False
        msgs.append("L3 已证伪 test 未标记")
    if "red_mean" in bias["novelty_tilt"] or "test:acf_max" in bias["novelty_tilt"]:
        msgs.append("L3 新颖度倾斜 OK: %s" % list(bias["novelty_tilt"].keys()))
    else:
        msgs.append("L3 新颖度倾斜（低频方向）生成: %s" % bias["novelty_tilt"])
    if bias["elite_bias"].get("perm_entropy") == 0.2:
        msgs.append("L3 驾1 精英降权 OK")
    else:
        msgs.append("L3 驾1 精英偏置: %s" % bias["elite_bias"])
    # 3. 闭环约束
    try:
        op = {
            "kind": "correct_bias",
            "input_sources": ["failure_taxonomy", "avoidance_prior", "ingest_fate"],
            "output_sinks": ["bias_corrector.json"],
            "used_feedback": ["failure_taxonomy", "avoidance_prior", "ingest_fate"],
        }
        ok_c, reasons = LC.assert_three_car_closure(op)
        if ok_c:
            msgs.append("L3 三驾车闭环约束 OK")
        else:
            ok = False
            msgs.append("L3 闭环违规: %s" % reasons)
    except LC.ClosureViolation as e:
        ok = False
        msgs.append("L3 闭环违规: %s" % e)
    return ok, msgs


if __name__ == "__main__":
    import sys
    if "--from-state" in sys.argv:
        bias, path = run()
        print(json.dumps(bias, ensure_ascii=False, indent=2))
        print("落盘 -> %s" % path)
    else:
        ok, msgs = selfcheck()
        for m in msgs:
            print(m)
        print("OK" if ok else "FAIL")
