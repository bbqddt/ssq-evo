"""诚实页脚 —— 单一事实来源（single source of truth）。

用途
----
本项目所有对外出口（看板、预注册文件、审计报告、digest）**必须**带上同一句页脚，
避免出现"某处写着找到结构、另一处写着只是候选"的口径分裂。

为什么需要这句页脚
------------------
即便最终确认存在 σ≈3.5% 的边际偏倚，它对头奖概率的影响也是**可忽略量级**：
双色球头奖概率 = 1 / (C(33,6) × 16) = 1 / 17,721,088 ≈ 1/1772 万。
边际频率的百分比级偏移，无法跨越组合爆炸造成的概率鸿沟。

**结构 ≠ 印钞机。** 任何把"发现统计结构"暗示成"可以盈利"的表述都是误导，
必须被这句页脚当场拦下。

用法
----
    import honesty_footer as HF
    lines.append(HF.footer_md())      # Markdown 引用块
    payload["footer"] = HF.HONESTY_FOOTER   # JSON 字段

修改本文件即全站同步；不要在别处复制这句话（复制会漂移）。
"""

HONESTY_FOOTER = (
    "即便确认 σ≈3.5% 边际偏倚，它不改变头奖概率的量级（1/1772 万）。"
    "这是结构，不是印钞机。"
)

# 概率口径（供其他地方引用，避免各处硬编码不一致）
JACKPOT_ODDS = 17721088          # C(33,6) * 16
JACKPOT_ODDS_TEXT = "1/1772 万"


def footer_md():
    """返回 Markdown 引用块形式的页脚。"""
    return "> **%s**" % HONESTY_FOOTER


def footer_text():
    return HONESTY_FOOTER


if __name__ == "__main__":
    print(footer_md())
