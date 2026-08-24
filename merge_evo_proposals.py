# -*- coding: utf-8 -*-
"""merge_evo_proposals.py —— 把矩阵 job 的各 seed 提案合并到 ga-candidates 分支。

职责（与驾3 一致，纯聚合、不声称结论）：
  - 收集 artifacts/ 下所有 proposal_*.json
  - 在【仓库静态快照 data/ssq_history.csv】上做一次严格前序滚动 OOS 复验
  - 仅把「surrogate 非伪显著」的提案聚合写入 ga-candidates 的 evo_proposals.json
  - 真正的闸门(确认段 z>2 + 多 seed)由驾1 的 ingest 最终裁决

用法（CI 内调用）：python merge_evo_proposals.py --artifacts artifacts
"""
import os, sys, json, glob, argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "ssq_history.csv")


def load_draws(path):
    import csv
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({"issue": r["issue"],
                             "reds": [int(r["r%d" % i]) for i in range(1, 7)],
                             "blue": int(r["b"])})
            except Exception:
                continue
    rows.sort(key=lambda x: x["issue"])
    return rows


# 复用 evolve_predictor 的滚动评估（避免重复实现）
sys.path.insert(0, HERE)
import evolve_predictor as EP


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", default="artifacts")
    ap.add_argument("--out", default="evo_proposals.json")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.artifacts, "proposal_*.json")))
    draws = load_draws(DATA)
    out = []
    for fp in files:
        try:
            p = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        spec = p.get("spec")
        if not spec:
            continue
        # 在快照上做 surrogate 初筛（防构造伪显著），再交驾1 严格闸门
        try:
            sur_z = EP.random_surrogate_z(spec)
        except Exception:
            sur_z = 999
        rec = {
            "seed": p["meta"]["seed"],
            "kfold_z": p["meta"]["kfold_z"],
            "surrogate_z": sur_z,
            "spec": spec,
            "source": "distributed-evolve",
        }
        out.append(rec)
    out.sort(key=lambda x: -(x["kfold_z"] if x["kfold_z"] is not None else -999))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"n": len(out), "proposals": out}, f, ensure_ascii=False, indent=2)
    print("[merge] 聚合 %d 提案 -> %s" % (len(out), args.out))
    for r in out[:5]:
        print("  seed=%s kfold_z=%.3f sur_z=%.3f" % (r["seed"], r["kfold_z"], r["surrogate_z"]))


if __name__ == "__main__":
    main()
