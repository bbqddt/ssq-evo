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
import math
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

def sm_vector_mag(reds, blues):
    # "频率/振动"：每期 6 个红球视作 6 个角度 2π r/33 的单位向量，
    # 求和后的合向量模长（相干性度量）
    ang = 2 * np.pi * reds.astype(float) / 33.0
    cx = np.cos(ang).sum(axis=1)
    cy = np.sin(ang).sum(axis=1)
    return np.sqrt(cx ** 2 + cy ** 2)

def sm_vector_phase(reds, blues):
    ang = 2 * np.pi * reds.astype(float) / 33.0
    cx = np.cos(ang).sum(axis=1)
    cy = np.sin(ang).sum(axis=1)
    return np.arctan2(cy, cx)

def sm_complex_field(reds, blues):
    # 红+蓝共 7 个号码视作 7 相位的合向量模（更强的"场"映射）
    nums = np.column_stack([reds, blues.reshape(-1, 1)]).astype(float)
    ang = 2 * np.pi * nums / 34.0
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

def t_corr_dim_slope(x, m_max=7, tau=1, r_log=None):
    x = (x - x.mean()) / (x.std() + 1e-12)
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

def t_approx_entropy(x, m=2, r=None, sub=1200):
    x = np.asarray(x, float)
    n = len(x)
    if n > sub:
        x = x[:sub]
        n = len(x)
    if r is None:
        r = 0.2 * (x.std() + 1e-12)
    def _phi(m_):
        if n <= m_:
            return 0.0
        cnt = 0.0
        denom = n - m_ + 1
        for i in range(n - m_ + 1):
            a = x[i:i + m_]
            c = 0
            for j in range(n - m_ + 1):
                if np.max(np.abs(a - x[j:j + m_])) <= r:
                    c += 1
            cnt += math.log(c / denom)
        return cnt / denom
    phi1 = _phi(m)
    phi2 = _phi(m + 1)
    return float(phi1 - phi2)  # 随机 -> 大；确定性 -> 小 -> 'low'

def t_rq_determinism(x, m=2, r=None, sub=600):
    x = np.asarray(x, float)
    n = len(x)
    if n > sub:
        x = x[:sub]
        n = len(x)
    if r is None:
        r = 0.1 * (x.std() + 1e-12)
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
    d = np.zeros(M)
    for i in range(M):
        dist = np.sqrt(((emb - emb[i]) ** 2).sum(axis=1))
        dist[i] = np.inf
        j = np.argmin(dist)
        if i + tau < M and j + tau < M:
            d[i] = np.log((np.sqrt(((emb[i + tau] - emb[j + tau]) ** 2).sum()) + 1e-12) /
                          (dist[j] + 1e-12))
    return float(np.mean(d))  # 随机 ~0/负；确定性正 -> 'high'

TESTS = {
    "fft_peak":   (t_fft_peak, "high", "light"),
    "acf_max":    (t_acf_max, "high", "light"),
    "dfa_alpha":  (t_dfa_alpha, "high", "light"),
    "mi_max":     (t_mi_max, "high", "light"),
    "corr_dim_slope": (t_corr_dim_slope, "high", "light"),
    "perm_entropy": (t_perm_entropy, "low", "heavy"),
    "approx_entropy": (t_approx_entropy, "low", "heavy"),
    "rq_determinism": (t_rq_determinism, "high", "heavy"),
    "lyap":       (t_lyap_rosenstein, "high", "heavy"),
}

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

# ---------------------------------------------------------------------------
# 4. 单算子评估
# ---------------------------------------------------------------------------

def evaluate(sig_name, test_name, reds, blues, rng, k_sur, sur_type="aaft"):
    if sig_name not in SIGMAPS or test_name not in TESTS:
        return None
    x = SIGMAPS[sig_name](reds, blues)
    func, direction, tier = TESTS[test_name]
    real = func(x)
    if not math.isfinite(real):
        return None
    surs = []
    for _ in range(k_sur):
        if sur_type == "aaft":
            sx = aaft_surrogate(x, rng)
        else:
            sx = shuffle_surrogate(x, rng)
        sv = func(sx)
        if math.isfinite(sv):
            surs.append(sv)
    surs = np.array(surs)
    if surs.size == 0:
        return None
    mean_s, std_s = surs.mean(), surs.std() + 1e-12
    z = (real - mean_s) / std_s
    if direction == "high":
        p = (1.0 + np.sum(surs >= real)) / (1.0 + surs.size)
    else:
        p = (1.0 + np.sum(surs <= real)) / (1.0 + surs.size)
    return {
        "sig": sig_name, "test": test_name, "tier": tier, "direction": direction,
        "stat": real, "sur_mean": float(mean_s), "sur_std": float(std_s),
        "z": float(z), "p_raw": float(p), "k_sur": int(surs.size), "sur_max": float(surs.max()), "sur_min": float(surs.min()),
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

class Evolution:
    def __init__(self, reds, blues, rng, k_light=25, k_heavy=10, epochs=6, pop=24):
        self.reds = reds
        self.blues = blues
        self.rng = rng
        self.k_light = k_light
        self.k_heavy = k_heavy
        self.epochs = epochs
        self.pop = pop
        self.sig_names = list(SIGMAPS.keys())
        self.test_names = list(TESTS.keys())
        self.all_evals = []      # 汇总每轮所有评估（用于 FDR）
        self.leaderboard = {}    # (sig,test) -> best eval

    def _k(self, tier):
        return self.k_light if tier == "light" else self.k_heavy

    def _eval(self, sig, test):
        tier = TESTS[test][2]
        ev = evaluate(sig, test, self.reds, self.blues, self.rng, self._k(tier))
        if ev is None:
            return None
        self.all_evals.append(ev)
        key = (sig, test)
        if key not in self.leaderboard or ev["p_raw"] < self.leaderboard[key]["p_raw"]:
            self.leaderboard[key] = ev
        return ev

    def _random_config(self):
        return (self.rng.choice(self.sig_names), self.rng.choice(self.test_names))

    def run(self):
        # 初始随机种群
        pop = [self._random_config() for _ in range(self.pop)]
        for ep in range(self.epochs):
            evals = []
            for sig, test in pop:
                ev = self._eval(sig, test)
                if ev:
                    evals.append((sig, test, ev))
            # 选择：本代按 p_raw 取前 50%
            evals.sort(key=lambda t: t[2]["p_raw"])
            survivors = [(s, t) for s, t, _ in evals[:max(2, len(evals) // 2)]]
            # 变异 + 重组 生成下一代
            newpop = list(survivors)
            while len(newpop) < self.pop:
                if self.rng.random() < 0.5 and len(survivors) >= 2:
                    a, b = self.rng.choice(len(survivors), 2, replace=False)
                    sa, ta = survivors[a]
                    sb, tb = survivors[b]
                    # 重组：交换信号或检验
                    if self.rng.random() < 0.5:
                        newpop.append((sb, ta))
                    else:
                        newpop.append((sa, tb))
                else:
                    newpop.append(self._random_config())
            pop = newpop
        return self.leaderboard, self.all_evals

# ---------------------------------------------------------------------------
# 7. 样本外验证
# ---------------------------------------------------------------------------

def out_of_sample(ev, reds, blues, rng, frac=0.2, k_sur=25):
    """在最近 frac 比例的数据上，用同算子重算 p；通过则需 p<0.01。"""
    n = len(reds)
    cut = int(n * (1 - frac))
    r_tr, b_tr = reds[cut:], blues[cut:]
    if len(r_tr) < 60:
        return None
    tier = ev["tier"]
    res = evaluate(ev["sig"], ev["test"], r_tr, b_tr, rng,
                   k_sur if tier == "light" else 10)
    if res is None:
        return None
    return res["p_raw"]
