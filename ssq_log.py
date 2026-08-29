"""统一日志模块 (ssq_log)

背景：全库 34 处 `except Exception: pass` 静默吞异常，导致失败不可观测
（历史事故：UnboundLocalError 被吞后容器静默空转）。

本模块提供最小侵入的日志能力，不引入 logging 配置负担，直接输出到
stdout（Docker 日志可采集），并保证绝不因日志本身抛异常。
"""
import os
import sys
import time
import traceback

# 日志级别：可通过环境变量 SSQ_LOG_LEVEL 调整 (DEBUG/INFO/WARN/ERROR)
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_LEVEL = _LEVELS.get(os.environ.get("SSQ_LOG_LEVEL", "INFO").upper(), 20)

# 相同 (tag, 异常类型, 消息) 的日志在窗口期内只打印一次，防止刷屏
_DEDUPE_WINDOW = float(os.environ.get("SSQ_LOG_DEDUPE_SEC", "300"))
_seen = {}
_MAX_SEEN = 5000


def _emit(level, tag, msg):
    if _LEVELS.get(level, 20) < _LEVEL:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [{level}] [{tag}] {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass  # 日志绝不能反过来搞崩业务


def _dedupe_ok(key):
    now = time.time()
    prev = _seen.get(key)
    if prev is not None and (now - prev) < _DEDUPE_WINDOW:
        return False
    if len(_seen) > _MAX_SEEN:
        _seen.clear()
    _seen[key] = now
    return True


def log_exception(tag, exc, context="", dedupe=True):
    """记录被吞掉的异常。用于 except 块内。

    用法::

        try:
            ...
        except Exception as _e:
            log_exception("self_check", _e, "config drift check")
    """
    key = (tag, type(exc).__name__, str(exc)[:120], context)
    if dedupe and not _dedupe_ok(key):
        return
    msg = f"{type(exc).__name__}: {exc}"
    if context:
        msg = f"{context} | {msg}"
    _emit("WARN", tag, msg)


def warn(tag, msg, dedupe=True):
    if dedupe and not _dedupe_ok(("w", tag, str(msg)[:200])):
        return
    _emit("WARN", tag, msg)


def info(tag, msg, dedupe=False):
    _emit("INFO", tag, msg)


def error(tag, msg, exc=None, dedupe=True):
    if dedupe and not _dedupe_ok(("e", tag, str(msg)[:200])):
        return
    text = msg
    if exc is not None:
        text = f"{msg} | {type(exc).__name__}: {exc}"
    _emit("ERROR", tag, text)


def critical(tag, msg, exc=None):
    """不可去重 —— 数据丢失/结构损坏级别的事件必须每次都留痕。"""
    text = msg
    if exc is not None:
        text = f"{msg} | {type(exc).__name__}: {exc}"
    _emit("ERROR", "CRITICAL:" + tag, text)


def tb(tag, exc):
    """打印完整 traceback（仅用于真正需要堆栈的场景）。"""
    _emit("ERROR", tag, "".join(traceback.format_exception(
        type(exc), exc, exc.__traceback__)).rstrip())
