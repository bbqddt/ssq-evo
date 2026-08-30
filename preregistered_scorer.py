"""预注册前瞻打分器（宿主专用）—— ARTIFACT_SUSPECTED 的唯一零成本推进路径。

背景
----
2026-08-29 夜注册了一个可证伪的前瞻预测（audit/marginal_bias_preregistered.json）：
33 个红球的边际频率偏差向量。判决依据是**未来开奖**：
若未来窗口的偏差向量与注册向量显著正相关（秩 p<0.05），
则静态偏倚假设得到样本外确认；若长期不显著，则进一步支持伪影判定。

为什么自动化
------------
等开奖确认需 7~22 年，人的时间尺度上只能**让机器持续记账**。
每积累一期自动打一次分，证据随开奖免费累积，无需任何人工干预。

多重警示（诚实要求）
--------------------
多次窥视（peeking）会累积假阳性风险。本打分器如实记录**已窥视次数**，
并给出 Bonferroni 后的累计阈值 —— 窥视 k 次后单次显著阈值应为 0.05/k。
结论只认累计校正后的显著性。

用法
----
    python preregistered_scorer.py            # 打分并追加日志
    python preregistered_scorer.py --status   # 只看状态不打分
"""

import json
import os
import sys
from datetime import datetime

import numpy as np

import data as D
import honesty_footer as HF
import paths
from exchangeable_probe import N_BALL, N_PICK, gen_uniform

REG_PATH = ("audit", "marginal_bias_preregistered.json")
LOG_PATH = ("audit", "preregistered_scores.jsonl")
MIN_NEW = 50          # 低于此数不打分（噪声太大，无信息）
MC = 400              # 蒙特卡洛零假设重复数


def load_registry():
    p = paths.p(*REG_PATH)
    if not os.path.exists(p):
        return None, "预注册文件不存在: %s" % p
    return json.load(open(p, encoding="utf-8")), None


# ---------------------------------------------------------------------------
# 哈希锚定（F 项）：预注册文件防事后篡改
# ---------------------------------------------------------------------------
ANCHOR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "anchors", "preregistered.sha256")


def _sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def anchor():
    """把预注册文件的 sha256 写入 git 仓库内锚点（随仓库提交 = 时间戳+内容铁证）。"""
    reg_path = paths.p(*REG_PATH)
    if not os.path.exists(reg_path):
        print("[anchor] 预注册文件不存在")
        return None
    h = _sha256_of(reg_path)
    os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
    with open(ANCHOR_PATH, "w", encoding="utf-8") as f:
        f.write("%s\n# anchored_at %s\n# 预注册内容此后不得修改; 重注册须另行说明理由\n"
                % (h, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("[anchor] 已锚定: %s" % ANCHOR_PATH)
    print("[anchor] sha256 = %s" % h[:32] + "...")
    return h


def verify_anchor():
    """校验当前预注册文件与 git 锚点是否一致。无锚点 ⇒ (False, '未锚定')。"""
    reg_path = paths.p(*REG_PATH)
    if not os.path.exists(ANCHOR_PATH):
        return False, "未锚定"
    if not os.path.exists(reg_path):
        return False, "预注册文件不存在"
    anchored = open(ANCHOR_PATH, encoding="utf-8").readline().strip()
    current = _sha256_of(reg_path)
    return (current == anchored), ("一致" if current == anchored else
                                   "不一致! 预注册内容在锚定后被修改")


def score(min_new=MIN_NEW, mc=MC, seed=20260830, verbose=True):
    ok, msg = verify_anchor()
    if not ok:
        print("[scorer] ⛔ 拒绝打分: 预注册完整性校验失败(%s)。" % msg)
        print("[scorer]    预注册被改动 = 注册失效, 任何'确认'都不可信。")
        return {"status": "REGISTRY_TAMPERED", "reason": msg}
    reg, err = load_registry()
    if reg is None:
        print("[scorer] %s" % err)
        return None
    v = np.array([reg["dev_pct"][str(i + 1)] for i in range(N_BALL)])  # 注册向量

    master = D.load_master(paths.master_csv())
    reds, blues, _ = D.to_arrays(master)
    n_basis = reg.get("n_basis", 0)
    n = len(reds)
    n_new = n - n_basis
    if n_new <= 0:
        if verbose:
            print("[scorer] 注册基准 n=%d, 当前 n=%d —— 尚无新开奖可打分" % (n_basis, n))
            print("[scorer] 注册时间: %s" % reg.get("registered_at"))
        return {"status": "NO_NEW_DRAWS", "n_basis": n_basis, "n_now": n}

    new_reds = reds[n_basis:]
    if verbose:
        print("[scorer] 注册基准 n=%d, 新开奖 %d 期" % (n_basis, n_new))

    if n_new < min_new:
        if verbose:
            print("[scorer] 新开奖 %d < %d，样本不足暂不打分（避免纯噪声窥视）"
                  % (n_new, min_new))
        return {"status": "INSUFFICIENT", "n_new": n_new, "min_new": min_new}

    # 新窗口偏差向量
    c = np.bincount(new_reds.ravel(), minlength=N_BALL + 1)[1:N_BALL + 1].astype(float)
    e = n_new * N_PICK / N_BALL
    u = (c - e) / e * 100.0

    r_obs = float(np.corrcoef(v, u)[0, 1])
    hit = float(np.mean(np.sign(v) == np.sign(u)))

    # 蒙特卡洛零假设：同期数均匀随机
    rng = np.random.default_rng(seed)
    null_r = []
    null_h = []
    for _ in range(mc):
        rr = gen_uniform(n_new, rng)
        cc = np.bincount(rr.ravel(), minlength=N_BALL + 1)[1:N_BALL + 1].astype(float)
        uu = (cc - e) / e * 100.0
        null_r.append(float(np.corrcoef(v, uu)[0, 1]))
        null_h.append(float(np.mean(np.sign(v) == np.sign(uu))))
    null_r = np.array(null_r)
    null_h = np.array(null_h)
    p_r = float((null_r >= r_obs).mean() + 0.5 / mc)
    p_h = float((null_h >= hit).mean() + 0.5 / mc)

    # 多重窥视校正
    n_peeks = _peek_count() + 1
    alpha_adj = 0.05 / n_peeks

    res = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_basis": n_basis, "n_new": n_new,
        "r": round(r_obs, 4), "p_r": round(p_r, 4),
        "dir_hit": round(hit, 4), "p_dir": round(p_h, 4),
        "null_r_mean": round(float(null_r.mean()), 4),
        "null_r_sd": round(float(null_r.std()), 4),
        "peek_index": n_peeks, "alpha_peek_adj": round(alpha_adj, 5),
        "significant_raw": bool(p_r < 0.05),
        "significant_peek_adj": bool(p_r < alpha_adj),
        "verdict": ("CONFIRMED_PEER_ADJ" if p_r < alpha_adj else
                    ("SIGNAL_RAW_ONLY" if p_r < 0.05 else "NULL_SO_FAR")),
        "footer": HF.HONESTY_FOOTER,
    }
    if verbose:
        print("[scorer] 新窗口 n=%d:" % n_new)
        print("    与注册向量相关 r = %+.4f   零假设 %+.4f±%.4f   秩 p = %.4f"
              % (r_obs, null_r.mean(), null_r.std(), p_r))
        print("    方向命中率 = %.1f%%   秩 p = %.4f" % (100 * hit, p_h))
        print("    第 %d 次窥视 ⇒ Bonferroni 阈值 α = %.4f" % (n_peeks, alpha_adj))
        print("    判定: %s" % res["verdict"])
        print("    [页脚] %s" % HF.HONESTY_FOOTER)

    _append_log(res)
    return res


def _peek_count():
    p = paths.p(*LOG_PATH)
    if not os.path.exists(p):
        return 0
    k = 0
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            k += 1
    return k


def _append_log(res):
    p = paths.p(*LOG_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")


def prospective_power_curve(n_list=(50, 200, 500, 1000, 2000, 3500, 7000),
                            sigma_pct=3.5, m=200, mc=200, seed=31337):
    """前瞻证据累积曲线：新开奖累积到 n 期时，打分器能多可靠地判决？

    构造：把注册向量 v 的**方向**当作偏倚方向、幅度重标定到 σ，
    生成"未来"数据，用**打分器自身的判据**（相关 r 的蒙特卡洛秩 p）评分。
    ⇒ 得到的检出率是"在该 σ 真实存在的前提下，累积 n 期后能确认的概率"。

    注意：这里没有用 v 当真值，而是把 v 去均值后重标定到 σ，
    避免把 v 自带的估计噪声当成信号而高估功效。
    """
    reg, err = load_registry()
    if reg is None:
        print("[scorer] %s" % err)
        return None
    v = np.array([reg["dev_pct"][str(i + 1)] for i in range(33)], float)
    v = v - v.mean()
    if v.std() > 0:
        v = v / v.std() * sigma_pct
    w = np.exp(v / 100.0)
    p_bias = w / w.sum()

    e_per = 6.0 / 33.0
    rng = np.random.default_rng(seed)
    out = []
    for n_new in n_list:
        # 零分布：同期数均匀随机 vs v 的相关
        null = []
        for _ in range(mc):
            u = np.zeros(33)
            for _d in range(n_new):
                for b in np.sort(rng.choice(33, 6, replace=False)):
                    u[b] += 1
            u = (u - n_new * e_per) / (n_new * e_per) * 100.0
            null.append(float(np.corrcoef(v, u)[0, 1]))
        null = np.array(null)
        thr = float(np.quantile(null, 0.95))
        hits = 0
        for _ in range(m):
            u = np.zeros(33)
            for _d in range(n_new):
                for b in rng.choice(33, 6, replace=False, p=p_bias):
                    u[b] += 1
            u = (u - n_new * e_per) / (n_new * e_per) * 100.0
            if float(np.corrcoef(v, u)[0, 1]) >= thr:
                hits += 1
        out.append({"n_new": n_new, "detect_rate": hits / m,
                    "null_r_sd": round(float(null.std()), 4)})
    return {"sigma_pct": sigma_pct, "m": m, "mc": mc, "curve": out}


def status():
    reg, err = load_registry()
    if reg is None:
        print("[scorer] %s" % err)
        return
    master = D.load_master(paths.master_csv())
    reds, _, _ = D.to_arrays(master)
    n_new = len(reds) - reg.get("n_basis", 0)
    peeks = _peek_count()
    print("[scorer] 注册于 %s (基准 n=%d)" % (reg.get("registered_at"), reg.get("n_basis")))
    print("[scorer] 当前新开奖 %d 期；已正式打分 %d 次（打分门槛 n_new>=%d）"
          % (n_new, peeks, MIN_NEW))
    p = paths.p(*LOG_PATH)
    if os.path.exists(p):
        lines = [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]
        if lines:
            last = json.loads(lines[-1])
            print("[scorer] 最近一次: %s  r=%+.4f p=%.4f 判定=%s"
                  % (last["ts"], last["r"], last["p_r"], last["verdict"]))


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        score()
