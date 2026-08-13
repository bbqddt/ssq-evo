# -*- coding: utf-8 -*-
"""
双色球历史序列 · 自适应连续结构搜索引擎  (core)
================================================
定位：沿用户所提"时间 simultaneity / 频率-振动 / 隐藏确定性"方向，
对开奖序列做 *可演进* 的结构搜索计算。每一项都含 surrogate 对照
(AAFT 保留频谱+分布、破坏时序；shuffle 破坏时序)，并用 BH 假发现率
(FDR) 跨全部候选校正，防止演化搜索本身挖出假规律。

重要边界（写进代码，不是口头）：
  - 本引擎检验"序列中是否存在可检测结构"，不构成对时间是否存在的
    形而上学证明；即便某算子显著，也只说明"此序列有非随机结构"，
    不赋予任何预知权。
  - 持续返回 null 是科学结果（证据的缺席随样本与假设空间增强），
    不是失败。
  - 唯一"成功条件"：某算子在 FDR 校正后 q<0.01 且样本外复现 -> 触发警报。
"""
import json
import math
import os as _os
import multiprocessing as mp
import numpy as np

# ---------------------------------------------------------------------------
# 1. 信号映射 (signal maps)
#    把每期 (6 红 + 1 蓝) 映射成一条 1D 实序列 x[0..N-1]
#    用户说的"能量/频率/振动"在此形式化为不同的映射。
# ---------------------------------------------------------------------------

def _red_norm(reds):
    # reds: (N,6) int array; 归一化到 [0,1] 便于跨映射比较
    return reds.astype(float)

def sm_red_sum(reds, blues):
    return reds.sum(axis=1).astype(float)

def sm_red_mean(reds, blues):
    return reds.mean(axis=1).astype(float)

def sm_red_energy(reds, blues):
    # "能量"：平方和
    return (reds.astype(float) ** 2).sum(axis=1)

def sm_red_weighted(reds, blues):
    # 位置加权：sum(i * r_i)，i=1..6
    w = np.arange(1, 7)
    return (reds.astype(float) * w).sum(axis=1)

def sm_red_span(reds, blues):
    return reds.max(axis=1) - reds.min(axis=1)

def sm_red_delta_mean(reds, blues):
    s = np.sort(reds, axis=1)
    return np.diff(s, axis=1).mean(axis=1)

def sm_red_parity(reds, blues):
    # 奇数红球个数
    return (reds % 2 == 1).sum(axis=1).astype(float)

def sm_blue(reds, blues):
    return blues.astype(float)

def sm_vector_mag(reds, blues, div=33):
    # "频率/振动"：每期 6 个红球视作 6 个角度 2π r/div 的单位向量，
    # 求和后的合向量模长（相干性度量）。div 是可进化参数。
    ang = 2 * np.pi * reds.astype(float) / float(div)
    cx = np.cos(ang).sum(axis=1)
    cy = np.sin(ang).sum(axis=1)
    return np.sqrt(cx ** 2 + cy ** 2)

def sm_vector_phase(reds, blues, div=33):
    ang = 2 * np.pi * reds.astype(float) / float(div)
    cx = np.cos(ang).sum(axis=1)
    cy = np.sin(ang).sum(axis=1)
    return np.arctan2(cy, cx)

def sm_complex_field(reds, blues, div=34):
    # 红+蓝共 7 个号码视作 7 相位的合向量模（更强的"场"映射）。div 可进化。
    nums = np.column_stack([reds, blues.reshape(-1, 1)]).astype(float)
    ang = 2 * np.pi * nums / float(div)
    cx = np.cos(ang).sum(axis=1)
    cy = np.sin(ang).sum(axis=1)
    return np.sqrt(cx ** 2 + cy ** 2)

SIGMAPS = {
    "red_sum": sm_red_sum,
    "red_mean": sm_red_mean,
    "red_energy": sm_red_energy,
    "red_weighted": sm_red_weighted,
    "red_span": sm_red_span,
    "red_delta_mean": sm_red_delta_mean,
    "red_parity": sm_red_parity,
    "blue": sm_blue,
    "vector_mag": sm_vector_mag,
    "vector_phase": sm_vector_phase,
    "complex_field": sm_complex_field,
}

# ---------------------------------------------------------------------------
# 2. 检验统计 (tests)  —— 每个返回作用于 1D 序列 x 的标量
#    direction: 'high' 表示 统计量越大越像结构(随机源应更小)
#               'low'  表示 统计量越小越像结构(随机源应更大)
#    tier: 'light' 可配较多 surrogate；'heavy' 计算贵，少配 surrogate 且子采样
# ---------------------------------------------------------------------------

def t_fft_peak(x):
    x = x - x.mean()
    n = len(x)
    if n < 8:
        return np.nan
    p = np.abs(np.fft.rfft(x)) ** 2
    p = p / p.sum()
    return float(p[1:].max())

def t_acf_max(x, maxlag=40):
    x = x - x.mean()
    n = len(x)
    if n < maxlag + 2:
        maxlag = n - 2
    if maxlag < 1:
        return np.nan
    best = 0.0
    for lag in range(1, maxlag + 1):
        c = np.corrcoef(x[:-lag], x[lag:])[0, 1]
        if not math.isnan(c) and abs(c) > best:
            best = abs(c)
    return float(best)

def t_dfa_alpha(x, scales=None):
    y = np.cumsum(x - x.mean())
    n = len(y)
    if scales is None:
        scales = np.unique(np.logspace(np.log10(10), np.log10(max(11, n // 4)), 20).astype(int))
    F, S = [], []
    for s in scales:
        if s >= n:
            break
        num = n // s
        if num < 4:
            continue
        rms = 0.0
        for i in range(num):
            seg = y[i * s:(i + 1) * s]
            t = np.arange(len(seg))
            p = np.polyfit(t, seg, 1)
            rms += np.sqrt(np.mean((seg - np.polyval(p, t)) ** 2))
        F.append(rms / num)
        S.append(s)
    if len(S) < 3:
        return np.nan
    return float(np.polyfit(np.log10(S), np.log10(F), 1)[0])

def t_mi_max(x, maxlag=20, bins=12):
    x = x.astype(float)
    n = len(x)
    best = 0.0
    for lag in range(1, maxlag + 1):
        if lag >= n:
            break
        a, b = x[:-lag], x[lag:]
        H = np.histogram2d(a, b, bins=bins)[0].astype(float) + 1e-12
        P = H / H.sum()
        pa = P.sum(axis=1)
        pb = P.sum(axis=0)
        mi = 0.0
        for i in range(bins):
            for j in range(bins):
                if P[i, j] > 0:
                    mi += P[i, j] * math.log2(P[i, j] / (pa[i] * pb[j]))
        if mi > best:
            best = mi
    return float(best)

def t_corr_dim_slope(x, m_max=7, tau=1, r_log=None, sub=1200):
    x = (x - x.mean()) / (x.std() + 1e-12)
    n = len(x)
    if n > sub:                      # 与其它 heavy 检验一致：子采样，避免 O(n^2) 在 3489 点上爆炸
        x = x[:sub]
        n = len(x)
    if r_log is None:
        r_log = np.linspace(-1.5, 0.2, 14)
    Ds = []
    for m in range(2, m_max + 1):
        M = n - (m - 1) * tau
        if M < 50:
            Ds.append(np.nan)
            continue
        emb = np.array([x[i:i + m * tau:tau] for i in range(M)])
        from scipy.spatial.distance import pdist
        d = pdist(emb)
        d = d[d > 0]
        C = np.array([np.mean(d < (10 ** rl)) for rl in r_log])
        mask = (C > 0) & np.isfinite(C)
        if mask.sum() < 3:
            Ds.append(np.nan)
            continue
        Ds.append(np.polyfit(r_log[mask], np.log10(C[mask]), 1)[0])
    Ds = np.array(Ds)
    Ds = Ds[np.isfinite(Ds)]
    if Ds.size < 2:
        return np.nan
    # 返回随 m 增长的速度：若为常数(低维)则斜率小；随机则随 m 增 -> 用末-首近似
    return float(Ds[-1] - Ds[0])

def t_perm_entropy(x, order=3, delay=1, sub=2000):
    x = np.asarray(x, float)
    n = len(x)
    if n > sub:
        x = x[:sub]
        n = len(x)
    if n <= order * delay:
        return np.nan
    counts = {}
    for i in range(n - order * delay):
        seg = x[i:i + order * delay:delay]
        rank = tuple(np.argsort(np.argsort(seg)))
        counts[rank] = counts.get(rank, 0) + 1
    total = sum(counts.values())
    probs = np.array(list(counts.values())) / total
    pe = -np.sum(probs * np.log(probs))
    return float(pe)  # 随机序列 PE 较大；确定性序列 PE 较小 -> direction 'low'

def t_approx_entropy(x, m=2, r_factor=None, r=None, sub=1200):
    x = np.asarray(x, float)
    n = len(x)
    if n > sub:
        x = x[:sub]
        n = len(x)
    if n <= m + 1:
        return np.nan
    if r is None:
        r = (r_factor if r_factor is not None else 0.2) * (x.std() + 1e-12)
    from scipy.spatial.distance import cdist
    def _phi(m_):
        if n <= m_:
            return 0.0
        emb = np.array([x[i:i + m_] for i in range(n - m_ + 1)])
        D = cdist(emb, emb, metric="chebyshev")   # (M,M) 距离矩阵，C 实现，告别 O(n^2) Python 双循环
        cnt = (D <= r).sum(axis=1)
        return np.mean(np.log(cnt / (n - m_ + 1)))
    return float(_phi(m) - _phi(m + 1))  # 随机 -> 大；确定性 -> 小 -> 'low'

def t_rq_determinism(x, m=2, r_factor=None, r=None, sub=600):
    x = np.asarray(x, float)
    n = len(x)
    if n > sub:
        x = x[:sub]
        n = len(x)
    if r is None:
        r = (r_factor if r_factor is not None else 0.1) * (x.std() + 1e-12)
    # 递归图：距离 < r 记 1
    diff = np.abs(x[:, None] - x[None, :])
    rec = (diff <= r)
    total = rec.sum()
    if total == 0:
        return np.nan
    det = 0
    for d in range(1, n):
        diag = np.diag(rec, d).astype(np.int8)
        if diag.sum() < 2:
            continue
        # 向量化游程统计：长度 >= 2 的连续段计入确定性
        p = np.concatenate(([0], diag, [0]))
        dd = np.diff(p)
        starts = np.where(dd == 1)[0]
        ends = np.where(dd == -1)[0]
        lengths = ends - starts
        det += lengths[lengths >= 2].sum()
    return float(det / total)  # 随机低；确定性高 -> 'high'

def t_lyap_rosenstein(x, m=2, tau=1, sub=600):
    x = np.asarray(x, float)
    n = len(x)
    if n > sub:
        x = x[:sub]
        n = len(x)
    M = n - (m - 1) * tau
    if M < 50:
        return np.nan
    emb = np.array([x[i:i + m * tau:tau] for i in range(M)])
    from scipy.spatial.distance import cdist
    D = cdist(emb, emb, metric="euclidean")         # (M,M) 距离矩阵，C 实现
    np.fill_diagonal(D, np.inf)
    j = np.argmin(D, axis=1)
    fi = np.arange(M)
    mask = (fi + tau < M) & (j + tau < M)
    num = np.sqrt(((emb[fi[mask] + tau] - emb[j[mask] + tau]) ** 2).sum(axis=1))
    den = D[fi[mask], j[mask]]
    return float(np.mean(np.log((num + 1e-12) / (den + 1e-12))))  # 随机 ~0/负；确定性正 -> 'high'

TESTS = {
    "fft_peak":   (t_fft_peak, "high", "light"),
    "acf_max":    (t_acf_max, "high", "light"),
    "dfa_alpha":  (t_dfa_alpha, "high", "light"),
    "mi_max":     (t_mi_max, "high", "light"),
    "corr_dim_slope": (t_corr_dim_slope, "high", "heavy"),
    "perm_entropy": (t_perm_entropy, "low", "heavy"),
    "approx_entropy": (t_approx_entropy, "low", "heavy"),
    "rq_determinism": (t_rq_determinism, "high", "heavy"),
    "lyap":       (t_lyap_rosenstein, "high", "heavy"),
}

# ---------------------------------------------------------------------------
# 2b. 参数基因组 (parameter genome)
#     把"公式/算法的旋钮"形式化为可进化的参数。每个检验 / 信号映射可声明
#     其参数范围 (low, high, step)；演化通过突变/重组这些参数做 hill-climbing。
# ---------------------------------------------------------------------------

# 检验可调参数 (键名须与该检验函数签名中的关键字参数一致)
PARAM_SCHEMA = {
    "acf_max":        {"maxlag": (5, 60, 1)},
    "mi_max":         {"maxlag": (2, 30, 1), "bins": (6, 20, 1)},
    "corr_dim_slope": {"m_max": (3, 9, 1), "tau": (1, 3, 1)},
    "perm_entropy":   {"order": (2, 5, 1), "delay": (1, 3, 1)},
    "approx_entropy": {"m": (1, 3, 1), "r_factor": (0.10, 0.40, 0.05)},
    "rq_determinism": {"m": (2, 4, 1), "r_factor": (0.05, 0.30, 0.05)},
    "lyap":           {"m": (2, 4, 1), "tau": (1, 3, 1)},
    "fft_peak":       {},
    "dfa_alpha":      {},
}

# 信号映射可调参数 (仅向量类映射有 div)
SIG_PARAM_SCHEMA = {
    "vector_mag":   {"div": (30, 36, 1)},
    "vector_phase": {"div": (30, 36, 1)},
    "complex_field":{"div": (30, 36, 1)},
}

SIG_PARAM_SIGMAPS = set(SIG_PARAM_SCHEMA.keys())
SIG_NAMES = list(SIGMAPS.keys())
TEST_NAMES = list(TESTS.keys())


def _random_params(sig, test, rng):
    """随机生成一个基因组参数块 {'_sig':{...}, '_test':{...}}。"""
    tp = {}
    for k, (lo, hi, st) in PARAM_SCHEMA.get(test, {}).items():
        n = int(round((hi - lo) / st)) + 1
        tp[k] = int(lo + st * rng.integers(0, n))
    sp = {}
    for k, (lo, hi, st) in SIG_PARAM_SCHEMA.get(sig, {}).items():
        n = int(round((hi - lo) / st)) + 1
        sp[k] = int(lo + st * rng.integers(0, n))
    return {"_sig": sp, "_test": tp}


def random_genome(rng):
    sig = rng.choice(SIG_NAMES)
    test = rng.choice(TEST_NAMES)
    return {"sig": sig, "test": test, "params": _random_params(sig, test, rng)}


def mutate_genome(g, rng):
    """对基因组做突变：宏观换模块 或 微调某个旋钮（hill-climbing 核心）。"""
    ng = {"sig": g["sig"], "test": g["test"],
          "params": {k: dict(v) for k, v in g["params"].items()}}
    r = rng.random()
    if r < 0.15:                       # 宏观变异：换检验
        ng["test"] = rng.choice(TEST_NAMES)
        ng["params"]["_test"] = _random_params(ng["sig"], ng["test"], rng)["_test"]
    elif r < 0.30:                     # 宏观变异：换信号映射
        ng["sig"] = rng.choice(SIG_NAMES)
        ng["params"]["_sig"] = _random_params(ng["sig"], ng["test"], rng)["_sig"]
    elif r < 0.85:                     # 微调检验参数
        schema = PARAM_SCHEMA.get(ng["test"], {})
        if schema and rng.random() < 0.7:
            k = rng.choice(list(schema.keys()))
            lo, hi, st = schema[k]
            cur = ng["params"]["_test"].get(k, lo)
            ng["params"]["_test"][k] = int(min(hi, max(lo, cur + st * rng.choice([-1, 1]))))
        else:
            s2 = SIG_PARAM_SCHEMA.get(ng["sig"], {})
            if s2:
                k = rng.choice(list(s2.keys()))
                lo, hi, st = s2[k]
                cur = ng["params"]["_sig"].get(k, lo)
                ng["params"]["_sig"][k] = int(min(hi, max(lo, cur + st * rng.choice([-1, 1]))))
    else:                              # 微调信号参数
        s2 = SIG_PARAM_SCHEMA.get(ng["sig"], {})
        if s2:
            k = rng.choice(list(s2.keys()))
            lo, hi, st = s2[k]
            cur = ng["params"]["_sig"].get(k, lo)
            ng["params"]["_sig"][k] = int(min(hi, max(lo, cur + st * rng.choice([-1, 1]))))
    return ng


def genome_key(sig, test, params):
    """基因组的稳定字符串键（用于 leaderboard 与 tried 去重）。"""
    return f"{sig}|{test}|" + json.dumps(params, sort_keys=True)

# ---------------------------------------------------------------------------
# 3. surrogate
# ---------------------------------------------------------------------------

def shuffle_surrogate(x, rng):
    return rng.permutation(x)

def aaft_surrogate(x, rng):
    """Amplitude Adjusted Fourier Transform surrogate:
    保留功率谱(线性相关性)+边际分布，仅破坏非线性时序结构。"""
    x = np.asarray(x, float)
    n = len(x)
    fx = np.fft.rfft(x)
    ph = rng.uniform(0, 2 * math.pi, len(fx))
    fx_r = fx * np.exp(1j * ph)
    y = np.fft.irfft(fx_r, n)
    order = np.argsort(np.argsort(y))
    x_sorted = np.sort(x)
    return x_sorted[order]


def _gen_surrogates(x, k, rng, sur_type):
    """批量生成 k 个 surrogate，shape (k, n)。一次 FFT/IFFT + 向量化重排，
    远快于逐个调用 aaft_surrogate。aaft 保留谱+边际分布(正确零假设)；
    shuffle 仅破坏时序。"""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0 or k <= 0:
        return np.empty((0, 0))
    fx = np.fft.rfft(x)
    mag = np.abs(fx)
    M = len(fx)
    if sur_type == "shuffle":
        u = rng.random((k, n))
        order = np.argsort(u, axis=1)
        return x[order].astype(float)
    # random-phase (amplitude-adjusted) surrogate
    ph = rng.uniform(0, 2 * np.pi, (k, M))
    Y = np.fft.irfft(mag * np.exp(1j * ph), n, axis=1)
    ranks = np.argsort(np.argsort(Y, axis=1), axis=1)
    xs = np.sort(x)
    return xs[ranks].astype(float)

# ---------------------------------------------------------------------------
# 4. 单算子评估
# ---------------------------------------------------------------------------

def evaluate(sig_name, test_name, reds, blues, rng, k_sur, sur_type="aaft", params=None):
    if sig_name not in SIGMAPS or test_name not in TESTS:
        return None
    params = params or {"_sig": {}, "_test": {}}
    sig_params = params.get("_sig", {})
    test_params = params.get("_test", {})
    try:
        if sig_name in SIG_PARAM_SIGMAPS:
            x = SIGMAPS[sig_name](reds, blues, **sig_params)
        else:
            x = SIGMAPS[sig_name](reds, blues)
    except Exception:
        return None
    func, direction, tier = TESTS[test_name]
    try:
        real = func(x, **test_params)
    except Exception:
        return None
    if not math.isfinite(real):
        return None
    n = len(x)
    try:
        surs = _gen_surrogates(x, int(k_sur), rng, sur_type)
    except Exception:
        surs = np.empty((0, n))
    svals = []
    for i in range(surs.shape[0]):
        sx = surs[i]
        try:
            sv = func(sx, **test_params)
        except Exception:
            continue
        if math.isfinite(sv):
            svals.append(sv)
    svals = np.array(svals)
    if svals.size == 0:
        return None
    mean_s, std_s = svals.mean(), svals.std() + 1e-12
    z = (real - mean_s) / std_s
    if direction == "high":
        p = (1.0 + np.sum(svals >= real)) / (1.0 + svals.size)
    else:
        p = (1.0 + np.sum(svals <= real)) / (1.0 + svals.size)
    return {
        "sig": sig_name, "test": test_name, "tier": tier, "direction": direction,
        "params": params,
        "stat": real, "sur_mean": float(mean_s), "sur_std": float(std_s),
        "z": float(z), "p_raw": float(p), "k_sur": int(svals.size), "sur_max": float(svals.max()), "sur_min": float(svals.min()),
    }

# ---------------------------------------------------------------------------
# 5. BH-FDR 校正
# ---------------------------------------------------------------------------

def bh_fdr(pvals):
    p = np.asarray(pvals, float)
    n = p.size
    order = np.argsort(p)
    ranked = p[order]
    q = ranked * n / (np.arange(1, n + 1))
    # 保证单调不增
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.clip(q, 0, 1)
    out = np.empty(n)
    out[order] = q
    return out

# ---------------------------------------------------------------------------
# 6. 演化搜索
# ---------------------------------------------------------------------------

def _eval_worker(task):
    """进程池 worker：每个任务独立 rng（按种子），避免跨进程共享 Generator。"""
    sig, test, params, reds, blues, k, sur_type, seed = task
    rng = np.random.default_rng(seed)
    return evaluate(sig, test, reds, blues, rng, k, sur_type=sur_type, params=params)


class Evolution:
    """基因组式演化搜索：每个个体 = (信号映射, 检验, 参数块)。
    支持：(1) 精英跨轮 seed；(2) 参数突变/重组(hill-climbing)；(3) tried 去重集；
    (4) 多进程并行评估(按基因组)，把 8 核全用上。"""

    def __init__(self, reds, blues, rng, k_light=25, k_heavy=10, epochs=6, pop=24,
                 elites=None, frontier=None, sur_type="aaft", n_workers=0):
        self.reds = reds
        self.blues = blues
        self.rng = rng
        self.k_light = k_light
        self.k_heavy = k_heavy
        self.epochs = epochs
        self.pop = pop
        self.elites = [dict(g) for g in (elites or [])]
        self.frontier = frontier or {"tried": []}
        self.all_evals = []                          # 本论全部评估（喂 FDR）
        self.leaderboard = {}                        # gkey -> 该基因组最优 eval
        self.tried = set(self.frontier.get("tried", []))
        self.sur_type = sur_type
        # 并行度：默认 min(CPU, pop)；单核环境退化为 1
        self.n_workers = n_workers or max(1, min(_os.cpu_count() or 4, max(2, pop)))

    def _k(self, tier):
        return self.k_light if tier == "light" else self.k_heavy

    def _seed_pop(self):
        pop = []
        n_elite = min(len(self.elites), max(2, self.pop // 3))
        for g in self.elites[:n_elite]:
            pop.append(dict(g))
        while len(pop) < self.pop:
            pop.append(random_genome(self.rng))
        return pop

    def run(self):
        pop = self._seed_pop()
        pool = None
        if self.n_workers > 1 and _os.name == "posix":
            try:
                pool = mp.get_context("fork").Pool(processes=self.n_workers)
            except Exception:
                pool = None
        try:
            for ep in range(self.epochs):
                # 去重：本轮/跨轮已测过的基因组直接跳过，省时间
                to_eval = [g for g in pop
                           if genome_key(g["sig"], g["test"], g["params"]) not in self.tried]
                if not to_eval:
                    to_eval = pop
                tasks = []
                for g in to_eval:
                    k = self._k(TESTS[g["test"]][2])
                    seed = int(self.rng.integers(0, 2 ** 31 - 1))
                    tasks.append((g["sig"], g["test"], g["params"],
                                 self.reds, self.blues, k, self.sur_type, seed))
                if pool is not None:
                    res_iter = pool.imap_unordered(_eval_worker, tasks)
                else:
                    res_iter = (_eval_worker(t) for t in tasks)
                evals = []
                for ev in res_iter:
                    if ev is None:
                        continue
                    key = genome_key(ev["sig"], ev["test"], ev["params"])
                    ev["gkey"] = key
                    evals.append(ev)
                    self.all_evals.append(ev)
                    if key not in self.leaderboard or ev["p_raw"] < self.leaderboard[key]["p_raw"]:
                        self.leaderboard[key] = ev
                    self.tried.add(key)
                # 选择：按本基因组最优 p_raw 取前 50%
                evals_sorted = sorted(evals, key=lambda e: self.leaderboard[e["gkey"]]["p_raw"]) if evals else []
                survivors = evals_sorted[:max(2, len(evals_sorted) // 2)]
                base_pool = survivors if survivors else [random_genome(self.rng) for _ in range(2)]
                newpop = [dict(g) for g in base_pool]
                while len(newpop) < self.pop:
                    if self.rng.random() < 0.5 and len(base_pool) >= 2:
                        a, b = self.rng.choice(len(base_pool), 2, replace=False)
                        ga, gb = base_pool[a], base_pool[b]
                        # 重组：交换信号映射 或 检验（保留各自参数作起点）
                        if self.rng.random() < 0.5:
                            newpop.append({"sig": gb["sig"], "test": ga["test"],
                                           "params": {"_sig": dict(ga["params"]["_sig"]),
                                                      "_test": dict(ga["params"]["_test"])}})
                        else:
                            newpop.append({"sig": ga["sig"], "test": gb["test"],
                                           "params": {"_sig": dict(gb["params"]["_sig"]),
                                                      "_test": dict(gb["params"]["_test"])}})
                    else:
                        # 突变（hill-climbing 主体）：以幸存者为基微调参数/模块
                        base = self.rng.choice(base_pool) if base_pool else random_genome(self.rng)
                        if self.rng.random() < 0.3:
                            newpop.append(random_genome(self.rng))   # 少量纯随机保多样性
                        else:
                            newpop.append(mutate_genome(base, self.rng))
                pop = newpop
        finally:
            if pool is not None:
                pool.close()
                pool.join()
        return self.leaderboard, self.all_evals

# ---------------------------------------------------------------------------
# 7. 样本外验证
# ---------------------------------------------------------------------------

def out_of_sample(ev, reds, blues, rng, frac=0.2, k_sur=25):
    """在最近 frac 比例的数据上，用同算子(含参数)重算 p；通过则需 p<0.01。"""
    n = len(reds)
    cut = int(n * (1 - frac))
    r_tr, b_tr = reds[cut:], blues[cut:]
    if len(r_tr) < 60:
        return None
    tier = ev["tier"]
    res = evaluate(ev["sig"], ev["test"], r_tr, b_tr, rng,
                   k_sur if tier == "light" else 10, params=ev.get("params"))
    if res is None:
        return None
    return res["p_raw"]
