# formula_research.py — 原创公式基元注册（预留接口）
#
# 用途：formula_composer.py 加载时调用 FR.register()，
#       将本项目原创的代数基元注入 engine_core.BASE_SIGNALS 字母表。
# 当前：无原创基元待注册（register() 为幂等空操作）。
# 扩展方式：在 register() 内调用 RZ.register(...) 添加新基元。

import representation_zoo as RZ


def register():
    """注册原创公式基元到全局字母表。当前为空（幂等安全）。"""
    # 未来添加原创基元示例：
    # RZ.register("my_signal", lambda reds, blues: ..., doc="我的原创信号")
    pass
