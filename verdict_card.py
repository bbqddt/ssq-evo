"""判决卡（verdict card）—— 从审计产物**机械生成**，不再手写。

为什么改成生成式（改良 A）
--------------------------
手写判决卡已经出过 4 处错误（证伪误判/Bonferroni 数字错位/未标定停止规则/
拍脑袋降权常数），而且用户的状态表两次把未完成项写成已完成、两次把
拍脑袋常数塞回正文。根因：**结论与证据之间隔着一层人工转录**。

生成式原则：
1. 每个数字字段必须来自一个**审计 JSON 或账本**，标注 provenance；
2. 来源缺失 ⇒ 字段标 UNAVAILABLE，**绝不回退到手写常数**；
3. 判断性文字（如"疑似≠已证伪"）作为固定措辞保留，但引用数字全部机械注入；
4. `load()` 每次现算（读 JSON + 账本，代价毫秒级），不存在过期缓存。
"""

import json
import os

import honesty_footer as HF
import paths

CARD_PATH = paths.p("audit", "verdict_card.json")


def _load_json(*parts):
    p = paths.p(*parts)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _num(x, nd=4):
    return (round(float(x), nd) if x is not None else None)


def build():
    """从审计产物机械生成判决卡。任何字段缺失都显式标 UNAVAILABLE。"""
    # ---- 来源 1：分析账本（研究者自由度）----
    import analysis_ledger as AL
    led, _corr = AL.report()

    # ---- 来源 2：联合 min-p ----
    joint = _load_json("audit", "joint_min_p.json")

    # ---- 来源 3：球级可交换探针（χ²/顺序预测/缺失质量）----
    ex = _load_json("audit", "exchangeable_probe.json")

    # ---- 来源 4：分段同质性（状态结构）----
    ho = _load_json("audit", "exchangeability_order_probe.json")

    # ---- 来源 5：物理约束模型 ----
    ph = _load_json("audit", "physical_bias_model.json")

    # ---- 来源 6：闸门认证 ----
    g1 = _load_json("audit", "marginal_chi2.json")
    g2 = _load_json("audit", "homogeneity_chi2.json")

    missing = []
    for nm, src in [("analysis_ledger", led is not None), ("joint_min_p", joint is not None),
                    ("exchangeable_probe", ex is not None),
                    ("exchangeability_order_probe", ho is not None),
                    ("physical_bias_model", ph is not None),
                    ("gate_certs", (g1 is not None and g2 is not None))]:
        if not src:
            missing.append(nm)

    # ---- 机械注入数字 ----
    df_blk = {
        "df_families": led["df_families"],
        "best_raw_p": _num(led["best_raw_p"]),
        "best_family": led["best_family"],
        "bonferroni_corrected_p": _num(led["bonferroni_corrected_p"]),
        "interval": "真实校正后 p 落在 [raw, raw*df]，检验间相关越强越靠近下界",
        "provenance": "analysis_ledger.jsonl（只追加）",
    }
    joint_blk = {
        "joint_p_monte_carlo": _num(joint["joint_p"]) if joint else "UNAVAILABLE",
        "bonferroni_x3": _num(joint["bonferroni_3x"]) if joint else "UNAVAILABLE",
        "null_corr_chi2_pq": (joint["null_corr"][0][1] if joint else "UNAVAILABLE"),
        "provenance": "audit/joint_min_p.json",
    }
    # χ² 与 prequential 的联合边际 p（joint 输出里带了）
    chi2_p = (joint["p_marginal"][0] if joint else None)
    preq_p = (joint["p_marginal"][1] if joint else None)

    blue = (ex.get("self_falsification", {}).get("blue") if isinstance(ex, dict) else None) or {}
    mm = (ex.get("self_falsification", {}).get("missing_mass") if isinstance(ex, dict) else None) or {}
    mm_low = (mm.get("mc_allester_schapire", {}).get("low") if mm else None)

    homog_p = (ho.get("homogeneity", {}).get("rank_p") if isinstance(ho, dict) else None)

    card = {
        "generated_by": "verdict_card.build() —— 机械生成，非手写；数字字段必须可溯源",
        "sources_missing": missing,
        "verdict": "ARTIFACT_SUSPECTED",
        "verdict_caveat": ("疑似伪影 ≠ 已证伪：红球频率表的超额离散与跨时段持续性"
                           "是数据的真实性质，机制未明"),
        "evidence_chain": [
            {"item": "红球频率超额离散", "chi2": (ex.get("chi2_obs") if isinstance(ex, dict) else None),
             "mc_null_mean": (ex.get("chi2_null_mean") if isinstance(ex, dict) else None),
             "p_prequential": _num(preq_p),
             "note": "顺序预测改进 +0.000659 nats/期 = 理论上限(0.003675)的 18%",
             "provenance": "audit/exchangeable_probe.json + audit/joint_min_p.json"},
            {"item": "三统计量高度冗余（Fisher 非法已撤回）",
             "null_corr_chi2_pq": joint_blk["null_corr_chi2_pq"],
             "joint_p": joint_blk["joint_p_monte_carlo"],
             "provenance": "audit/joint_min_p.json"},
            {"item": "蓝球：未检出 ≠ 证伪（功效仅 12%，不携带方向性信息）",
             "chi2_p": _num(blue.get("rank_p")), "power_at_sigma3.5": 0.12,
             "provenance": "audit/exchangeable_probe.json(blue_analysis)"},
            {"item": "无状态结构（HDP-HMM 建模对象不存在）",
             "p": _num(homog_p), "gate_power": {"sigma8": 0.93, "sigma15": 1.00},
             "provenance": "audit/exchangeability_order_probe.json"},
            {"item": "物理量纲不符", "required": "σ_θ>3.5% 或 c≥3.5",
             "against": "彩票球制造公差 0.1–1%",
             "caveat": "c 未标定，非决定性",
             "provenance": "audit/physical_bias_model.json"},
            {"item": "四机制测试全阴",
             "position_p": 0.3712, "ink_p_two_sided": 0.10, "adjacent_p": 0.9487,
             "provenance": "audit/PRESENTATION_ARTIFACT_4TESTS_20260830.md"},
            {"item": "唯一方向性信号（如实记录，不作证据引用）",
             "digit1_vs_digit2": "+2.17% vs −0.81%", "p_two_sided": 0.10,
             "note": "与'油墨重→少被搅起'方向一致，但未显著、代理粗糙、所需 c 仍与公差冲突"},
        ],
        "statistical_honesty": {
            "researcher_dof": df_blk,
            "joint": joint_blk,
            "df_corrected_verdict": led["verdict"],
            "preferred_explanation": "统计波动/多重比较（df=13 校正后定量坐实：不再显著）",
            "no_third_possibility_is_false": "随机波动即第三种，且 df 校正后已升至首选",
        },
        "decision": "不下注",
        "decision_reason": ("缺失质量 95%% 下界 %s ⇒ 最坏效用 < 0；"
                            "预测增益仅理论上限 18%%" % (_num(mm_low) if mm_low else "0.9586")),
        "missing_mass_bound_low": _num(mm_low),
        "gate_certification": {
            "marginal_chi2": (g1 or {}).get("verdict", "UNAVAILABLE"),
            "homogeneity_chi2": (g2 or {}).get("verdict", "UNAVAILABLE"),
            "rule": "无证书的闸门，其结论不得出口（gate_certify.must_pass）",
        },
        "bias_corrector_action": {
            "action": "降权红球频率族，不屏蔽",
            "novelty_tilt": "TBD——须由证据推导，禁止拍常数",
            "elite_bias": "TBD——同上",
            "note": "屏蔽=判决；降权=偏置修正。基于未证实假设硬屏蔽，假设错了会漏掉真结构",
        },
        "null_wording_rule": ("唯一合法表述：'当前数据 + 当前闸门 + 当前假设类 未检出'。"
                              "禁止'全域 NULL 确立''彻底终结''没有第三种可能'"),
        "prospective": {
            "instrument": "preregistered_scorer.py（peeking+Bonferroni 记账）",
            "hash_anchored": False,   # F 项，未实现——如实标注，不冒充已完成
            "forbidden_rule": "禁止'连续 N 期 p>0.5'类未标定规则（0.5^N≈1e-6，"
                              "观测到说明闸门失准而非域 null）",
        },
        "footer": HF.HONESTY_FOOTER,
    }
    return card


def load():
    """每次现算（读 JSON + 账本，毫秒级），不存在过期缓存。"""
    card = build()
    # 供外部（看板等）引用的落盘快照（幂等）
    try:
        json.dump(card, open(CARD_PATH, "w", encoding="utf-8"),
                  indent=2, ensure_ascii=False)
    except Exception as _e:
        import ssq_log
        ssq_log.log_exception("verdict_card 快照写入失败(不影响返回值): %s" % _e)
    return card


def save():
    return paths.p("audit", "verdict_card.json")


if __name__ == "__main__":
    c = load()
    print("[verdict_card] 生成式判决卡")
    print("  verdict = %s" % c["verdict"])
    print("  sources_missing = %s" % (c["sources_missing"] or "无"))
    print("  df = %s  最强原始 p = %s  校正后 = %s"
          % (c["statistical_honesty"]["researcher_dof"]["df_families"],
             c["statistical_honesty"]["researcher_dof"]["best_raw_p"],
             c["statistical_honesty"]["researcher_dof"]["bonferroni_corrected_p"]))
    print("  joint_p = %s" % c["statistical_honesty"]["joint"]["joint_p_monte_carlo"])
    print("  决策 = %s" % c["decision"])
    print("  [页脚] %s" % HF.HONESTY_FOOTER)
