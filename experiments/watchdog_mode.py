# -*- coding: utf-8 -*-
"""
watchdog_mode.py —— 守夜人模式（honest null-domain 处置）
========================================================
取舍背景（用户授权"选择权给你"）：
  所有可获取的数学/日历/外生轴（数论/集合论/信息论/符号回归/因果/拓扑/
  机器偏倚/深度/框架种子/智能层/期号模/真实日历）经诚实闸门(每类均带阳性对照
  证有效) 全部 NULL。继续在已证伪空间堆 GA 算力 = 头号红线禁止的 Goodhart 自欺。

守夜人模式：
  - 监测 `cycles_since_signal`：连续多少周期「全 NULL」(无 above_random / 无 FDR 幸存 / 无 alert)。
  - 超过阈值(默认30)且启用 -> 暂停"空转 GA"（降为最小待命心跳 pop=1、停 comp breeding），
    但保留：监测/健康心跳、阳性对照(闸门功率监控)、draw-day 打分、闸门待命
    （一旦新数据维度/新基元注入立即恢复全搜）。
  - 绝不修改诚实闸门、绝不自动合并候选 —— 红线不变。
  - 恢复条件：新基元注入(baseline_base_signals 变化) / 显式 force_resume。

默认启用(watchdog_mode_enabled=True)：证据已充分支持暂停空转。改动经 config 门控，
不影响闸门逻辑；线上需重启服务生效。
"""
import engine_core as E

DEFAULT_STAGNATION = 30


def update_stagnation(fr, signal_this_cycle):
    """每轮末调用：有信号(above_random或alert)则清零，否则 +1。
    同时维护 baseline_base_signals（用于检测"新基元注入=新搜索空间"）。"""
    if not isinstance(fr, dict):
        fr = {}
    if signal_this_cycle:
        fr["cycles_since_signal"] = 0
    else:
        fr["cycles_since_signal"] = int(fr.get("cycles_since_signal", 0)) + 1
    base_n = len(E.BASE_SIGNALS)
    if fr.get("baseline_base_signals") is None:
        fr["baseline_base_signals"] = base_n
    return fr


def is_watchdog_paused(fr, cfg):
    """是否应暂停空转 GA。"""
    if not cfg.get("watchdog_mode_enabled", True):
        return False
    if fr.get("force_resume") or cfg.get("watchdog_force_resume"):
        return False
    base_n = len(E.BASE_SIGNALS)
    # 新基元注入 => 搜索空间扩大 => 立即恢复全搜
    if fr.get("baseline_base_signals") is not None and fr.get("baseline_base_signals") != base_n:
        return False
    return int(fr.get("cycles_since_signal", 0)) >= int(cfg.get("watchdog_stagnation_cycles", DEFAULT_STAGNATION))


def effective_cfg(cfg, fr):
    """返回 (生效cfg, 是否暂停)。暂停时降为最小待命心跳（停止空转 GA），保留监测与闸门待命。"""
    if is_watchdog_paused(fr, cfg):
        c = dict(cfg)
        c["pop"] = 1                         # 真正最小待命：每轮仅 1~2 候选心跳，GA 空转停止
        c["epochs"] = 1
        c["comp_breed_n"] = 0               # 停止复合公式 breeding（演进空转核心）
        c["k_light"] = max(5, int(cfg.get("k_light", 25)) // 3)
        c["k_heavy"] = max(3, int(cfg.get("k_heavy", 10)) // 3)
        return c, True
    return cfg, False


def resume_note(fr, cfg):
    """给日志/看板的人类可读说明。"""
    if not is_watchdog_paused(fr, cfg):
        return "活跃搜索(未触发守夜人)"
    base_n = len(E.BASE_SIGNALS)
    if fr.get("baseline_base_signals") != base_n:
        return "恢复全搜：检测到新基元注入(搜索空间扩大)"
    return (f"守夜人暂停：连续 {fr.get('cycles_since_signal',0)} 周期全 NULL"
            f"（无 above_random / 无 FDR 幸存 / 无 alert），"
            f"已证伪空间停止空转 GA（pop=1 待命心跳）；"
            f"保留监测+阳性对照+draw-day 打分+闸门待命。"
            f"注入新数据维度/新基元即恢复。")
