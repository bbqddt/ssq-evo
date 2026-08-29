# -*- coding: utf-8 -*-
"""手写 GRU 序列模型（循环记忆归纳偏置），49维输出(33红+16蓝 one-hot)，
walk-forward 独立测试段评真实命中率。对照: logistic/MLP/MLP-rich/CNN 全 null。
GRU 权重用末步截断梯度近似更新(仅供结构探针, 不影响命中率评估的正确性)。
"""
import csv, math, json, numpy as np

rng = np.random.default_rng(20260825)
np.seterr(all='ignore')
def sigmoid(a): return 1.0/(1.0+np.exp(-a))

# ---------- 数据 ----------
rows=[]
with open(r'D:/ssq_evo_data/ssq_master.csv', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try:
            reds=tuple(int(r['r%d'%i]) for i in (1,2,3,4,5,6)); blue=int(r['b'])
            rows.append((int(r['issue']), reds, blue))
        except: pass
rows.sort(key=lambda x:x[0]); N=len(rows)
print('N=%d'%N)

X=np.zeros((N,7),np.float32)
for i,(iss,reds,blue) in enumerate(rows):
    for j in range(6): X[i,j]=reds[j]/33.0
    X[i,6]=blue/16.0

def yvec(reds,blue):
    v=np.zeros(49,np.float32)
    for r in reds: v[r-1]=1.0
    v[33+blue-1]=1.0
    return v
Y=np.array([yvec(r[1],r[2]) for r in rows],np.float32)

# ---------- GRU ----------
class GRU:
    def __init__(self, Xin, H, Out):
        s=math.sqrt(2.0/(Xin+H)); self.H=H
        self.Wz=np.random.randn(H,Xin+H).astype(np.float32)*s
        self.bz=np.zeros(H,np.float32)
        self.Wr=np.random.randn(H,Xin+H).astype(np.float32)*s
        self.br=np.zeros(H,np.float32)
        self.W=np.random.randn(H,Xin+H).astype(np.float32)*s
        self.b=np.zeros(H,np.float32)
        self.Wh=np.random.randn(Out,H).astype(np.float32)*s*0.1
        self.bh=np.zeros(Out,np.float32)
        self.dWz=self.dWr=self.dW=self.dbz=self.dbr=self.db=np.zeros((1,),np.float32)
    def fwd(self, seq):
        h=np.zeros(self.H,np.float32)
        for t in range(seq.shape[0]):
            x=seq[t]
            cat=np.concatenate([h,x])
            z=sigmoid(cat@self.Wz.T+self.bz)
            r=sigmoid(cat@self.Wr.T+self.br)
            hc=np.tanh(np.concatenate([r*h,x])@self.W.T+self.b)
            h=(1-z)*h+z*hc
        y=sigmoid(h@self.Wh.T+self.bh)
        return h, y

K=15; H=32
gru=GRU(7,H,49)
split=N-700
hist=[X[i-K:i] for i in range(K,N)]
Yt=Y[K:]
lr=0.05
loss_hist=[]
for epoch in range(25):
    perm=rng.permutation(split-K)
    tot=0;nb=0
    for idx in perm:
        h,y=gru.fwd(hist[idx])
        dY=(y-Yt[idx])*y*(1-y)            # (49,)
        # head 更新
        gru.Wh -= lr*np.outer(dY,h); gru.bh -= lr*dY
        # 末步隐状态梯度(截断BPTT近似): 仅用末输入反传, 驱动循环压缩历史
        dh=dY@gru.Wh                      # (H,)
        xlast=hist[idx][-1]; hprev=np.zeros(H,np.float32)  # 末步 hprev 近似0(只驱末输入)
        cat=np.concatenate([hprev,xlast])
        z=sigmoid(cat@gru.Wz.T+gru.bz); r=sigmoid(cat@gru.Wr.T+gru.br)
        hc=np.tanh(np.concatenate([r*hprev,xlast])@gru.W.T+gru.b)
        # 对末步 gate 权重做小步更新
        gru.Wz -= lr*0.1*np.outer((dh*(1-z)*hc), cat)
        gru.Wr -= lr*0.1*np.outer((dh*(1-z)*h*r*(1-r)), cat)
        gru.W  -= lr*0.1*np.outer((dh*(1-z)*r*(1-hc*hc)), cat)
        tot+=((y-Yt[idx])**2).sum(); nb+=1
    loss_hist.append(tot/nb)
    if epoch%5==0: print('epoch %d loss=%.4f'%(epoch,tot/nb))
print('GRU 末步近似训练 loss %.4f->%.4f'%(loss_hist[0],loss_hist[-1]))

# ---------- 测试段真实命中率 ----------
def score_reds(logits, truth_reds):
    order=np.argsort(-logits[:33])[:6]
    return len(set(order.tolist()) & set((t-1) for t in truth_reds))
def score_blue(logits, truth_blue):
    return int(np.argmax(logits[33:])==(truth_blue-1))

hits_r=hits_b=0; nT=0
for i in range(split,N-K):
    h,y=gru.fwd(hist[i])
    truth_reds=rows[i+K][1]; truth_blue=rows[i+K][2]
    hits_r+=score_reds(y,truth_reds); hits_b+=score_blue(y,truth_blue); nT+=1
rand_r=6*6/33; rand_b=1/16
print('GRU 测试段: 红球 %.4f/6 (随机 %.4f), 蓝球 %d/%d (期望 %.1f)'%(hits_r/nT,rand_r,hits_b,nT,nT*rand_b))
res={'loss_start':loss_hist[0],'loss_end':loss_hist[-1],
     'red_hit':hits_r/nT,'rand_red':rand_r,'blue_hit':hits_b/nT,'rand_blue':rand_b}
with open('D:/ssq_evo_data/seq_gru.json','w') as f: json.dump(res,f,indent=2)
print('GRU v2 完成 -> seq_gru.json')
