# -*- coding: utf-8 -*-
"""
layered_null.py —— 分层空模型（B 块）
====================================
直接回应「时间是否基本」：用不同零假设数据集给同一统计打标签。

  - subset_marginal : 保留时间序，但每期号码从经验边际分布重抽 => 摧毁每期内部组合结构，
                      保留期间时间依赖。用于判断「结构是否只活在每期组合属性里」。
  - permute_draws   : 彻底打乱各期顺序（摧毁时间轴），但保留每期自身内容。与 engine_core
                      既有 shuffle surrogate（作用在 1D 信号序列上）等价，保留作对照/阳性对照。

这两个 null 与 engine_core 既有 shuffle/AAFT/twin 互补：
  - shuffle surrogate         = 摧毁时间序（信号层面，主线主零假设）
  - AAFT   surrogate          = 保留频谱/时间结构（对照：结构是否依赖时间）
  - subset_marginal（本模块） = 摧毁每期组合结构（新增：结构是否只活在组合属性）
分层对比让每个轴得到「在哪些零假设下存活」的诚实标签，逼近「全域 null」边界。
"""
import numpy as np


def permute_draws(reds, blues, rng):
    """打乱期序（行），摧毁时间轴；保留每期内容。"""
    n = reds.shape[0]
    idx = rng.permutation(n)
    return reds[idx].copy(), blues[idx].copy()


def subset_marginal(reds, blues, rng):
    """保留期序，每期号码从经验边际分布重抽 => 摧毁期内组合结构，保留期间时间依赖。

    红球：按全部历史出现频率加权，每期抽 6 个无放回（= 经验边际下的随机 6-子集）。
    蓝球：从经验边际重抽。这样保留「球值边际分布」但摧毁任何「每期内部约束」
    （间隔/奇偶平衡/特定球必现 等）。
    """
    N, K = reds.shape
    flat = reds.ravel()
    vals, counts = np.unique(flat, return_counts=True)
    probs = counts / counts.sum()
    new_reds = np.empty((N, K), dtype=reds.dtype)
    for i in range(N):
        pick = rng.choice(vals, size=K, replace=False, p=probs)
        new_reds[i] = np.sort(pick)
    bvals, bcounts = np.unique(blues.ravel(), return_counts=True)
    bprobs = bcounts / bcounts.sum()
    new_blues = rng.choice(bvals, size=blues.shape, replace=True, p=bprobs).astype(blues.dtype)
    return new_reds, new_blues


def null_dataset(kind, reds, blues, rng):
    if kind == "permute":
        return permute_draws(reds, blues, rng)
    if kind == "marginal":
        return subset_marginal(reds, blues, rng)
    raise ValueError("unknown null kind: %r" % kind)
