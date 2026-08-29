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
import ssq_log

# 评估缓存逻辑版本：evaluate() 的 surrogate 生成/统计逻辑一旦变更，必须 +1，
# 使旧缓存键全部失效（旧逻辑产出的 eval 不应被新逻辑复用，否则静默返回陈旧结果）。
# 这是对"种子级缓存"方案隐含的版本隔离缺口的补强（原方案仅按 genome+数据+seed 键）。
EVAL_CACHE_VERSION = 1


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

    def get(self, genome_key, fp, seed=None, k_sur=None, sur_type=None):
        # 种子级键：同 (基因组, 数据集, surrogate种子, k_sur, sur_type) 才复用。
        # 避免不同 surrogate 设置互相污染，同时让「相同 genome 跨 epoch 重评估」命中缓存、
        # 跳过昂贵的 surrogate 重算（surrogate 占单轮评估 ~60% 时间）。
        # 前缀 v{EVAL_CACHE_VERSION}：evaluate() 的 surrogate/统计逻辑变更时 +1 令旧键全失效。
        key = f"v{EVAL_CACHE_VERSION}|{genome_key}|{fp}|{seed}|{k_sur}|{sur_type}"
        with self._lock:
            v = self._mem.get(key)
        if v is not None:
            self.hits += 1
            return v
        self.misses += 1
        return None

    def put(self, genome_key, fp, eval_dict, seed=None, k_sur=None, sur_type=None):
        key = f"v{EVAL_CACHE_VERSION}|{genome_key}|{fp}|{seed}|{k_sur}|{sur_type}"
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
            except Exception as _e:
                ssq_log.log_exception("cache", _e, "cache.py:120 silent-except")

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


# ---------------------------------------------------------------------------
# 3) 真·多进程评估（运算速度 / 算力的主要杠杆）
# ---------------------------------------------------------------------------
# 线程池对纯 Python 循环的检验函数(perm_entropy 等)不释放 GIL => 近似串行还白加
# 切换开销（实测 8 核反而比串行慢 0.9x）。改用进程池，把 CPU 密集的检验真正并行。
# 关键优化：reds/blues 只经 initializer 传给子进程【一次】，task 不再打包整个数组
# （否则 Windows spawn 下每个 task 都 pickle 大数组 => 开销爆炸）。task 仅含小元组，
# 跨进程极轻。统计结果与线程池/串行严格一致（每个 task 自带 seed 建独立 rng）。
_EVAL_REDS = None
_EVAL_BLUES = None
_EVAL_POOL = None          # 复用的进程池（跨 epoch 共享，避免每 epoch 重 spawn）
_EVAL_POOL_DATA_ID = None  # 当前池对应的 reds 对象 id，变更则重建池


def _init_eval_worker(reds, blues):
    global _EVAL_REDS, _EVAL_BLUES
    _EVAL_REDS = reds
    _EVAL_BLUES = blues


def _eval_worker_noshare(task):
    """task = (sig, test, params, k, sur_type, seed)；数组经全局注入，不随 task 传送。"""
    import numpy as np
    import engine_core as E
    sig, test, params, k, sur_type, seed = task
    rng = np.random.default_rng(int(seed))
    return E.evaluate(sig, test, _EVAL_REDS, _EVAL_BLUES, rng, k,
                      sur_type=sur_type, params=params)


def eval_parallel_map(tasks, reds, blues, max_workers=None):
    """评估专用并行映射：进程池（数组经 initializer 注入，task 仅小元组）。

    返回 list（保序）。n_workers<=1 或无任务 => 串行。进程池创建失败(spawn 受限等)
    => 自动回退串行（统计等价），不影响正确性，仅损失加速。

    关键优化：进程池【模块级复用】——只在首次（或数据集变更时）spawn 一次，
    之后所有 epoch 共享同一池，把 import scipy/numpy + 大数组初始化的开销摊薄到一次，
    避免每 epoch 重 spawn（实测每 epoch 重 spawn 比串行更慢 ~0.56x）。
    """
    global _EVAL_POOL, _EVAL_POOL_DATA_ID
    if max_workers is None:
        import os as _os
        max_workers = max(1, min(_os.cpu_count() or 4, 8))
    # 主进程也注入（串行兜底 + 单测可直跑）
    _init_eval_worker(reds, blues)
    if max_workers <= 1 or not tasks:
        return [_eval_worker_noshare(t) for t in tasks]
    try:
        import multiprocessing as mp
        # 数据集变更（不同 reds 对象）则重建池，避免旧 worker 持有过期数据
        if _EVAL_POOL is None or id(reds) != _EVAL_POOL_DATA_ID:
            if _EVAL_POOL is not None:
                try:
                    _EVAL_POOL.terminate()
                except Exception as _e:
                    ssq_log.log_exception("cache", _e, "cache.py:214 silent-except")
            # maxtasksperchild：每个 worker 处理 N 个任务后自动回收重建，防止 24x7 长时
            # 运行下 numpy/scipy 内存碎片化导致 worker 内存只增不减（实测长跑 daemon 会爬升）。
            # 重建开销极小（仅重新 import），换来内存长期稳定。Windows 上 forkserver/loky 不可用，
            # maxtasksperchild 是 stdlib 内唯一可行的长稳增强。
            _EVAL_POOL = mp.Pool(processes=max_workers, initializer=_init_eval_worker,
                                initargs=(reds, blues), maxtasksperchild=200)
            _EVAL_POOL_DATA_ID = id(reds)
        return _EVAL_POOL.map(_eval_worker_noshare, tasks)
    except Exception as e:
        import sys
        print("[cache] 进程池不可用(%s)，回退串行" % e, file=sys.stderr)
        return [_eval_worker_noshare(t) for t in tasks]


def close_eval_pool():
    """显式释放复用的进程池（可选，进程退出时自动回收）。"""
    global _EVAL_POOL, _EVAL_POOL_DATA_ID
    if _EVAL_POOL is not None:
        try:
            _EVAL_POOL.terminate()
        except Exception as _e:
            ssq_log.log_exception("cache", _e, "cache.py:236 silent-except")
        _EVAL_POOL = None
        _EVAL_POOL_DATA_ID = None
