# -*- coding: utf-8 -*-
"""
failure_absorber.py —— 学习模块 L1：失败吸收器（纠正「走错的路」）
================================================================

定位（呼应 2026-08-22 用户论断：学习要在错误道路上纠正/吸收/改良）：
  搜索（GA/谱/因果）只会「在给定假设空间里找最优」；它走过的路若全是死胡同，
  也只是重复踩。失败吸收器让系统**把失败编码成知识**：
    - failure_taxonomy：每类失败为什么失败（周期性幻觉 / 边界伪结构 / 样本不足 /
      低频球偏倚 / 冷号陷阱 / 退化统计 / 单折脆弱确认 …）。
    - avoidance_prior：新候选在 discovery 段先跑「失败自查清单」，命中已知失败模式
      → 降权或拦截，让三驾车主动少走死胡同。

基石约束（learning_contract，永久执行）：
  - 基石二：输入必须来自三驾车真实产出（驾1 闸门 state + 驾3 提案过闸 fate）；
           产出必须回馈三驾车（avoidance_prior 注入驾1+驾3 候选生成）。
  - 基石一：本模块只用反过拟合信号（oot_blind_p / wf_verdict / random_control_label …），
           绝不读 in_sample_accuracy 等 FORBIDDEN。
  - 基石四：本模块只【记录+回馈偏置】，绝不自动 merge 任何结构进 SIGMAPS。

数据流：
  驾1 state.json + 驾3 ingest_fate.jsonl
        │
        ▼
  classify_failures()  →  failure_taxonomy（带计数/最近观测）
        │
        ▼
  build_avoidance_prior()  →  avoidance_prior.json（回馈落点）
        │
        ▼
  run_cycle / ci_evolve 读 avoidance_prior 生成候选时降权/拦截（基石二回馈）

零重型依赖：只依赖 learning_contract（同目录）。可被 pytest 单测。
"""
import os
import json
import datetime

import learning_contract as LC
import paths


DATA_DIR = paths.DATA_DIR
FAILURE_TAXONOMY_FILE = "failure_taxonomy.json"
AVOIDANCE_PRIOR_FILE = "avoidance_prior.json"
INGEST_FATE_FILE = "ingest_fate.jsonl"

# 失败分类标签（人类可读 + 机器可查）
FAILURE_LABELS = {
    "periodic_hallucination": "周期性幻觉（acf/perm_entropy 在随机数据也显著，AAFT 零假设下不消失）",
    "boundary_artifact": "边界伪结构（random_control_label 判 ARTIFACT_BY_CONSTRUCTION，确定性填充惩罚值造成开头尖峰）",
    "small_sample": "样本不足（OOT/confirm 折数太少，单点 p 不可信）",
    "low_number_bias": "低频球偏倚（朴素边际法系统性低号偏倚，破同分规则小球优先）",
    "cold_number_trap": "冷号陷阱（递归率/相空间类信号在冷号段出现确定性伪显著）",
    "degenerate_stat": "退化统计（best_z 荒谬，检验 stat 分母近零，显著性不可信）",
    "single_fold_fragile": "单折脆弱确认（#41 确认段仅 1 折，复现不稳）",
    "multiplicity_noise": "多重比较噪声（best_p 落在 null 期望最小 p 量级，非结构）",
    "null_honest": "诚实 null（全部闸门 UNCONFIRMED，域大概率无结构 — 这是正确结论，非失败）",
}


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
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
    except Exception:
        return []
    return out


def classify_car1_failures(state):
    """从驾1 闸门 state 抽取失败类型清单。

    返回 list[str]（FAILURE_LABELS 的键）。只基于白名单反馈信号，绝不碰 FORBIDDEN。
    """
    labels = []
    if state is None:
        return labels

    best_q = state.get("best_q")
    fdr_q = state.get("fdr_q", 0.05)
    wf_verdict = state.get("wf_verdict")
    wf_n_confirm = state.get("wf_n_confirm")
    alert = state.get("alert")
    artifact_prone = state.get("artifact_prone") or []
    z_hist = state.get("best_z_history") or []
    n_eval = state.get("n_eval")
    best_p = state.get("best_p")

    # 退化统计：best_z 荒谬
    for v in z_hist:
        if isinstance(v, (int, float)) and (abs(v) > 1e6):
            labels.append("degenerate_stat")
            break

    # 边界伪结构：被随机对照闸门标记的 artifact_prone 信号
    if artifact_prone:
        labels.append("boundary_artifact")

    # 单折脆弱确认：#41 仅 1 折
    if wf_verdict == "SIGNAL" and (wf_n_confirm is not None and wf_n_confirm < 2):
        labels.append("single_fold_fragile")

    # 多重比较噪声：best_p 落在 null 期望最小 p 量级但过了 FDR
    if best_q is not None and best_q < fdr_q and best_p is not None and n_eval:
        expected_min_p = 1.0 / (n_eval + 1)
        if best_p > expected_min_p:
            labels.append("multiplicity_noise")

    # 诚实 null：全部 UNCONFIRMED（非失败，但记录以便看板区分）
    if (best_q is None or best_q >= fdr_q) and not alert and wf_verdict != "SIGNAL":
        labels.append("null_honest")

    return labels


def classify_car3_fate(fate_records):
    """从驾3 提案过闸记录抽取失败类型。

    fate_records: list[dict]，每条含 {sig, test, label, artifact}。
    返回 list[str]。
    """
    labels = []
    if not fate_records:
        return labels
    n_rej = sum(1 for r in fate_records if r.get("label") != "SURVIVOR")
    n_artifact = sum(1 for r in fate_records if r.get("artifact"))
    n_total = len(fate_records)
    if n_total and n_artifact / n_total > 0.3:
        # 驾3 提案里大量在随机数据也 SURVIVOR → 提案族偏好边界/伪结构
        labels.append("boundary_artifact")
    if n_total and n_rej / n_total > 0.8:
        # 绝大多数提案被拒 → 驾3 搜索空间大概率偏离真结构
        labels.append("multiplicity_noise")
    return labels


def classify_failures(state, fate_records=None):
    """合并驾1 + 驾3 的失败分类（契约基石二：输入来自三驾车）。"""
    labels = classify_car1_failures(state)
    labels += classify_car3_fate(fate_records or [])
    # 去重保序
    seen = set()
    uniq = []
    for l in labels:
        if l not in seen:
            seen.add(l)
            uniq.append(l)
    return uniq


def build_avoidance_prior(taxonomy, decay=0.9):
    """从 failure_taxonomy 构建 avoidance_prior（回馈落点）。

    规则：
      - 出现次数越多、最近越频繁 → prior_weight 越高（越该避开）。
      - null_honest 不计为「要避开」（它是诚实结论，不是错路）。
      - 每条 prior 附「自查动作」：新候选命中即降权/拦截。
    返回 dict（可直接 JSON 序列化）。
    """
    prior = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "rules": []}
    for key, info in taxonomy.get("labels", {}).items():
        if key == "null_honest":
            continue
        count = info.get("count", 0)
        if count <= 0:
            continue
        weight = round(1.0 - decay ** count, 4)  # 次数越多越接近 1（强避开）
        prior["rules"].append({
            "failure": key,
            "meaning": FAILURE_LABELS.get(key, key),
            "occurrences": count,
            "avoid_weight": weight,
            "action": "discovery_selfcheck",  # 新候选先跑对应自查，命中则降权
        })
    prior["n_rules"] = len(prior["rules"])
    return prior


def update_taxonomy(prev_taxonomy, new_labels, cycle_id):
    """累加式更新 failure_taxonomy（记忆跨轮累积）。

    prev_taxonomy: dict 或 None；new_labels: list[str]；cycle_id: int。
    返回新 taxonomy dict（含 labels{key:{count,last_seen_cycle}}）。
    """
    tax = {"labels": {}, "updated_cycle": cycle_id}
    if prev_taxonomy and isinstance(prev_taxonomy.get("labels"), dict):
        tax["labels"] = {k: dict(v) for k, v in prev_taxonomy["labels"].items()}
    for l in new_labels:
        if l not in tax["labels"]:
            tax["labels"][l] = {"count": 0, "last_seen_cycle": None}
        tax["labels"][l]["count"] = tax["labels"][l]["count"] + 1
        tax["labels"][l]["last_seen_cycle"] = cycle_id
    return tax


def run(state, fate_records=None, data_dir=DATA_DIR):
    """L1 主入口：吸收一轮失败 → 更新 taxonomy → 构建 avoidance_prior → 落盘。

    满足 learning_contract 基石二：声明输入来自三驾车、产出回馈三驾车。
    返回 (taxonomy, prior, closure_ok, closure_reasons)。
    """
    # —— 契约：声明本次操作的闭环来源（基石二）——
    op = {
        "kind": "absorb",
        "input_sources": ["car1_gate_state"],
        "output_sinks": ["avoidance_prior_injection"],
        "used_feedback": ["oot_blind_p", "bh_fdr_q", "random_control_label",
                          "wf_verdict", "zero_hypothesis_cross"],
    }
    if fate_records:
        op["input_sources"].append("car3_proposal_fate")
    closure_ok, closure_reasons = LC.assert_three_car_closure(op)
    if not closure_ok:
        # 闭环约束违反：按契约不得静默继续，抛异常让 daemon 感知
        raise LC.ClosureViolation("L1 失败吸收器闭环约束违反: " + "; ".join(closure_reasons))

    # 驾3 fate 若未显式传入，尝试从数据卷读 ingest_fate.jsonl（结构化落盘）
    if fate_records is None:
        fate_records = _load_jsonl(os.path.join(data_dir, INGEST_FATE_FILE))
        if fate_records:
            op["input_sources"].append("car3_proposal_fate")

    cycle_id = (state or {}).get("cycle_id", 0)
    new_labels = classify_failures(state, fate_records)
    prev = _load_json(os.path.join(data_dir, FAILURE_TAXONOMY_FILE))
    taxonomy = update_taxonomy(prev, new_labels, cycle_id)
    prior = build_avoidance_prior(taxonomy)

    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, FAILURE_TAXONOMY_FILE), "w", encoding="utf-8") as f:
        json.dump(taxonomy, f, ensure_ascii=False, indent=2)
    with open(os.path.join(data_dir, AVOIDANCE_PRIOR_FILE), "w", encoding="utf-8") as f:
        json.dump(prior, f, ensure_ascii=False, indent=2)

    return taxonomy, prior, closure_ok, closure_reasons


def selfcheck():
    """自检：契约一致 + 分类函数基本可用。"""
    ok = True
    msgs = []
    # 契约闭环自检（模拟一次操作）
    fake_state = {"cycle_id": 1, "best_q": 0.04, "fdr_q": 0.05, "best_z_history": [1e9],
                  "artifact_prone": ["red_recurrence_mean"], "wf_verdict": "SIGNAL",
                  "wf_n_confirm": 1, "best_p": 0.01, "n_eval": 600}
    fake_fate = [{"sig": "x", "test": "y", "label": "REJECT", "artifact": False}]
    try:
        tax, prior, cok, creasons = run(fake_state, fake_fate, data_dir=".")
        msgs.append("L1 端到端 run() OK；taxonomy labels=%s" % list(tax["labels"].keys()))
        msgs.append("avoidance_prior rules=%d" % prior["n_rules"])
        if not cok:
            ok = False
            msgs.append("闭环约束违反: " + "; ".join(creasons))
    except Exception as e:
        ok = False
        msgs.append("L1 run() 异常: %s" % e)
    return ok, msgs


def main():
    """CLI：默认跑 selfcheck；--from-state 从真实 state.json + ingest_fate 跑 L1。"""
    import argparse
    ap = argparse.ArgumentParser(description="L1 失败吸收器")
    ap.add_argument("--from-state", action="store_true",
                    help="从 DATA_DIR/state.json + ingest_fate.jsonl 跑 L1（daemon 调用）")
    ap.add_argument("--state", default=None, help="state.json 路径（默认 DATA_DIR/state.json）")
    a = ap.parse_args()

    if not a.from_state:
        ok, msgs = selfcheck()
        for m in msgs:
            print(m)
        print("OK" if ok else "FAIL")
        return

    state_path = a.state or os.path.join(DATA_DIR, "state.json")
    state = _load_json(state_path)
    if state is None:
        print("[L1] 无 state.json（首轮或加载失败），跳过")
        return
    fate_records = _load_jsonl(os.path.join(DATA_DIR, INGEST_FATE_FILE))
    try:
        tax, prior, cok, creasons = run(state, fate_records, data_dir=DATA_DIR)
        print("[L1] taxonomy labels=%s" % list(tax["labels"].keys()))
        print("[L1] avoidance_prior rules=%d" % prior["n_rules"])
        print("[L1] 闭环约束 OK=%s" % cok)
    except LC.ClosureViolation as e:
        print("[L1] ALERT 闭环约束违反: %s" % e)


if __name__ == "__main__":
    main()
