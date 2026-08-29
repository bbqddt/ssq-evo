# -*- coding: utf-8 -*-
"""
lenses_external.py — 外部数学框架接入 ssq_evo 的统一透镜模块
==============================================================
三大框架作为"新公式基元生成器 / 新结构透镜"参与公式研发：
  - gplearn        : 符号回归，演化候选公式树（扩 base-signal 基元 alphabet）
  - causal-learn   : PC 因果发现，做因果特征选择（剔除伪相关噪声）
  - ripser         : 持续同调，非时序拓扑结构诊断（检验是否偏离随机）

全部汇入统一闸门（与 run_cycle 一致）：
  1) 严格前序 OOS：训练段(前60%) | 测试段(后40%)，测试段永不进训练
  2) 随机标签置换检验：固定各透镜打分，shuffle 真实命中图案 200 次得 null 分布 -> 经验 p
  3) 随机序列对照：ripser 拓扑在真实序列 vs 20 次洗牌序列上的偏离 z 值
  4) BH-FDR 多候选校正：跨三个透镜候选统一 False Discovery Rate 闸门
  红线：本模块只"评估/起草"候选，绝不把任何候选自动合并进引擎演进。
"""
import json, os, sys, warnings, random
import numpy as np
warnings.filterwarnings("ignore")
np.random.seed(42); random.seed(42)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "ssq_history.csv")
OUT  = "D:/ssq_evo_data/lenses_external_report.json"

# ---------- 数据加载 ----------
def load():
    import csv
    reds, blues, issues = [], [], []
    with open(DATA) as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                rs = [int(row[f"r{i}"]) for i in range(1,7)]
                bs = int(row["b"]); iss = int(row["issue"])
            except (KeyError, ValueError):
                continue
            reds.append(rs); blues.append(bs); issues.append(iss)
    return np.array(reds), np.array(blues), np.array(issues)

# ---------- 特征工程：每个 (t, ball) 一个样本 ----------
# 严格只用第 t 期【之前】的历史（禁止泄漏当期目标）
def build_xy(reds):
    N, B = reds.shape[0], 33
    sum_norm = reds.sum(axis=1) / (33*3.0)   # draw-level，合法（非当期球标签）
    gap = np.zeros((N, B)); freq = np.zeros((N, B)); rec = np.zeros((N, B))
    W = 50
    last_seen = -np.ones(B, dtype=int)
    window = []   # 最近 W 期出现过的球集合（不含当期）
    for t in range(N):
        win_set = set().union(*window) if window else set()
        win_len = max(1, len(window))
        for i in range(B):
            gap[t, i] = (t - last_seen[i]) if last_seen[i] >= 0 else W
            rec[t, i] = 1.0 if last_seen[i] == t-1 else 0.0   # 上一期是否出现（lag-1）
            freq[t, i] = (1.0*sum(1 for s in window if (i+1) in s)) / win_len
        # 用当期更新历史（供 t+1 使用）
        for i in reds[t]:
            last_seen[i-1] = t
        window.append(set(int(x) for x in reds[t]))
        if len(window) > W:
            window.pop(0)
    gap = np.clip(gap, 0, W)/W
    y = np.zeros((N, B))
    for t in range(N):
        for i in range(B):
            y[t, i] = 1.0 if (i+1) in reds[t] else 0.0
    X = np.column_stack([gap.ravel(), freq.ravel(), rec.ravel(),
                        np.repeat(sum_norm, B), np.repeat(np.arange(N)/N, B)])
    Y = y.ravel()
    draw_idx = np.repeat(np.arange(N), B)
    return X, Y, draw_idx, N, B

# ---------- OOS 评估：固定打分 -> top6 -> 命中率 + 置换 p ----------
def oos_eval(scores, Y, draw_idx, N, B, n_perm=200):
    # 测试段 = 后 40% 期号
    test_draws = np.arange(int(N*0.6), N)
    tmask = np.isin(draw_idx, test_draws)
    s_test = scores[tmask]; y_test = Y[tmask]; di_test = draw_idx[tmask]
    # 逐 draw 选 top6
    hit_obs = 0; ntest = 0
    order = np.argsort(di_test)
    di_sorted = di_test[order]; s_sorted = s_test[order]; y_sorted = y_test[order]
    uniq = np.unique(di_sorted)
    for d in uniq:
        m = di_sorted == d
        sc = s_sorted[m]; yy = y_sorted[m]
        top = np.argsort(-sc)[:6]
        hit_obs += yy[top].sum(); ntest += 1
    obs = hit_obs / max(1,ntest)
    # 随机基线期望
    exp = 6.0 * (6.0/33.0)
    # 置换 null：固定打分，shuffle y_test 图案
    nulls = []
    yy2 = y_sorted.copy()
    for _ in range(n_perm):
        np.random.shuffle(yy2)
        h = 0
        for d in uniq:
            m = di_sorted == d
            sc = s_sorted[m]; yv = yy2[m]
            top = np.argsort(-sc)[:6]
            h += yv[top].sum()
        nulls.append(h/ntest)
    nulls = np.array(nulls)
    p = (1 + (nulls >= obs).sum()) / (1 + n_perm)
    return dict(oos_hit=round(float(obs),4), expected_random=round(float(exp),4),
                null_mean=round(float(nulls.mean()),4), null_std=round(float(nulls.std()),4),
                perm_p=round(float(p),4), n_test_draws=int(ntest))

# ================= 透镜 A：gplearn 符号回归 =================
def lens_gp(X, Y, draw_idx, N, B):
    from gplearn.genetic import SymbolicRegressor
    tr = draw_idx < int(N*0.6)
    # gplearn 在 66k 样本上较快；限制规模
    sr = SymbolicRegressor(population_size=150, generations=15,
                           stopping_criteria=0.0001, parsimony_coefficient=0.001,
                           random_state=42, verbose=0,
                           feature_names=["gap","freq","rec","sum","t"])
    sr.fit(X[tr], Y[tr])
    scores = sr.predict(X)
    res = oos_eval(scores, Y, draw_idx, N, B)
    best = sr._program if hasattr(sr, "_program") else None
    formula = str(best) if best is not None else "n/a"
    res.update(method="gplearn_symbolic", formula=formula[:300])
    return res

# ================= 透镜 B：causal-learn PC 因果特征选择 =================
def lens_causal(X, Y, draw_idx, N, B):
    from causallearn.search.ConstraintBased.PC import pc
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    cols = ["gap","freq","rec_lag1","sum_draw","t"]
    # PC 只用球级特征(前3列)避免 draw-level 列在 33 球内恒定导致的退化；y 作第4列
    ball = X[:, :3]
    data = np.ascontiguousarray(np.column_stack([ball, Y]), dtype=float)
    causal_feats = [0,1,2]
    try:
        cg = pc(data, alpha=0.05, show_progress=False)
        adj = cg.G
        causal_feats = [j for j in range(3) if adj[3, j] != 0 or adj[j, 3] != 0]
        if not causal_feats:
            causal_feats = [0,1,2]
    except Exception as e:
        causal_feats = [0,1,2]
    # logistic：因果特征 + 合法 draw-level 特征(sum_draw, t)
    sel = causal_feats + [3,4]
    tr = draw_idx < int(N*0.6)
    scaler = StandardScaler().fit(X[tr])
    Xs = scaler.transform(X)
    clf = LogisticRegression(max_iter=1000, C=1.0, class_weight="balanced")
    clf.fit(Xs[tr][:, sel], Y[tr])
    scores = np.zeros(X.shape[0])
    scores[tr] = clf.predict_proba(Xs[tr][:, sel])[:,1]
    scores[~tr] = clf.predict_proba(Xs[~tr][:, sel])[:,1]
    res = oos_eval(scores, Y, draw_idx, N, B)
    res.update(method="causallearn_pc", causal_features=[cols[j] for j in causal_feats])
    return res

# ================= 透镜 C：ripser 拓扑结构诊断 =================
def lens_tda(reds, blues):
    from ripser import ripser as rd
    N = reds.shape[0]
    pts = np.zeros((N, 7))
    pts[:, :6] = reds / 33.0
    pts[:, 6] = blues / 16.0
    # 子采样以控速
    if N > 200:
        idx = np.linspace(0, N-1, 200).astype(int)
        pts = pts[idx]
    dgm = rd(pts, maxdim=1, n_perm=200)["dgms"]
    def summ(d):
        d = d[~np.isinf(d[:,1])]; 
        if d.shape[0]==0: return 0.0, 0
        life = d[:,1]-d[:,0]
        return float(life.sum()), int((life>0.05).sum())
    s0, c0 = summ(dgm[0]); s1, c1 = summ(dgm[1])
    obs = dict(method="ripser_topology", H0_persist=round(s0,4), H0_bars=c0,
               H1_persist=round(s1,4), H1_bars=c1)
    # 随机序列对照：洗牌 20 次
    null_s1 = []
    for _ in range(20):
        rp = reds.copy()
        for c in range(6):
            np.random.shuffle(rp[:, c])
        p2 = np.zeros((N,7)); p2[:,:6]=rp/33.0; p2[:,6]=blues/16.0
        if N>200: p2=p2[idx]
        d2 = rd(p2, maxdim=1, n_perm=200)["dgms"]
        _, _c = summ(d2[1]); null_s1.append(_c)
    null_s1 = np.array(null_s1)
    z = (c1 - null_s1.mean())/ (null_s1.std()+1e-9)
    if z > 1.96:
        verdict = "TOPO_MORE_LOOPS_THAN_RANDOM"   # 真实序列拓扑环多于随机，值得进一步盯
    elif z < -1.96:
        verdict = "TOPO_NO_STRUCTURE_FEWER_LOOPS"  # 真实比随机更"均匀"，无可用结构
    else:
        verdict = "NULL_TOPOLOGY"
    obs.update(null_H1_bars_mean=round(float(null_s1.mean()),4),
               topo_deviation_z=round(float(z),3),
               verdict=verdict,
               note="z<0 表示真实序列 1D 拓扑环少于随机洗牌序列，即真实数据至少与随机一样均匀——无可利用结构")
    return obs

# ================= 统一闸门：BH-FDR =================
def bh_fdr(results, alpha=0.05):
    ps = [r["perm_p"] for r in results if "perm_p" in r]
    if not ps: return
    order = np.argsort(ps); m=len(ps)
    sig=[]
    for k,i in enumerate(order,1):
        if ps[i] <= (k/m)*alpha: sig.append(i)
    for j,r in enumerate(results):
        if "perm_p" in r:
            r["fdr_significant"] = (j in sig) if sig else False
            r["verdict"] = "SURVIVOR" if (j in sig) else "ARTIFACT_BY_CONSTRUCTION_OR_NULL"

# ================= 主流程 =================
def main():
    reds, blues, issues = load()
    print(f"[load] draws={reds.shape[0]} issues {issues[0]}..{issues[-1]}")
    X, Y, draw_idx, N, B = build_xy(reds)
    print(f"[feat] X={X.shape} B={B}")
    results = []
    print("[lens A] gplearn symbolic regression ...")
    results.append(lens_gp(X, Y, draw_idx, N, B))
    print("   ", results[-1])
    print("[lens B] causal-learn PC feature selection ...")
    results.append(lens_causal(X, Y, draw_idx, N, B))
    print("   ", results[-1])
    print("[lens C] ripser topology diagnostic ...")
    results.append(lens_tda(reds, blues))
    print("   ", results[-1])
    # 统一 FDR 闸门
    bh_fdr(results)
    report = dict(
        note="外部数学框架统一透镜评估报告（严格前序OOS + 置换p + 随机对照 + BH-FDR）",
        expected_random_red_hits_per_draw=round(6.0*(6.0/33.0),4),
        red_line="本模块只评估/起草候选，绝不自动合并进引擎演进",
        results=results,
    )
    with open(OUT, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[done] report -> {OUT}")
    # 断言诚实底线
    surv = [r for r in results if r.get("verdict")=="SURVIVOR"]
    print(f"[gate] SURVIVOR={len(surv)} / {len([r for r in results if 'perm_p' in r])} 预测类候选通过FDR")
    for r in results:
        if "perm_p" in r:
            print(f"   {r['method']:22s} oos={r['oos_hit']:.4f} nullμ={r['null_mean']:.4f} p={r['perm_p']:.4f} -> {r['verdict']}")

if __name__ == "__main__":
    main()
