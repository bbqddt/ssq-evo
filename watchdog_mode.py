# watchdog_mode.py — 守夜人模式（引擎内嵌停滞检测）
#
# 用途：当连续多轮无信号时，缩减 GA 预算（pop=1, epochs=1）避免空转浪费算力，
#       同时保留监测+闸门待命。一旦有信号即自动恢复。
# 与外部 watchdog.ps1（进程级健康监控）互补：本模块管"引擎内是否在空转"，
# watchdog.ps1 管"容器是否活着+代码是否最新"。

import copy

# 默认阈值：连续 N 轮全 NULL → 触发暂停
_STAGNATION_THRESHOLD = 30


def effective_cfg(cfg, frontier):
    """根据停滞状态返回修正后的配置 + 是否已暂停。

    Returns:
        (cfg_copy, paused): paused=True 表示守夜人模式已激活，GA 预算被压缩。
    """
    cfg = copy.deepcopy(cfg)
    paused = is_watchdog_paused(frontier, cfg)
    if paused:
        cfg["pop"] = 1
        cfg["epochs"] = 1
        cfg["comp_breed_n"] = 1   # 保留最小繁育能力（压到0会导致seed_gen1(n=0) ValueError）
    return cfg, paused


def update_stagnation(frontier, has_signal):
    """更新停滞计数器。有信号则清零，否则 +1。"""
    cycles = frontier.get("cycles_since_signal", 0)
    if has_signal:
        cycles = 0
    else:
        cycles += 1
    frontier["cycles_since_signal"] = cycles
    return frontier


def is_watchdog_paused(frontier, cfg):
    """判断是否应进入守夜人模式（连续全 NULL 超过阈值）。"""
    # 兼容两种键名：run_cycle 用 watchdog_stagnation_cycles，本模块用 _threshold
    threshold = cfg.get("watchdog_stagnation_cycles") or cfg.get("watchdog_stagnation_threshold", _STAGNATION_THRESHOLD)
    return frontier.get("cycles_since_signal", 0) >= threshold


def resume_note(frontier, cfg):
    """生成守夜人状态日志行。"""
    cycles = frontier.get("cycles_since_signal", 0)
    threshold = cfg.get("watchdog_stagnation_threshold", _STAGNATION_THRESHOLD)
    if cycles >= threshold:
        return f"PAUSED: {cycles} cycles without signal (threshold={threshold}), running minimal"
    elif cycles > threshold * 0.5:
        return f"WARN: {cycles} cycles without signal (approaching threshold={threshold})"
    else:
        return f"active: {cycles} cycles since last signal"
