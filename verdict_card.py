"""判决卡（verdict card）—— 单一事实来源，注入 daily_digest 每轮固定字段。

诚实要求（2026-08-29/30 全部实证结论的凝练，改这里前先对账 audit/*.json）
------------------------------------------------------------------------
1. 判决 = ARTIFACT_SUSPECTED（疑似伪影）。**"疑似" ≠ "已证伪"**：
   红球频率表的超额离散与跨时段持续性是数据的真实性质，机制未明。
2. **蓝球 p=0.886 不能当证伪**：其功效仅 12%（σ=3.5%），低功效未检出不携带信息。
3. Fisher 合并非法（三统计量零假设下 r=+0.997）；
   联合 p 只能用 min-p 蒙特卡洛 = 0.0167（Bonferroni×3 = 0.050）。
4. 联合 p **未校正研究者自由度**（从众多变体中挑出这 3 个统计量的过程），
   真实证据强度 ≤ 0.0167。
5. 禁止措辞："全域 NULL 确立""彻底终结""没有第三种可能"——
   第三种解释（统计波动/多重比较）在四机制测试全阴后已是**首选**。
6. 前瞻判据 = `preregistered_scorer.py`（peeking 加 Bonferroni 记账），
   **不得**使用"连续 N 期 p>0.5"这类未标定规则
   （校准闸门下该事件概率 0.5^N≈10⁻⁶，观测到它说明闸门失准而非域 null）。
7. 降权而非屏蔽：bias_corrector 的降权幅度须由证据推导，
   **不得**拍常数（那是"看起来合理的阈值"陷阱）。
"""

import json
import os

import honesty_footer as HF
import paths

CARD_PATH = paths.p("audit", "verdict_card.json")

CARD = {
    "verdict": "ARTIFACT_SUSPECTED",
    "verdict_caveat": ("疑似伪影 ≠ 已证伪：红球频率表的超额离散与跨时段持续性"
                       "是数据的真实性质，机制未明"),
    "evidence_chain": [
        "红球频率超额离散（χ²=45.0，蒙特卡洛零假设 26.71±6.26，秩 p=0.0233）；"
        "预测增益 +0.000659 nats/期 = 理论上限(0.003675)的 18%",
        "三统计量高度冗余：零假设下 corr(χ², prequentialΔ)=+0.997、corr(χ²,OOS相关)=+0.683"
        " ⇒ 同一张红球频率计数表的三个切面，Fisher 合并(p=0.0011)非法已撤回",
        "蓝球：χ² p=0.886、prequential p=0.885——但其功效仅 12%（槽位 3496=红球 1/6，"
        "零假设相对 sd 6.55% vs 3.91%），**未检出 ≠ 证伪**，不携带方向性信息",
        "无状态结构：分段同质性 χ²=74.23 vs 81.52±11.60（置换 p=0.7262），"
        "且该闸门经对照验证（σ=8%→93%、σ=15%→100%）⇒ HDP-HMM 建模对象不存在",
        "物理量纲不符：3.5% 频率离散需 σ_θ>3.5% 或 c≥3.5，"
        "远超彩票球制造公差 0.1–1%（c 未标定，非决定性）",
        "四机制测试全阴：位置边际 p=0.371（次序统计量零假设）、油墨代理双侧 p≈0.10、"
        "相邻共现 p=0.949、蓝球 prequential p=0.885",
        "唯一方向性信号：1位数球 +2.17% vs 2位数球 −0.81%（与'油墨重→少被搅起'一致），"
        "双侧 p≈0.10 未显著、代理粗糙、所需 c 仍与公差冲突——如实记录，不作证据引用",
    ],
    "statistical_honesty": {
        "joint_p_monte_carlo": 0.0167,
        "joint_p_bonferroni_x3": 0.050,
        "bh_fdr_q_of_chi2": 0.0189,
        "researcher_dof_uncorrected": True,
        "note": "联合 p 未校正'从众多变体中挑出这 3 个统计量'的研究者自由度，"
                "真实证据强度 ≤ 0.0167",
        "preferred_explanation": "统计波动/多重比较（四机制测试全阴后升至首选）",
        "fertility_note": "不存在'没有第三种可能'——随机波动即第三种",
    },
    "decision": "不下注",
    "decision_reason": ("缺失质量 95% 下界 0.9586 ⇒ 最坏效用 < 0；"
                        "预测增益仅理论上限 18%，无法转化为正期望收益"),
    "remaining_paths": [
        {"path": "预注册向量前瞻打分", "cost": "零成本",
         "instrument": "preregistered_scorer.py（peeking+Bonferroni 记账）",
         "note": "禁止使用'连续 N 期 p>0.5'类未标定规则：校准闸门下"
                 "P(连续20期>0.5)=0.5^20≈1e-6，观测到它说明闸门失准而非域 null"},
        {"path": "物理测量球体（称重/测直径/测圆度）", "cost": "数周",
         "instrument": "audit/PHYSICAL_MEASUREMENT_PROTOCOL.md",
         "note": "唯一能同时标定灵敏度 c 与 σ_θ 的路径；测量者须对统计向量盲态"},
    ],
    "bias_corrector_action": {
        "action": "降权红球频率族，不屏蔽",
        "novelty_tilt": "TBD——须由证据推导（如按联合 p 映射），禁止拍常数",
        "elite_bias": "TBD——同上",
        "note": "屏蔽 = 判决；降权 = 偏置修正。基于未证实假设硬屏蔽信号族，"
                "假设错了会漏掉真结构",
    },
    "null_wording_rule": ("唯一合法表述：'当前数据 + 当前闸门 + 当前假设类 未检出'。"
                          "禁止'全域 NULL 确立''彻底终结''没有第三种可能'"),
    "footer": HF.HONESTY_FOOTER,
}


def load():
    """读卡（若 audit/verdict_card.json 存在则以落盘版为准，保证单一事实来源）。"""
    if os.path.exists(CARD_PATH):
        return json.load(open(CARD_PATH, encoding="utf-8"))
    return CARD


def save():
    os.makedirs(os.path.dirname(CARD_PATH), exist_ok=True)
    json.dump(CARD, open(CARD_PATH, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    return CARD_PATH


if __name__ == "__main__":
    p = save()
    print("[verdict_card] 已写: %s" % p)
    print("[verdict_card] 判决 = %s" % CARD["verdict"])
    print("[页脚] %s" % HF.HONESTY_FOOTER)
