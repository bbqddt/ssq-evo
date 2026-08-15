# -*- coding: utf-8 -*-
"""
diff_formula.py —— 可微 Formula 候选生成器（带确认闸门）
====================================================
把离散基因组的「连续可调参数」用数值坐标上升（有限差分）在【发现段】优化，
目标：最小化 surrogate p（最大化偏离 null），从而更高效地探索假设空间
（比纯随机突变更省评估、更聚焦）。

铁律（借 #41 闭环纪律，防 null 域造假阳性）：
  1) 优化**只**发生在发现段（discovery_frac 前缀）。确认段（后部）优化器从未见过。
  2) 参数有界（CONT_*.bounds），防止极端过拟合到噪声。
  3) 优化后基因组**冻结**，必须经 evaluator.confirm_candidate 的 walk-forward
     确认闸门裁决（verdict=SIGNAL 才算数）。本模块绝不自己宣布"有结构"。
  4) 零新依赖：纯 numpy 有限差分（不引 JAX），可复现、可审计、可阳性对照。

数值梯度而非解析梯度：基信号构造含排序/取模/取整等不可微操作，有限差分
把 evaluate_on_discovery 当黑盒标量函数处理，对低维参数空间足够且稳健。
"""
import copy
import hashlib
import numpy as np
import engine_core as E
import evaluator as EV


def _deep_copy_params(params):
    """深拷贝基因组（含嵌套 _test/_comp dict），避免优化器原地修改污染调用方。"""
    return copy.deepcopy(params)


# ---------------------------------------------------------------------------
# 连续可优化参数空间（参数路径 + 边界 + 初值）
# 聚焦各检验的真实连续超参；comp 公式的整型 k 也作为一维坐标。
# ---------------------------------------------------------------------------
CONT_TEST_PARAMS = {
    "acf_max":        [("maxlag", 2, 40, 5)],
    "mi_max":         [("bins", 4, 24, 12), ("maxlag", 2, 20, 8)],
    "dfa_alpha":      [("tau_max", 2, 8, 4)],
    "perm_entropy":   [("order", 2, 5, 3), ("delay", 1, 5, 1)],
    "sample_entropy": [("m", 1, 3, 2), ("r_factor", 0.1, 0.3, 0.2)],
    "approx_entropy": [("m", 1, 3, 2), ("r_factor", 0.1, 0.3, 0.2)],
    "corr_dim_slope": [("m", 2, 7, 4), ("tau", 1, 3, 1)],
    "multiscale_se":  [("m", 1, 3, 2), ("tau_max", 2, 5, 3), ("r_factor", 0.1, 0.3, 0.2)],
}
COMP_K_BOUNDS = (1, 20)   # comp 公式算子参数 k（整型，作为一维坐标）


def _cont_spec(sig, test):
    """返回该基因组的连续参数定义：list of (path, lo, hi)。path ∈ {'test:KEY','comp:k'}。"""
    spec = [("test:" + k, lo, hi) for (k, lo, hi, _init) in CONT_TEST_PARAMS.get(test, [])]
    if sig == "comp":
        spec.append(("comp:k", COMP_K_BOUNDS[0], COMP_K_BOUNDS[1]))
    return spec


def _get_param(params, path):
    sec, key = path.split(":", 1)
    if sec == "test":
        return params.get("_test", {}).get(key)
    if sec == "comp":
        return params.get("_comp", {}).get(key)
    return None


def _set_param(params, path, val):
    sec, key = path.split(":", 1)
    if sec == "test":
        params.setdefault("_test", {})[key] = val
    elif sec == "comp":
        params.setdefault("_comp", {})[key] = val


def _init_params(sig, test, rng):
    """为 (sig, test) 造一个带连续参数初值的基因组。"""
    params = {"_sig": {}, "_test": {}, "_reorder": "identity"}
    for (k, _lo, _hi, init) in CONT_TEST_PARAMS.get(test, []):
        params["_test"][k] = init
    if sig == "comp":
        params["_comp"] = {
            "op": "lag", "a": "red_sum", "b": "red_sum",
            "k": int(rng.integers(COMP_K_BOUNDS[0], COMP_K_BOUNDS[1] + 1)),
            "read": "cont",
        }
    return params


def _param_hash(sig, test, params):
    """参数指纹：保证同一基因组→同一评估 rng（目标函数确定性，有限差分才有意义）。"""
    payload = f"{sig}|{test}|" + json_dumps(params)
    return int(hashlib.sha1(payload.encode()).hexdigest()[:12], 16)


def json_dumps(o):
    import json
    return json.dumps(o, sort_keys=True, default=str)


def _objective_on_discovery(sig, test, params, reds_d, blues_d, k_sur):
    """在发现段上评估该基因组，返回 surrogate p_raw（越低越好）。确定性 rng。"""
    h = _param_hash(sig, test, params)
    rng = np.random.default_rng(h)
    ev = E.evaluate(sig, test, reds_d, blues_d, rng, k_sur, params=params)
    if ev is None:
        return 1.0  # 构造失败 => 视为最差
    return float(ev["p_raw"])


def _score(sig, test, params, reds_d, blues_d, k_sur):
    """优化目标：有符号统计极值（z 分数，越低=越极端于检验方向）。

    为何不用 surrogate p_raw：rank p 在 k_sur 有限时存在地板 1/(k_sur+1)，
    任何足以压过全部 surrogate 的结构（含噪声偶然尖峰）都会触地板，导致目标函数
    在真实结构与噪声伪峰之间无梯度、优化器停于局部伪峰（acf_max「lags 上取 max」
    尤甚）。z 分数度量「偏离 null 的幅度」而非「是否排第一」，不触地板，能让优化器
    沿真实结构方向爬升。返回值：high 方向取 -z，low 方向取 +z（统一「越低越好」）。
    """
    h = _param_hash(sig, test, params)
    rng = np.random.default_rng(h)
    ev = E.evaluate(sig, test, reds_d, blues_d, rng, k_sur, params=params)
    if ev is None:
        return 1e9  # 构造失败 => 最差
    z = ev.get("z", 0.0)
    direction = ev.get("direction", "high")
    return (-z) if direction == "high" else z


def optimize_on_discovery(sig, test, params, reds, blues, discovery_frac=0.7,
                          k_sur=60, n_steps=12, step_frac=0.5, n_anchors=12):
    """数值坐标上升：在发现段上最小化有符号极值 _score（= 偏离 null 幅度最大化）。

    返回 (最优params, 最优p_raw, 轨迹[score])。p_raw 仅作报告（确认闸门仍用它裁决）。
    """
    N = len(reds)
    d = max(20, int(N * discovery_frac))
    reds_d, blues_d = reds[:d], blues[:d]
    spec = _cont_spec(sig, test)
    if not spec:
        # 无连续参数可优化：直接评估
        p0 = _objective_on_discovery(sig, test, params, reds_d, blues_d, k_sur)
        return params, p0, [p0]

    cur = _deep_copy_params(params)  # 必须深拷贝：_test/_comp 是嵌套 dict，避免优化器原地改写入参（污染调用方 base）
    best_score = _score(sig, test, cur, reds_d, blues_d, k_sur)
    traj = [best_score]

    # 粗网格预扫描：每个参数在 [lo,hi] 均匀取 n_anchors 点，取最优锚点（防局部最优）
    for (path, lo, hi) in spec:
        anchors = np.linspace(lo, hi, n_anchors)
        best_anchor_score, best_anchor_v = best_score, _get_param(cur, path)
        for av in anchors:
            av = int(round(av)) if ("r_factor" not in path) else float(av)
            _set_param(cur, path, av)
            s = _score(sig, test, cur, reds_d, blues_d, k_sur)
            if s < best_anchor_score:
                best_anchor_score, best_anchor_v = s, av
        _set_param(cur, path, best_anchor_v)
        best_score = best_anchor_score

    # 局部坐标精炼
    for _step in range(n_steps):
        improved = False
        for (path, lo, hi) in spec:
            val = _get_param(cur, path)
            if val is None:
                continue
            span = (hi - lo)
            delta = max(span * step_frac * 0.15, 0.5)
            cand_deltas = [0.0, delta, -delta]
            best_local_score = best_score
            best_local_val = val
            for dd in cand_deltas:
                nv = val + dd
                if "r_factor" not in path:
                    nv = int(round(nv))
                nv = max(lo, min(hi, nv))
                if nv == val:
                    continue
                _set_param(cur, path, nv)
                s = _score(sig, test, cur, reds_d, blues_d, k_sur)
                if s < best_local_score:
                    best_local_score = s
                    best_local_val = nv
                _set_param(cur, path, val)  # 还原
            if best_local_val != val:
                _set_param(cur, path, best_local_val)
                best_score = best_local_score
                improved = True
        traj.append(best_score)
        if not improved:
            break  # 已收敛
    disc_p = _objective_on_discovery(sig, test, cur, reds_d, blues_d, k_sur)
    return cur, disc_p, traj


# ---------------------------------------------------------------------------
# 顶层：跑一批可微候选，各自优化后冻结，交 #41 确认闸门裁决
# ---------------------------------------------------------------------------
# 候选基因组模板池：选有连续参数、且对周期/相关结构敏感的 (sig,test) 组合
CANDIDATE_POOL = [
    ("red_sum", "acf_max"),
    ("red_sum", "mi_max"),
    ("red_sum", "dfa_alpha"),
    ("red_sum", "perm_entropy"),
    ("blue", "acf_max"),
    ("blue", "mi_max"),
    ("comp", "acf_max"),
    ("comp", "mi_max"),
    ("comp", "dfa_alpha"),
]


def run_diff_search(reds, blues, rng, n_candidates=8, discovery_frac=0.7,
                    k_sur_opt=60, n_steps=12,
                    wf_n_folds=3, wf_disc_frac=0.7, wf_k_sur=25,
                    only_signal=True, confirm=True):
    """生成并优化 n_candidates 个可微候选，逐个过 #41 确认闸门。

    返回 list[dict]: {sig, test, params, disc_p, wf_verdict, wf_conf_p, wf_disc_p}
    纪律：优化只在发现段；确认闸门对冻结候选在全量数据上 walk-forward 裁决。

    confirm=False 时跳过模块内确认闸门（仅做发现段优化并返回候选），交由调用方
    （如 run_cycle 的统一 FDR + OOT + 交叉零假设 + #41 闸门）重判，避免双重确认开销。
    """
    results = []
    pool = list(CANDIDATE_POOL)
    for i in range(n_candidates):
        sig, test = pool[i % len(pool)]
        base = _init_params(sig, test, rng)
        opt_params, disc_p, _traj = optimize_on_discovery(
            sig, test, base, reds, blues,
            discovery_frac=discovery_frac, k_sur=k_sur_opt, n_steps=n_steps)
        genome = {"sig": sig, "test": test, "params": opt_params}
        # 冻结候选 → #41 确认闸门（全量数据 walk-forward，确认段优化器未见）
        wf = None
        if confirm:
            wf = EV.confirm_candidate(genome, reds, blues, rng,
                                      n_folds=wf_n_folds,
                                      discovery_frac=wf_disc_frac,
                                      k_sur=wf_k_sur)
        rec = {
            "sig": sig, "test": test, "params": opt_params,
            "disc_p": disc_p,
            "wf_verdict": (wf["verdict"] if wf else None),
            "wf_conf_p": (wf["conf_combined_p"] if wf else None),
            "wf_disc_p": (wf["disc_combined_p"] if wf else None),
        }
        results.append(rec)
        if not only_signal or (wf and wf["verdict"] == "SIGNAL"):
            pass  # 全部记录，调用方按 verdict 过滤
    return results
