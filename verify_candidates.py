# -*- coding: utf-8 -*-
"""云端候选【预过滤】脚本 —— 不是闸门，也不是验证器
================================================
诚实定位（诚实红线契约）：
  - 本脚本运行在 GitHub Actions（驾3 云端），只接收 ci_evolve.py 的 PROPOSAL_ONLY 产物。
  - 它只做三件【真实存在但有限】的事：结构/类型校验、跨 seed 去重、来源(provenance)汇总。
  - 它【不做】BH-FDR、【不做】OOT、【不做】随机重放、【不做】任何显著性判定。
    它没有这些能力，也绝不假称具备（护栏契约：未经验证的层不得冒充闸门）。
  - 输出的每个候选 verdict 恒为 PENDING_LOCAL_VERIFICATION；
    SIGNAL/NULL 判定权唯一属于本地驾1（ingest_candidates.py，真实数据 D:\\ssq_evo_data，
    完整闸门 = label_axis 分层 null + BH-FDR + OOT + random_control_label + #41 walk-forward）。
  - 如实描述现有晋级链（不是本脚本加的锁）：过闸 SURVIVOR 种子自动并入 frontier.json（既有设计）；
    晋级【生产候选集】由 firewall.promote() 强制 human_signoff=True，无人签字即拒绝。
    本脚本自身不新增任何闸门，只做结构预过滤与留痕。

调用方式（workflow collect job）：
  python verify_candidates.py --candidates _cands --output verified_candidates.json
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

REQUIRED_KEYS = ("sig", "test")          # params 允许为 None（部分 test 无参）
VALID_TYPES = {"sig": str, "test": str}


def load_seed_files(path):
    """加载 seed 提案文件。path 可为目录（ci_evolve 产物 *_seed_*.json / candidates_seed_*.json）
    或单个 JSON 文件。返回 (candidates_with_provenance, rejected, metas)。"""
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.json")))
    elif any(ch in path for ch in "*?["):
        files = sorted(glob.glob(path))
    else:
        files = [path]
    if not files:
        raise FileNotFoundError(f"no candidate files found at: {path}")

    cands, rejected, metas = [], [], []
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                blob = json.load(f)
        except Exception as e:
            rejected.append({"file": fp, "reason": f"unparseable_json: {e!r}"[:200]})
            continue
        meta = blob.get("meta", {})
        if meta.get("role") != "PROPOSAL_ONLY":
            # 诚实红线：只接受明确标注 PROPOSAL_ONLY 的产物，其余一律拒收并留痕
            rejected.append({"file": fp,
                             "reason": f"role!=PROPOSAL_ONLY (got {meta.get('role')!r})"})
            continue
        metas.append({"file": os.path.basename(fp), **meta})
        for c in blob.get("candidates", []):
            cands.append({"genome": c,
                          "source_file": os.path.basename(fp),
                          "source_seed": meta.get("seed"),
                          "source_git_sha": meta.get("git_sha")})
    return cands, rejected, metas


def prefilter(cands):
    """真实且有限的预过滤：结构/类型校验 + 跨 seed 去重。无任何统计判定。"""
    accepted, rejected = [], []
    seen = set()
    for item in cands:
        g = item["genome"]
        # 1) 结构/类型校验（真实检查，失败即拒收留痕）
        bad = None
        if not isinstance(g, dict):
            bad = "genome_not_dict"
        else:
            for k, t in VALID_TYPES.items():
                if not isinstance(g.get(k), t):
                    bad = f"key_{k}_type_{t.__name__}"
                    break
            if bad is None and "params" in g and g["params"] is not None \
                    and not isinstance(g["params"], (dict, list, int, float, str)):
                bad = "key_params_type"
        if bad:
            rejected.append({"source_file": item["source_file"],
                             "source_seed": item["source_seed"],
                             "genome": g, "reason": bad})
            continue
        # 2) 跨 seed 去重（同 sig/test/params 视为同一提案，保留首个并记录重复数）
        key = json.dumps([g.get("sig"), g.get("test"), g.get("params")],
                         sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            item["duplicate_of_earlier"] = True
        else:
            seen.add(key)
        # 3) 判定权声明：预过滤无权给出任何显著性结论
        item["verdict"] = "PENDING_LOCAL_VERIFICATION"
        item["human_signoff_required"] = True
        accepted.append(item)
    return accepted, rejected


def main():
    ap = argparse.ArgumentParser(
        description="Cloud PREFILTER for GA proposals (NOT a gate)")
    ap.add_argument("--candidates", required=True,
                    help="seed 提案目录或 JSON 文件（ci_evolve 产物）")
    ap.add_argument("--output", required=True, help="预过滤审计输出路径")
    args = ap.parse_args()

    raw, broken, metas = load_seed_files(args.candidates)
    accepted, rejected = prefilter(raw)

    out = {
        "role": "PREFILTER_AUDIT_ONLY",
        "gate_owner": "local 驾1 ingest_candidates.py (D:\\ssq_evo_data, full gate)",
        "verdict_semantics": "PENDING_LOCAL_VERIFICATION 是本脚本唯一可能的 verdict；"
                             "本脚本无 BH-FDR/OOT/随机重放能力，不判定 SIGNAL/NULL",
        "human_signoff_required_before_frontier": True,
        "prefilter_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "seed_files": metas,
        "n_input": len(raw) + len(broken),
        "n_accepted": len(accepted),
        "n_rejected": len(rejected) + len(broken),
        "accepted": accepted,
        "rejected": broken + rejected,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[prefilter] 输入 {out['n_input']} 提案 -> 结构合格 {len(accepted)}"
          f" /拒收 {out['n_rejected']} -> {args.output}")
    for r in (broken + rejected)[:10]:
        print(f"[prefilter] REJECT {r.get('file', r.get('source_file'))}: {r['reason']}")
    print("[prefilter] 声明：本步骤不是闸门。SIGNAL/NULL 判定只属于本地统一闸门"
          "（ingest_candidates.py）；生产晋级必须由 firewall.promote(human_signoff=True) 人类签字。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
