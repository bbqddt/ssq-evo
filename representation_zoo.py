# -*- coding: utf-8 -*-
"""
representation_zoo.py —— 扩轴编码器（A 块：表征扩容）
================================================
把每期开奖从「时间序单轴」重画成多种坐标通道，每个通道输出一条 1D 序列 x，
交给现有 engine_core.evaluate / evaluator 的同一套闸门裁决。不预设任何轴为结论。

设计原则（呼应「别把时间序当成唯一、默认的轴」）：
  - 注册新轴 = 往 engine_core.SIGMAPS 注入新信号函数；evaluate / _build_x 自动接纳。
  - 轴分组：baseline(时间基线) / algebraic(代数 Z_33 模运算) / combinatorial(组合子集) /
            parity_prime_energy(奇偶质合能量)。「公式轴(comp)」由 diff_formula 进化，
            在驱动器中单独处理，不在此硬编码。
  - 不重新发明轮子：parity/prime/gap/energy/zone 等基信号 engine_core 已有；
    本模块只新增代数 mod 家族与少数组合/质心通道，并把它们编成可迭代 AXES 表供驱动器扫描。
"""
import numpy as np
import engine_core as E


# ---------------------------------------------------------------------------
# 新增信号函数（每个返回长度 N = reds.shape[0] 的 float 数组）
# ---------------------------------------------------------------------------
def red_sum_mod3(reds, blues):
    """代数轴：红球和模 3（把 1..33 当 Z_33 元素映射到 Z_3）。"""
    return (reds.sum(axis=1) % 3).astype(float)


def red_sum_mod7(reds, blues):
    return (reds.sum(axis=1) % 7).astype(float)


def red_sum_mod31(reds, blues):
    return (reds.sum(axis=1) % 31).astype(float)


def red_prod_mod33(reds, blues):
    """代数轴：红球积模 33（乘法结构，非线性于和）。"""
    p = np.ones(reds.shape[0], dtype=np.int64)
    for j in range(6):
        p = (p * reds[:, j].astype(np.int64)) % 33
    return p.astype(float)


def red_pair_meandist(reds, blues):
    """组合轴：排序后两两球平均距离（组合间隔结构，非时间）。"""
    s = np.sort(reds, axis=1)
    diffs = np.diff(s, axis=1)
    return diffs.mean(axis=1).astype(float)


def red_centroid(reds, blues):
    """能量轴：球号质心（归一化到中心 17，归零）。"""
    return (reds.mean(axis=1) / 17.0 - 1.0).astype(float)


# 本模块新增信号（与 engine_core 既有基信号合并成完整轴表）
NEW_SIGNALS = {
    "red_sum_mod3": red_sum_mod3,
    "red_sum_mod7": red_sum_mod7,
    "red_sum_mod31": red_sum_mod31,
    "red_prod_mod33": red_prod_mod33,
    "red_pair_meandist": red_pair_meandist,
    "red_centroid": red_centroid,
}


def register():
    """把新增信号注入 engine_core.SIGMAPS，并刷新 BASE_SIGNALS（供 comp 子表达式引用）。
    幂等：已存在则跳过。"""
    for name, fn in NEW_SIGNALS.items():
        if name not in E.SIGMAPS:
            E.SIGMAPS[name] = fn
    E.BASE_SIGNALS = list(E.SIGMAPS.keys())


# ---------------------------------------------------------------------------
# 轴表（驱动器按 group 扫描；不绑定任何结论）
# 每个条目：{group, sig, tests, note}
# ---------------------------------------------------------------------------
AXES = [
    # 时间基线（已有，作为对照）
    {"group": "baseline", "sig": "red_sum", "tests": ["acf_max", "mi_max", "dfa_alpha"], "note": "时间序基线：红球和随期号"},
    {"group": "baseline", "sig": "blue", "tests": ["acf_max", "mi_max"], "note": "蓝球随期号"},
    # 代数轴（新增 + 已有 mod11/mod16）
    {"group": "algebraic", "sig": "red_sum_mod3", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "红球和模3（Z_33→Z_3）"},
    {"group": "algebraic", "sig": "red_sum_mod7", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "红球和模7"},
    {"group": "algebraic", "sig": "red_sum_mod11", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "红球和模11"},
    {"group": "algebraic", "sig": "red_sum_mod31", "tests": ["acf_max", "perm_entropy"], "note": "红球和模31"},
    {"group": "algebraic", "sig": "red_prod_mod33", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "红球积模33（乘法结构）"},
    # 组合轴（新增 + 已有）
    {"group": "combinatorial", "sig": "red_pair_meandist", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "排序后两两平均距离（组合间隔）"},
    {"group": "combinatorial", "sig": "red_gap_mean", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "相邻球平均间隔"},
    {"group": "combinatorial", "sig": "red_runs", "tests": ["acf_max", "perm_entropy"], "note": "连续同区游程"},
    {"group": "combinatorial", "sig": "red_zone_entropy", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "分区熵（组合分布）"},
    # 奇偶质合能量轴（已有）
    {"group": "parity_prime_energy", "sig": "red_parity", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "奇偶计数"},
    {"group": "parity_prime_energy", "sig": "red_prime_count", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "质数球计数"},
    {"group": "parity_prime_energy", "sig": "red_energy", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "能量（平方和）"},
    {"group": "parity_prime_energy", "sig": "red_centroid", "tests": ["acf_max", "perm_entropy", "mi_max"], "note": "球号质心（归一化）"},
    {"group": "parity_prime_energy", "sig": "red_recurrence_mean", "tests": ["acf_max", "perm_entropy"], "note": "递归率（相空间）"},
]
