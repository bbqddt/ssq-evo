# -*- coding: utf-8 -*-
"""
novelty_search.py —— Novelty Search / 多样性维持模块
======================================================
解决 GA 在全 null 坍景下种群坍缩到单一基因型的问题（截图 gen 4–18 全 0.451 即此症）。

核心思想（Lehman & Stanley 2011, NEAT 的 novelty search 变体）：
  不奖励"过闸"（Goodhart 红线），而是奖励**行为新颖度**——即公式的输出模式与
  已见过的所有公式有多不同。在平坦 fitness 景观(all-null)中，这提供持续的选择压力，
  驱动 GA 广搜结构空间而非收敛到局部最优。

红线锁死：
  - 新颖度只影响 selection（谁进入下一代），绝不影响 gate verdict（BH-FDR/OOT/#41）
  - 不自动合并任何候选到 frontier，闸门仍是唯一仲裁
  - 传统 fitness(p_raw + OOS hit_rate)仍参与混合；有真信号时自动回归传统选择

接口：
  NoveltyArchive    —— 新颖度存档（capped archive + kNN 距离）
  behavior_fp       —— 从 eval dict + 数据算行为指纹（固定长度向量）
  novelty_fitness   —— 混合 fitness = α·传统 + (1-α)·归一化新颖度
"""
import numpy as np
import engine_core as E


# ---------------------------------------------------------------------------
# 行为指纹：公式的"输出签名"（不依赖闸门结果）
# ---------------------------------------------------------------------------
def behavior_fp(eval_dict, reds, blues, n_bins=12):
    """从已评估的基因组计算行为指纹（固定长度 numpy 向量）。

    指纹构成（全部从公式输出序列提取，不涉及 p 值/闸门）：
      fp[0..n_bins-1]   : 输出序列的等频分位数直方图（捕获分布形状）
      fp[n_bins]        : 序列均值（标准化）
      fp[n_bins+1]      : 序列标准差（对数）
      fp[n_bins+2]      : 偏度
      fp[n_bins+3]      : 峰度
      fp[n_bins+4]      : lag-1 自相关（时序结构）
      fp[n_bins+5]      : 过零率（振荡频率代理）
      fp[n_bins+6]      : 结构特征：comp 树深度（基信号=0）
      fp[n_bins+7]      : 结构特征：节点数近似（params 字典大小）

    总维度 = n_bins + 8 = 20。足够区分不同公式行为，又足够紧凑。
    """
    sig = eval_dict.get("sig", "")
    test = eval_dict.get("test", "")
    params = eval_dict.get("params", {}) or {}

    # 重建信号输出 x（轻量：不跑 surrogate，只取原始序列）
    try:
        x = E._build_x(sig, reds, blues, params)
    except Exception:
        # fallback: 用纯零向量（所有这样的 fallback 会聚在一起，
        # 自然被 novelty search 视为"同一类"而降低其多样性贡献）
        x = np.zeros(min(len(reds), 500), dtype=float)

    if x is None or x.size < 20:
        x = np.zeros(100, dtype=float)

    x = np.asarray(x, float)[:min(len(x), 2000)]  # 截断避免长序列拖慢
    n = len(x)
    fp = np.zeros(n_bins + 8, dtype=np.float64)

    # 1. 等频分位数直方图
    if n > n_bins:
        edges = np.percentile(x, np.linspace(0, 100, n_bins + 1))
        fp[:n_bins] = np.histogram(x, bins=edges)[0].astype(float)
        # 归一化为概率分布（使距离度量不受量纲影响）
        s = fp[:n_bins].sum()
        if s > 0:
            fp[:n_bins] /= s
    else:
        fp[0] = 1.0  # 太短无法分箱

    # 2. 统计矩
    mu = np.nanmean(x); sd = np.nanstd(x) + 1e-12
    fp[n_bins] = mu / (abs(mu) + 1.0)          # 有界均值
    fp[n_bins + 1] = np.log(sd + 1e-12)         # 对数标准差
    if sd > 1e-12:
        fp[n_bins + 2] = float(np.mean(((x - mu) / sd) ** 3))   # 偏度
        fp[n_bins + 3] = float(np.mean(((x - mu) / sd) ** 4) - 3.0)  # 超额峰度

    # 3. 时序特征
    if n > 2:
        xc = x - mu
        var = np.sum(xc ** 2)
        if var > 0:
            fp[n_bins + 4] = float(np.sum(xc[:-1] * xc[1:]) / var)  # lag-1 ACF
        fp[n_bins + 5] = float(np.mean(np.diff(np.sign(x - mu)) != 0))  # zero-crossing rate

    # 4. 结构特征（comp vs base signal 可区分）
    if sig == "comp" and "_comp" in params:
        cp = params["_comp"]
        if isinstance(cp, dict):
            fp[n_bins + 6] = float(cp.get("depth", 0))
            fp[n_bins + 7] = float(len(cp.get("nodes", [])) if "nodes" in cp else
                                   sum(1 for k in cp if k not in ("depth", "op")))
    # base signal: depth=0, nodes≈sig/test hash proxy
    else:
        fp[n_bins + 6] = 0.0
        fp[n_bins + 7] = float(hash(sig + test) % 1000) / 1000.0  # 确定性散列

    return fp


# ---------------------------------------------------------------------------
# 新颖度存档
# ---------------------------------------------------------------------------
class NoveltyArchive:
    """Capped archive of behavior fingerprints with k-NN novelty scoring.

    Usage:
        archive = NoveltyArchive(max_size=500, k_nn=15)
        fp = behavior_fp(eval_dict, reds, blues)
        score = archive.novelty(fp)     # 查询（不改 archive）
        archive.add(fp, metadata)       # 加入存档
    """

    def __init__(self, max_size=500, k_nn=15):
        self.max_size = max_size
        self.k_nn = k_nn
        self._fps = []       # list of ndarray fingerprints
        self._meta = []      # parallel list of optional metadata dicts
        self._matrix = None  # cached distance matrix (lazy)

    def add(self, fingerprint, metadata=None):
        """Add a fingerprint to the archive. Evicts oldest when full."""
        self._fps.append(np.asarray(fingerprint, dtype=np.float64).copy())
        self._meta.append(metadata or {})
        self._matrix = None  # invalidate cache
        # Simple eviction: FIFO (could upgrade to farthest-point sampling)
        while len(self._fps) > self.max_size:
            self._fps.pop(0)
            self._meta.pop(0)

    def _dist_matrix(self):
        """Build/rebuild pairwise distance matrix."""
        if self._matrix is not None and self._matrix.shape[0] == len(self._fps):
            return self._matrix
        n = len(self._fps)
        if n == 0:
            self._matrix = np.zeros((0, 0))
            return self._matrix
        arr = np.array(self._fps)
        # Euclidean distance on normalized features
        # (each feature already roughly in [0,1] or bounded by construction)
        diffs = arr[:, None, :] - arr[None, :, :]
        self._matrix = np.sqrt((diffs ** 2).sum(axis=2))
        return self._matrix

    def novelty(self, fingerprint):
        """Compute novelty score: average distance to k nearest neighbors.

        Returns float('inf') if archive has < k_nn entries (max novelty).
        Higher = more novel (different from everything seen before).
        """
        fp = np.asarray(fingerprint, dtype=np.float64)
        n = len(self._fps)
        if n < self.k_nn:
            return float('inf')
        # Distance from query to all archived points
        arr = np.array(self._fps)
        dists = np.sqrt(((arr - fp[None, :]) ** 2).sum(axis=1))
        k_nearest = np.partition(dists, min(self.k_nn - 1, len(dists) - 1))[:self.k_nn]
        return float(np.mean(k_nearest))

    def diversity_index(self):
        """Mean pairwise distance in current archive (population health metric)."""
        n = len(self._fps)
        if n < 2:
            return 0.0
        dm = self._dist_matrix()
        # Upper triangle (excluding diagonal)
        tri = dm[np.triu_indices(n, k=1)]
        return float(tri.mean()) if tri.size > 0 else 0.0

    def __len__(self):
        return len(self._fps)

    def __repr__(self):
        return f"NoveltyArchive(size={len(self)}, max={self.max_size}, k={self.k_nn})"


# ---------------------------------------------------------------------------
# 混合 fitness
# ---------------------------------------------------------------------------
def novelty_fitness(traditional_fit, novelty_score, alpha=0.5,
                    trad_range=(0.0, 1.0), nov_range=(0.0, 1.0)):
    """Blend traditional fitness with novelty score.

    Args:
        traditional_fit: 原始 _fitness() 值（越高越好）
        novelty_score:   新颖度分数（越高越好，来自 NoveltyArchive.novelty()）
        alpha:           传统权重 [0,1]。alpha=1 纯传统；alpha=0 纯新颖度。
                        全 null 场景建议 alpha∈[0.2, 0.4]（新颖度主导）。
        trad_range:      传统 fitness 的预期范围（用于归一化）
        nov_range:       新颖度的预期范围（用于归一化）

    Returns:
        混合 fitness（越高越好），范围 ≈ [0, 1]
    """
    t_lo, t_hi = trad_range
    n_lo, n_hi = nov_range

    # 归一化到 [0, 1]
    t_norm = np.clip((traditional_fit - t_lo) / max(t_hi - t_lo, 1e-12), 0.0, 1.0)

    if np.isinf(novelty_score) or novelty_score > 1e9:
        n_norm = 1.0  # 无邻居 → 最大新颖度
    else:
        n_norm = np.clip((novelty_score - n_lo) / max(n_hi - n_lo, 1e-12), 0.0, 1.0)

    return float(alpha * t_norm + (1.0 - alpha) * n_norm)


# ---------------------------------------------------------------------------
# 自适应 alpha：当传统 fitness 方差极小时（全 null 坍景），自动降 alpha 让新颖度主导
# ---------------------------------------------------------------------------
def adaptive_alpha(fitness_values, base_alpha=0.5, floor=0.15):
    """根据当前轮次 fitness 分布自适应调整 alpha。

    若 fitness 方差 < 阈值（平坦景观），返回 floor（新颖度主导）；
    否则返回 base_alpha（传统为主）。
    """
    arr = np.array(fitness_values, dtype=float)
    if arr.size < 2:
        return base_alpha
    var = arr.var()
    # 经验阈值：方差 < 0.001 视为"平坦"
    if var < 0.001:
        return floor
    # 平滑过渡：var ∈ [0.001, 0.01] 时线性插值
    if var < 0.01:
        t = (var - 0.001) / 0.009
        return floor + (base_alpha - floor) * t
    return base_alpha


if __name__ == "__main__":
    # ---- 冒烟测试 ----
    import data as D
    m = D.load_master("D:/ssq_evo_data/ssq_master.csv")
    reds, blues, issues = D.to_arrays(m)
    print("[novelty] 载入 %d 期" % reds.shape[0])

    # 模拟几个 eval dict（用真实 evaluate 生成）
    rng = np.random.default_rng(42)
    evals = []
    for sig in ("red_sum", "blue", "red_gap_mean"):
        for test in ("autocorr_lag1", "perm_entropy", "t_dfa_alpha"):
            ev = E.evaluate(sig, test, reds, blues, rng, k_sur=5)
            if ev:
                evals.append(ev)
    print("[novelty] 生成了 %d 个真实 eval" % len(evals))

    # 测试行为指纹
    fps = [behavior_fp(ev, reds, blues) for ev in evals]
    print("[novelty] 指纹维度=%d  样本=" % fps[0].size, fps[0][:6])

    # 测试 archive + novelty scoring
    arch = NoveltyArchive(max_size=200, k_nn=min(5, len(evals)))
    scores = []
    for i, (ev, fp) in enumerate(zip(evals, fps)):
        sc = arch.novelty(fp)
        scores.append(sc)
        arch.add(fp, {"sig": ev["sig"], "test": ev["test"]})
        print("  [%d] sig=%-14s test=%-16s novel=%.4f" % (i, ev["sig"], ev["test"], sc))

    print("\n[novelty] archive size=%d  diversity=%.4f" % (len(arch), arch.diversity_index()))

    # 测试混合 fitness
    trad_fits = [0.451] * 8 + [0.502, 0.398]  # 模拟截图中坍缩场景
    al = adaptive_alpha(trad_fits)
    print("[novelty] adaptive_alpha(坍缩 fitness): %.2f  (base=0.5)" % al)

    trad_fits2 = [0.1, 0.9, 0.5, 0.7, 0.2, 0.85]
    al2 = adaptive_alpha(trad_fits2)
    print("[novelty] adaptive_alpha(多样 fitness): %.2f  (base=0.5)" % al2)

    # 测试 novelty_fitness 混合
    nf = [novelty_fitness(t, s, alpha=al) for t, s in zip([0.451]*len(scores), scores)]
    print("\n[novelty] 混合 fitness (坍缩α=%.2f):" % al)
    for i, (ev, nf_val) in enumerate(zip(evals, nf)):
        print("  [%d] %-14s %-16s trad=%.3f  novel=%.4f  blended=%.4f" %
              (i, ev["sig"], ev["test"], 0.451, scores[i], nf_val))

    print("\n[novelty] PASS")
