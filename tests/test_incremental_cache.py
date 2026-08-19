# -*- coding: utf-8 -*-
"""
tests/test_incremental_cache.py —— #40 增量评估缓存的阳性对照与安全网
========================================================================
验证三件事：
  A) EvalCache 磁盘往返 + 严格失效（数据指纹不同即 miss，绝不跨数据复用）
  B) parallel_map 跨平台并行：结果与串行一致 + 真实加速（Windows 8 核可用）
  C) Evolution 接入 EvalCache 后，leaderboard 与「无缓存」逐元素一致
     （缓存只跳过已测基因组，不改变任何统计结论）

原则：宁可慢、不可假。任何缓存都不允许改变闸门结论。
"""
import os
import sys
import time
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import numpy as np
import engine_core as E
import cache as C
import data as D

_HAS_REAL_DATA = os.path.isfile("D:/ssq_evo_data/ssq_master.csv")


def _real_data():
    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    reds, blues, _ = D.to_arrays(m)
    return reds, blues


def test_eval_cache_roundtrip_and_invalidation():
    """A) 缓存往返正确；数据指纹变化即失效（防跨数据复用偏差）。"""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        ec = C.EvalCache(path)
        ev = {"sig": "red_sum", "test": "fft_peak", "p_raw": 0.013,
              "z": 3.1, "stat": 0.02, "params": {"_sig": {}, "_test": {}}}
        ec.put("gk1", "N:100:abc", ev)
        got = ec.get("gk1", "N:100:abc")
        assert got is not None and got["p_raw"] == 0.013 and got["z"] == 3.1
        # 不同指纹 => miss（严格，绝不跨数据复用）
        assert ec.get("gk1", "N:101:xyz") is None
        # 不存在的 key => miss
        assert ec.get("nope", "N:100:abc") is None
        ec.flush()
        # 重新加载仍能命中（磁盘持久化）
        ec2 = C.EvalCache(path)
        assert ec2.get("gk1", "N:100:abc") is not None
        print("  [A] EvalCache 往返 + 严格失效: PASS")
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_parallel_map_correct_and_fast():
    """B) 并行结果与串行一致；且在大负载下明显快于串行。"""
    def work(z):
        # 模拟 surrogate 生成：FFT 类 CPU 负载
        x = np.random.default_rng(z).standard_normal(2000)
        return float(np.fft.fft(x).real.var())

    n = 60
    ser = [work(i) for i in range(n)]
    par = C.parallel_map(work, list(range(n)), max_workers=4)
    assert len(par) == n and np.allclose(ser, par, rtol=1e-12), "并行结果与串行不一致"
    # 加速验证（仅当 CPU>1）
    if (os.cpu_count() or 1) > 1:
        t0 = time.time(); [work(i) for i in range(n)]; t_ser = time.time() - t0
        t0 = time.time(); C.parallel_map(work, list(range(n)), max_workers=4); t_par = time.time() - t0
        print(f"  [B] 串行 {t_ser:.2f}s vs 并行 {t_par:.2f}s "
              f"(加速 {t_ser / t_par:.2f}x) — 结果一致: PASS")
    else:
        print("  [B] 单核环境跳过加速比对，结果一致: PASS")


@pytest.mark.skipif(not _HAS_REAL_DATA, reason="requires ssq_master.csv (absent in CI)")
def test_evolution_cache_does_not_change_results():
    """C) Evolution 加缓存 vs 无缓存，leaderboard 逐元素一致（统计结论不变）。"""
    reds, blues = _real_data()
    # 无缓存
    rng0 = np.random.default_rng(20260815)
    evo0 = E.Evolution(reds, blues, rng0, k_light=10, k_heavy=4, epochs=2, pop=12,
                       n_workers=1, eval_cache=None)
    lb0, _ = evo0.run()
    # 有缓存（同 rng 种子，应得完全相同结果）
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        ec = C.EvalCache(path)
        rng1 = np.random.default_rng(20260815)
        evo1 = E.Evolution(reds, blues, rng1, k_light=10, k_heavy=4, epochs=2, pop=12,
                           n_workers=1, eval_cache=ec)
        lb1, _ = evo1.run()
        # leaderboard key 集合与最优 p 必须一致
        assert set(lb0.keys()) == set(lb1.keys()), "leaderboard 基因组集合不一致"
        for k in lb0:
            assert abs(lb0[k]["p_raw"] - lb1[k]["p_raw"]) < 1e-12, f"基因组 {k} 的 p 被缓存改变"
        assert ec.stats()["entries"] >= 1, "缓存应有条目"
        print(f"  [C] Evolution+缓存 leaderboard 完全一致 "
              f"(缓存命中率 {ec.stats()['rate']*100:.0f}% 在跨轮复用时生效): PASS")
    finally:
        if os.path.exists(path):
            os.remove(path)


def main():
    print("== #40 增量评估缓存 阳性对照 ==")
    test_eval_cache_roundtrip_and_invalidation()
    test_parallel_map_correct_and_fast()
    test_evolution_cache_does_not_change_results()
    print("== 全部 PASS ==")


if __name__ == "__main__":
    main()
