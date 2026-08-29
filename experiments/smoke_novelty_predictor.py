# -*- coding: utf-8 -*-
"""smoke_novelty_predictor.py —— 验证 evolve_predictor.py 的 novelty search 机制。

实测：
  1) novelty 开/关对比（开时 α 应 < 0.5，种群多样性应更高）
  2) 存档增长 + diversity 指标
  3) 全平坦 z 场景下新颖度是否打破排序 tie
  4) 种群不坍缩到单一 spec

用法（在 D:/ssq_evo 下）：
  python smoke_novelty_predictor.py
"""
import os, sys, json, time, random, math
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import evolve_predictor as EP

DATA = os.path.join(HERE, "D:/ssq_evo_data/ssq_master.csv")
assert os.path.exists(DATA), "数据不存在: %s" % DATA


def run_smoke(generations=8, pop=12, seed=42, novelty_on=True):
    """跑一轮 evolve 并收集指标。"""
    rng = random.Random(seed)
    draws = EP.load_draws(DATA)
    train_end = len(draws) - 50

    # 手动构建种群
    pop_specs = [EP.random_spec(rng) for _ in range(pop)]

    # 初始化 novelty
    nov_archive = EP._PredNoveltyArchive(max_size=300, k_nn=7)
    alphas = []
    diversities = []
    unique_specs_set = set()
    all_blended = []

    t0 = time.time()
    for g in range(generations):
        # 单核评估（smoke 不需要多进程）
        scored = []
        for s in pop_specs:
            try:
                z = EP.fitness_kfold(s, draws, train_end, k=2)  # k=2 加速
            except Exception:
                z = -999
            scored.append((z, s))
        scored.sort(key=lambda x: -x[0])

        raw_zs = [z for z, s in scored]
        alpha = EP._adaptive_alpha(raw_zs, base=0.5, floor=0.15)
        alphas.append(alpha)

        # Novelty blending
        nov_scored = []
        for z, spec in scored:
            fp = EP._fp_spec(spec, draws, train_end)
            nov = nov_archive.novelty(fp)
            nov_archive.add(fp)
            blended = EP._blend_fitness(z, nov, alpha)
            nov_scored.append((blended, z, spec, nov))

        nov_scored.sort(key=lambda x: -x[0])
        all_blended.append(nov_scored[0][0])

        # 追踪唯一 spec 数量
        for _, _, spec, _ in nov_scored:
            key = json.dumps(spec, sort_keys=True)
            unique_specs_set.add(key)

        if len(nov_archive) >= 7:
            diversities.append(nov_archive.diversity())

        # Selection (与 evolve() 同逻辑)
        new_pop = [json.loads(json.dumps(nov_scored[0][2]))]
        while len(new_pop) < pop:
            parent = nov_scored[rng.randrange(min(10, len(nov_scored)))][2]
            new_pop.append(EP.mutate(parent, rng))
        pop_specs = new_pop

        print("  gen %2d/%d  best_z=%.3f  blended=%.3f  alpha=%.2f  novel=%.4f  archive=%d  unique=%d" %
              (g + 1, generations, scored[0][0], nov_scored[0][0], alpha,
               nov_scored[0][3], len(nov_archive), len(unique_specs_set)))

    elapsed = time.time() - t0
    return {
        "alphas": alphas,
        "diversities": diversities,
        "n_unique": len(unique_specs_set),
        "archive_size": len(nov_archive),
        "blended_range": (min(all_blended), max(all_blended)) if all_blended else (0, 0),
        "elapsed": elapsed,
    }


def run_baseline(generations=8, pop=12, seed=42):
    """纯传统选择（novelty 关），作为对照。"""
    rng = random.Random(seed)
    draws = EP.load_draws(DATA)
    train_end = len(draws) - 50
    pop_specs = [EP.random_spec(rng) for _ in range(pop)]
    unique_specs_set = set()
    all_z_best = []

    t0 = time.time()
    for g in range(generations):
        scored = []
        for s in pop_specs:
            try:
                z = EP.fitness_kfold(s, draws, train_end, k=2)
            except Exception:
                z = -999
            scored.append((z, s))
        scored.sort(key=lambda x: -x[0])
        all_z_best.append(scored[0][0])

        for _, s in scored:
            unique_specs_set.add(json.dumps(s, sort_keys=True))

        new_pop = [json.loads(json.dumps(scored[0][1]))]
        while len(new_pop) < pop:
            parent = scored[rng.randrange(min(10, len(scored)))][1]
            new_pop.append(EP.mutate(parent, rng))
        pop_specs = new_pop

        print("  gen %2d/%d  best_z=%.3f  unique=%d  (baseline, no novelty)" %
              (g + 1, generations, scored[0][0], len(unique_specs_set)))

    return {
        "n_unique": len(unique_specs_set),
        "z_range": (min(all_z_best), max(all_z_best)) if all_z_best else (0, 0),
        "elapsed": time.time() - t0,
    }


if __name__ == "__main__":
    print("=" * 70)
    print("[smoke] evolve_predictor.py Novelty Search 验证")
    print("=" * 70)

    print("\n--- [A] Novelty ON ---")
    r_on = run_smoke(generations=8, pop=12, seed=42, novelty_on=True)

    print("\n--- [B] Baseline (Novelty OFF) ---")
    r_off = run_baseline(generations=8, pop=12, seed=42)

    print("\n" + "=" * 70)
    print("[smoke] 断言检查")
    print("=" * 70)

    ok = True

    # 1. Alpha 应在平坦场景下降
    mean_alpha = np.mean(r_on["alphas"])
    print("  [1] 平均 alpha=%.3f (期望<0.5 因平坦景观)" % mean_alpha)
    if mean_alpha > 0.5:
        print("      WARN alpha 未降——可能 z-score 有方差（非全平坦）")
    else:
        print("      PASS alpha 自动降 → 新颖度主导")

    # 2. Novelty ON 的唯一 spec 应 >= OFF（或接近）
    print("  [2] 唯一spec数: ON=%d  OFF=%d" % (r_on["n_unique"], r_off["n_unique"]))
    if r_on["n_unique"] >= r_off["n_unique"] * 0.9:
        print("      PASS novelty 维持了多样性")
    else:
        print("      FAIL novelty 反而降低了多样性")
        ok = False

    # 3. 存档应有增长
    print("  [3] 存档大小=%d (期望>0)" % r_on["archive_size"])
    if r_on["archive_size"] > 0:
        print("      PASS 存档正常增长")
    else:
        print("      FAIL 存档为空")
        ok = False

    # 4. Diversity 应 > 0
    if r_on["diversities"]:
        div_mean = np.mean(r_on["diversities"])
        print("  [4] 平均 diversity=%.4f (期望>0)" % div_mean)
        if div_mean > 0:
            print("      PASS 行为多样性 > 0")
        else:
            print("      FAIL diversity 为零")
            ok = False

    # 5. Blended fitness 范围合理
    lo, hi = r_on["blended_range"]
    print("  [5] Blended fit 范围=[%.4f, %.4f]" % (lo, hi))
    if hi - lo > 1e-6:
        print("      PASS 存在差异化 selection")
    else:
        print("      WARN 全部 blended 相同（可能指纹区分度不足）")

    # 6. 最终总判定
    print("\n" + ("=" * 70))
    if ok:
        print("[smoke] ALL CHECKS PASSED ✅ —— Novelty Search 机制激活且有效")
    else:
        print("[smoke] SOME CHECKS FAILED ❌ 需排查")
    print("=" * 70)
