# -*- coding: utf-8 -*-
"""
零依赖引擎配置加载器。

优先使用 pyyaml（若已安装）；否则回退到极小手写解析器，
仅支持本项目用到的简单结构：注释(#)、缩进嵌套(2空格)、
标量值(int/float/bool/str)。最终返回扁平 dict（键名与 run_cycle.DEFAULT_CFG 对齐）。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, "engine.yaml")

_FLAT_KEYS = [
    "epochs", "pop", "seed", "oos_frac",
    "k_light", "k_heavy", "k_causal",
    "fdr_q", "alert_q", "alert_oos_p",
    "wf_n_folds", "wf_disc_frac",
]


def _try_yaml(path):
    try:
        import yaml
    except Exception:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _minimal_parse(path):
    """极简 YAML 解析：支持 2 空格缩进嵌套 + 标量。"""
    tree = {}
    stack = [(-1, tree)]
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            key, _, val = line.strip().partition(":")
            key = key.strip()
            val = val.strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1]
            if val == "":
                node = {}
                parent[key] = node
                stack.append((indent, node))
            else:
                parent[key] = _coerce(val)
    return tree


def _coerce(v):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _flatten(d, out):
    for k, v in d.items():
        if isinstance(v, dict):
            _flatten(v, out)
        else:
            out[k] = v
    return out


def load_engine_config(path=None):
    """返回扁平 dict；缺失键回退到 run_cycle.DEFAULT_CFG 等价默认值。"""
    path = path or DEFAULT_PATH
    parsed = _try_yaml(path)
    if parsed is None:
        parsed = _minimal_parse(path)
    flat = _flatten(parsed, {}) if parsed else {}
    # 缺失键兜底（保证向后兼容）
    for k in _FLAT_KEYS:
        if k not in flat:
            flat[k] = _DEFAULTS[k]
    return flat


_DEFAULTS = {
    "epochs": 6, "pop": 24, "seed": 20260813, "oos_frac": 0.2,
    "k_light": 25, "k_heavy": 10, "k_causal": 50,
    "fdr_q": 0.05, "alert_q": 0.01, "alert_oos_p": 0.01,
    "wf_n_folds": 3, "wf_disc_frac": 0.7,
}


if __name__ == "__main__":
    import json
    print(json.dumps(load_engine_config(), indent=2, ensure_ascii=False))
