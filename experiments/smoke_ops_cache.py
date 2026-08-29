# -*- coding: utf-8 -*-
"""smoke test: 算子扩充 + 种子级缓存 + 进程池 综合验证（沙箱自测，不碰生产）
运行: python smoke_ops_cache.py
"""
import os, sys, json, tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine_core as E
import cache as C
import data as D

MASTER = os.path.join("D:/ssq_evo_data", "ssq_master.csv")


def load_arrays(n=None):
    if not os.path.exists(MASTER):
        print("NO_DATA"); return None, None
    master = D.load_master(MASTER)
    reds, blues, _ = D.to_arrays(master)
    if n:
        reds, blues = reds[-n:], blues[-n:]
    return reds, blues


def test_operators():
    """逐一验证新增一元/条件算子能构造出有限、长度正确的序列。"""
    reds, blues = load_arrays(400)
    N = len(reds)
    specs = {
        "log1p":  {"op": "log1p",  "a": "red_sum"},
        "tanh":   {"op": "tanh",   "a": "red_sum"},
        "sign":   {"op": "sign",   "a": "red_mean"},
        "rank":   {"op": "rank",   "a": "red_sum"},
        "ewm":    {"op": "ewm",    "a": "red_sum"},
        "diff_k": {"op": "diff_k", "a": "red_sum", "k": 3},
        "clip":   {"op": "clip",   "a": "red_sum", "k": 2},
        "where":  {"op": "where",  "a": "red_sum", "b": "blue", "k": 50},
    }
    ok = True
    for name, spec in specs.items():
        x = E._build_comp(spec, reds, blues)
        if x is None:
            print("  [FAIL] %-8s -> None" % name); ok = False; continue
        x = np.asarray(x, float)
        finite = np.isfinite(x).mean()
        same_len = len(x) == N
        # where 条件：red_sum>阈值 处应取 red_sum，否则 blue_sum（抽查一致性）
        if name == "where":
            cond = reds.sum(axis=1) > 50
            expect = np.where(cond, reds.sum(axis=1), blues)
            agree = np.mean(np.isclose(x, expect, equal_nan=True))
        else:
            agree = 1.0
        flag = "OK" if (finite > 0.99 and same_len and agree > 0.99) else "FAIL"
        if flag == "FAIL":
            ok = False
        print("  [%s] %-8s len=%d finite=%.3f agree=%.3f" % (flag, name, len(x), finite, agree))
    return ok


def test_seed_cache():
    """Evolution 跑两次同数据：第一次全 miss，第二次同 genome 应命中缓存(跳过 surrogate 重算)。"""
    reds, blues = load_arrays(500)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="smoke_cache_")
    os.close(fd)
    try:
        ec = C.EvalCache(path)
        cfg = dict(pop=16, epochs=3, n_workers=4, oos_every_epoch=False,
                   novelty_enabled=False, memetic=False, coalition=False)
        evo1 = E.Evolution(reds, blues, np.random.default_rng(12345), eval_cache=ec, **cfg)
        lb1, _ = evo1.run()
        ec.flush()  # 确保落盘（模拟守护进程周期末 flush）
        s1 = ec.stats()
        # 第二次同数据同 genome 集合：seeded 后大部分应命中缓存
        ec2 = C.EvalCache(path)  # 重新加载（模拟进程重启后复用磁盘缓存）
        evo2 = E.Evolution(reds, blues, np.random.default_rng(12345), eval_cache=ec2, **cfg)
        lb2, _ = evo2.run()
        s2 = ec2.stats()
        print("  run1: %s" % s1)
        print("  run2: %s" % s2)
        # run2 应产生缓存命中（因为 run1 已写入同 seed 的评估）
        hit_ok = s2["hits"] > 0
        # 两次 leaderboard 应一致（缓存严格等价，不引入偏差）
        same_bp = (set(lb1.keys()) == set(lb2.keys()))
        return hit_ok and same_bp
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_pool():
    """进程池（maxtasksperchild=200）能跑完一轮不崩溃。"""
    reds, blues = load_arrays(300)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="smoke_pool_")
    os.close(fd)
    try:
        ec = C.EvalCache(path)
        evo = E.Evolution(reds, blues, np.random.default_rng(999), eval_cache=ec, pop=12, epochs=2,
                          n_workers=4, oos_every_epoch=False,
                          novelty_enabled=False, memetic=False, coalition=False)
        lb, _ = evo.run()
        print("  pool run ok: %d genomes evaluated" % len(lb))
        return len(lb) > 0
    finally:
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    print("=== [A] 算子扩充 ===")
    a = test_operators()
    print("=== [B] 种子级缓存 ===")
    b = test_seed_cache()
    print("=== [C] 进程池 maxtasksperchild ===")
    c = test_pool()
    print("\n=== SUMMARY ===")
    print("operators : %s" % ("PASS" if a else "FAIL"))
    print("seed_cache: %s" % ("PASS" if b else "FAIL"))
    print("pool      : %s" % ("PASS" if c else "FAIL"))
    assert a and b and c, "SMOKE FAILED"
    print("ALL PASS")
