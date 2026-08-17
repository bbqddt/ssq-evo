# -*- coding: utf-8 -*-
"""
formula_viz.py —— 可视化 Formula 语言（带确认闸门）
=================================================
把 diff_formula 进化出的「可微 Formula 候选基因组」渲染成人类可读的数学表达式，
并附 #41 确认闸门裁决状态（SIGNAL / UNCONFIRMED / NULL / ARTIFACT_BY_CONSTRUCTION）。

核心价值：让"公式的进化"看得见、可审计——每个候选公式不仅展示它"长什么样"，
还展示它"过没过诚实闸门"。这是把用户的 formula 方法论与诚实护栏咬合的可视化落地。

不引第三方依赖（纯 numpy + 标准库），可复现、可进 Docker、可被 run_cycle 调用。

产出：
  - formula_language.json : 机器可读 + 看板可消费（每轮追加/覆盖最新）
  - formula_language.html: 自包含可视化报告（浏览器/CloudStudio 直接打开）
"""
import os
import json
import html
import datetime
import numpy as np

import engine_core as E
import diff_formula as DF


# ---------------------------------------------------------------------------
# 确认闸门状态元数据（中文 + 颜色 + 说明）——诚实护栏的可视化映射
# ---------------------------------------------------------------------------
GATE_META = {
    "SIGNAL": {
        "zh": "疑似结构",
        "color": "#d97706",
        "desc": "发现段显著且经 #41 walk-forward 确认段复现；仅代表该序列在该算子下非随机，不等于可预测。",
    },
    "UNCONFIRMED": {
        "zh": "未确认",
        "color": "#6b7280",
        "desc": "发现段偏离 null，但确认段未能独立复现 => 过拟合/噪声，不报。",
    },
    "NULL": {
        "zh": "无结构",
        "color": "#94a3b8",
        "desc": "在分层零假设下不显著 => 与随机无异。",
    },
    "ARTIFACT_BY_CONSTRUCTION": {
        "zh": "构造伪结构(已拦截)",
        "color": "#dc2626",
        "desc": "该轴在纯随机数据上也 SURVIVOR => 显著系信号构造本身所致，已被随机对照闸门降级。",
    },
    "ERROR": {
        "zh": "计算异常",
        "color": "#7c3aed",
        "desc": "公式轴在该轮计算失败，不计入候选。",
    },
}

READ_ZH = {
    "cont": "延续(同向)",
    "rev": "反转(反向)",
    "mean": "均值回归",
    "osc": "振荡(过零)",
}

OP_SYM = {
    "+": "+", "-": "-", "*": "*", "/": "/",
    "diff": "Δ", "z": "z", "lag": "lag", "pow": "pow", "thresh": "thr",
    "sin": "sin", "cos": "cos", "abs": "abs",
}


# ---------------------------------------------------------------------------
# 渲染：嵌套 comp 基因组 -> 可读表达式
# ---------------------------------------------------------------------------
def render_operand(o, depth=0):
    """渲染一个操作数：基信号名 或 嵌套 comp dict。"""
    if isinstance(o, dict):
        return render_comp(o, depth + 1)
    return str(o)


def render_comp(cp, depth=0):
    """把嵌套 {op,a,b,k,read} 渲染成人类可读表达式。

    约定（对齐 engine_core.apply_comp 语义）：
      lag(a, k)  -> a(t-k)        ；diff(a) -> Δa = a(t)-a(t-1)
      z(a)       -> z(a)          ；thresh(a)-> a-med(a)
      pow(a,b)   -> a^|b|         ；sin/cos/abs 单目
    """
    if not isinstance(cp, dict):
        return str(cp)
    op = cp.get("op", "?")
    a = cp.get("a")
    b = cp.get("b")
    k = cp.get("k", 1)
    if op in ("sin", "cos", "abs"):
        return "%s(%s)" % (op, render_operand(a, depth))
    if op == "lag":
        return "%s(t-%d)" % (render_operand(a, depth), int(k))
    if op == "diff":
        return "Δ(%s)" % render_operand(a, depth)
    if op == "z":
        return "z(%s)" % render_operand(a, depth)
    if op == "thresh":
        return "(%s - med)" % render_operand(a, depth)
    if op == "pow":
        return "%s^|%s|" % (render_operand(a, depth), render_operand(b, depth))
    # 二元 + - * /
    if op in ("+", "-", "*", "/"):
        return "(%s %s %s)" % (render_operand(a, depth), OP_SYM.get(op, op), render_operand(b, depth))
    return "comp(%s)" % op


def render_test(test, params):
    """渲染检验及其连续参数。"""
    tp = (params or {}).get("_test", {}) or {}
    if tp:
        kv = ", ".join("%s=%s" % (k, v) for k, v in tp.items())
        return "%s(%s)" % (test, kv)
    return test


def render_genome(genome):
    """渲染完整基因组：Formula 表达式 + 检验 + 读取规则。

    例：Formula: (red_sum(t-3) - red_sum)  | Test: acf_max(maxlag=12) | Read: 反转
    """
    sig = genome.get("sig")
    test = genome.get("test")
    params = genome.get("params", {}) or {}
    if sig == "comp":
        expr = render_comp(params.get("_comp", {}))
    else:
        expr = sig
    read = params.get("_comp", {}).get("read") if sig == "comp" else None
    read_zh = (" | Read: " + READ_ZH.get(read, read)) if read else ""
    return "Formula: %s  | Test: %s%s" % (expr, render_test(test, params), read_zh)


def gate_of(rec):
    """从一条 diff_search 记录推断闸门状态（优先 wf_verdict）。"""
    verdict = rec.get("wf_verdict")
    if verdict == "SIGNAL":
        return "SIGNAL"
    if verdict == "UNCONFIRMED":
        return "UNCONFIRMED"
    # 无确认（confirm=False）或确认段失败：依 disc_p 给出 discover-only 标签
    disc_p = rec.get("disc_p")
    if disc_p is not None and disc_p < 0.05:
        return "UNCONFIRMED"  # 发现段显著但未确认
    return "NULL"


# ---------------------------------------------------------------------------
# 构建可视化记录 + HTML
# ---------------------------------------------------------------------------
def build_records(results):
    """把 diff_search 结果列表转成带渲染公式 + 闸门的可视化记录。"""
    recs = []
    for r in results:
        g = {"sig": r.get("sig"), "test": r.get("test"), "params": r.get("params", {})}
        gate = gate_of(r)
        recs.append({
            "formula": render_genome(g),
            "sig": r.get("sig"),
            "test": r.get("test"),
            "disc_p": r.get("disc_p"),
            "wf_verdict": r.get("wf_verdict"),
            "wf_conf_p": r.get("wf_conf_p"),
            "wf_disc_p": r.get("wf_disc_p"),
            "gate": gate,
            "gate_zh": GATE_META.get(gate, {}).get("zh", gate),
            "gate_color": GATE_META.get(gate, {}).get("color", "#000"),
        })
    # 排序：SIGNAL > UNCONFIRMED(disc_p小) > NULL，便于看板聚焦
    order = {"SIGNAL": 0, "UNCONFIRMED": 1, "NULL": 2, "ARTIFACT_BY_CONSTRUCTION": 3, "ERROR": 4}
    recs.sort(key=lambda x: (order.get(x["gate"], 9), x["disc_p"] if x["disc_p"] is not None else 1.0))
    return recs


def _row_html(i, r):
    dp = ("%.4g" % r["disc_p"]) if isinstance(r["disc_p"], float) else "-"
    cp = ("%.4g" % r["wf_conf_p"]) if isinstance(r["wf_conf_p"], float) else "-"
    vp = ("%.4g" % r["wf_disc_p"]) if isinstance(r["wf_disc_p"], float) else "-"
    gate = html.escape(r["gate_zh"])
    color = r["gate_color"]
    return ('<tr>'
            '<td class="num">' + str(i + 1) + '</td>'
            '<td class="formula">' + html.escape(r["formula"]) + '</td>'
            '<td><span class="badge" style="background:' + color + '">' + gate + '</span></td>'
            '<td class="num">' + dp + '</td>'
            '<td class="num">' + cp + '</td>'
            '<td class="num">' + vp + '</td>'
            '</tr>')


def to_html(recs, meta=None):
    """生成自包含 HTML 报告（浅色主题，直接浏览器打开）。

    用占位符 + .replace() 拼接（不用 % 格式化），回避 CSS 中大量字面 '%' 与
    HTML 中 ';' 被误判为格式符的问题。
    """
    meta = meta or {}
    ts = meta.get("ts", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    rows = "\n".join(_row_html(i, r) for i, r in enumerate(recs))
    n_signal = sum(1 for r in recs if r["gate"] == "SIGNAL")
    n_unconf = sum(1 for r in recs if r["gate"] == "UNCONFIRMED")
    n_null = sum(1 for r in recs if r["gate"] == "NULL")
    legend = "".join(
        '<span class="leg"><i style="background:' + m["color"] + '"></i>' + m["zh"] + '</span>'
        for m in GATE_META.values()
    )
    tpl = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Formula 语言 · 带确认闸门</title>
<style>
  body{font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
       background:#f8fafc;color:#0f172a;margin:0;padding:24px;}
  h1{font-size:20px;margin:0 0 4px;}
  .sub{color:#475569;font-size:13px;margin-bottom:16px;}
  .summary{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;}
  .card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px 16px;min-width:96px;}
  .card b{display:block;font-size:22px;}
  .card span{font-size:12px;color:#64748b;}
  table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;
        box-shadow:0 1px 3px rgba(0,0,0,.06);}
  th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #eef2f7;font-size:13px;vertical-align:top;}
  th{background:#f1f5f9;color:#334155;font-weight:600;}
  td.num{font-family:ui-monospace,Menlo,Consolas,monospace;color:#475569;white-space:nowrap;}
  td.formula{font-family:ui-monospace,Menlo,Consolas,monospace;color:#0f172a;}
  .badge{color:#fff;border-radius:6px;padding:2px 8px;font-size:12px;font-weight:600;white-space:nowrap;}
  .legend{margin:14px 0 0;font-size:12px;color:#475569;}
  .leg{display:inline-flex;align-items:center;margin-right:14px;}
  .leg i{width:11px;height:11px;border-radius:3px;display:inline-block;margin-right:5px;}
</style></head>
<body>
<h1>Formula 语言 · 带确认闸门</h1>
<div class="sub">生成时间 __TS__ ｜ 双色球结构搜索引擎 ssq_evo ｜ 公式进化 + #41 诚实确认闸门</div>
<div class="summary">
  <div class="card"><b style="color:#d97706">__NSIG__</b><span>疑似结构(SIGNAL)</span></div>
  <div class="card"><b style="color:#6b7280">__NUNCONF__</b><span>未确认(UNCONFIRMED)</span></div>
  <div class="card"><b style="color:#94a3b8">__NNULL__</b><span>无结构(NULL)</span></div>
  <div class="card"><b>__NTOTAL__</b><span>候选公式总数</span></div>
</div>
<table>
  <thead><tr><th>#</th><th>Formula 表达式（带检验/读取规则）</th><th>确认闸门</th>
  <th>发现 p</th><th>确认 p</th><th>发现段合并 p</th></tr></thead>
  <tbody>__ROWS__</tbody>
</table>
<div class="legend">__LEGEND__</div>
<p class="sub" style="margin-top:14px">说明：发现 p = 发现段 surrogate 秩 p；确认 p = #41 walk-forward 确认段合并 p；
确认闸门=SIGNAL 仅代表该序列在该算子下非随机，<b>不构成任何预测/下注权</b>。持续 null 是诚实的科学结果。</p>
</body></html>"""
    return (tpl
            .replace("__TS__", html.escape(ts))
            .replace("__NSIG__", str(n_signal))
            .replace("__NUNCONF__", str(n_unconf))
            .replace("__NNULL__", str(n_null))
            .replace("__NTOTAL__", str(len(recs)))
            .replace("__ROWS__", rows)
            .replace("__LEGEND__", legend))


def emit(reds, blues, rng, out_dir="D:/ssq_evo_data", confirm=True, n_candidates=8):
    """跑 diff_search + 渲染 + 写 JSON/HTML。返回记录列表。"""
    results = DF.run_diff_search(reds, blues, rng, n_candidates=n_candidates,
                                 confirm=confirm, discovery_frac=0.7, k_sur_opt=40, n_steps=10)
    recs = build_records(results)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {"ts": ts, "recs": recs,
               "summary": {"n_signal": sum(1 for r in recs if r["gate"] == "SIGNAL"),
                           "n_unconfirmed": sum(1 for r in recs if r["gate"] == "UNCONFIRMED"),
                           "n_null": sum(1 for r in recs if r["gate"] == "NULL")}}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "formula_language.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "formula_language.html"), "w", encoding="utf-8") as f:
        f.write(to_html(recs, {"ts": ts}))
    print("[formula_viz] 渲染 %d 条公式候选 -> formula_language.json/html" % len(recs))
    for r in recs[:5]:
        print("   ", r["gate_zh"], "|", r["formula"])
    return recs


def main():
    import data as D
    path = "D:/ssq_evo_data/ssq_master.csv"
    m = D.load_master(path)
    if not m:
        print("[formula_viz] 未找到真实数据，退出")
        return
    reds, blues, _ = D.to_arrays(m)
    rng = np.random.default_rng(20260817)
    emit(reds, blues, rng, confirm=True, n_candidates=8)


if __name__ == "__main__":
    main()
