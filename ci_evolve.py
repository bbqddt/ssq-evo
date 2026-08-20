# -*- coding: utf-8 -*-
"""ci_evolve.py —— 驾3 分布式 GA 进化（无状态、吃静态快照）

每个 GitHub Actions runner 用不同 --seed 独立跑 engine_core.Evolution，
吐出 top-K 候选基因组 JSON（【提案】，非结论）。

分工（防 Goodhart / 不绕过统一闸门）：
  - 驾3（本脚本）：在【静态快照】上做多样化种子搜索，只负责「提案」候选基因组；
  - 驾1（ingest_candidates.py）：在【完整真实数据】上过统一闸门
    （label_axis 分层 null + random_control_label 构造伪结构拦截），
    只有 SURVIVOR 且未被构造伪结构降级者，才作为精英种子并入 frontier。

注意：CI 用快照、驾1 用真实全量，p 值本就会不同——结论权以驾1 闸门为准。
本脚本只是把搜索广度与种子多样性外包到云端免费算力。
"""
import os
import sys
import json
import argparse

import numpy as np
import data as D
import engine_core as E


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--data",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "data", "ssq_history.csv"))
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--pop", type=int, default=16)
    ap.add_argument("--k-light", type=int, default=15)
    ap.add_argument("--k-heavy", type=int, default=6)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    m = D.load_master(args.data)
    if not m:
        print("[ci_evolve] 无数据:", args.data)
        sys.exit(2)
    reds, blues, _issues = D.to_arrays(m)
    N = int(reds.shape[0])
    print("[ci_evolve] seed=%d 载入 %d 期快照" % (args.seed, N))

    rng = np.random.default_rng(args.seed)
    evo = E.Evolution(reds, blues, rng,
                      epochs=args.epochs, pop=args.pop,
                      k_light=args.k_light, k_heavy=args.k_heavy,
                      sur_type="aaft", n_workers=0)
    leaderboard, all_evals = evo.run()

    items = sorted(leaderboard.values(),
                   key=lambda e: (e.get("p_raw") if e.get("p_raw") is not None else 1.0)
                   )[:args.top_k]
    cands = []
    for e in items:
        cands.append({
            "sig": e.get("sig"),
            "test": e.get("test"),
            "params": e.get("params"),
            "p_raw": e.get("p_raw"),
            "z": e.get("z"),
            "tier": e.get("tier"),
        })

    out = {
        "meta": {
            "seed": args.seed,
            "data_rows": N,
            "epochs": args.epochs,
            "pop": args.pop,
            "k_light": args.k_light,
            "k_heavy": args.k_heavy,
            "n_evaluated": len(all_evals),
            "git_sha": os.environ.get("GITHUB_SHA", "local"),
            "role": "PROPOSAL_ONLY",
        },
        "candidates": cands,
    }
    out_path = args.out or ("candidates_seed_%d.json" % args.seed)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[ci_evolve] 写出 %d 候选 -> %s" % (len(cands), out_path))
    if cands:
        b = cands[0]
        print("[ci_evolve] best: sig=%s test=%s p_raw=%s z=%s"
              % (b.get("sig"), b.get("test"), b.get("p_raw"), b.get("z")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
