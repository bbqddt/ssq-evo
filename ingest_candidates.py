# -*- coding: utf-8 -*-
"""ingest_candidates.py —— 驾1 摄入驾3 提案候选，过统一闸门后并入 frontier

流程：
  1. 取驾3 推到 ga-candidates 分支的 candidates.json
     （git fetch + git show，不触碰工作树；公开仓库无需鉴权）；
     也可用 --local <file> 做本地测试/干跑。
  2. 对每条候选，在【完整真实数据】上跑 run_axes.label_axis（分层 null）
     + random_control_label（构造伪结构拦截）。
  3. 只有 label==SURVIVOR 且未触发构造伪结构者，作为精英种子并入 frontier.json；
     驾1 下一轮 GA 会以这些种子起种群。

诚实护栏（不可协商）：
  - 候选只是「提案」，绝不绕过统一闸门直接进生产；
  - 若真实数据上无任何候选过闸门 → 诚实结论「提案未获确认」，不强行并入；
  - 这把云端免费算力的搜索广度/种子多样性，安全地嫁接进唯一真相源。
"""
import os
import sys
import json
import argparse
import subprocess

import numpy as np
import data as D
import run_axes as RA
import frontier as FR
import engine_core as E

DATA_DIR = os.environ.get("DATA_DIR", "D:/ssq_evo_data")
REPO = os.path.dirname(os.path.abspath(__file__))
BRANCH = "ga-candidates"
CAND_FILE = "candidates.json"
GATE_SEED = 20260820


def fetch_candidates(local_path=None):
    if local_path:
        try:
            with open(local_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("[ingest] 本地文件读取失败:", e)
            return None
    try:
        subprocess.run(["git", "fetch", "origin", BRANCH],
                       cwd=REPO, check=True, capture_output=True)
    except Exception as e:
        print("[ingest] git fetch 失败（可能分支尚未创建）:", e)
        return None
    try:
        out = subprocess.run(["git", "show", "origin/%s:%s" % (BRANCH, CAND_FILE)],
                             cwd=REPO, check=True, capture_output=True, text=True).stdout
        return json.loads(out)
    except Exception as e:
        print("[ingest] 读取 candidates.json 失败（分支/文件可能不存在）:", e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default=None, help="本地候选文件（干跑/测试用）")
    ap.add_argument("--dry-run", action="store_true", help="只评估不写入 frontier")
    args = ap.parse_args()

    master = os.path.join(DATA_DIR, "ssq_master.csv")
    m = D.load_master(master)
    if not m:
        print("[ingest] 无真实数据:", master)
        return 2
    reds, blues, _ = D.to_arrays(m)
    N = int(reds.shape[0])
    rng = np.random.default_rng(GATE_SEED)

    blob = fetch_candidates(args.local)
    if not blob:
        print("[ingest] 无候选可摄入")
        return 0
    cands = blob.get("candidates", [])
    print("[ingest] 载入 %d 候选（云端提案），在 %d 期真实数据上过闸门"
          % (len(cands), N))

    f = FR.load_frontier(DATA_DIR)
    added = 0
    rejected = 0
    for c in cands:
        sig, test, params = c.get("sig"), c.get("test"), (c.get("params") or {})
        if sig is None or test is None:
            continue
        rec = RA.label_axis(sig, [test], reds, blues, rng, k_sur=40, params=params)
        ctrl = RA.random_control_label(sig, [test], N, seed=GATE_SEED, k_sur=60)
        label = rec.get("label")
        artifact = (ctrl == "SURVIVOR")  # 纯随机也 SURVIVOR => 构造伪结构
        if label == "SURVIVOR" and not artifact:
            g = {"sig": sig, "test": test, "params": params}
            gkey = E.genome_key(sig, test, params)
            if g not in f["elites"]:
                f["elites"].append(g)
                f["tried"].append(gkey)
                added += 1
                print("  [并入精英] sig=%s test=%s p_shuffle=%s"
                      % (sig, test, rec.get("p_shuffle")))
            else:
                print("  [已存在] sig=%s test=%s" % (sig, test))
        else:
            rejected += 1
            print("  [拒绝] sig=%s test=%s label=%s artifact=%s"
                  % (sig, test, label, artifact))

    print("[ingest] 通过闸门=%d  拒绝=%d  新增精英=%d" % (added, rejected, added))
    if added and not args.dry_run:
        FR.save_frontier(DATA_DIR, f)
        print("[ingest] 已写入 frontier（精英种子=%d）" % len(f["elites"]))
    elif args.dry_run:
        print("[ingest] DRY-RUN：未写入")
    else:
        print("[ingest] 本轮无候选通过闸门（诚实结论：提案未获确认）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
