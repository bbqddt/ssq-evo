# -*- coding: utf-8 -*-
"""paths.py —— 全库唯一的路径解析入口

背景：全库 10+ 处各自写 `os.environ.get("DATA_DIR", <宿主机盘符路径>)`，
容器内部署时虽有 env，但一旦 env 缺失就退化成不存在的宿主机路径；
experiments/ 下还有直接写死数据目录的。任何新代码都不得再自行拼盘符路径。

本模块把路径决策收敛到一处：
    1. 环境变量 DATA_DIR（Docker 部署传 /app/data）优先
    2. 宿主机候选目录存在则用它（本机数据不落在 C 盘）
    3. 兜底 项目根/data（容器内 DATA_DIR 由 compose 挂载，此值不应被用到）

用法::

    import paths
    MASTER = os.path.join(paths.DATA_DIR, "ssq_master.csv")
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 宿主机候选（按优先级）；容器内都不会命中，会落到下面的兜底。
# 这里出现盘符是本模块的职责所在（唯一允许写盘符的地方）。
_HOST_CANDIDATES = [
    r"D:\ssq_evo_data",  # audit-ok: 宿主机候选，仅当目录存在时才选中
    r"D:/ssq_evo_data",  # audit-ok: 同上（斜杠写法等价）
]


def default_data_dir():
    """解析数据目录。见模块 docstring 的三级优先级。"""
    env = os.environ.get("DATA_DIR")
    if env:
        return env
    for cand in _HOST_CANDIDATES:
        if os.path.isdir(cand):
            return cand
    # 兜底：项目根下的 data/（容器里 DATA_DIR 必被设置，走到这里说明配置异常）
    return os.path.join(HERE, "data")


DATA_DIR = default_data_dir()


def master_csv():
    return os.path.join(DATA_DIR, "ssq_master.csv")


def p(*parts):
    """paths.p("frontier.json") → DATA_DIR/frontier.json"""
    return os.path.join(DATA_DIR, *parts)
