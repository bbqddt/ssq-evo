# -*- coding: utf-8 -*-
"""
ssq_evo 序列模型 v1 —— 埋头训练
=================================
把最近 K=15 期当作上下文(每期=33红出现+16蓝出现=49维)，训练 MLP 直接预测
下一期的 33 维红球出现概率 + 16 维蓝球。严格 walk-forward：
  训练 t in [K, TRAIN)，测试 t in [TRAIN, N)
评估：红球取预测概率 top6 与实际集合重合数；蓝球取 argmax。
不谈哲学，先跑出数字。
"""
import csv, math, json
import numpy as np

MASTER = r'D:/ssq_evo_data/ssq_master.csv'
draws = []
with open(MASTER, encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try:
            issue = int(r['issue'])
            reds = tuple(sorted(int(r['r%d' % i]) for i in (1,2,3,4,5,6)))
            blue = int(r['b'])
            draws.append((issue, reds, blue))
        except Exception:
            pass
draws.sort(key=lambda x: x[0])
N = len(draws)
TRAIN = int(N * 0.8)
K = 15

# 每期向量: 33红出现(1/0) + 16蓝出现(1/0) = 49
def vec(reds, blue):
    v = np.zeros(49, dtype=np.float32)
    for b in reds: v[b-1] = 1.0
    v[33 + blue - 1] = 1.0
    return v

Xall = np.stack([vec(r[1], r[2]) for r in draws])  # (N,49)

# 构建样本
def make_samples(split_end):
    Xs, Yr, Yb, actual = [], [], [], []
    for t in range(K, split_end):
        Xs.append(Xall[t-K:t].reshape(-1))           # (K*49,)
        Yr.append(Xall[t, :33].copy())               # 红球目标(1/0)
        yb = np.zeros(16, dtype=np.float32); yb[draws[t][2]-1] = 1.0
        Yb.append(yb)
        actual.append((set(draws[t][1]), draws[t][2]))
    return np.array(Xs), np.array(Yr), np.array(Yb), actual

Xtr, Yr_tr, Yb_tr, _ = make_samples(TRAIN)
print('训练样本=%d  输入维=%d' % (Xtr.shape[0], Xtr.shape[1]))

# ---- MLP + Adam ----
def sigmoid(x): return 1.0/(1.0+np.exp(-np.clip(x,-30,30)))
def bce_logits(z,y): return np.mean(np.maximum(z,0)-z*y+np.log(1+np.exp(-np.abs(z))))

class MLP:
    def __init__(self, dims, lr=0.01):
        self.W=[]; self.b=[]
        for i in range(len(dims)-1):
            s=math.sqrt(2.0/dims[i])
            self.W.append(np.random.randn(dims[i],dims[i+1]).astype(np.float32)*s)
            self.b.append(np.zeros(dims[i+1],dtype=np.float32))
        self.mW=[np.zeros_like(w) for w in self.W]; self.vW=[np.zeros_like(w) for w in self.W]
        self.mb=[np.zeros_like(x) for x in self.b]; self.vb=[np.zeros_like(x) for x in self.b]
        self.lr=lr; self.t=0
    def forward(self,X):
        self.cache=[X]; a=X
        for i in range(len(self.W)-1):
            z=a@self.W[i]+self.b[i]; a=np.maximum(0,z); self.cache.append((z,a))
        z=a@self.W[-1]+self.b[-1]; self.cache.append(z); return z
    def backward(self,Y):
        z=self.cache[-1]; dz=sigmoid(z)-Y
        gW=[None]*len(self.W); gb=[None]*len(self.b)
        gb[-1]=dz.sum(0); gW[-1]=self.cache[-2][1].T@dz; da=dz@self.W[-1].T
        for i in range(len(self.W)-2,-1,-1):
            z,a=self.cache[i+1]; dr=(z>0).astype(np.float32)*da
            a_prev = self.cache[i] if i==0 else self.cache[i][1]
            gb[i]=dr.sum(0); gW[i]=a_prev.T@dr; da=dr@self.W[i].T
        return gW,gb
    def step(self,gW,gb):
        self.t+=1; b1,b2,eps=0.9,0.999,1e-8
        for i in range(len(self.W)):
            self.mW[i]=b1*self.mW[i]+(1-b1)*gW[i]; self.mb[i]=b1*self.mb[i]+(1-b1)*gb[i]
            self.vW[i]=b2*self.vW[i]+(1-b2)*(gW[i]**2); self.vb[i]=b2*self.vb[i]+(1-b2)*(gb[i]**2)
            mh=self.mW[i]/(1-b1**self.t); vh=self.vW[i]/(1-b2**self.t)
            self.W[i]-=self.lr*mh/(np.sqrt(vh)+eps); self.b[i]-=self.lr*self.mb[i]/(np.sqrt(self.vb[i])+eps)

dims=[K*49, 128, 64, 49]
model=MLP(dims, lr=0.02)
B=256
idx=np.arange(Xtr.shape[0])
best=1e9
for epoch in range(50):
    np.random.shuffle(idx)
    tot=0; nb=0
    for s in range(0,len(idx),B):
        bidx=idx[s:s+B]
        Xb=Xtr[bidx]; Yb_=np.concatenate([Yr_tr[bidx],Yb_tr[bidx]],axis=1)
        z=model.forward(Xb); loss=bce_logits(z,Yb_); tot+=loss*len(bidx); nb+=len(bidx)
        gW,gb=model.backward(Yb_); model.step(gW,gb)
    if (epoch+1)%10==0: print('  epoch %2d loss=%.4f'%(epoch+1, tot/nb))

# ---- 测试 ----
Xte,Yr_te,Yb_te,actual_te=make_samples(N)
# 仅测试段
ts=np.arange(TRAIN-K, N-K)  # make_samples 用 [K,split_end)，需重切
# 重做只取测试段
Xts=[]; acts=[]
for t in range(TRAIN, N):
    Xts.append(Xall[t-K:t].reshape(-1))
    acts.append((set(draws[t][1]), draws[t][2]))
Xts=np.array(Xts)
z=model.forward(Xts)
red_logits=z[:,:33]; blue_logits=z[:,33:]
hits=[]
for i in range(len(acts)):
    top6=set(np.argsort(-red_logits[i])[:6]+1)
    hits.append(len(top6 & acts[i][0]))
hr=sum(hits)/len(hits)
blue_hit=sum(1 for i in range(len(acts)) if (np.argmax(blue_logits[i])+1)==acts[i][1])

# 随机基线
import random
random.seed(2026); rnd=random.Random(2026)
base=[]
for t in range(TRAIN,N):
    a=set(rnd.sample(range(1,34),6)); base.append(len(a & set(draws[t][1])))
BASE=sum(base)/len(base)

print('\n=== 序列 MLP v1 结果 ===')
print('  MLP 红球命中 = %.4f' % hr)
print('  随机基线     = %.4f' % BASE)
print('  边缘         = %.4f' % (hr-BASE))
print('  蓝球命中     = %d/%d (随机期望 %.1f)' % (blue_hit, len(acts), len(acts)/16))

out={'model':'MLP seq K=15','ml_red_hit':float(hr),'random_baseline':float(BASE),
     'edge':float(hr-BASE),'blue_hit':int(blue_hit),'blue_total':len(acts)}
with open(r'D:/ssq_evo_data/seq_model_v1.json','w',encoding='utf-8') as f:
    json.dump(out,f,indent=2)
print('已存档 D:/ssq_evo_data/seq_model_v1.json')
print('结论: %s' % ('仍≈随机 -> 继续迭代下一版' if abs(hr-BASE)<0.2 else '发现边缘, 需确认'))
