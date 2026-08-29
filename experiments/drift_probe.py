# -*- coding: utf-8 -*-
"""时间频率漂移探针：每个球(33红+16蓝)在数据集早/晚期出现频率是否非平稳。
与自相关/ML 完全独立的假设(非平稳性=潜在结构)。严格 Bonferroni 多重比较。
"""
import csv, json, numpy as np
from scipy import stats

rows=[]
with open(r'D:/ssq_evo_data/ssq_master.csv', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try:
            reds=tuple(int(r['r%d'%i]) for i in (1,2,3,4,5,6)); blue=int(r['b'])
            rows.append((int(r['issue']), reds, blue))
        except: pass
rows.sort(key=lambda x:x[0]); N=len(rows)
print('N=%d'%N)

# 时间分箱: 前半 / 后半 (2箱) + 4等分箱(趋势)
def drift(ball, is_blue):
    cnt=np.zeros(4)   # 4个时间四分位
    total=np.zeros(4)
    for i,(iss,reds,blue) in enumerate(rows):
        q=i*4//N
        total[q]+=1
        if is_blue:
            if blue==ball: cnt[q]+=1
        else:
            if ball in reds: cnt[q]+=1
    # 2x2 半段
    early=cnt[0]+cnt[1]; late=cnt[2]+cnt[3]
    early_t=total[0]+total[1]; late_t=total[2]+total[3]
    # chi2 2x2
    tbl=[[early, early_t-early],[late, late_t-late]]
    try:
        c2_2,p_2=stats.chi2_contingency(tbl, correction=True)[:2]
    except: p_2=1.0
    # 趋势: 4箱 出现率 vs 箱序, Cochran-Armitage 风格用 spearman
    rates=cnt/total
    rho,p_trend=stats.spearmanr(rates, [0,1,2,3]) if total.min()>0 else (0,1)
    return p_2, p_trend, rates

results=[]
for ball in range(1,34):
    p2,pt,rates=drift(ball, False)
    results.append(('red',ball,p2,pt,rates))
for ball in range(1,17):
    p2,pt,rates=drift(ball, True)
    results.append(('blue',ball,p2,pt,rates))

m=len(results)
# Bonferroni: 对每个球取 min(p2,p_trend) 作该球最强证据
balls=[]
for kind,ball,p2,pt,rates in results:
    pmin=min(p2,pt)
    balls.append((kind,ball,pmin,p2,pt,rates))

# 期望: 在 null 下, 49个独立检验中最小 p 应 ~ 均匀; 经 Bonferroni, 显著阈值 0.05/49=0.00102
sig=[b for b in balls if b[2] < 0.05/m]
print('Bonferroni 阈值 = %.5f'%(0.05/m))
print('通过 Bonferroni 的球(非平稳): %d / %d'%(len(sig), m))
for b in sorted(sig, key=lambda x:x[2])[:10]:
    print('  %s%d p_min=%.4f (2x2=%.4f 趋势=%.4f) rates=%s'%(b[0],b[1],b[2],b[3],b[4],[round(x,3) for x in b[5]]))
# 也报告未校正下最极端者(观察选择偏差)
print('--- 未校正最极端 5 个(仅供参考, 选择偏差) ---')
for b in sorted(balls, key=lambda x:x[2])[:5]:
    print('  %s%d p_min=%.4f rates=%s'%(b[0],b[1],b[2],[round(x,3) for x in b[5]]))

res={'N':N,'bonf_thr':0.05/m,'n_sig':len(sig),
     'min_p':min(b[2] for b in balls),
     'worst':[{'kind':b[0],'ball':b[1],'p':b[2]} for b in sorted(balls,key=lambda x:x[2])[:5]]}
with open('D:/ssq_evo_data/drift_probe.json','w') as f: json.dump(res,f,indent=2)
print('漂移探针完成 -> drift_probe.json')
