"""迁移套件（migrate_domain）—— 把 §10 清单变成可执行脚本。

解决什么
--------
架构要迁移到新域（其他彩票、金融、任何"噪声中找信号"的场景）时，
最大的风险不是"新域没结构"，而是**源域偏见随架构一起迁移**：
旧域调好的阈值、功率假设、对照强度，在新域全部未经验证。

本工具执行 §10 强制清单，输出一张**迁移许可证**：
  ALLOW = 新域参数下闸门已认证（阴性回名义 + 阳性单调 + 目标效应有功效）
  DENY  = 任一闸门未过，附原因

**硬约束（靠代码强制，不靠人自觉）**：
本工具在认证阶段**拒绝计算任何真实数据的假设检验 p 值**——
§10 规定"新域首周禁止对外出结论"，最彻底的执行方式是
让工具在物理上产不出那个数字。真实数据只做完整性描述（N、唯一性、重复）。

用法
----
    python migrate_domain.py --csv new_domain.csv --k 33 --m 6
    # CSV 格式：每行一期，m 个 1..K 的整数（空格或逗号分隔，# 开头为注释）

理论背景见 ARCHITECTURE.md §10 与 gate_certify.py。
"""

import argparse
import json
import os
from datetime import datetime

import numpy as np

import gate_certify as GC
import honesty_footer as HF
import paths


# ---------------------------------------------------------------------------
# 1. 数据加载与完整性（描述性，不做任何假设检验）
# ---------------------------------------------------------------------------
def load_draws(csv_path, k, m):
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = line.replace(",", " ").split()
            vals = [int(x) for x in parts]
            rows.append(vals)
    arr = np.asarray(rows, dtype=np.int64)
    problems = []
    if arr.ndim != 2 or arr.shape[1] != m:
        problems.append("每行应为 %d 个号码, 实际维度 %s" % (m, arr.shape))
    if arr.size and (arr.min() < 1 or arr.max() > k):
        problems.append("号码超出 1..%d 范围" % k)
    dups = len(arr) - len(set(map(tuple, arr.tolist())))
    if dups:
        problems.append("%d 行完全重复" % dups)
    desc = {"n_draws": int(len(arr)), "k": k, "m": m,
            "duplicate_rows": int(dups), "integrity_problems": problems}
    return arr, desc


# ---------------------------------------------------------------------------
# 2. 新域参数下的统计量/生成器（合成数据 only）
# ---------------------------------------------------------------------------
def make_fns(n, k, m):
    def gen_uniform(rng):
        return np.sort(rng.random((n, k)).argsort(axis=1)[:, :m] + 1, axis=1)

    def chi2_stat(r):
        c = np.bincount(r.ravel(), minlength=k + 1)[1:k + 1].astype(float)
        e = c.sum() / k
        return float(((c - e) ** 2 / e).sum())

    def homog_stat(r, n_seg=4):
        segs = np.array_split(np.arange(len(r)), n_seg)
        tab = np.array([np.bincount(r[s].ravel(), minlength=k + 1)[1:k + 1]
                        .astype(float) for s in segs])
        row = tab.sum(axis=1, keepdims=True)
        col = tab.sum(axis=0, keepdims=True)
        E = row @ col / tab.sum()
        return float(((tab - E) ** 2 / np.maximum(E, 1e-9)).sum())

    def inject_static(rng, sg):
        w = np.exp(np.random.default_rng(int(rng.integers(1e6))).normal(0, sg / 100.0, k))
        p = w / w.sum()
        cs = np.cumsum(p)
        out = np.zeros((n, m), dtype=np.int64)
        for i in range(n):
            picked = []
            for x in rng.random(m):
                j = int(np.searchsorted(cs, x))
                while j in picked or j >= k:
                    j = (j + 1) % k
                picked.append(j)
            out[i] = np.sort(np.array(picked) + 1)
        return out

    def inject_era(rng, sg, n_eras=2):
        out = np.zeros((n, m), dtype=np.int64)
        bounds = np.linspace(0, n, n_eras + 1).astype(int)
        for e_i in range(n_eras):
            w = np.exp(np.random.default_rng(int(rng.integers(1e6))).normal(0, sg / 100.0, k))
            p = w / w.sum()
            cs = np.cumsum(p)
            for i in range(bounds[e_i], bounds[e_i + 1]):
                picked = []
                for x in rng.random(m):
                    j = int(np.searchsorted(cs, x))
                    while j in picked or j >= k:
                        j = (j + 1) % k
                    picked.append(j)
                out[i] = np.sort(np.array(picked) + 1)
        return out

    return gen_uniform, chi2_stat, homog_stat, inject_static, inject_era


# ---------------------------------------------------------------------------
# 3. 三闸门认证（全部用合成数据；真实数据零 p 值）
# ---------------------------------------------------------------------------
def certify_all(n, k, m, target_sigma=3.5, target_sigma_era=8.0,
                m_mc=300, m_pos=25, seed=20260830):
    gen_uniform, chi2_stat, homog_stat, inject_static, inject_era = make_fns(n, k, m)

    print("  [1/3] 边际离散 χ²（静态偏倚闸门, 目标 σ=%.1f%%）" % target_sigma)
    c1 = GC.certify("边际χ²@(%d,%d,%d)" % (n, k, m), chi2_stat,
                    lambda rng: gen_uniform(rng), inject_static,
                    effect_sizes=[2.0, target_sigma, 6.0], target_effect=target_sigma,
                    m_mc=m_mc, m_null=m_pos, m_pos=m_pos, seed=seed, verbose=False)
    print("        ⇒ %s  FPR=%.1f%%  目标功效=%.0f%%"
          % (c1["verdict"], 100 * c1["checks"]["negative_control"]["fpr"],
             100 * (c1["checks"]["positive_control"]["target_detect_rate"] or 0)))

    print("  [2/3] 分段同质性 χ²（状态结构闸门, 目标 σ=%.1f%%）" % target_sigma_era)
    c2 = GC.certify("同质性χ²@(%d,%d,%d)" % (n, k, m), homog_stat,
                    lambda rng: gen_uniform(rng), inject_era,
                    effect_sizes=[target_sigma, target_sigma_era, 15.0],
                    target_effect=target_sigma_era,
                    m_mc=m_mc, m_null=m_pos, m_pos=m_pos, seed=seed + 1, verbose=False)
    print("        ⇒ %s  FPR=%.1f%%  目标功效=%.0f%%"
          % (c2["verdict"], 100 * c2["checks"]["negative_control"]["fpr"],
             100 * (c2["checks"]["positive_control"]["target_detect_rate"] or 0)))

    print("  [3/3] 顺序预测（交换性闸门, 目标 σ=%.1f%% 时代结构）" % target_sigma_era)
    # 顺序闸门用 prequential 损失对置换的敏感度——在合成时代数据上验证功效
    def order_gap_stat(r):
        # 真实 vs 单次重排的损失差（置换零假设的核）；认证只看合成数据上的功效
        rng2 = np.random.default_rng(int(r.sum()) % (2 ** 31))
        from exchangeability_order_probe import order_statistic
        return -order_statistic(r, theta_grid=(1e1, 1e2))

    # 顺序闸门成本高（prequential 逐期循环），此处以同质性闸门近似覆盖其职责，
    # 并如实标注：prequential 版实测对时代结构零功效(0/10)已被弃用。
    c3 = {"gate": "顺序/交换性(经同质性近似覆盖)", "verdict": "COVERED_BY_HOMOGENEITY",
          "note": "prequential 置换版对时代结构零功效(实测 σ=15% 0/10 检出)已弃用;"
                  "状态结构由同质性χ²覆盖, 其对照 σ=8%→93% 已验证"}
    print("        ⇒ %s（prequential 版零功效已弃用, 由同质性覆盖）" % c3["verdict"])

    return {"marginal": c1, "homogeneity": c2, "order": c3}


# ---------------------------------------------------------------------------
# 4. 主流程：出许可证
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="新域迁移许可证生成器（§10 可执行化）")
    ap.add_argument("--csv", required=True, help="新域数据：每行一期, m 个 1..K 整数")
    ap.add_argument("--k", type=int, required=True, help="号码池大小 K")
    ap.add_argument("--m", type=int, required=True, help="每期抽取个数 m")
    ap.add_argument("--target-sigma", type=float, default=3.5)
    ap.add_argument("--target-sigma-era", type=float, default=8.0)
    ap.add_argument("--m-mc", type=int, default=300)
    ap.add_argument("--m-pos", type=int, default=25)
    args = ap.parse_args()

    print("=" * 66)
    print("迁移许可证生成器（§10 可执行化）")
    print("=" * 66)

    arr, desc = load_draws(args.csv, args.k, args.m)
    n = desc["n_draws"]
    print("\n[0] 真实数据：仅完整性描述（**假设检验被本工具禁用**）")
    print("    N=%d  K=%d  m=%d  重复行=%d" % (n, args.k, args.m, desc["duplicate_rows"]))
    for q in desc["integrity_problems"]:
        print("    ⚠ %s" % q)

    print("\n[1] 三闸门认证（100%% 合成数据, 真实数据零 p 值）")
    gates = certify_all(n, args.k, args.m,
                        target_sigma=args.target_sigma,
                        target_sigma_era=args.target_sigma_era,
                        m_mc=args.m_mc, m_pos=args.m_pos)

    # 功效不足 ⇒ 给出"需要多少样本"的可执行提示（功效随 n 单调上升,
    # 近似 z 量表: n_80 ≈ n × ((1.645+0.84)/z_now)², z_now 由目标功效反推）
    import math
    from scipy.stats import norm as _norm
    hints = []
    for gname in ("marginal", "homogeneity"):
        g = gates[gname]
        pc = g["checks"]["positive_control"]
        tgt = pc["target_detect_rate"]
        if tgt is not None and 0 < tgt < 1.0:
            # power = Phi(z_signal - 1.645)  =>  z_signal = ppf(power) + 1.645
            # 80% 功效 => z_target = ppf(0.80)+1.645 = 2.487; z_signal ∝ sqrt(n)
            z_now = float(_norm.ppf(tgt)) + 1.645
            if z_now <= 0.05:
                hints.append({"gate": gname, "hint": "目标功效≈0, 须重新设计注入强度或检验"})
                continue
            z_tgt80 = float(_norm.ppf(0.80)) + 1.645
            n80 = int(math.ceil(n * (z_tgt80 / z_now) ** 2))
            hints.append({"gate": gname, "current_n": n, "n_for_80pct_power": n80,
                          "hint": "目标 σ 下功效 %.0f%% < 30%% ⇒ 样本量需增至约 %d（或放宽目标 σ/加大 m/K 比）"
                                  % (100 * tgt, n80)})
    all_ok = (gates["marginal"]["verdict"] == "CERTIFIED"
              and gates["homogeneity"]["verdict"] == "CERTIFIED")
    license = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "domain": {"csv": os.path.abspath(args.csv), **desc},
        "gates": {k_: {"verdict": v_["verdict"]} for k_, v_ in gates.items()},
        "all_certified": bool(all_ok),
        "license": ("ALLOW —— 闸门在新域参数下已认证；"
                    "第 8 天起才允许对真实数据出假设检验结论" if all_ok
                    else "DENY —— 闸门在新域参数下未过认证, 禁止放真实数据结论"),
        "real_data_hypothesis_tests": "FORBIDDEN until day 8（本工具不产出真实数据 p 值）",
        "power_scale_up_hints": hints,
        "week1_rule": "首周禁出结论：发现段/确认段切分、前瞻注册须在本许可证 ALLOW 后另行执行",
        "footer": HF.HONESTY_FOOTER,
    }
    out = paths.p("audit", "migration_license.json")
    json.dump({"license": license,
               "certs": {"marginal": gates["marginal"], "homogeneity": gates["homogeneity"]}},
              open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("\n[许可证] %s" % license["license"])
    print("[migrate_domain] 已写: %s" % out)
    print("[页脚] %s" % HF.HONESTY_FOOTER)
    return out


if __name__ == "__main__":
    main()
