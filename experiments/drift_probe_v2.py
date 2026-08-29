# -*- coding: utf-8 -*-
"""时间频率漂移探针 v2：严格版。
v1 用 4箱 Spearman, n=4 时 scipy t-近似给出伪 p=0.0000(精确置换 p≈0.083) -> 假阳性。
本版改用 Cochran-Armitage 趋势检验(chi2_1, 对小数稳健) + 10个时间十分位箱,
并保留 2x2 半段 chi2 作交叉验证。Bonferroni(49球) 多重比较。
"""
import csv, json, math, numpy as np
from scipy import stats

rows=[]
with open(r'D:/ssq_evo_data/ssq_master.csv', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try:
            reds=tuple(int(r['r%d'%i]) for i in (1,2,3,4,5,6)); blue=int(r['b'])
            rows.append((int(r['issue']), reds, blue))
        except: pass
rows.sort(key=lambda x:x[0]); N=len(rows)
print('N=%d, 时间十分位箱 k=10'%N)

def cochran_armitage(success, total, scores):
    # 标准公式: Z = Σ(x_i - n_i*p̂)*t_i / sqrt( p̂(1-p̂)*[Σn_i t_i^2 - (Σn_i t_i)^2/N] )
    k=len(success); n=sum(total); x=sum(success)
    if n==0: return 1.0
    p=x/n
    num=sum((success[i]-total[i]*p)*scores[i] for i in range(k))
    var=p*(1-p)*(sum(total[i]*scores[i]**2 for i in range(k))
               - (sum(total[i]*scores[i] for i in range(k)))**2/n)
    if var<=0: return 1.0
    Z=num/math.sqrt(var)
    return float(stats.chi2.sf(Z*Z, 1))

def ball_trend(ball, is_blue):
    # 10 十分位箱
    succ=np.zeros(10); tot=np.zeros(10)
    for i,(iss,reds,blue) in enumerate(rows):
        q=min(9, i*10//N)
        tot[q]+=1
        hit = (blue==ball) if is_blue else (ball in reds)
        if hit: succ[q]+=1
    scores=list(range(10))
    p_ca=cochran_armitage(succ, tot, scores)
    # 2x2 半段交叉验证
    early=succ[0:5].sum(); late=succ[5:10].sum()
    early_t=tot[0:5].sum(); late_t=tot[5:10].sum()
    tbl=[[early,early_t-early],[late,late_t-late]]
    try: p_2=stats.chi2_contingency(tbl,correction=True)[1]
    except: p_2=1.0
    rates=succ/tot
    return p_ca, p_2, rates

results=[]
for ball in range(1,34): results.append(('red',ball)+ball_trend(ball,False))
for ball in range(1,17): results.append(('blue',ball)+ball_trend(ball,True))

m=len(results)
bonf=0.05/m
sig=[r for r in results if min(r[2],r[3])<bonf]
print('Bonferroni 阈值 = %.5f'%bonf)
print('通过 Bonferroni 的球(非平稳): %d / %d'%(len(sig),m))
for r in sorted(sig,key=lambda x:min(x[2],x[3]))[:10]:
    print('  %s%d CA_p=%.4f 2x2_p=%.4f rates=%s'%(r[0],r[1],r[2],r[3],[round(x,3) for x in r[4]]))
print('--- 未校正最极端 5 (选择偏差, 仅供参考) ---')
for r in sorted(results,key=lambda x:min(x[2],x[3]))[:5]:
    print('  %s%d min_p=%.4f rates=%s'%(r[0],r[1],min(r[2],r[3]),[round(x,3) for x in r[4]]))

res={'N':N,'bonf_thr':bonf,'n_sig':len(sig),
     'min_p':min(min(r[2],r[3]) for r in results),
     'worst':[{'kind':r[0],'ball':r[1],'p':min(r[2],r[3])} for r in sorted(results,key=lambda x:min(x[2],x[3]))[:5]]}
with open('D:/ssq_evo_data/drift_probe_v2.json','w') as f: json.dump(res,f,indent=2)
print('漂移探针 v2 完成 -> drift_probe_v2.json')
