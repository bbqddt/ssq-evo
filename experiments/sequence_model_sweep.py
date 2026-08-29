# -*- coding: utf-8 -*-
"""seq_model_sweep: 对 K in [5,30,60] 跑 rich-feature MLP, 测上下文长度敏感性。后台跑。"""
import csv, math, json, random
import numpy as np

MASTER=r'D:/ssq_evo_data/ssq_master.csv'
draws=[]
with open(MASTER,encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try: draws.append((int(r['issue']),tuple(sorted(int(r['r%d'%i]) for i in (1,2,3,4,5,6))),int(r['b'])))
        except: pass
draws.sort(key=lambda x:x[0]); N=len(draws); TRAIN=int(N*0.8)
ps=[[0]*N for _ in range(34)]; ls=[[0]*N for _ in range(34)]
for t in range(N):
    for b in range(1,34):
        ps[b][t]=ps[b][t-1] if t>0 else 0; ls[b][t]=ls[b][t-1] if t>0 else 0
    for b in draws[t][1]: ps[b][t]+=1; ls[b][t]=t
sums=[sum(r[1]) for r in draws]
def feat(t):
    hi=t; f=np.zeros(151,dtype=np.float32); red=draws[t][1]; blue=draws[t][2]
    for b in red: f[b-1]=1.0
    f[33+blue-1]=1.0
    for b in range(1,34):
        rf10=(ps[b][hi]-(ps[b][hi-10] if hi>=10 else 0))/min(10,hi+1)
        rf30=(ps[b][hi]-(ps[b][hi-30] if hi>=30 else 0))/min(30,hi+1)
        gap=(hi-ls[b][hi]) if ls[b][hi]>0 else 100
        f[49+b-1]=rf10; f[82+b-1]=rf30; f[115+b-1]=min(gap,100)/100.0
    f[148]=sums[t]/183.0; f[149]=(sums[t]%2); return f
Xall=np.stack([feat(t) for t in range(N)])
def sigmoid(x): return 1.0/(1.0+np.exp(-np.clip(x,-30,30)))
def bce(z,y): return np.mean(np.maximum(z,0)-z*y+np.log(1+np.exp(-np.abs(z))))
class MLP:
    def __init__(self,dims,lr=0.01):
        self.W=[];self.b=[]
        for i in range(len(dims)-1):
            s=math.sqrt(2.0/dims[i]); self.W.append(np.random.randn(dims[i],dims[i+1]).astype(np.float32)*s); self.b.append(np.zeros(dims[i+1],dtype=np.float32))
        self.mW=[np.zeros_like(w) for w in self.W];self.vW=[np.zeros_like(w) for w in self.W];self.mb=[np.zeros_like(x) for x in self.b];self.vb=[np.zeros_like(x) for x in self.b];self.lr=lr;self.t=0
    def forward(self,X):
        self.cache=[X];a=X
        for i in range(len(self.W)-1): z=a@self.W[i]+self.b[i];a=np.maximum(0,z);self.cache.append((z,a))
        z=a@self.W[-1]+self.b[-1];self.cache.append(z);return z
    def backward(self,Y):
        z=self.cache[-1];dz=sigmoid(z)-Y;gW=[None]*len(self.W);gb=[None]*len(self.b)
        gb[-1]=dz.sum(0);gW[-1]=self.cache[-2][1].T@dz;da=dz@self.W[-1].T
        for i in range(len(self.W)-2,-1,-1):
            z,a=self.cache[i+1];dr=(z>0).astype(np.float32)*da;ap=self.cache[i] if i==0 else self.cache[i][1]
            gb[i]=dr.sum(0);gW[i]=ap.T@dr;da=dr@self.W[i].T
        return gW,gb
    def step(self,gW,gb):
        self.t+=1;b1,b2,eps=0.9,0.999,1e-8
        for i in range(len(self.W)):
            self.mW[i]=b1*self.mW[i]+(1-b1)*gW[i];self.mb[i]=b1*self.mb[i]+(1-b1)*gb[i]
            self.vW[i]=b2*self.vW[i]+(1-b2)*(gW[i]**2);self.vb[i]=b2*self.vb[i]+(1-b2)*(gb[i]**2)
            mh=self.mW[i]/(1-b1**self.t);vh=self.vW[i]/(1-b2**self.t)
            self.W[i]-=self.lr*mh/(np.sqrt(vh)+eps);self.b[i]-=self.lr*self.mb[i]/(np.sqrt(self.vb[i])+eps)
def run(K):
    def mk(end):
        Xs=[];Yr=[];Yb=[]
        for t in range(K,end):
            Xs.append(Xall[t-K:t].reshape(-1));yr=np.zeros(33,dtype=np.float32)
            for b in draws[t][1]:yr[b-1]=1.0;Yr.append(yr);yb=np.zeros(16,dtype=np.float32);yb[draws[t][2]-1]=1.0;Yb.append(yb)
        return np.array(Xs),np.array(Yr),np.array(Yb)
    Xtr,Yr_tr,Yb_tr=mk(TRAIN)
    m=MLP([K*151,256,128,64,49],lr=0.015);B=256;idx=np.arange(Xtr.shape[0])
    for ep in range(40):
        np.random.shuffle(idx)
        for s in range(0,len(idx),B):
            bidx=idx[s:s+B];Xb=Xtr[bidx];Yb_=np.concatenate([Yr_tr[bidx],Yb_tr[bidx]],1)
            z=m.forward(Xb);gW,gb=m.backward(Yb_);m.step(gW,gb)
    Xts=[];acts=[]
    for t in range(TRAIN,N):Xts.append(Xall[t-K:t].reshape(-1));acts.append((set(draws[t][1]),draws[t][2]))
    z=m.forward(np.array(Xts));rl=z[:,:33];bl=z[:,33:]
    hits=[len(set(np.argsort(-rl[i])[:6]+1)&acts[i][0]) for i in range(len(acts))]
    hr=sum(hits)/len(hits);bh=sum(1 for i in range(len(acts)) if (np.argmax(bl[i])+1)==acts[i][1])
    rnd=random.Random(2026);base=[len(set(rnd.sample(range(1,34),6))&set(draws[t][1])) for t in range(TRAIN,N)]
    BASE=sum(base)/len(base)
    return hr,BASE,bh,len(acts)
res={}
for K in [5,30,60]:
    hr,BASE,bh,n=run(K)
    res[f'K{K}']={'red_hit':float(hr),'random':float(BASE),'edge':float(hr-BASE),'blue':f'{bh}/{n}'}
    print(f'K={K}: red={hr:.4f} rand={BASE:.4f} edge={hr-BASE:+.4f} blue={bh}/{n}')
json.dump(res,open(r'D:/ssq_evo_data/seq_sweep.json','w'),indent=2)
print('sweep 完成 -> seq_sweep.json')
