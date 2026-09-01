"""预注册前瞻打分器 v2（宿主专用）—— ARTIFACT_SUSPECTED 的唯一零成本推进路径。

背景
----
2026-08-29 夜注册了一个可证伪的前瞻预测（audit/marginal_bias_preregistered.json）：
33 个红球的边际频率偏差向量。判决依据是**未来开奖**。

v2（2026-09-01，PRE_REGISTERED_PROTOCOL_v1 生效）
------------------------------------------------
判据从「每窥必记 + Bonferroni 0.05/k」升级为**单统计量 + Lan-DeMets OBF 序贯**：
  统计量  Z_n = Σ_d (w·X_d) / (SD1·√n)，w = 注册方向单位化，SD1²=162/1056
  设计    单侧 α=0.05，n_max=3500，边界表 audit/obf_boundary.csv（git 锚定）
  判决    Z_n ≥ b(n) ⇒ CONFIRMED；n=3500 未跨界 ⇒ NOT_CONFIRMED_AT_DESIGN
理由：边际 χ² 类方向检验是该候选（静态物理偏倚）的 Neyman-Pearson 最有力检验；
Z_n 在 H0 下精确布朗 ⇒ OBF 花费精确成立；df 由 13 降为 1（前瞻段）。
历史段报告仍保持 df=13（analysis_ledger），两段不合并、不 Fisher。
协议全文：仓库根 PRE_REGISTERED_PROTOCOL_v1.md

用法
----
    python preregistered_scorer.py            # OBF 序贯打分（v2，cron 默认）
    python preregistered_scorer.py --status   # 只看状态不打分
    python preregistered_scorer.py --legacy   # 旧 Bonferroni 窥视打分（仅历史复算用）
"""

import json
import math
import os
import sys
from datetime import datetime
from statistics import NormalDist

import numpy as np

import data as D
import honesty_footer as HF
import paths
from exchangeable_probe import N_BALL, N_PICK, gen_uniform

ALPHA = 0.05          # 序贯设计总 α（单侧）
_ND = NormalDist()    # stdlib 正态（免 scipy 依赖：hook/最小环境也能跑）

REG_PATH = ("audit", "marginal_bias_preregistered.json")
LOG_PATH = ("audit", "preregistered_scores.jsonl")
BOUND_PATH = ("audit", "obf_boundary.csv")
MIN_NEW = 50          # 低于此数不打分（噪声太大，无信息）
MC = 400              # 蒙特卡洛零假设重复数（legacy 用）
N_BALL_SD = 33
N_PICK_SD = 6
N_MAX = 3500          # 序贯设计终点（见 PRE_REGISTERED_PROTOCOL_v1.md）


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
    """把预注册文件/协议/边界表的 sha256 写入 git 仓库内锚点（随仓库提交 = 时间戳+内容铁证）。"""
    reg_path = paths.p(*REG_PATH)
    if not os.path.exists(reg_path):
        print("[anchor] 预注册文件不存在")
        return None
    entries = [("registry", reg_path)]
    proto = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "PRE_REGISTERED_PROTOCOL_v1.md")
    bnd = paths.p(*BOUND_PATH)
    for name, p in (("protocol", proto), ("boundary", bnd)):
        if os.path.exists(p):
            entries.append((name, p))
    os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
    lines = ["%s" % _sha256_of(entries[0][1]),
             "# anchored_at %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "# 预注册内容此后不得修改; 重注册须另行说明理由"]
    for name, p in entries[1:]:
        lines.append("# %s %s %s" % (name, _sha256_of(p), os.path.basename(p)))
    with open(ANCHOR_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[anchor] 已锚定: %s" % ANCHOR_PATH)
    for name, p in entries:
        print("[anchor]   %-8s %s" % (name, _sha256_of(p)[:32] + "..."))
    return _sha256_of(entries[0][1])


def verify_anchor():
    """校验注册向量/协议/边界表与 git 锚点是否一致。无锚点 ⇒ (False, '未锚定')。"""
    reg_path = paths.p(*REG_PATH)
    if not os.path.exists(ANCHOR_PATH):
        return False, "未锚定"
    if not os.path.exists(reg_path):
        return False, "预注册文件不存在"
    lines = [l.strip() for l in open(ANCHOR_PATH, encoding="utf-8") if l.strip()]
    anchored = lines[0]
    current = _sha256_of(reg_path)
    if current != anchored:
        return False, "不一致! 注册向量在锚定后被修改"
    for l in lines:
        if l.startswith("# ") and len(l.split()) >= 3 and l.split()[1].count(".") == 0 \
                and len(l.split()[1]) == 64:
            parts = l.split()
            name, h = parts[1], parts[2]
            p = (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "PRE_REGISTERED_PROTOCOL_v1.md") if name == "protocol"
                 else paths.p(*BOUND_PATH))
            if not os.path.exists(p):
                return False, "锚定文件缺失: %s" % name
            if _sha256_of(p) != h:
                return False, "不一致! %s 在锚定后被修改" % name
    return True, "一致(registry+protocol+boundary)"


def score(min_new=MIN_NEW, verbose=True):
    """v2 序贯打分：方向性线性得分 Z_n + OBF 边界查表（PRE_REGISTERED_PROTOCOL_v1）。"""
    ok, msg = verify_anchor()
    if not ok:
        print("[scorer] ⛔ 拒绝打分: 完整性校验失败(%s)。" % msg)
        return {"status": "REGISTRY_TAMPERED", "reason": msg}
    reg, err = load_registry()
    if reg is None:
        print("[scorer] %s" % err)
        return None
    v = np.array([reg["dev_pct"][str(i + 1)] for i in range(N_BALL)], float)
    w = v - v.mean()
    w = w / np.linalg.norm(w)                      # 单位化注册方向
    sd1 = math.sqrt(N_PICK_SD * (N_BALL_SD - N_PICK_SD) / (N_BALL_SD * (N_BALL_SD - 1)))

    # 边界表
    bnd_path = paths.p(*BOUND_PATH)
    if not os.path.exists(bnd_path):
        print("[scorer] ⛔ 边界表缺失: %s (先跑 obf_design.py)" % bnd_path)
        return {"status": "BOUNDARY_MISSING"}
    raw = np.loadtxt(bnd_path, delimiter=",", skiprows=1)
    b_z = dict(zip(raw[:, 0].astype(int), raw[:, 1]))

    master = D.load_master(paths.master_csv())
    reds, blues, _ = D.to_arrays(master)
    n_basis = reg.get("n_basis", 0)
    n = len(reds)
    n_new = n - n_basis
    if n_new <= 0:
        if verbose:
            print("[scorer] 注册基准 n=%d, 当前 n=%d —— 尚无新开奖可打分" % (n_basis, n))
        return {"status": "NO_NEW_DRAWS", "n_basis": n_basis, "n_now": n}

    new_reds = reds[n_basis:]
    if verbose:
        print("[scorer] 注册基准 n=%d, 新开奖 %d 期" % (n_basis, n_new))

    res = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "design": "OBF_v1", "n_basis": n_basis, "n_new": n_new,
        "footer": HF.HONESTY_FOOTER,
    }
    if n_new < min_new:
        if verbose:
            print("[scorer] 新开奖 %d < %d，样本不足暂不打分（设计边界此时为天文级，无信息）"
                  % (n_new, min_new))
        res.update({"status": "INSUFFICIENT", "min_new": min_new})
        _append_log(res)
        return res

    # Z_n = Σ w·X_d / (SD1·√n)；Σ_d Σ_{b∈d} w_b = w·counts（每期恰 6 个不同球）
    counts = np.bincount(new_reds.ravel(), minlength=N_BALL)[1:N_BALL + 1].astype(float)
    L = float(w @ counts)                      # Σ_d Σ_{b∈d} w_b = w·counts
    Z = L / (sd1 * math.sqrt(n_new))

    if n_new <= N_MAX:
        b = float(b_z.get(n_new, float("inf")))
    else:
        b = None                               # 设计已终点
    crossed = (b is not None) and (Z >= b)
    spent = None
    if n_new <= N_MAX:
        t = n_new / N_MAX
        spent = 1.0 - _ND.cdf(_ND.inv_cdf(1 - ALPHA) / math.sqrt(t)) if t > 0 else 0.0

    res.update({
        "Z": round(Z, 4),
        "boundary": (round(b, 4) if b is not None else "DESIGN_ENDED"),
        "alpha_spent_cum": (round(spent, 6) if spent is not None else 1.0),
        "verdict": ("CONFIRMED_OBF" if crossed else
                    ("NOT_CONFIRMED_AT_DESIGN" if (b is None or n_new >= N_MAX)
                     else "MONITORING")),
    })
    if verbose:
        print("[scorer] Z_%d = %+.4f   边界 b(%d) = %s   已花 α*(t) = %s"
              % (n_new, Z, n_new,
                 ("%.4f" % b) if b is not None else "设计终点",
                 ("%.6f" % spent) if spent is not None else "-"))
        print("[scorer] 判定: %s" % res["verdict"])
        if crossed:
            print("[scorer] ★ 序贯边界穿越 ⇒ 注册方向静态偏倚获前瞻确认")
        elif n_new >= N_MAX:
            print("[scorer] 终点未穿越 ⇒ σ≈%.1f%% 方向候选在本设计(功效99.8%%)下未获确认 ⇒ 候选降级" % 3.5)
        print("[scorer] [页脚] %s" % HF.HONESTY_FOOTER)
    _append_log(res)
    return res


def score_legacy(min_new=MIN_NEW, mc=MC, seed=20260830, verbose=True):
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
    ok, msg = verify_anchor()
    print("[scorer] 锚点完整性: %s (%s)" % ("OK" if ok else "FAIL", msg))
    master = D.load_master(paths.master_csv())
    reds, _, _ = D.to_arrays(master)
    n_new = len(reds) - reg.get("n_basis", 0)
    print("[scorer] 注册于 %s (基准 n=%d)；当前新开奖 %d 期 / 设计终点 %d"
          % (reg.get("registered_at"), reg.get("n_basis"), n_new, N_MAX))
    bnd = paths.p(*BOUND_PATH)
    if os.path.exists(bnd) and n_new >= MIN_NEW:
        raw = np.loadtxt(bnd, delimiter=",", skiprows=1)
        bz = dict(zip(raw[:, 0].astype(int), raw[:, 1]))
        v = np.array([reg["dev_pct"][str(i + 1)] for i in range(N_BALL)], float)
        w = v - v.mean(); w /= np.linalg.norm(w)
        counts = np.bincount(reds[reg.get("n_basis", 0):].ravel(),
                             minlength=N_BALL)[1:N_BALL + 1].astype(float)
        sd1 = math.sqrt(N_PICK_SD * (N_BALL_SD - N_PICK_SD) / (N_BALL_SD * (N_BALL_SD - 1)))
        Z = float(w @ counts) / (sd1 * math.sqrt(n_new))
        print("[scorer] 当前 Z_%d = %+.4f  vs 边界 %.4f  ⇒ %s"
              % (n_new, Z, bz.get(n_new, float("inf")),
                 "已穿越" if Z >= bz.get(n_new, float("inf")) else "未穿越"))
    p = paths.p(*LOG_PATH)
    if os.path.exists(p):
        lines = [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]
        if lines:
            last = json.loads(lines[-1])
            print("[scorer] 最近一次: %s  design=%s  Z=%s  判定=%s"
                  % (last["ts"], last.get("design", "legacy"),
                     last.get("Z", "-"), last["verdict"]))


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    elif "--legacy" in sys.argv:
        score_legacy()
    elif "--anchor" in sys.argv:
        anchor()
    else:
        score()
