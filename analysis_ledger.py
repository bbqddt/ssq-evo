"""分析账本（Analysis Ledger）—— 研究者自由度的结构性对策。

解决的漏洞
----------
2026-08-29/30 发现的最大未解决漏洞：**研究者自由度未被校正**。
联合 min-p (0.0167) 只校正了"最终选定的 3 个统计量之间的相关性"，
没有校正"从 χ² / K 扫描 / 连续vs随机分段 / 70-30 切分 / lag 分析 /
prequential / 油墨代理 / 相邻共现 等众多变体中挑出它们"的过程。

每一次"试一种统计量"都是一次假阳性机会。不记账，就无法校正；
无法校正，"首选解释=统计波动"就永远只是口头评估。

机制
----
1. 在历史（发现段）数据上每跑一个假设检验，调用 `log()` 记一笔。
2. `effective_families()` 给出已消耗的检验族数 df。
3. `dof_corrected_p()` 用 Bonferroni 把原始 p 乘以 df。
   （Bonferroni 对**相关**检验是保守上界：真实校正后的 p 落在
   [原始 p, Bonferroni p] 区间内——相关越强越靠近下界。）
4. **关键约束：账本只能追加，不能删改。** 事后删条目 = 洗掉假阳性机会。

诚实规则
--------
- 账本覆盖**历史发现段数据**上的检验。前瞻（打分器）数据不在账本内——
  前瞻用 peeking 计数（preregistered_scorer 已实现），两套账分开。
- 回填（backfill）是诚实的：这些检验确实跑过，漏记比多记更糟。
"""

import json
import os
from datetime import datetime

import honesty_footer as HF
import paths

LEDGER_PATH = paths.p("audit", "analysis_ledger.jsonl")


def log(test_family, variant, p_value, module="", note=""):
    """在历史发现段数据上跑过一个假设检验 ⇒ 记一笔。只追加。"""
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "test_family": test_family,
        "variant": variant,
        "p_value": (round(float(p_value), 6) if p_value is not None else None),
        "module": module,
        "note": note,
    }
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def entries():
    if not os.path.exists(LEDGER_PATH):
        return []
    out = []
    for line in open(LEDGER_PATH, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def effective_families():
    """已消耗的**检验族**数（同一族的不同变体算一族——变体间高度相关）。"""
    fams = []
    for e in entries():
        if e["test_family"] not in fams:
            fams.append(e["test_family"])
    return len(fams), fams


def dof_corrected_p(p_value):
    """Bonferroni 校正（对相关检验是保守上界）。

    返回 dict：raw / df / corrected / interval。
    真实校正后的 p 落在 [raw, corrected] 内——检验间相关越强越靠近下界。
    """
    df, fams = effective_families()
    df = max(1, df)
    return {"raw": float(p_value), "df": df,
            "corrected": float(min(1.0, p_value * df)),
            "interval": "[raw, raw*df]（相关越强越靠近下界）",
            "families": fams}


# ---------------------------------------------------------------------------
# 回填：2026-08-29/30 实际跑过的检验族（漏记比多记更糟）
# ---------------------------------------------------------------------------
BACKFILL = [
    ("红球边际离散(χ²)", "M=400 蒙特卡洛零假设", 0.0188),
    ("K段持续性相关", "K=4", 0.0188),
    ("K段持续性相关", "K=6", 0.0438),
    ("K段持续性相关", "K=8", 0.0087),
    ("K段持续性相关", "K=12", 0.0112),
    ("K段持续性相关", "K=16", 0.0138),
    ("K段持续性相关", "K=24", 0.0238),
    ("连续vs随机分段", "连续0.0885/随机0.0614", 0.0338),
    ("样本外70/30相关", "前70%估→后30%验", 0.0988),
    ("顺序预测prequential", "θ网格(1e4,3e4,1e5), M=60", 0.0250),
    ("分段同质性(状态结构)", "n_seg=4, M=400置换", 0.7262),
    ("位置边际", "次序统计量零假设, M=400", 0.3712),
    ("油墨位数代理", "偏差vs位数, M=400", 0.1020),
    ("油墨7段面积代理", "偏差vs7段面积, M=400", 0.1080),
    ("相邻号码共现", "32对χ², M=400", 0.9487),
    ("蓝球边际(χ²)", "M=400", 0.8862),
    ("蓝球顺序预测", "M=100", 0.8850),
    ("蓝球样本外相关", "前70%→后30%", 0.9365),
]


def backfill_if_empty():
    """账本为空时回填两天实际跑过的检验族（幂等：非空则不动）。"""
    if entries():
        return False
    for fam, var, p in BACKFILL:
        log(fam, var, p, module="backfill-20260829/30",
            note="2026-08-29/30 会话中实际执行的检验")
    return True


def report():
    """出具研究者自由度报告：对最强单检验结果做 df 校正。"""
    backfill_if_empty()
    df, fams = effective_families()
    # 全账本中最强的 p（同一族取最小变体 p——族内变体高度相关）
    best = None
    by_fam = {}
    for e in entries():
        if e["p_value"] is None:
            continue
        f = e["test_family"]
        if f not in by_fam or e["p_value"] < by_fam[f]:
            by_fam[f] = e["p_value"]
    best_fam = min(by_fam, key=by_fam.get)
    best_p = by_fam[best_fam]
    corr = dof_corrected_p(best_p)
    out = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "df_families": df,
        "family_list": fams,
        "best_family": best_fam, "best_raw_p": best_p,
        "bonferroni_corrected_p": corr["corrected"],
        "interpretation_interval": corr["interval"],
        "verdict": ("df 校正后仍显著(<0.05)" if corr["corrected"] < 0.05
                    else "df 校正后**不再显著**(≥0.05) —— 统计波动解释进一步强化"),
        "footer": HF.HONESTY_FOOTER,
    }
    return out, corr


if __name__ == "__main__":
    out, corr = report()
    print("=" * 66)
    print("研究者自由度报告（分析账本）")
    print("=" * 66)
    print("已消耗检验族数 df = %d" % out["df_families"])
    for f in out["family_list"]:
        print("   - %s" % f)
    print()
    print("全账本最强单检验: %s  原始 p = %.4f" % (out["best_family"], out["best_raw_p"]))
    print("Bonferroni(df=%d) 校正后 p = %.4f" % (out["df_families"], out["bonferroni_corrected_p"]))
    print("真实校正后 p 的区间: %s" % out["interpretation_interval"])
    print("⇒ %s" % out["verdict"])
    print()
    print("[页脚] %s" % HF.HONESTY_FOOTER)
    json.dump(out, open(paths.p("audit", "analysis_ledger_report.json"), "w",
                        encoding="utf-8"), indent=2, ensure_ascii=False)
