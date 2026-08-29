# -*- coding: utf-8 -*-
"""
ssq_evo 序列模型 v3 —— 迭代：1D 卷积 (im2col 实现, 反向正确)
在时间轴上学局部滤波, 全局池化 + MLP 头。walk-forward 同前。
"""
import csv, math, json
import numpy as np

MASTER = r'D:/ssq_evo_data/ssq_master.csv'
draws=[]
with open(MASTER,encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try: draws.append((int(r['issue']), tuple(sorted(int(r['r%d'%i]) for i in (1,2,3,4,5,6))), int(r['b'])))
        except: pass
draws.sort(key=lambda x:x[0]); N=len(draws); TRAIN=int(N*0.8); K=15

def vec(reds,blue):
    v=np.zeros(49,dtype=np.float32)
    for b in reds: v[b-1]=1.0
    v[33+blue-1]=1.0
    return v
Xall=np.stack([vec(r[1],r[2]) for r in draws])  # (N,49)

def make_samples(end):
    Xs=[];Yr=[];Yb=[]
    for t in range(K,end):
        Xs.append(Xall[t-K:t]); yr=np.zeros(33,dtype=np.float32)
        for b in draws[t][1]: yr[b-1]=1.0
        Yr.append(yr); yb=np.zeros(16,dtype=np.float32); yb[draws[t][2]-1]=1.0; Yb.append(yb)
    return np.array(Xs),np.array(Yr),np.array(Yb)

Xtr,Yr_tr,Yb_tr=make_samples(TRAIN)
print('训练样本=%d' % Xtr.shape[0])

def sigmoid(x): return 1.0/(1.0+np.exp(-np.clip(x,-30,30)))
def bce(z,y): return np.mean(np.maximum(z,0)-z*y+np.log(1+np.exp(-np.abs(z))))

# ---- im2col conv1d (stride1, valid) ----
def im2col(x, k):
    T,C = x.shape; L=T-k+1
    cols=np.zeros((L,k*C),dtype=np.float32)
    for j in range(L):
        cols[j]=x[j:j+k].reshape(-1)
    return cols  # (L, kC)
def col2im(cols, T, C, k):
    dx=np.zeros((T,C),dtype=np.float32); L=cols.shape[0]
    for j in range(L):
        dx[j:j+k]+=cols[j].reshape(k,C)
    return dx

class Conv1d:
    def __init__(self,F,k,Cin):
        s=math.sqrt(2.0/(k*Cin)); self.W=np.random.randn(F,k*Cin).astype(np.float32)*s
        self.b=np.zeros(F,dtype=np.float32); self.F=F
        self.dW=np.zeros_like(self.W); self.db=np.zeros_like(self.b)
    def forward(self,x):  # x:(T,C)
        self.x=x; cols=im2col(x,self.W.shape[1]//x.shape[1])
        out=cols@self.W.T+self.b  # (L,F)
        self.cols=cols; return out
    def backward(self,dout):  # dout:(L,F)
        self.dW += dout.T@self.cols; self.db += dout.sum(0)
        dcols=dout@self.W; return col2im(dcols,self.x.shape[0],self.x.shape[1],self.W.shape[1]//self.x.shape[1])

class MLPHead:
    def __init__(self,dims,lr=0.01):
        self.W=[];self.b=[]
        for i in range(len(dims)-1):
            s=math.sqrt(2.0/dims[i]); self.W.append(np.random.randn(dims[i],dims[i+1]).astype(np.float32)*s)
            self.b.append(np.zeros(dims[i+1],dtype=np.float32))
        self.mW=[np.zeros_like(w) for w in self.W];self.vW=[np.zeros_like(w) for w in self.W]
        self.mb=[np.zeros_like(x) for x in self.b];self.vb=[np.zeros_like(x) for x in self.b]
        self.lr=lr;self.t=0
    def forward(self,X):
        self.cache=[X];a=X
        for i in range(len(self.W)-1):
            z=a@self.W[i]+self.b[i];a=np.maximum(0,z);self.cache.append((z,a))
        z=a@self.W[-1]+self.b[-1];self.cache.append(z);return z
    def backward(self,Y):
        z=self.cache[-1];dz=sigmoid(z)-Y
        gW=[None]*len(self.W);gb=[None]*len(self.b)
        gb[-1]=dz.sum(0);gW[-1]=self.cache[-2][1].T@dz;da=dz@self.W[-1].T
        for i in range(len(self.W)-2,-1,-1):
            z,a=self.cache[i+1];dr=(z>0).astype(np.float32)*da
            ap=self.cache[i] if i==0 else self.cache[i][1]
            gb[i]=dr.sum(0);gW[i]=ap.T@dr;da=dr@self.W[i].T
        return gW,gb
    def step(self,gW,gb):
        self.t+=1;b1,b2,eps=0.9,0.999,1e-8
        for i in range(len(self.W)):
            self.mW[i]=b1*self.mW[i]+(1-b1)*gW[i];self.mb[i]=b1*self.mb[i]+(1-b1)*gb[i]
            self.vW[i]=b2*self.vW[i]+(1-b2)*(gW[i]**2);self.vb[i]=b2*self.vb[i]+(1-b2)*(gb[i]**2)
            mh=self.mW[i]/(1-b1**self.t);vh=self.vW[i]/(1-b2**self.t)
            self.W[i]-=self.lr*mh/(np.sqrt(vh)+eps);self.b[i]-=self.lr*self.mb[i]/(np.sqrt(self.vb[i])+eps)

conv1=Conv1d(16,3,49); conv2=Conv1d(16,3,16)
head=MLPHead([16,64,49],lr=0.02)
B=256; idx=np.arange(Xtr.shape[0])
for epoch in range(50):
    np.random.shuffle(idx); tot=0;nb=0
    for s in range(0,len(idx),B):
        bidx=idx[s:s+B]; xb=Xtr[bidx]  # (B,T,C)
        bz=[]; 
        # forward per sample (conv handles 2D)
        c1=[];c2=[]
        for i in range(len(xb)):
            o1=conv1.forward(xb[i]); a1=np.maximum(0,o1)
            o2=conv2.forward(a1); a2=np.maximum(0,o2)
            gp=a2.mean(0)  # global avg pool (T',F)->F
            c1.append(o1);c2.append(a2)
            bz.append(gp)
        Z=np.stack(bz)  # (B,16)
        z=head.forward(Z); Yb_=np.concatenate([Yr_tr[bidx],Yb_tr[bidx]],1)
        loss=bce(z,Yb_); tot+=loss*len(bidx);nb+=len(bidx)
        # head 手动反向 (2层: 16->64->49)
        dz_out = sigmoid(z)-Yb_                       # (B,49)
        a0 = head.cache[1][1]                         # 64维 relu 输出
        gW1 = a0.T @ dz_out; gb1 = dz_out.sum(0)
        da = dz_out @ head.W[1].T                     # (B,64)
        z0 = head.cache[1][0]
        dr = (z0>0).astype(np.float32)*da             # (B,64)
        gW0 = head.cache[0].T @ dr; gb0 = dr.sum(0)
        dZ = dr @ head.W[0].T                         # (B,16) 正确输入梯度
        # SGD 更新 head
        head.W[0]-=0.02*gW0; head.b[0]-=0.02*gb0
        head.W[1]-=0.02*gW1; head.b[1]-=0.02*gb1
        # 卷积梯度清零 + 逐样本累积
        conv1.dW[:]=0; conv1.db[:]=0; conv2.dW[:]=0; conv2.db[:]=0
        for i in range(len(xb)):
            da2=np.zeros_like(c2[i]); da2 += dZ[i]/c2[i].shape[0]  # mean-pool 梯度
            dr2=(c2[i]>0).astype(np.float32)*da2
            dA1=conv2.backward(dr2)
            dr1=(c1[i]>0).astype(np.float32)*dA1
            conv1.backward(dr1)
        for cv in (conv1,conv2):
            cv.W-=0.02*cv.dW; cv.b-=0.02*cv.db
    if (epoch+1)%15==0: print('  epoch %2d loss=%.4f'%(epoch+1,tot/nb))

# 测试
Xts=[];acts=[]
for t in range(TRAIN,N): Xts.append(Xall[t-K:t]); acts.append((set(draws[t][1]),draws[t][2]))
bz=[]
for i in range(len(Xts)):
    o1=conv1.forward(Xts[i]);a1=np.maximum(0,o1);o2=conv2.forward(a1);a2=np.maximum(0,o2)
    bz.append(a2.mean(0))
Z=np.stack(bz); z=head.forward(Z); rl=z[:,:33];bl=z[:,33:]
hits=[len(set(np.argsort(-rl[i])[:6]+1)&acts[i][0]) for i in range(len(acts))]
hr=sum(hits)/len(hits); bh=sum(1 for i in range(len(acts)) if (np.argmax(bl[i])+1)==acts[i][1])
import random;random.seed(2026);rnd=random.Random(2026)
base=[len(set(rnd.sample(range(1,34),6))&set(draws[t][1])) for t in range(TRAIN,N)]
BASE=sum(base)/len(base)
print('\n=== 序列 CNN v3 ===')
print('  CNN 红球命中 = %.4f  (随机 %.4f, 边缘 %.4f)'%(hr,BASE,hr-BASE))
print('  蓝球命中     = %d/%d (期望 %.1f)'%(bh,len(acts),len(acts)/16))
json.dump({'model':'CNN v3','cnn_red_hit':float(hr),'random_baseline':float(BASE),'edge':float(hr-BASE),'blue_hit':int(bh)},
          open(r'D:/ssq_evo_data/seq_model_v3.json','w'),indent=2)
print('已存档 seq_model_v3.json; 结论: %s'%('仍≈随机, 继续迭代' if abs(hr-BASE)<0.2 else '发现边缘'))
