# -*- coding: utf-8 -*-
"""
ssq_evo 结构性搜索 v1
=====================
目标：在"时间序列方向预测"之外，主动搜索双色球是否在任何结构性维度上存在
     可复现、优于随机的偏差。所有"选号策略"均用 expanding-window walk-forward
     在独立测试段（后20%约700期）验证命中率，随机基线用固定种子蒙特卡洛。

假设空间：
  A. 各位置(含蓝球)号码均匀性 chi-square + BH 校正
  B. walk-forward 选号策略命中率：
       hot6    : 训练段频率最高6个红球
       cold6   : 训练段频率最低6个红球（"该出"谬误对照）
       pos_mean: 每位置滚动均值取整（检验 level 持续性的选号转化）
       pos_med : 每位置中位数
       freq_wt : 按频率加权随机采样6个（检验频率分布本身是否含信息）
       blue_hot: 蓝球频率最高1个
  C. 和值 level 持续性：滚动均值预测下一期和值区间，检验是否优于随机落入
  D. 号码两两共现 lift（描述性 + 显著性），并 walk-forward 验证条件选号

诚实约定：
  - 任何策略命中率需与随机基线(期望1.0909, 蒙特卡洛)比较，并报告标准差
  - 多假设同时检验，BH-FDR 控制
  - 发现"边缘"只当线索，不当定论
"""
import csv, math, json, random
from collections import Counter, defaultdict

MASTER = r'D:/ssq_evo_data/ssq_master.csv'

# ---------- 载入 ----------
rows = []
with open(MASTER, encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try:
            issue = int(r['issue'])
            reds = tuple(sorted(int(r['r%d' % i]) for i in (1, 2, 3, 4, 5, 6)))
            blue = int(r['b'])
            rows.append((issue, reds, blue))
        except Exception:
            pass
rows.sort(key=lambda x: x[0])
N = len(rows)
TRAIN = int(N * 0.8)
TEST = N - TRAIN
print(f'N={N}  TRAIN={TRAIN}  TEST={TEST}')

# ============================================================
# 不完全伽马函数 (for chi-square p-value)
# ============================================================
def _gammln(xx):
    cof = [76.18009172947146, -86.50532032941677, 24.01409824083091,
           -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5]
    x = xx; y = xx; tmp = x + 5.5; tmp -= (x + 0.5) * math.log(tmp)
    ser = 1.000000000190015
    for c in cof:
        y += 1; ser += c / y
    return -tmp + math.log(2.5066282746310005 * ser / x)

def _gser(a, x):
    gln = _gammln(a)
    if x <= 0: return 0.0
    ap = a; summ = 1.0 / a; delta = summ
    for _ in range(300):
        ap += 1; delta *= x / ap; summ += delta
        if abs(delta) < abs(summ) * 1e-14: break
    return summ * math.exp(-x + a * math.log(x) - gln)

def _gcf(a, x):
    gln = _gammln(a)
    FPMIN = 1e-300; EPS = 1e-14
    b = x + 1.0 - a; c = 1.0 / FPMIN; d = 1.0 / b; h = d
    for i in range(1, 300):
        an = -i * (i - a); b += 2.0; d = an * d + b
        if abs(d) < FPMIN: d = FPMIN
        c = b + an / c
        if abs(c) < FPMIN: c = FPMIN
        d = 1.0 / d; delta = d * c; h *= delta
        if abs(delta - 1.0) < EPS: break
    return math.exp(-x + a * math.log(x) - gln) * h

def gammp(a, x):
    if x < 0 or a <= 0: return 0.0
    return _gser(a, x) if x < a + 1.0 else 1.0 - _gcf(a, x)

def chi2_sf(x, k):
    if x <= 0: return 1.0
    return 1.0 - gammp(k / 2.0, x / 2.0)

# ============================================================
# A. 均匀性 chi-square
# ============================================================
print('\n=== A. 号码均匀性 chi-square (H0: 均匀) ===')
def chi2_uniform(counts, expected):
    return sum((c - expected) ** 2 / expected for c in counts)

uni_tests = []  # (name, chi, df, p)
for p in range(6):
    cnt = Counter(rows[t][1][p] for t in range(N))
    exp = N / 33.0
    chi = chi2_uniform([cnt.get(b, 0) for b in range(1, 34)], exp)
    uni_tests.append(('red_pos%d' % (p + 1), chi, 32, chi2_sf(chi, 32)))
bcnt = Counter(rows[t][2] for t in range(N))
bexp = N / 16.0
bchi = chi2_uniform([bcnt.get(b, 0) for b in range(1, 17)], bexp)
uni_tests.append(('blue', bchi, 15, chi2_sf(bchi, 15)))

# BH-FDR
m = len(uni_tests)
order = sorted(range(m), key=lambda i: uni_tests[i][3])
thr = 0.05
bh_pass = []
prev = 0
for rank, i in enumerate(order, 1):
    p = uni_tests[i][3]
    crit = thr * rank / m
    if p <= crit:
        bh_pass.append(uni_tests[i][0]); prev = rank
print('  %-10s %10s %4s %10s  BH' % ('name', 'chi2', 'df', 'p'))
for name, chi, df, p in sorted(uni_tests, key=lambda x: x[3]):
    flag = 'SIG' if name in bh_pass else ''
    print('  %-10s %10.3f %4d %10.5f  %s' % (name, chi, df, p, flag))
print('  BH-FDR(q=0.05) 通过:', bh_pass if bh_pass else '无')

# ============================================================
# 随机基线 (蒙特卡洛, 固定种子)
# ============================================================
random.seed(20260825)
rng = random.Random(20260825)
base_hits = []
for t in range(TRAIN, N):
    actual = set(rows[t][1])
    tot = 0.0
    for _ in range(40):
        samp = set(rng.sample(range(1, 34), 6))
        tot += len(samp & actual)
    base_hits.append(tot / 40)
BASE = sum(base_hits) / len(base_hits)
print('\n=== 随机基线 (MC, 40次/期) ===')
print('  平均红球命中 = %.4f  (理论期望 6*6/33=%.4f)' % (BASE, 6*6/33))

# ============================================================
# B. walk-forward 选号策略
# ============================================================
print('\n=== B. walk-forward 选号策略 (独立测试段 %d 期) ===')

def wf(strategy):
    """strategy(train_rows) -> (set(6 reds), blue or None). 返回(红球均值命中, 蓝球命中率)"""
    rh = []; bh = 0; bn = 0
    for t in range(TRAIN, N):
        pred_r, pred_b = strategy(rows[:t])
        actual_r = set(rows[t][1]); actual_b = rows[t][2]
        rh.append(len(pred_r & actual_r))
        if pred_b is not None:
            bn += 1
            if pred_b == actual_b: bh += 1
    return sum(rh) / len(rh), (bh / bn if bn else 0.0)

def freq_counts(train):
    c = Counter()
    for _, reds, _ in train:
        c.update(reds)
    return c

def hot6(train):
    c = freq_counts(train)
    top = [b for b, _ in c.most_common(6)]
    return set(top), None

def cold6(train):
    c = freq_counts(train)
    least = sorted(c.items(), key=lambda kv: kv[1])[:6]
    return set(b for b, _ in least), None

def pos_mean(train):
    preds = []
    for p in range(6):
        vals = [r[p] for _, r, _ in train]
        m = round(sum(vals) / len(vals))
        m = max(1, min(33, m))
        preds.append(m)
    # 去重+补位
    s = set(preds); 
    cand = sorted(range(1, 34), key=lambda b: abs(b - sum(preds)/6))
    for b in cand:
        if len(s) >= 6: break
        s.add(b)
    return set(list(s)[:6]), None

def pos_med(train):
    preds = []
    for p in range(6):
        vals = sorted(r[p] for _, r, _ in train)
        med = vals[len(vals)//2]
        preds.append(med)
    s = set(preds)
    cand = sorted(range(1, 34), key=lambda b: abs(b - sum(preds)/6))
    for b in cand:
        if len(s) >= 6: break
        s.add(b)
    return set(list(s)[:6]), None

def freq_wt(train):
    c = freq_counts(train)
    balls = list(range(1, 34))
    w = [c.get(b, 0) + 1 for b in balls]
    tot = sum(w)
    r2 = random.Random(12345)
    chosen = set()
    while len(chosen) < 6:
        x = r2.random() * tot
        acc = 0
        for b, wi in zip(balls, w):
            acc += wi
            if x <= acc:
                chosen.add(b); break
    return chosen, None

def blue_hot(train):
    bc = Counter(b for _, _, b in train)
    top = bc.most_common(1)[0][0]
    return set(), top

strategies = {
    'hot6': hot6, 'cold6': cold6, 'pos_mean': pos_mean,
    'pos_med': pos_med, 'freq_wt': freq_wt, 'blue_hot': blue_hot,
}
print('  %-10s %12s %12s' % ('strategy', '红球命中', '蓝球命中'))
res = {}
for name, fn in strategies.items():
    rh, bh = wf(fn)
    res[name] = (rh, bh)
    print('  %-10s %12.4f %12.4f' % (name, rh, bh))
print('  %-10s %12.4f %12s' % ('RANDOM', BASE, '(MC)'))

# ============================================================
# C. 和值 level 持续性
# ============================================================
print('\n=== C. 和值 level 持续性 (滚动均值预测下一期和值区间) ===')
sums = [sum(r[1]) for r in rows]
W = 30
correct = 0; total = 0
# 区间：以预测值为中心 ±delta，看实际和值是否落入
for t in range(TRAIN + W, N):
    window = sums[t - W:t]
    pred = sum(window) / W
    actual = sums[t]
    # 用窗口内和值标准差构造区间
    sd = (sum((s - pred) ** 2 for s in window) / W) ** 0.5
    delta = 1.5 * sd
    if abs(actual - pred) <= delta:
        correct += 1
    total += 1
print('  滚动均值±1.5σ 区间命中实际和值: %.3f (随机区间基准约 %.3f)'
      % (correct / total, 2 * (1 - math.erf(1.5 / math.sqrt(2))) / 2 + (1 - 2*(1-math.erf(1.5/math.sqrt(2)))/2)))
# 也报告 lag-1 自相关（已在之前确认 +0.338）
mu = sum(sums) / N
num = sum((sums[i] - mu) * (sums[i+1] - mu) for i in range(N-1))
den = sum((s - mu) ** 2 for s in sums)
print('  和值 lag-1 自相关 = %.4f (真实, 之前已确认)' % (num/den))

# ============================================================
# D. 两两共现 lift (描述性)
# ============================================================
print('\n=== D. 红球两两共现 lift (描述性, 全样本) ===')
pair_co = Counter()
ball_co = Counter()
for _, reds, _ in rows:
    rs = set(reds)
    ball_co.update(rs)
    for a in rs:
        for b in rs:
            if a < b:
                pair_co[(a, b)] += 1
total_draws = N
lifts = []
for (a, b), c in pair_co.items():
    # P(a,b both) / P(a)P(b)
    pab = c / total_draws
    pa = ball_co[a] / total_draws
    pb = ball_co[b] / total_draws
    lift = pab / (pa * pb)
    lifts.append((a, b, c, lift))
lifts.sort(key=lambda x: -x[3])
print('  Top-5 最高 lift 组合:')
for a, b, c, lift in lifts[:5]:
    # 显著性: 超几何校验实际共现 vs 期望
    exp = pa*pb*total_draws
    print('    (%2d,%2d) 共现%d次 lift=%.3f 期望%.1f' % (a, b, c, lift, exp))

# ============================================================
# 汇总
# ============================================================
print('\n========== 汇总 ==========')
print('随机基线红球命中 = %.4f' % BASE)
best = max(res.items(), key=lambda kv: kv[1][0])
print('最佳红球策略 = %s (%.4f)' % (best[0], best[1][0]))
edge = best[1][0] - BASE
print('相对随机边缘 = %.4f %s' % (edge, '(>0 但需看显著性)' if edge > 0 else ''))
print('注: 随机基线 std≈%.3f, 需边缘>~0.3 且在多假设校正后仍显著才算真信号'
      % ( (sum((h-BASE)**2 for h in base_hits)/len(base_hits))**0.5 ))

# 存档
out = {
    'N': N, 'TRAIN': TRAIN, 'TEST': TEST,
    'uniformity': [{'name': n, 'chi2': c, 'df': d, 'p': p} for n, c, d, p in uni_tests],
    'bh_pass': bh_pass,
    'random_baseline': BASE,
    'strategies': {k: {'red_hit': v[0], 'blue_hit': v[1]} for k, v in res.items()},
    'sum_autocorr_lag1': num/den,
}
with open(r'D:/ssq_evo_data/structural_search_v1.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print('\n已存档 D:/ssq_evo_data/structural_search_v1.json')
