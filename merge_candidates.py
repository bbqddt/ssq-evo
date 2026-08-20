# -*- coding: utf-8 -*-
"""merge_candidates.py —— 把各 seed 的候选 artifact 合并成一个 candidates.json

由 workflow 的 collect 任务调用：下载所有 cand-seed-* artifact 到 _cands 目录，
合并 candidates 列表，写 candidates.json（供驾1 拉取摄入）。

只做合并，不做任何统计判定——结论权在驾1 的 ingest 闸门。
"""
import os
import sys
import json
import glob


def main():
    src_dir = sys.argv[1] if len(sys.argv) > 1 else "_cands"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "candidates.json"

    files = sorted(glob.glob(os.path.join(src_dir, "candidates_seed_*.json")))
    all_cands = []
    metas = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                blob = json.load(f)
        except Exception as e:
            print("[merge] 跳过损坏文件 %s: %s" % (fp, e))
            continue
        cs = blob.get("candidates", [])
        all_cands.extend(cs)
        metas.append(blob.get("meta", {}))

    out = {
        "meta": {
            "role": "PROPOSAL_ONLY",
            "n_seed_files": len(files),
            "n_candidates": len(all_cands),
            "seed_metas": metas,
            "generated_by": "merge_candidates.py (workflow collect)",
        },
        "candidates": all_cands,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[merge] %d 个 seed 文件 -> %d 候选 -> %s"
          % (len(files), len(all_cands), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
