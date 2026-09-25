# -*- coding: utf-8 -*-
"""method_arena.py v3 —— 方法竞技场（方法论优胜劣汰的制度化裁判）

v3（2026-09-25 晚）统计单元改为独立 run：v2 用 run 内逐期得分做 t 检验，但预测
窗口滑动重叠 → 序列相关 → 朴素 SE 系统性低估方差 → 阴性擂台三方法全部假警报
（z=+15.46/+13.37/+5.09）。v3 以 run 均值为观测单元（run 间独立），方差天然包含
run 内相关，阴性假警报全部消失。v3 = 当前冻结判据版本。

v2（2026-09-25 早）重设计阳性擂台。v1 用"合成数据上的命中率 vs 随机基线"做阳性判据，
但命中率的噪声来自摇奖本身（每期仅开 6/33），任何 top6 选号法在这个指标上的功效
都低到不可判决——裁判自己没有判决力，不配当裁判。v1 首审"三方法全灭"实为
**裁判无功效**，不是方法全灭（诚实自我审判，见 MISSION §6 修订）。

擂台（v2 引入、v3 沿用）：
    擂台1（阳性·选号方向漂移）：
        统计候选方法在【注入预注册偏倚】的训练数据上选出的号码，沿预注册方向 w 的
        投影得分 s_i = Σ_{b∈pred_i} w_b。真有提取能力的选号，其选号会系统性偏向
        偏倚方向；纯随机/盲视方法得分 = 居中分布。
        判据：z = (mean_有偏 − mean_均匀)/(sd_均匀·√(2/N_TEST)) > 1.645
    擂台2（阴性·漂移无假警报）：
        同一方法在【均匀】训练数据上两组种子独立跑，漂移得分不得显著（|z|<1.645）。
    双过 = CERTIFIED；任一败 = REJECTED。

判据冻结在文件内（v3 先于任何后续候选评估）。改判据 = 新版本 + 全体候选重审。

用法：
    python method_arena.py                     # 检现役方法
    python method_arena.py --func mymethod.py  # 检自定义候选（predict(train)->(reds,blue)）
"""
import argparse, json, math, os, random, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

PREREG = paths.p('audit', 'marginal_bias_preregistered.json')
N_WARM  = 900     # 训练热身期数
N_TEST  = 120     # 评估期数
Z_CRIT  = 1.645   # 单侧 5%

def load_prereg_w():
    """预注册偏差向量，center+normalize（与 OBF 协议同一定义）。"""
    pr = json.load(open(PREREG, encoding='utf-8'))
    dev = {int(k): v/100.0 for k, v in pr['dev_pct'].items()}
    w = np.array([dev.get(i, 0.0) for i in range(1, 34)])
    return w / np.linalg.norm(w)

W = load_prereg_w()

def pred_score(reds):
    return float(sum(W[b-1] for b in reds))

def make_biased(rng, n, p):
    out = []
    for _ in range(n):
        pool = list(range(1, 34)); reds = []
        for _ in range(6):
            pick = rng.choices(pool, weights=[p[i-1] for i in pool], k=1)[0]
            reds.append(pick); pool.remove(pick)
        out.append(sorted(reds))
    return out

def make_uniform(rng, n):
    return [sorted(rng.sample(range(1, 34), 6)) for _ in range(n)]

def to_draws(seq):
    return [{'issue': str(20000+i), 'reds': r, 'blue': 8} for i, r in enumerate(seq)]

def collect_scores(predict_fn, seed, biased):
    if biased:
        pr = json.load(open(PREREG, encoding='utf-8'))
        dv = {int(k): v/100.0 for k, v in pr['dev_pct'].items()}
        p = np.array([1.0 + dv.get(i+1, 0.0) for i in range(33)]); p = p/p.sum()
    D = to_draws(make_biased(random.Random(seed), N_WARM+N_TEST, p) if biased
                 else make_uniform(random.Random(seed), N_WARM+N_TEST))
    scores = []
    for i in range(len(D)-N_TEST, len(D)):
        reds, _blue = predict_fn(D[:i])
        scores.append(pred_score(reds))
    return np.array(scores)

def certify(name, predict_fn):
    # 统计单位 = 一个独立 run（run 内预测窗口重叠、序列相关，朴素 SE 无效——v1/v2 阴性
    # 假警报的根源）。以 run 均值为观测单元，跨 run 方差天然包含序列相关，SE 合法。
    BIAS_SEEDS  = (101, 102)
    UNIF_SEEDS  = (201, 202, 301, 402)
    mb = [collect_scores(predict_fn, s, True).mean()  for s in BIAS_SEEDS]
    mu = [collect_scores(predict_fn, s, False).mean() for s in UNIF_SEEDS]
    mb, mu = np.array(mb), np.array(mu)
    # 阳性：有偏 run 均值 > 均匀 run 均值（run 级 t）
    se = math.sqrt(mb.var(ddof=1)/len(mb) + mu.var(ddof=1)/len(mu))
    z_pos = (mb.mean() - mu.mean()) / se if se > 0 else 0.0
    # 阴性：均匀 run 均值整体应 = 0（对称零假设，run 级 t）
    se0 = mu.std(ddof=1)/math.sqrt(len(mu))
    z_neg = mu.mean()/se0 if se0 > 0 else 0.0
    pos_ok = z_pos > Z_CRIT
    neg_ok = abs(z_neg) < Z_CRIT
    verdict = 'CERTIFIED' if (pos_ok and neg_ok) else 'REJECTED'
    print('[arena] %-28s 阳性漂移 z=%+.2f (%s, 有偏%.3f/均匀%.3f) | 阴性零检验 z=%+.2f (%s) => %s'
          % (name, z_pos, '过' if pos_ok else '败', mb.mean(), mu.mean(),
             z_neg, '过' if neg_ok else '假警报!', verdict))
    return verdict

# ---------- 现役方法适配 ----------
def _fn_comp():
    import predict_tonight as pt
    trees = pt.load_comp_trees()
    def f(train):
        reds, blue, info = pt.predict_from_comp_ensemble(trees, train)
        return reds, blue
    return f

def _fn_signal():
    import predict_tonight as pt
    def f(train):
        reds, blue, info = pt.predict_from_signal(train, 'red_gap_max')
        return reds, blue
    return f

def _fn_marginal():
    import predict_tonight as pt
    def f(train):
        return pt.predict(train)
    return f

BUILTIN = {'comp_ensemble': _fn_comp, 'signal_regime_red_gap_max': _fn_signal,
           'recency_marginal': _fn_marginal}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--func')
    args = ap.parse_args()
    print('[arena3] 判据冻结: N_WARM=%d N_TEST=%d Z_CRIT=%.3f 方向=预注册向量(center+norm)'
          % (N_WARM, N_TEST, Z_CRIT))
    if args.func:
        ns = {}
        exec(open(args.func, encoding='utf-8').read(), ns)
        certify(args.func, ns['predict']); return
    for name, mk in BUILTIN.items():
        try:
            certify(name, mk())
        except Exception as e:
            print('[arena3] %-28s ERROR: %s' % (name, e))

if __name__ == '__main__':
    main()
