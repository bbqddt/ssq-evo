# -*- coding: utf-8 -*-
"""
ssq_evo 结构性搜索 v3 —— 灵活 ML walk-forward
=============================================
用每球独立 logistic 回归(手动GD+L2)，特征含多窗口滚动频率/间隔/上期结构和值/
与上期球的共现lift/奇偶/球号，严格 expanding 训练，独立测试段选 top6 评命中率。
若灵活模型仍≈随机 -> 当前数据下无可榨结构的最强统计陈述。
"""
import csv, math
from collections import Counter

MASTER = r'D:/ssq_evo_data/ssq_master.csv'
rows = []
with open(MASTER, encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try:
            rows.append((int(r['issue']), tuple(int(r['r%d'%i]) for i in (1,2,3,4,5,6)), int(r['b'])))
        except Exception:
            pass
rows.sort(key=lambda x: x[0])
N = len(rows)
TRAIN = int(N * 0.8)
sums = [sum(r[1]) for r in rows]

# 前缀计数: ps[b][t] = ball b 在 draws[0..t] 出现次数 (b:1..33, t:0..N-1)
ps = [[0]*N for _ in range(34)]
last_seen_prefix = [[0]*N for _ in range(34)]  # last_seen_prefix[b][t] = 最近出现索引(含t)
for t in range(N):
    for b in range(1,34):
        ps[b][t] = ps[b][t-1] if t>0 else 0
        last_seen_prefix[b][t] = last_seen_prefix[b][t-1] if t>0 else 0
    for b in rows[t][1]:
        ps[b][t] += 1
        last_seen_prefix[b][t] = t

# 全训练段 lift(b,j) 用作共现特征
train_co = Counter()
train_b = Counter()
for t in range(TRAIN):
    rs = set(rows[t][1]); train_b.update(rs)
    for a in rs:
        for b in rs:
            if a<b: train_co[(a,b)]+=1
T0 = TRAIN
pb = {b: train_b.get(b,0)/T0 for b in range(1,34)}
def lift_full(a,b):
    k=(min(a,b),max(a,b)); c=train_co.get(k,0)
    pa,pbb=pb[a],pb[b]
    return (c/T0)/(pa*pbb) if pa>0 and pbb>0 else 1.0

def feat(t, b):
    """预测 draw t 时, 球 b 的特征 (只用 draws<t)."""
    hi = t-1
    rf50 = (ps[b][hi] - (ps[b][hi-50] if hi>=50 else 0)) / min(50, hi+1)
    rf200 = (ps[b][hi] - (ps[b][hi-200] if hi>=200 else 0)) / min(200, hi+1)
    ls = last_seen_prefix[b][hi]
    gap = (hi - ls) if ls>0 else 100
    last_sum = sums[hi] if hi>=0 else 102
    last_draw = rows[hi][1]
    co = sum(lift_full(b, j) for j in last_draw) / 6.0
    return [rf50, rf200, gap/100.0, last_sum/183.0, co, b%2, b/33.0]

# 训练集: t in [201, TRAIN)
def build_train():
    X=[]; Y=[]
    for t in range(201, TRAIN):
        for b in range(1,34):
            X.append(feat(t,b))
            Y.append(1.0 if b in rows[t][1] else 0.0)
    return X,Y
print('构建训练特征...')
Xtr, Ytr = build_train()
print('  样本=%d 正例=%d' % (len(Xtr), int(sum(Ytr))))

# logistic 回归 (GD + L2)
nfeat=7
w=[0.0]*nfeat; bias=0.0; lr=0.1; reg=0.01
def sigmoid(z): return 1/(1+math.exp(-z))
for epoch in range(40):
    gw=[0.0]*nfeat; gb=0.0
    for xi,yi in zip(Xtr,Ytr):
        z=bias+sum(w[k]*xi[k] for k in range(nfeat))
        p=sigmoid(z); e=p-yi
        for k in range(nfeat): gw[k]+=e*xi[k]
        gb+=e
    for k in range(nfeat): w[k]-=lr*(gw[k]/len(Xtr)+reg*w[k])
    bias-=lr*(gb/len(Xtr))
print('  模型训练完成')

# 测试段: 选 top6 by prob
hits=[]
for t in range(TRAIN, N):
    probs=[]
    for b in range(1,34):
        z=bias+sum(w[k]*feat(t,b)[k] for k in range(nfeat))
        probs.append((b, sigmoid(z)))
    probs.sort(key=lambda x:-x[1])
    top6=set(b for b,_ in probs[:6])
    hits.append(len(top6 & set(rows[t][1])))
hr=sum(hits)/len(hits)

# 随机基线 (固定种子)
import random
random.seed(99); rnd=random.Random(99)
base=[]
for t in range(TRAIN,N):
    a=set(rnd.sample(range(1,34),6)); base.append(len(a & set(rows[t][1])))
BASE=sum(base)/len(base)

print('\n=== v3 ML walk-forward 结果 ===')
print('  ML top6 红球命中 = %.4f' % hr)
print('  随机基线         = %.4f' % BASE)
print('  边缘             = %.4f %s' % (hr-BASE, '(需>0.3且显著)' if hr-BASE>0 else ''))
print('  结论: %s' % ('灵活ML仍≈随机 -> 当前数据无可榨结构' if abs(hr-BASE)<0.2 else '发现边缘, 需进一步确认'))

import json
out={'ml_red_hit':hr,'random_baseline':BASE,'edge':hr-BASE}
with open(r'D:/ssq_evo_data/structural_search_v3.json','w',encoding='utf-8') as f:
    json.dump(out,f,indent=2)
print('已存档 D:/ssq_evo_data/structural_search_v3.json')
