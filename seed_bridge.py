# seed_bridge.py — 外部框架种子注入桥接（预留接口）
#
# 用途：消费式读取外部符号回归框架（如 gplearn）产出的公式种子，
#       将其注入 GA 搜索起点，扩大搜索空间。
# 当前：无外部框架接入（load_seeds_consume 返回空列表）。
# 扩展方式：实现 load_seeds_consume() 返回公式树列表。


def load_seeds_consume():
    """消费式读取外部框架种子。返回公式树列表（空=无外部种子）。"""
    return []
