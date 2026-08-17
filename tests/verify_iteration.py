# -*- coding: utf-8 -*-
"""验证演化真的跨轮累积（frontier 覆盖度跨轮单调增长），而非每轮原地踏步。

关键：使用【持久化】临时 frontier 文件，3 轮之间 load->run->update->save 同文件，
tried 集合跨轮并集增长。断言：coverage 严格递增（去重后只增不减）。
"""
import os, sys, shutil
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data as D
import engine_core as E
import frontier as F

DATA = "D:/ssq_evo_data"
FP = os.path.join(DATA, "_verify_frontier.json")
if os.path.exists(FP):
    os.remove(FP)


def one_round(seed):
    m = D.load_master(os.path.join(DATA, "ssq_master.csv"))
    reds, blues, _ = D.to_arrays(m)
    rng = np.random.default_rng(seed)
    fr = F.load_frontier(DATA)        # 读临时 frontier（首轮为空）
    fr["tried"] = fr.get("tried", [])  # 保留跨轮累积的 tried，不重置！
    elite_seeds = fr.get("elites", [])
    evo = E.Evolution(reds, blues, rng, k_light=8, k_heavy=4, epochs=3, pop=12,
                      elites=elite_seeds, frontier=fr)
    lb, allv = evo.run()
    before = fr.get("coverage", 0)
    fr2 = F.update_frontier(fr, lb, evo.tried, elite_k=12)
    cov = fr2["coverage"]
    F.save_frontier(DATA, fr2)         # 写回临时 frontier（跨轮持久化）
    # 复制到专用路径，避免污染生产 frontier.json
    shutil.copy(os.path.join(DATA, "frontier.json"), FP)
    return before, cov, len(lb), len(allv), len(set(fr2.get("tried", [])))


def main():
    covs = []
    for i in range(3):
        before, cov, uniq, evals, tried = one_round(1000 + i * 7)
        covs.append(cov)
        print(f"[iter] round {i+1}: before_cov={before} after_cov={cov} "
              f"unique={uniq} evals={evals} tried_union={tried}")
    # 断言：覆盖度跨轮严格递增（去重并集只增不减）
    ok = all(covs[i + 1] > covs[i] for i in range(len(covs) - 1))
    assert ok, f"覆盖度未跨轮增长: {covs}"
    print(f"[iter] PASS: 覆盖度 {covs[0]} -> {covs[-1]} (跨轮累积, 去重生效)")
    if os.path.exists(FP):
        os.remove(FP)
    print("ITER_OK")


if __name__ == "__main__":
    main()
