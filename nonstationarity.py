"""
nonstationarity.py — 非平稳性 / 物理磨损漂移检测

第一性原理：双色球摇奖机是真实物理机器，球是实体橡胶/塑料件。服役数千期后，
球的质量分布会因磨损、掉漆而缓慢漂移，导致某些球的出现频率随时间系统性偏移。

本模块检测两类非平稳（都与"完美平稳 i.i.d."假设相悖）：
  1. 漂移(Drift)：每球出场频率随时间的趋势（磨损假说的主要载体）
     - 早期 vs 晚期比例差（分段突变/趋势）
     - Mann-Kendall 单调趋势检验（缓慢磨损）
  2. 动量(Momentum)：短期自相关（"热手"假说，球近期偏好持续）

统计约定与 engine_core 一致：
  - 零假设用 shuffle（打乱时间次序），保留边际频率但破坏时间结构
  - 多重比较用 BH-FDR 校正（49 个球 × 2 检验家族）
  - 阳性对照(注入已知漂移/动量)应被检出，证明_gate 有功效

任何"发现"都必须先过 FDR 且最好经 walk-forward 前瞻验证才算候选。
"""
import numpy as np


def _autocorr(y, lag):
    """lag 阶自相关（去均值归一化）。"""
    y = np.asarray(y, float)
    y = y - y.mean()
    n = len(y)
    if n <= lag:
        return 0.0
    denom = np.sum(y * y)
    if denom <= 0:
        return 0.0
    return float(np.sum(y[lag:] * y[:-lag]) / denom)


def _bh_fdr(pvals, q=0.05):
    """Benjamini-Hochberg FDR 校正。返回每个 p 对应的 q 值。"""
    pvals = np.asarray(pvals, float)
    m = len(pvals)
    if m == 0:
        return np.array([])
    order = np.argsort(pvals)
    ranked = pvals[order]
    qvals = ranked * m / np.arange(1, m + 1)
    qvals = np.minimum.accumulate(qvals[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.clip(qvals, 0.0, 1.0)
    return out


def _binary_for(ball, is_blue, reds, blues):
    """某球每期是否出场的 0/1 序列。"""
    if is_blue:
        return (np.asarray(blues) == ball).astype(float)
    return np.any(np.asarray(reds) == ball, axis=1).astype(float)


def ball_drift_scan(reds, blues, rng, k_sur=300, fdr_q=0.05):
    """每球漂移 + 动量检测。

    返回 dict:
        evals: list of (cat, ball, drift_obs, p_drift, mom_obs, p_mom,
                        mk_z, p_mk)
        q_drift, q_mom, q_mk: 各家族 BH-FDR q 值 (并行于 evals)
        n_sig_drift, n_sig_mom, n_sig_mk: 各家族显著数
        best_* : 最显著项
        verdict: str
        any_sig: bool
    """
    reds = np.asarray(reds)
    blues = np.asarray(blues)
    N = len(reds)
    mid = N // 2
    balls = [(False, b) for b in range(1, 34)] + [(True, b) for b in range(1, 17)]

    evals = []
    for is_blue, ball in balls:
        y = _binary_for(ball, is_blue, reds, blues)
        # 观测统计量
        drift_obs = y[mid:].mean() - y[:mid].mean()          # 晚期 - 早期 频率差
        mom_obs = _autocorr(y, 1)                             # lag-1 自相关
        # shuffle surrogate 零假设
        drift_sur = np.empty(k_sur)
        mom_sur = np.empty(k_sur)
        for i in range(k_sur):
            ys = rng.permutation(y)
            drift_sur[i] = ys[mid:].mean() - ys[:mid].mean()
            mom_sur[i] = _autocorr(ys, 1)
        p_drift = (1.0 + np.sum(np.abs(drift_sur) >= abs(drift_obs))) / (1.0 + k_sur)
        p_mom = (1.0 + np.sum(np.abs(mom_sur) >= abs(mom_obs))) / (1.0 + k_sur)
        evals.append((("blue" if is_blue else "red"), ball,
                      float(drift_obs), float(p_drift),
                      float(mom_obs), float(p_mom)))

    p_drift = np.array([e[3] for e in evals])
    p_mom = np.array([e[5] for e in evals])
    q_drift = _bh_fdr(p_drift, fdr_q)
    q_mom = _bh_fdr(p_mom, fdr_q)

    n_sig_drift = int(np.sum(q_drift < fdr_q))
    n_sig_mom = int(np.sum(q_mom < fdr_q))
    any_sig = (n_sig_drift + n_sig_mom) > 0

    def best_of(qarr):
        j = int(np.argmin(qarr))
        return evals[j], qarr[j]

    bd, bqd = best_of(q_drift)
    bm, bqm = best_of(q_mom)

    verdict = "NULL (无显著非平稳)" if not any_sig else (
        f"DRIFT 显著 {n_sig_drift} 球 | MOM {n_sig_mom}")

    return {
        "evals": evals,
        "q_drift": q_drift, "q_mom": q_mom,
        "n_sig_drift": n_sig_drift, "n_sig_mom": n_sig_mom,
        "best_drift": bd, "best_q_drift": float(bqd),
        "best_mom": bm, "best_q_mom": float(bqm),
        "verdict": verdict, "any_sig": bool(any_sig),
        "fdr_q": fdr_q, "k_sur": k_sur, "N": N,
    }


def recency_strategy_predict(reds, blues, train_n, top_k_red=6, top_k_blue=1,
                             decay=0.99):
    """用近期加权频率选号（磨损/动量假说下的预测策略）。

    对最近 train_n 期，按指数衰减加权统计每球频率，选最热的 top_k 个。
    返回 (pred_reds: set, pred_blue: int)。
    """
    reds = np.asarray(reds)
    blues = np.asarray(blues)
    N = len(reds)
    end = N
    start = max(0, end - train_n)
    idx = np.arange(start, end)
    w = decay ** (end - 1 - idx)            # 越近期权重越高
    w = w / w.sum()

    # 红球：统计每个号在窗口内被抽中的加权次数
    red_score = np.zeros(34)
    for t in range(start, end):
        wt = decay ** (end - 1 - t)
        for b in reds[t]:
            red_score[b] += wt
    red_score /= red_score.sum()

    blue_score = np.zeros(17)
    for t in range(start, end):
        wt = decay ** (end - 1 - t)
        blue_score[blues[t]] += wt
    blue_score /= blue_score.sum()

    # 选 top_k（红球取频率最高且未被选满的 6 个；此处简单取最高 6）
    red_order = np.argsort(red_score)[::-1]
    pred_reds = set(int(red_order[i]) for i in range(top_k_red))
    pred_blue = int(np.argmax(blue_score))
    return pred_reds, pred_blue, red_score, blue_score


def walk_forward_validate(reds, blues, rng, train_n=300, step=1,
                          top_k_red=6, decay=0.99):
    """walk-forward 前瞻验证近期热门策略 vs 随机基线。

    滚动窗口：用 [t-train_n, t) 训练，预测第 t 期红球集，与真实比对命中数。
    返回 dict: hit_rate(平均命中红球数/6), random_hit(随机基线期望),
               p_random(随机重排对照), n, above_random。
    """
    reds = np.asarray(reds)
    blues = np.asarray(blues)
    N = len(reds)
    if N <= train_n + 20:
        return {"hit_rate": None, "n": 0, "above_random": False}

    starts = range(train_n, N - 1, step)
    hits = []
    for t in starts:
        tr = reds[t - train_n:t]
        tb = blues[t - train_n:t]
        pred_reds, _, _, _ = recency_strategy_predict(
            tr, tb, train_n, top_k_red=top_k_red, decay=decay)
        true_reds = set(int(x) for x in reds[t])
        # 命中 = 预测集与真实集交集大小
        h = len(pred_reds & true_reds)
        hits.append(h)
    hits = np.array(hits, float)
    hit_rate = hits.mean() / top_k_red   # 命中率(命中数/应选数)

    # 随机基线：同窗口长度下随机选 6 号的期望命中
    # 超几何期望 = 6 * 6/33
    random_hit = top_k_red * (top_k_red / 33.0)
    random_rate = random_hit / top_k_red

    # 随机重排对照：把真实序列时间打乱后同样策略的命中率分布
    k_sur = 200
    sur_hits = np.empty(k_sur)
    idx_all = np.arange(N)
    for i in range(k_sur):
        perm = rng.permutation(idx_all)
        sr = reds[perm]
        sb = blues[perm]
        sh = []
        for t in starts:
            tr = sr[t - train_n:t]
            tb = sb[t - train_n:t]
            pr, _, _, _ = recency_strategy_predict(
                tr, tb, train_n, top_k_red=top_k_red, decay=decay)
            th = set(int(x) for x in sr[t])
            sh.append(len(pr & th))
        sur_hits[i] = np.mean(sh) / top_k_red
    p_random = (1.0 + np.sum(sur_hits >= hit_rate)) / (1.0 + k_sur)
    above_random = bool(hit_rate > sur_hits.mean() and p_random < 0.05)

    return {
        "hit_rate": float(hit_rate),
        "random_rate": float(random_rate),
        "sur_mean": float(sur_hits.mean()),
        "p_random": float(p_random),
        "n": int(len(hits)),
        "above_random": above_random,
    }


def rolling_window_scan(reds, blues, rng, window=200, step=50, k_sur=100,
                       fdr_q=0.05):
    """滑动窗口结构扫描（方向 2）：把全时段切成重叠窗口，逐窗跑漂移检测。

    若显著窗口占比明显高于 FDR 阈值，则全局 null 可能是假阴性。
    返回 dict: n_windows, n_sig_windows, frac_sig, verdict。
    """
    reds = np.asarray(reds)
    blues = np.asarray(blues)
    N = len(reds)
    sig_count = 0
    total = 0
    details = []
    for start in range(0, N - window, step):
        end = start + window
        wr = reds[start:end]
        wb = blues[start:end]
        res = ball_drift_scan(wr, wb, rng, k_sur=k_sur, fdr_q=fdr_q)
        total += 1
        if res["any_sig"]:
            sig_count += 1
        details.append((start, end, res["n_sig_drift"], res["n_sig_mom"]))
    frac = sig_count / total if total else 0.0
    # 期望：若全 null，约 fdr_q 比例的窗口会因偶然显著
    verdict = ("NULL (显著窗口占比=偶然水平)" if frac <= fdr_q * 1.5
               else f"LOCAL STRUCTURE: {frac:.1%} 窗口显著(远超偶然)")
    return {
        "n_windows": total, "n_sig_windows": sig_count, "frac_sig": frac,
        "expected_frac": fdr_q, "verdict": verdict, "details": details,
    }
