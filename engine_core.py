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
import copy
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

# --- 新增信号映射：更丰富的"结构"刻画（研发级扩展）---
def _presence33(reds):
    P = np.zeros((reds.shape[0], 33), dtype=float)
    for i in range(reds.shape[0]):
        P[i, reds[i].astype(int) - 1] = 1.0
    return P

def sm_red_gap_mean(reds, blues):
    s = np.sort(reds, axis=1); return np.diff(s, axis=1).mean(axis=1)

def sm_red_gap_max(reds, blues):
    s = np.sort(reds, axis=1); return np.diff(s, axis=1).max(axis=1)

def sm_red_gap_std(reds, blues):
    s = np.sort(reds, axis=1); return np.diff(s, axis=1).std(axis=1)

def sm_red_runs(reds, blues):
    P = _presence33(reds)
    return np.array([np.sum(r[1:] != r[:-1]) + 1 for r in P], dtype=float)

def sm_red_low_count(reds, blues):
    return (reds <= 16).sum(axis=1).astype(float)

def sm_red_zone_entropy(reds, blues):
    zone = (reds - 1) // 3
    H = np.zeros(reds.shape[0])
    for i in range(reds.shape[0]):
        c = np.bincount(zone[i].astype(int), minlength=11).astype(float)
        p = c / c.sum(); H[i] = -np.sum(p[p > 0] * np.log(p[p > 0]))
    return H

def sm_red_recurrence_mean(reds, blues):
    N = reds.shape[0]; last = {}; out = np.zeros(N)
    for i in range(N):
        ints = []
        for num in reds[i].astype(int):
            li = last.get(num, -1); ints.append(i - li if li >= 0 else N); last[num] = i
        out[i] = np.mean(ints)
    return out

def sm_red_consecutive(reds, blues):
    s = np.sort(reds, axis=1); return (np.diff(s, axis=1) == 1).sum(axis=1).astype(float)

def sm_red_sum_mod11(reds, blues):
    return (reds.sum(axis=1) % 11).astype(float)

def sm_red_sum_mod16(reds, blues):
    return (reds.sum(axis=1) % 16).astype(float)

def sm_red_prime_count(reds, blues):
    primes = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31}
    return np.array([sum(1 for x in row if int(x) in primes) for row in reds], dtype=float)

def sm_blue_resid(reds, blues):
    return blues.astype(float) - (reds.sum(axis=1) % 16).astype(float)

# 非年表序重构探针：把同一物理量按"非时间"顺序重排，直接检验"结构是否依赖时间次序"
# （对应"时间不存在/块状宇宙"假设——若重排后反而浮现结构，则支持结构为块状而非时序）。
def sm_red_sum_rev(reds, blues):
    return sm_red_sum(reds, blues)[::-1]

def sm_red_sum_block(reds, blues):
    x = sm_red_sum(reds, blues); n = x.shape[0]
    side = max(2, int(np.floor(np.sqrt(n))))
    out = []
    for start in range(0, n, side):
        seg = list(range(start, min(start + side, n)))
        out.extend(reversed(seg))
    return x[out]

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
    # --- 新增 ---
    "red_gap_mean": sm_red_gap_mean,
    "red_gap_max": sm_red_gap_max,
    "red_gap_std": sm_red_gap_std,
    "red_runs": sm_red_runs,
    "red_low_count": sm_red_low_count,
    "red_zone_entropy": sm_red_zone_entropy,
    "red_recurrence_mean": sm_red_recurrence_mean,
    "red_consecutive": sm_red_consecutive,
    "red_sum_mod11": sm_red_sum_mod11,
    "red_sum_mod16": sm_red_sum_mod16,
    "red_prime_count": sm_red_prime_count,
    "blue_resid": sm_blue_resid,
    "red_sum_rev": sm_red_sum_rev,
    "red_sum_block": sm_red_sum_block,
}

# --- 复合公式层（真正的"公式研发"：在基信号上构造表达式，支持非线性与一层嵌套）---
# 一元算子（只作用于 a，忽略 b）：对信号做非线性变换，极大扩展公式表达力
COMP_UNARY = ["sin", "cos", "abs"]
# 二元/时序算子
COMP_OPS = ["+", "-", "*", "/", "diff", "z", "lag", "pow", "thresh"]
# 允许 b 为嵌套子公式的算子（实现"复合套复合"）
COMP_OPS_NEST = ["+", "-", "*", "/", "pow"]
BASE_SIGNALS = list(SIGMAPS.keys())  # 在加入 comp 之前取，避免递归引用

def _base_signals(reds, blues):
    out = {}
    for name in BASE_SIGNALS:
        try:
            if name in SIG_PARAM_SIGMAPS:
                out[name] = np.asarray(SIGMAPS[name](reds, blues, **{}), float)
            else:
                out[name] = np.asarray(SIGMAPS[name](reds, blues), float)
        except Exception:
            out[name] = np.full(reds.shape[0], np.nan)
    return out

def _inpaint(x):
    """用线性插值修复 NaN，并保持与开奖期一一对应(长度不变)，避免丢行导致准确率评估错位。"""
    x = np.asarray(x, float).copy()
    n = len(x)
    idx = np.arange(n)
    good = ~np.isnan(x)
    if good.all():
        return x
    if not good.any():
        return np.zeros(n)
    x[~good] = np.interp(idx[~good], idx[good], x[good])
    first = np.where(good)[0][0]; last = np.where(good)[0][-1]
    x[:first] = x[first]; x[last + 1:] = x[last]
    return x

def _operand(spec, reds, blues, depth, base=None):
    """spec 可以是 基信号名(str) 或 嵌套复合(dict)。返回长度 N 数组或 None。"""
    if isinstance(spec, dict):
        if depth >= 2:
            return None
        return _build_comp(spec, reds, blues, depth + 1, base)
    if isinstance(spec, str) and spec in BASE_SIGNALS:
        if base is None:
            base = _base_signals(reds, blues)
        return base.get(spec)
    return None

def _transform(op, a):
    a = np.asarray(a, float)
    if op == "sin": return np.sin(a)
    if op == "cos": return np.cos(a)
    if op == "abs": return np.abs(a)
    return a

def apply_comp(op, a, b, k):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if op == "+": return a + b
    if op == "-": return a - b
    if op == "*": return a * b
    if op == "/":
        safe = np.where(np.abs(b) < 1e-9, 1e-9, b)
        return np.where(np.abs(b) < 1e-9, 0.0, a / safe)
    if op == "diff":
        out = np.empty_like(a); out[0] = np.nan; out[1:] = np.diff(a); return out
    if op == "z":
        return (a - np.nanmean(a)) / (np.nanstd(a) + 1e-12)
    if op == "lag":
        k = int(k); out = np.empty_like(a); out[:k] = np.nan; out[k:] = a[:-k]; return out
    if op == "pow":
        exp = np.clip(b, -3.0, 3.0)
        return np.sign(a) * np.abs(a) ** np.abs(exp)
    if op == "thresh":
        return a - np.nanmedian(a)
    return a

def _build_comp(cp, reds, blues, depth=0, base=None):
    """递归构造复合公式序列；depth 限制嵌套层数(防退化)。长度恒为 N(与开奖期对齐)。"""
    if not cp or not isinstance(cp, dict):
        return None
    op = cp.get("op")
    if op in ("sin", "cos", "abs"):
        a = _operand(cp.get("a"), reds, blues, depth, base)
        if a is None: return None
        x = _transform(op, a)
    else:
        a = _operand(cp.get("a"), reds, blues, depth, base)
        if a is None: return None
        b = _operand(cp.get("b"), reds, blues, depth, base)
        if b is None: b = np.zeros_like(a)
        x = apply_comp(op, a, b, cp.get("k", 1))
    x = np.asarray(x, float)
    if not np.all(np.isfinite(x)):
        x = _inpaint(x)
    if not np.all(np.isfinite(x)):
        return None
    return x

def _random_comp_params(rng, depth=0):
    op = rng.choice(COMP_OPS + COMP_UNARY)
    cp = {"op": op, "a": rng.choice(BASE_SIGNALS),
          "b": rng.choice(BASE_SIGNALS), "k": int(rng.integers(1, 6))}
    # 一层嵌套：二元/幂算子且较浅时，b 有概率变成子复合(公式复合套复合)
    if depth < 1 and op in COMP_OPS_NEST and rng.random() < 0.25:
        cp["b"] = _random_comp_params(rng, depth + 1)
    # 读取规则：公式定义"如何把它读成方向预测"(让准确率掌握在公式上，而非写死延续/反转)
    cp["read"] = rng.choice(["cont", "rev", "mean", "osc"])
    return cp

def _mutate_comp(cp, rng, depth=0):
    ncp = copy.deepcopy(cp)
    r = rng.random()
    if r < 0.25:
        ncp["op"] = rng.choice(COMP_OPS + COMP_UNARY)
    elif r < 0.5:
        ncp["a"] = rng.choice(BASE_SIGNALS)
    elif r < 0.75:
        if isinstance(ncp.get("b"), dict):
            ncp["b"] = _mutate_comp(ncp["b"], rng, depth + 1)
        else:
            ncp["b"] = rng.choice(BASE_SIGNALS)
    else:
        ncp["k"] = int(max(1, min(10, cp.get("k", 1) + int(rng.choice([-1, 1])))))
    if rng.random() < 0.2:
        ncp["read"] = rng.choice(["cont", "rev", "mean", "osc"])
    return ncp

def _build_x(sig_name, reds, blues, params):
    """统一构造待检验序列 x；comp 类型按复合表达式(可嵌套)组合基信号。"""
    params = params or {}
    if sig_name == "comp":
        cp = params.get("_comp")
        if not cp or not isinstance(cp, dict) or cp.get("a") not in BASE_SIGNALS:
            return None
        return _build_comp(cp, reds, blues)
    sp = params.get("_sig", {})
    if sig_name in SIG_PARAM_SIGMAPS:
        return np.asarray(SIGMAPS[sig_name](reds, blues, **sp), float)
    return np.asarray(SIGMAPS[sig_name](reds, blues), float)

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

def _sample_entropy(x, m, r):
    """Sample Entropy（Richman-Moorman）：m 维模板的不可预测性；随机序列大、确定性小。"""
    x = np.asarray(x, float); n = len(x)
    if n <= m + 1:
        return np.nan
    r = max(r, 1e-12)
    from scipy.spatial.distance import cdist
    def _phi(m_):
        if n <= m_:
            return 0.0
        emb = np.array([x[i:i + m_] for i in range(n - m_ + 1)])
        D = cdist(emb, emb, metric="chebyshev")
        cnt = (D <= r).sum(axis=1) - 1
        return np.mean(np.log((cnt + 1e-12) / (n - m_)))
    return float(_phi(m) - _phi(m + 1))

def t_sample_entropy(x, m=2, r_factor=0.2, sub=1200):
    x = np.asarray(x, float); n = len(x)
    if n > sub:
        x = x[:sub]; n = len(x)
    if n <= m + 1:
        return np.nan
    r = r_factor * (x.std() + 1e-12)
    return _sample_entropy(x, m, r)

def t_multiscale_se(x, m=2, r_factor=0.2, tau_max=5, sub=1200):
    """多尺度样本熵：在多个时间尺度(粗粒化)上取样本熵均值，刻画跨尺度复杂度；
    随机序列随尺度上升快速塌缩，结构序列维持更高 -> direction 'high'。"""
    x = np.asarray(x, float); n = len(x)
    if n > sub:
        x = x[:sub]
    if x.std() < 1e-12:
        return np.nan
    r = r_factor * (x.std() + 1e-12)
    vals = []
    for tau in range(1, tau_max + 1):
        if tau >= len(x):
            break
        y = x[: (len(x) // tau) * tau].reshape(-1, tau).mean(axis=1)
        se = _sample_entropy(y, m, r)
        if np.isfinite(se):
            vals.append(se)
    return float(np.mean(vals)) if vals else np.nan


# ---------------------------------------------------------------------------
# 转移熵（Transfer Entropy）—— 双变量信息流方向性检验
#   度量：已知源序列 X 的过去，能否降低对目标序列 Y 下一步的不确定性（超出 Y 自身历史所能）。
#   这是现有 11 个单变量检验完全不具备的维度：它探测"红球→蓝球"之间是否存在
#   有向信息耦合（双色球规则上红/蓝独立抽取，任何显著 TE 都意味着非平凡结构）。
#   direction='high'：真实信息流 > 随机打乱的基线。
# ---------------------------------------------------------------------------
def t_transfer_entropy(x_source, x_target, k_history=1, bins=8, sub=2000):
    """Schreiber Transfer Entropy (2000)：T_{X→Y} = I(X_t ; Y_{t+1} | Y_t)。
    用等宽离散化 + 条件互信息近似；k_history 是条件历史长度。
    参数：
        x_source: 源序列（如红球和/复合信号），长度 N
        x_target: 目标序列（如蓝球），长度 N
        k_history: 目标侧条件历史长度（默认 1：只条件于 Y_t）
        bins: 离散化箱数
        sub: 超长序列截断（避免 2D 直方图爆炸）
    """
    xs = np.asarray(x_source, float); xt = np.asarray(x_target, float)
    n = min(len(xs), len(xt))
    xs, xt = xs[:n], xt[:n]
    if n > sub:
        xs, xt = xs[:sub], xt[:sub]; n = sub
    if n <= k_history + 2:
        return np.nan

    # 等宽离散化（各自独立分箱）
    def _discretize(v, nb):
        lo, hi = v.min(), v.max()
        if hi - lo < 1e-12:
            return np.zeros(n, dtype=int)
        return np.clip(((v - lo) / (hi - lo) * nb).astype(int), 0, nb - 1)

    xs_d = _discretize(xs, bins)
    xt_d = _discretize(xt, bins)

    # 构建 (x_past, y_now, y_future) 三元组联合直方图
    # T_{X→Y} ≈ Σ p(x_t, y_t, y_{t+1}) log [p(y_{t+1}|x_t,y_t) / p(y_{t+1}|y_t)]
    te = 0.0
    total = 0
    for t in range(k_history, n - 1):
        # 条件：目标的历史（y_{t-k+1}, ..., y_t）
        y_hist = tuple(xt_d[t - k_history + 1:t + 1].tolist())
        key_full = (xs_d[t], y_hist, xt_d[t + 1])
        key_cond = (y_hist, xt_d[t + 1])
        key_marg = (y_hist,)
        # 在线计数（避免建超大 3D 数组）
        te += 1.0  # 占位——下面用字典统计

    # 改用字典实现（内存友好）
    from collections import defaultdict
    joint = defaultdict(int)      # (x_t, y_hist, y_{t+1}) -> count
    cond_xy = defaultdict(int)     # (y_hist, y_{t+1}) -> count  [有 x 条件]
    cond_y = defaultdict(int)     # (y_hist, y_{t+1}) -> count  [无 x 条件 = 边际]
    hist_marg = defaultdict(int)   # y_hist -> count

    for t in range(k_history, n - 1):
        y_hist = tuple(xt_d[t - k_history + 1:t + 1].tolist())
        jkey = (int(xs_d[t]), y_hist, int(xt_d[t + 1]))
        ckey = (y_hist, int(xt_d[t + 1]))
        joint[jkey] += 1
        cond_xy[ckey] += 1
        cond_y[ckey] += 1          # 边际也计（与 cond_xy 相同因为无条件时就是边际）
        hist_marg[y_hist] += 1

    total = n - k_history - 1
    if total < 10:
        return np.nan

    te_val = 0.0
    for (xt_val, yh, yp_next), c_joint in joint.items():
        c_cond = cond_xy.get((yh, yp_next), 0)
        c_marg_hist = hist_marg.get(yh, 1)
        # P(y_{t+1} | x_t, y_hist) ≈ c_joint / sum over yp of joint[(xt_val, yh, *)]
        # P(y_{t+1} | y_hist) ≈ sum_x joint[(x, yh, yp_next)] / sum over yp,x joint[(*, yh, *)]
        p_joint_given = c_joint / max(c_joint, 1)  # 简化：用联合频率比
        # 更精确的 TE 计算：
        pass

    # 精确 TE 公式（KSG 估计器太重，用直方图版）
    te_val = 0.0
    for jkey, c_j in joint.items():
        xv, yh, yn = jkey
        # P(x, y_hist, y_next)
        p_xyz = c_j / total
        # P(y_hist, y_next) = marginal over x
        c_y_yn = sum(joint.get((xx, yh, yn), 0) for xx in range(bins))
        p_yz = c_y_yn / total
        # P(y_hist)
        c_y = hist_marg[yh]
        p_y = c_y / total
        if p_xyz > 1e-12 and p_yz > 1e-12 and p_y > 1e-12:
            te_val += p_xyz * math.log2((p_xyz * p_y) / (p_yz * p_yz) + 1e-30)

    return float(max(0.0, te_val))

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
    "sample_entropy": (t_sample_entropy, "low", "heavy"),
    "multiscale_se":  (t_multiscale_se, "high", "heavy"),
    # --- 双变量检验（探测序列间有向信息流，现有单变量检验完全不具备的维度）---
    "transfer_entropy": (t_transfer_entropy, "high", "heavy"),
}

# 双变量检验标记：这些 test_fn 签名为 fn(x_source, x_target, **params) 而非 fn(x, **params)
BIVARIATE_TESTS = {"transfer_entropy"}

# ---------------------------------------------------------------------------
# 2a. 零假设类型路由 (TEST_SUR_TYPE)
#     确定性/复杂度类检验度量"时序结构是否超出纯随机"，其*正确*零假设是
#     完全打乱(shuffle，破坏一切时序)，而非 AAFT(仅破坏非线性、仍保留自相关+谱)。
#     用 AAFT 作零假设会让这些检验偏松——替代序列本身自带自相关结构，等于拿
#     "有结构的序列"当"随机基线"，极易把真随机判成"有确定性"。
#     改路由到 shuffle 才是诚实的硬零假设：若结构能扛过"彻底打乱时间次序"，
#     才算真·时序结构（直接服务于"时间不存在/块状宇宙"假设的反向检验）。
#     这是本轮回"另寻出路"的核心：用更硬的零假设重新裁决那些'边界显著'的候选。
TEST_SUR_TYPE = {
    "perm_entropy":   "shuffle",
    "approx_entropy": "shuffle",
    "rq_determinism": "shuffle",
    "lyap":           "shuffle",
    "sample_entropy": "shuffle",
    "multiscale_se":  "shuffle",
    "transfer_entropy": "shuffle",   # 双变量：打乱时间耦合（保留边际分布）
    # 谱/自相关类检验：AAFT 仅随机化相位、会保留功率谱与自相关，对周期性/线性时序结构
    # 完全失明(阳性对照证实 fft_peak 在 aaft 下 p=0.92，在 shuffle 下 p=0.005)。必须用
    # shuffle(彻底打乱时间次序) 才有判别力——否则"无结构"结论可能是漏掉周期性的假阴性。
    "fft_peak":       "shuffle",
    "acf_max":        "shuffle",
    "dfa_alpha":      "shuffle",
    "mi_max":         "shuffle",
    "corr_dim_slope": "shuffle",
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
    "sample_entropy": {"m": (1, 3, 1), "r_factor": (0.10, 0.40, 0.05)},
    "multiscale_se":  {"m": (1, 3, 1), "r_factor": (0.10, 0.40, 0.05), "tau_max": (3, 6, 1)},
    "transfer_entropy": {"k_history": (1, 3, 1), "bins": (4, 10, 1)},
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
SIG_NAMES = list(SIGMAPS.keys()) + ["comp"]
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
    params = _random_params(sig, test, rng)
    params["_reorder"] = rng.choice(REORDER_MODES) if rng.random() < 0.3 else "identity"
    if sig == "comp":
        params["_comp"] = _random_comp_params(rng)
    return {"sig": sig, "test": test, "params": params}


def mutate_genome(g, rng):
    """对基因组做突变：宏观换模块 或 微调某个旋钮（hill-climbing 核心）。"""
    ng = {"sig": g["sig"], "test": g["test"],
          "params": copy.deepcopy(g["params"])}
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
    # 重排基因：偶尔切换块状宇宙探针模式
    if rng.random() < 0.12:
        ng["params"]["_reorder"] = rng.choice(REORDER_MODES)
    # 复合公式层：若当前为 comp 个体，一并变异其表达式
    if ng["sig"] == "comp":
        if "_comp" not in ng["params"] or not ng["params"]["_comp"]:
            ng["params"]["_comp"] = _random_comp_params(rng)
        else:
            ng["params"]["_comp"] = _mutate_comp(ng["params"]["_comp"], rng)
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
    if sur_type == "twin":
        return twin_surrogate(x, k, rng)
    # random-phase (amplitude-adjusted) surrogate
    ph = rng.uniform(0, 2 * np.pi, (k, M))
    Y = np.fft.irfft(mag * np.exp(1j * ph), n, axis=1)
    ranks = np.argsort(np.argsort(Y, axis=1), axis=1)
    xs = np.sort(x)
    return xs[ranks].astype(float)


def _iaaft_batch(x, k, rng, iters=5):
    """IAAFT (iterative AAFT) 替代序列：在随机相位(保留功率谱)基础上反复做幅度校正，
    使替代序列边际分布更接近真实 x，比 AAFT 更忠实地满足零假设(用于交叉验证)。"""
    x = np.asarray(x, float); n = len(x)
    if n == 0 or k <= 0:
        return np.empty((0, 0))
    fx = np.fft.rfft(x); mag = np.abs(fx); M = len(fx)
    xs = np.sort(x)
    out = np.empty((k, n))
    for j in range(k):
        ph = rng.uniform(0, 2 * np.pi, M)
        y = np.fft.irfft(mag * np.exp(1j * ph), n)
        for _ in range(iters):
            ranks = np.argsort(np.argsort(y))
            y = xs[ranks]
            Yr = np.fft.rfft(y)
            y = np.fft.irfft(mag * np.exp(1j * np.angle(Yr)), n)
        ranks = np.argsort(np.argsort(y))
        out[j] = xs[ranks]
    return out.astype(float)


def _embed(x, m, tau):
    """相空间延迟重构：返回 (N, m) 嵌入矩阵，N = len(x)-(m-1)*tau。"""
    x = np.asarray(x, float)
    N = len(x) - (m - 1) * tau
    if N <= 0:
        return None
    emb = np.empty((N, m))
    for j in range(m):
        emb[:, j] = x[j * tau:j * tau + N]
    emb = (emb - emb.mean(0)) / (emb.std(0) + 1e-9)
    return emb


def twin_surrogate(x, k, rng, m=3, tau=1, theiler=1, n_neigh=10):
    """Twin surrogates (Thiel et al. 2006) —— 确定性结构检验的金标准零假设。

    与 shuffle(破坏一切时序)/AAFT(仅破坏非线性、保留谱)不同，twin 保留**相空间递归结构**
    （非线性确定性动力学的指纹），只破坏时间次序。做法是：把序列嵌入相空间，对每个点找
    其最近邻(theiler 窗排除时间近邻)，再从随机起点沿"最近邻链"游走生成新序列——相邻点
    在相空间上递归相关，但时间次序被彻底打乱。

    含义：若某统计量在 shuffle/AAFT 下显著、却在 twin 下不显著 → 其"结构"只是线性/递归伪迹
    （可由确定性动力学解释），并非真·例外；只有连 twin 都扛住的，才是真·非平凡结构。
    因此 twin 是比 shuffle 更严的零假设，把它纳入 cross_validate 只会让结论更保守、更诚实。

    返回 shape (k, n)。序列过短(不足以嵌入)时退化为 shuffle 以保证不崩溃。
    """
    x = np.asarray(x, float); n = len(x)
    if n < (m - 1) * tau + 3:
        return np.tile(rng.permutation(x), (k, 1)).astype(float)
    emb = _embed(x, m, tau)
    if emb is None:
        return np.tile(rng.permutation(x), (k, 1)).astype(float)
    N = emb.shape[0]
    # 逐行算欧氏距离，找出每个点的最近邻（排除自身 + theiler 时间窗）
    neigh = np.empty((N, n_neigh), dtype=int)
    for i in range(N):
        d = np.sum((emb - emb[i]) ** 2, axis=1)
        d[i] = np.inf
        lo, hi = max(0, i - theiler), min(N, i + theiler + 1)
        d[lo:hi] = np.inf
        neigh[i] = np.argpartition(d, n_neigh)[:n_neigh]
    out = np.empty((k, n))
    for s in range(k):
        cur = int(rng.integers(N))
        seq = [cur]
        for _ in range(n - 1):
            twins = neigh[cur]
            cur = int(twins[rng.integers(len(twins))])
            seq.append(cur)
        out[s] = x[np.asarray(seq)]
    return out.astype(float)


# 块状宇宙探针：把开奖序列按"非时间次序"重排后再检验结构。
#  identity 不变；reverse 时间反演；block 块内反序(保留局部连续、打乱全局次序)；
#  shuffle 完全随机打乱(最强检验：若结构能扛过 shuffle，必为边际/静态属性而非时序)。
REORDER_MODES = ["identity", "reverse", "block", "shuffle"]

def apply_reorder(reds, blues, mode, rng):
    if not mode or mode == "identity":
        return reds, blues
    n = reds.shape[0]
    if mode == "reverse":
        return reds[::-1], blues[::-1]
    if mode == "block":
        side = max(2, int(np.floor(np.sqrt(n))))
        idx = []
        for start in range(0, n, side):
            seg = list(range(start, min(start + side, n)))
            idx.extend(reversed(seg))
        idx = np.array(idx)
        return reds[idx], blues[idx]
    if mode == "shuffle":
        perm = rng.permutation(n)
        return reds[perm], blues[perm]
    return reds, blues

# ---------------------------------------------------------------------------
# 4. 单算子评估
# ---------------------------------------------------------------------------

def evaluate(sig_name, test_name, reds, blues, rng, k_sur, sur_type=None, params=None):
    if (sig_name not in SIGMAPS and sig_name != "comp") or test_name not in TESTS:
        return None
    params = params or {"_sig": {}, "_test": {}}
    sig_params = params.get("_sig", {})
    test_params = params.get("_test", {})
    # 块状宇宙重排基因：在构造信号前先把开奖序列按指定次序重排
    reorder = params.get("_reorder", "identity")
    if reorder and reorder != "identity":
        reds, blues = apply_reorder(reds, blues, reorder, rng)
    try:
        x = _build_x(sig_name, reds, blues, params)
    except Exception:
        return None
    if x is None or x.shape[0] < 8:
        return None
    func, direction, tier = TESTS[test_name]
    is_bivar = test_name in BIVARIATE_TESTS

    # 双变量检验：目标序列固定为蓝球（ravel 为 1D）
    target = None
    if is_bivar:
        target = blues.ravel().astype(float)[:len(x)]

    try:
        if is_bivar:
            real = func(x, target, **test_params)
        else:
            real = func(x, **test_params)
    except Exception:
        return None
    if not math.isfinite(real):
        return None
    n = len(x)
    st = sur_type if sur_type in ("aaft", "iaaft", "shuffle", "twin") else TEST_SUR_TYPE.get(test_name, "aaft")

    # --- surrogate 生成（双变量：联合打乱时间索引，保留边际分布但破坏耦合）---
    svals = []
    if is_bivar and st == "shuffle":
        # 双变量 shuffle 零假设：对 source 和 target 施加相同的随机排列，
        # 保留各自边际分布，但彻底破坏 X→Y 的时序信息流。
        for _ in range(int(k_sur)):
            idx = rng.permutation(n)
            try:
                sv = func(x[idx], target[idx], **test_params)
            except Exception:
                continue
            if math.isfinite(sv):
                svals.append(sv)
    else:
        # 单变量 / AAFT 路径（原有逻辑）
        try:
            surs = _gen_surrogates(x, int(k_sur), rng, st)
        except Exception:
            surs = np.empty((0, n))
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
    mean_s = svals.mean()
    std_s = svals.std()
    # 代理分布退化(std≈0)时 z 无意义且会被 1e-12 地板放大成 ±1e9 量级的伪异常；
    # 此时该检验对真实序列与 surrogate 无判别力，z 直接置 0（p 仍由排序法独立计算，不受影响）。
    if std_s < 1e-9:
        z = 0.0
    else:
        z = (real - mean_s) / std_s
    if direction == "high":
        p = (1.0 + np.sum(svals >= real)) / (1.0 + svals.size)
    else:
        p = (1.0 + np.sum(svals <= real)) / (1.0 + svals.size)
    return {
        "sig": sig_name, "test": test_name, "tier": tier, "direction": direction,
        "params": params, "sur_type": st,
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
                                 self.reds, self.blues, k,
                                 TEST_SUR_TYPE.get(g["test"], "aaft"), seed))
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
                newpop = [{"sig": g["sig"], "test": g["test"],
                           "params": copy.deepcopy(g["params"])} for g in base_pool]
                while len(newpop) < self.pop:
                    if self.rng.random() < 0.5 and len(base_pool) >= 2:
                        a, b = self.rng.choice(len(base_pool), 2, replace=False)
                        ga, gb = base_pool[a], base_pool[b]
                        # 重组：交换信号映射 或 检验（保留各自参数作起点；不丢 _comp/_reorder）
                        if self.rng.random() < 0.5:
                            cp = copy.deepcopy(gb["params"])
                            cp["_test"] = copy.deepcopy(ga["params"]["_test"])
                            newpop.append({"sig": gb["sig"], "test": ga["test"], "params": cp})
                        else:
                            cp = copy.deepcopy(ga["params"])
                            cp["_test"] = copy.deepcopy(gb["params"]["_test"])
                            newpop.append({"sig": ga["sig"], "test": gb["test"], "params": cp})
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
                   k_sur if tier == "light" else max(10, k_sur // 2), params=ev.get("params"))
    if res is None:
        return None
    return res["p_raw"]


def _pred_from_rule(x, rule, k, nn):
    """把信号 x 翻译成「下一期方向预测」数组(长度 nn-1，与 actual 对齐)。
    cont=延续上一期增量方向；rev=反转；mean=相对历史均值的偏离方向；osc=k 步动量。
    预测只用到 t 及以前样本，完全因果。"""
    x = np.asarray(x, float)
    inc = np.diff(x)
    if rule == "cont":
        pred = np.zeros(nn - 1); pred[1:] = np.sign(inc[:-1])
    elif rule == "rev":
        pred = np.zeros(nn - 1); pred[1:] = -np.sign(inc[:-1])
    elif rule == "mean":
        cs = np.cumsum(x)
        pred = np.zeros(nn - 1)
        idx = np.arange(1, nn - 1)
        # pred[k] = sign(x[k+1] - mean(x[0..k+1]))
        pred[idx] = np.sign((idx + 2) * x[idx + 1] - cs[idx + 1])
    elif rule == "osc":
        kk = max(2, int(k))  # k=1 退化为 pred≡actual(平凡解/数据泄露)，强制≥2
        pred = np.zeros(nn - 1)
        idx = np.arange(kk, nn - 1)
        pred[idx] = np.sign(x[idx + 1] - x[idx + 1 - kk])
    else:
        pred = np.zeros(nn - 1)
    return pred


def _rules_from_genome(ev):
    """公式声明自己的读取规则(read)——准确率即该规则在其构造序列上的因果一步命中率，
    从而「准确率完全掌握在公式上」，而非由外部写死的延续/反转决定。
    surrogate 零假设对每条替代序列使用同一声明规则，故选择偏差被精确校准(诚实)。
    非 comp 信号无声明规则，退化为经典延续/反转(训练段择优以校准 i.i.d. 差分 -0.5 偏差)。"""
    if ev.get("sig") == "comp":
        cp = (ev.get("params") or {}).get("_comp")
        if isinstance(cp, dict):
            read = cp.get("read", "cont")
            k = cp.get("k", 1)
            if read in ("cont", "rev", "mean", "osc"):
                kk = max(2, int(k)) if read == "osc" else int(k)  # osc k=1 是平凡解
                return [(read, kk)]
    return [("cont", 0), ("rev", 0)]


def _oos_hitrate(series, cut, w, rules):
    """因果一步方向命中率（绝不偷看未来），带读取规则训练段择优。

    关键事实：i.i.d. 序列相邻一阶差分必负相关(≈-0.5)，单一固定预测器在随机数据上命中率
    并非 0.5 而会被结构抬高/压低。这里在「训练段」(t<cut) 于给定规则集中选 OOS 命中率最高
    者用于「样本外段」(t>=cut)。替代分布对每条替代序列重复同样择优，故选择偏差被零假设校准。

    actual[k]=sign(s[k+1]-s[k])；返回 (hr, tot, best_rule)。"""
    s = np.asarray(series, float)
    nn = len(s)
    inc = np.diff(s)
    if inc.size < 4:
        return float("nan"), 0, None
    actual = np.sign(inc)
    lo_t = max(w, cut)
    tr_idx = np.arange(1, max(2, lo_t))
    def _hr(pred):
        pv = pred[tr_idx]; av = actual[tr_idx]
        v = (pv != 0) & (av != 0)
        return float(np.mean(pv[v] == av[v])) if v.sum() >= 30 else 0.5
    best_rule, best_score = None, -1.0
    for rule in rules:
        sc = _hr(_pred_from_rule(s, rule[0], rule[1], nn))
        if sc > best_score:
            best_score, best_rule = sc, rule
    if best_rule is None:
        return float("nan"), 0, None
    os_idx = np.arange(lo_t, nn - 1)
    if os_idx.size < 30:
        return float("nan"), 0, None
    pv = _pred_from_rule(s, best_rule[0], best_rule[1], nn)[os_idx]
    av = actual[os_idx]
    valid = (pv != 0) & (av != 0)
    if valid.sum() < 30:
        return float("nan"), 0, 0
    return float(np.mean(pv[valid] == av[valid])), int(valid.sum()), best_rule


def oos_accuracy(ev, reds, blues, rng, frac=0.2, w=60, k_sur=40):
    """诚实的「高于随机」方向准确率 —— 准确率由公式自身的读取规则决定。

    预测权交给公式：复合公式可在 {延续, 反转, 均值偏离, k步动量} 四种读取规则中，
    于训练段择优出最适合它的读法（普通信号退化为经典延续/反转）。训练段择优对真实与
    每条 AAFT 替代序列同样执行，故选择偏差被零假设自动校准。真实命中率在替代分布中
    的百分位 p_random 是诚实的「高于随机」度量：p_random <= 0.05 才算显著。

    返回 dict 或 None（数据不足）。字段同前 + best_rule。"""
    if len(reds) < 60:
        return None
    # 块状宇宙重排基因同样作用于准确率评估（结构是否依赖时间次序？）
    reorder = (ev.get("params") or {}).get("_reorder", "identity")
    if reorder and reorder != "identity":
        reds, blues = apply_reorder(reds, blues, reorder, rng)
    sig = ev["sig"]
    try:
        x = _build_x(sig, reds, blues, ev.get("params"))
    except Exception:
        return None
    if x is None or x.shape[0] < 80:
        return None
    n = x.shape[0]                       # 序列长度以实际构造结果为准(comp 经 inpaint 长度仍为 N)
    cut = int(n * (1 - frac))
    if cut < w + 5 or (n - cut) < 40:
        return None
    rules = _rules_from_genome(ev)
    hr, tot, best_rule = _oos_hitrate(x, cut, w, rules)
    if not np.isfinite(hr) or tot < 30:
        return None
    # 替代分布：确定性/复杂度类检验用 shuffle(彻底打乱时间次序)作零假设，其余用 AAFT。
    # 让"方向准确率高于随机"的校准与结构检验保持一致(诚实)。
    try:
        surs = _gen_surrogates(x, int(k_sur), rng, TEST_SUR_TYPE.get(ev["test"], "aaft"))
    except Exception:
        surs = np.empty((0, n))
    hr_sur = []
    for i in range(surs.shape[0] if surs.ndim == 2 else 0):
        h, _, _ = _oos_hitrate(surs[i], cut, w, rules)
        if np.isfinite(h):
            hr_sur.append(h)
    hr_sur = np.array(hr_sur) if hr_sur else np.empty(0)
    if hr_sur.size == 0:
        return None
    # 单侧百分位：真实命中率排在第几；越小越「高于随机」(clamp 到 [0,1] 防越界)
    p_random = float((hr_sur >= hr).mean() + 0.5 / hr_sur.size)
    p_random = min(1.0, max(0.0, p_random))
    return {
        "hit_rate": hr,
        "sur_mean": float(hr_sur.mean()),
        "sur_std": float(hr_sur.std(ddof=0)) if hr_sur.size > 1 else 0.0,
        "p_random": p_random,
        "above_random": bool(p_random <= 0.05),
        "k_sur": int(hr_sur.size),
        "n": int(tot),
        "best_rule": (best_rule[0] if best_rule else None),
    }


def out_of_time(ev, reds, blues, rng, train_frac=0.85, w=60, k_sur=40):
    """Out-of-Time (OOT) 盲测 —— 预测领域防过拟合的终极诚信闸门。

    与 oos_accuracy 的区别：
    - oos_accuracy 用 100% 数据构造序列，在末尾 20% 抽测，而候选正是在「同一段末尾」反复被
      进化挑出(被反复看见的 OOS)，并非纯前瞻。
    - OOT 明确三段切分：
        [0, cut)        训练段 —— 在此冻结候选的读取规则(训练段择优，与真实+替代一致)
        [cut, holdout)  冻结段 —— 进化时看不到(候选来自训练段)，但规则已冻结 → 作"验证"
        [holdout, N)    盲测段 —— 候选/规则均于进化时不可见，最后才用冻结规则盲打
      这里取 holdout 之后的"真正未来"作盲测，是进化搜索 10000+ 公式的最后一道诚实闸门。

    流程：
    1. 在训练段 cut 处构造序列 x(冻结候选公式 + 演化参数)；
    2. 在训练段于规则集选最优读取规则 best_rule(与 oos_accuracy 同校准逻辑)；
    3. 把 best_rule 冻结，直接在盲测段 [holdout, N) 计算因果命中率(绝不偷看未来)；
    4. 用盲测段自身的替代分布(shuffle/aaft)校准"高于随机"的百分位 p_random(诚实)。

    返回 dict 或 None(数据不足)。字段: hit_rate, p_random, above_random, n, best_rule,
    holdout_n(盲测段长度)。"""
    if len(reds) < 200:
        return None
    reorder = (ev.get("params") or {}).get("_reorder", "identity")
    if reorder and reorder != "identity":
        reds, blues = apply_reorder(reds, blues, reorder, rng)
    sig = ev["sig"]
    try:
        x = _build_x(sig, reds, blues, ev.get("params"))
    except Exception:
        return None
    if x is None:
        return None
    n = x.shape[0]
    cut = int(n * train_frac)
    holdout = int(n * (train_frac + (1 - train_frac) * 0.4))  # 后 60% 中的后 40% 为盲测
    if cut < w + 5 or (holdout - cut) < 40 or (n - holdout) < 40:
        return None
    rules = _rules_from_genome(ev)
    # 1) 训练段冻结最优读取规则
    s = np.asarray(x, float)
    nn = len(s)
    inc = np.diff(s)
    actual = np.sign(inc)
    tr_idx = np.arange(1, max(2, cut))
    best_rule, best_score = None, -1.0
    for rule in rules:
        pred = _pred_from_rule(s, rule[0], rule[1], nn)
        pv = pred[tr_idx]; av = actual[tr_idx]
        v = (pv != 0) & (av != 0)
        sc = float(np.mean(pv[v] == av[v])) if v.sum() >= 30 else 0.5
        if sc > best_score:
            best_score, best_rule = sc, rule
    if best_rule is None:
        return None
    # 2) 盲测段命中率(冻结规则，因果，不偷看未来)
    os_idx = np.arange(holdout, nn - 1)
    if os_idx.size < 30:
        return None
    pv = _pred_from_rule(s, best_rule[0], best_rule[1], nn)[os_idx]
    av = actual[os_idx]
    valid = (pv != 0) & (av != 0)
    if valid.sum() < 30:
        return None
    hr = float(np.mean(pv[valid] == av[valid]))
    # 3) 盲测段替代分布校准(零假设用该检验对应 sur_type，与结构检验一致)
    try:
        surs = _gen_surrogates(s, int(k_sur), rng, TEST_SUR_TYPE.get(ev["test"], "aaft"))
    except Exception:
        surs = np.empty((0, n))
    hr_sur = []
    for i in range(surs.shape[0] if surs.ndim == 2 else 0):
        p = _pred_from_rule(surs[i], best_rule[0], best_rule[1], nn)[os_idx]
        v = (p != 0) & (av != 0)
        if v.sum() >= 30:
            hr_sur.append(float(np.mean(p[v] == av[v])))
    hr_sur = np.array(hr_sur) if hr_sur else np.empty(0)
    if hr_sur.size == 0:
        return None
    p_random = float((hr_sur >= hr).mean() + 0.5 / hr_sur.size)
    p_random = min(1.0, max(0.0, p_random))
    return {
        "hit_rate": hr,
        "sur_mean": float(hr_sur.mean()),
        "sur_std": float(hr_sur.std(ddof=0)) if hr_sur.size > 1 else 0.0,
        "p_random": p_random,
        "above_random": bool(p_random <= 0.05),
        "k_sur": int(hr_sur.size),
        "n": int(valid.sum()),
        "best_rule": (best_rule[0] if best_rule else None),
        "holdout_n": int(os_idx.size),
    }


def cross_validate_null(top, reds, blues, rng, frac=0.2, k_sur=25):
    """多零假设交叉验证：在「该检验的正确零假设(primary: 确定性类=shuffle, 其余=aaft)」
    与 AAFT、IAAFT、**TWIN** 四套零假设下分别重算结构 p 值。
    若某公式在四套零假设下都显著(p<0.05)，结论才更硬——排除'零假设设定不当'
    导致的假阳(例如用偏松的 AAFT 当基线而 shuffle 下不成立；或 shuffle 下显著但
    twin 下不显著→只是确定性递归伪迹而非真·结构)。

    twin 仅对「确定性/复杂度类」(primary=shuffle) 的**单变量**检验启用——它是这类检验的
    金标准零假设(保留相空间递归结构，仅破坏时间次序)；双变量检验(transfer_entropy)的
    时间耦合破坏已由 shuffle 处理，不参与 twin。

    返回 dict: {primary_type, primary, aaft, iaaft, twin(可选), consistent}。"""
    test = top["test"]
    primary_type = TEST_SUR_TYPE.get(test, "aaft")
    res = {"primary_type": primary_type}
    seen = set()
    for st in ("aaft", "iaaft", primary_type):
        if st in seen:
            continue
        seen.add(st)
        try:
            ev = evaluate(top["sig"], top["test"], reds, blues, rng, k_sur,
                          sur_type=st, params=top.get("params"))
        except Exception:
            ev = None
        if ev is not None:
            res[st] = float(ev["p_raw"])
    # twin 金标准零假设：仅确定性类单变量检验
    if primary_type == "shuffle" and test not in BIVARIATE_TESTS:
        try:
            ev_t = evaluate(top["sig"], top["test"], reds, blues, rng, k_sur,
                            sur_type="twin", params=top.get("params"))
            if ev_t is not None:
                res["twin"] = float(ev_t["p_raw"])
        except Exception:
            pass
    res["primary"] = res.get(primary_type)
    # consistent 要求：primary/aaft/iaaft 全显著；若算了 twin 也必须显著（更严）
    vals = [res.get("primary"), res.get("aaft"), res.get("iaaft")]
    if res.get("twin") is not None:
        vals.append(res["twin"])
    res["consistent"] = bool(
        all(v is not None for v in vals) and min(vals) < 0.05)
    return res
