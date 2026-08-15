# -*- coding: utf-8 -*-
"""
cache.py —— 增量评估缓存 + 跨平台并行调度
=========================================
两个互不相关、但都为了「让诚实闭环跑得起、跑得快」的目标：

1) EvalCache: 基因组评估结果的磁盘缓存。
   - key = genome_key(sig,test,params) + 数据指纹(data_fingerprint)
   - 命中条件严格：只有「完全相同的基因组 + 完全相同的数据集」才复用，
     绝不跨不同数据复用（否则会引入统计偏差，违背我们的闸门纪律）。
   - 收益场景：--no-fetch 重跑(数据 N 不变→全命中)、同轮内重复基因组、
     跨轮但若数据集指纹恰好一致(如未开奖日的例行重跑)。
   - 安全：不缓存 surrogate（依赖 rng，不可复现），只缓存「最终 eval 字典」，
     等价于把一次完整评估的结果存下来，下次直接返回——统计上完全等价。

2) parallel_map: 跨平台并行映射。
   - Windows 下 os.name=="nt"，原 Evolution 的 fork-Pool 失效→退单进程(8核浪费)。
   - 改用 ThreadPoolExecutor：numpy 的 AAFT/FFT surrogate 生成会释放 GIL，
     线程并行对 CPU 密集的 numpy 负载有真实加速，且无 fork 的跨平台坑。
   - 每个 task 自带 seed、内部建独立 rng，线程安全。

设计原则：宁可慢、不可假。任何缓存都不改变统计结论。
"""
import os
import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor


def data_fingerprint(reds, blues, n_tail=60):
    """轻量数据指纹：N + 末尾 n_tail 期的哈希。

    用于缓存失效判定。N 相同且末尾窗口相同 => 视为同一数据集。
    增量新增 1 期会使 n_tail 窗口变化 => 指纹变 => 缓存失效（安全，不跨数据复用）。
    """
    reds = __import__("numpy").asarray(reds)
    blues = __import__("numpy").asarray(blues)
    N = len(reds)
    tail_r = reds[-n_tail:] if N >= n_tail else reds
    tail_b = blues[-n_tail:] if len(blues) >= n_tail else blues
    h = hashlib.sha1(
        tail_r.tobytes() + b"|" + tail_b.tobytes()
    ).hexdigest()[:16]
    return f"{N}:{h}"


class EvalCache:
    """基因组评估结果磁盘缓存（严格失效：genome_key + 数据指纹同时匹配才命中）。"""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._mem = {}
        self.hits = 0
        self.misses = 0
        self._dirty = 0
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    raw = json.load(f)
                # 过滤掉内部计数键
                self._mem = {k: v for k, v in raw.items() if not k.startswith("__")}
            except Exception:
                self._mem = {}

    @staticmethod
    def _sanitize(obj):
        """把 numpy 标量/数组转成 JSON 安全类型。"""
        np = __import__("numpy")
        if isinstance(obj, dict):
            return {k: EvalCache._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [EvalCache._sanitize(v) for v in obj]
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    def get(self, genome_key, fp):
        key = f"{genome_key}|{fp}"
        with self._lock:
            v = self._mem.get(key)
        if v is not None:
            self.hits += 1
            return v
        self.misses += 1
        return None

    def put(self, genome_key, fp, eval_dict):
        key = f"{genome_key}|{fp}"
        clean = self._sanitize(eval_dict)
        with self._lock:
            self._mem[key] = clean
            self._dirty += 1
        if self._dirty >= 50:
            self.flush()

    def flush(self):
        with self._lock:
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self._mem, f, ensure_ascii=False)
                self._dirty = 0
            except Exception:
                pass

    def stats(self):
        tot = self.hits + self.misses
        rate = (self.hits / tot) if tot else 0.0
        return {"hits": self.hits, "misses": self.misses,
                "rate": round(rate, 4), "entries": len(self._mem)}


def parallel_map(func, tasks, max_workers=None):
    """跨平台并行映射：优先线程池（numpy 释放 GIL，对 surrogate 生成有真实加速）。

    Windows 上 fork-Pool 不可用，线程池是唯一零坑的并行路径。
    返回 list[func(task)]（保序，与输入 tasks 一一对应）。
    """
    if max_workers is None:
        import os as _os
        max_workers = max(1, min(_os.cpu_count() or 4, 8))
    if max_workers <= 1 or not tasks:
        return [func(t) for t in tasks]
    # 线程池：每个 task 内部自带 seed 建独立 rng，无共享可变状态，线程安全
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(func, tasks))


# ---------------------------------------------------------------------------
# 跨进程/线程统一的「评估 worker」包装：把 genome 任务跑成 eval dict
# （保持与原 _eval_worker 相同契约，便于 Evolution.run 切换调度器）
# ---------------------------------------------------------------------------
def _default_eval_worker(task):
    """task = (sig, test, params, reds, blues, k, sur_type, seed)"""
    import numpy as np
    sig, test, params, reds, blues, k, sur_type, seed = task
    rng = np.random.default_rng(int(seed))
    import engine_core as E
    return E.evaluate(sig, test, reds, blues, rng, k, sur_type=sur_type, params=params)
