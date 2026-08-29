# -*- coding: utf-8 -*-
"""
serve.py —— 7x24 监控看板 (仅用标准库，无第三方依赖)
读取 state.json + SQLite，渲染：
  - 警报横幅 / 数据规模 / 最新周期 / 最优算子
  - 候选算子 leaderboard (Top 显著性)
  - p 值分布直方图 (SVG)
  - 历史 best_q 趋势 (SVG)
启动：python serve.py  ->  http://localhost:8088
"""
import os, json, sqlite3, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import ssq_log

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", HERE)
STATE = os.path.join(DATA_DIR, "state.json")
DB = os.path.join(DATA_DIR, "ssq_evo.db")
PORT = 8088


def load_state():
    if not os.path.exists(STATE):
        return None
    return json.load(open(STATE, encoding="utf-8"))


def latest_evals_p():
    if not os.path.exists(DB):
        return []
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        con.close(); return []
    rid = row[0]
    cur.execute("SELECT p_raw FROM evals WHERE run_id=?", (rid,))
    ps = [r[0] for r in cur.fetchall()]
    con.close()
    return ps


def p_hist_svg(ps, w=520, h=150):
    if not ps:
        return "<p style='color:#888'>暂无数据</p>"
    bins = [0, 0.01, 0.05, 0.1, 0.2, 0.5, 1.001]
    labels = ["<0.01", "0.01-0.05", "0.05-0.1", "0.1-0.2", "0.2-0.5", "0.5-1"]
    counts = [0] * (len(bins) - 1)
    for p in ps:
        for i in range(len(bins) - 1):
            if bins[i] <= p < bins[i + 1]:
                counts[i] += 1
                break
    mx = max(counts) or 1
    bw = w / len(counts)
    bars = ""
    for i, c in enumerate(counts):
        bh = (c / mx) * (h - 24)
        x = i * bw + 4
        y = h - 18 - bh
        color = "#c05621" if i < 2 else "#2b6cb0"
        bars += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw-8:.1f}" height="{bh:.1f}" fill="{color}" rx="2"/>'
        bars += f'<text x="{x+bw/2-4:.1f}" y="{y-3:.1f}" font-size="10" fill="#444">{c}</text>'
        bars += f'<text x="{x+bw/2-4:.1f}" y="{h-4:.1f}" font-size="9" fill="#666">{labels[i]}</text>'
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{bars}</svg>'


def trend_svg(history, w=520, h=160):
    # 容错：只认 dict 且含 best_q 的条目，避免泄漏变量/脏数据导致崩溃
    hist = [x for x in (history or []) if isinstance(x, dict) and "best_q" in x]
    if len(hist) < 2:
        return "<p style='color:#888'>样本不足，等待更多周期</p>"
    qs = [max(float(x.get("best_q", 1.0)), 1e-6) for x in reversed(hist)]
    n = len(qs)
    mx = max(qs); mn = min(qs)
    rng = (mx - mn) or 1
    pts = []
    for i, q in enumerate(qs):
        x = 30 + i * (w - 40) / max(1, n - 1)
        y = 10 + (1 - (q - mn) / rng) * (h - 30)
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline points="{poly}" fill="none" stroke="#2b6cb0" stroke-width="2"/>'
            f'<text x="6" y="16" font-size="10" fill="#666">q(高)</text>'
            f'<text x="6" y="{h-6}" font-size="10" fill="#666">q(低)</text></svg>')


def render():
    st = load_state()
    if not st:
        return "<h1>尚未运行</h1><p>请先执行 <code>python run_cycle.py</code></p>"
    ps = latest_evals_p()
    alert = st.get("alert")
    banner = ('<div style="background:#c53030;color:#fff;padding:12px 16px;border-radius:8px;'
              'font-weight:600;margin:12px 0">⚠ 警报：检测到候选结构！算子 '
              f'{st["best_sig"]}/{st["best_test"]} q={st["best_q"]:.2e}，样本外 p={st.get("oos_p")}，需人工复核。</div>'
              ) if alert else ('<div style="background:#2f855a;color:#fff;padding:10px 16px;border-radius:8px;'
              'margin:12px 0">本周期未检测到超越随机的可提取结构（null）。证据随样本与假设空间持续增强。</div>')

    cards = f"""
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin:12px 0">
      <div class="card"><div class="k">数据规模</div><div class="v">{st['n_issues']} 期</div><div class="s">末 {st['last_issue']}</div></div>
      <div class="card"><div class="k">周期 ID</div><div class="v">#{st['cycle_id']}</div><div class="s">新增 {st['added']}</div></div>
      <div class="card"><div class="k">最优 q (FDR)</div><div class="v">{st['best_q']:.2e}</div><div class="s">{st['best_sig']}/{st['best_test']}</div></div>
      <div class="card"><div class="k">样本外 p</div><div class="v">{st.get('oos_p') if st.get('oos_p') is not None else '—'}</div><div class="s">最近20%数据</div></div>
      <div class="card"><div class="k">本论评估</div><div class="v">{st['n_eval']}</div><div class="s">唯一算子 {st['n_unique']}</div></div>
    </div>"""

    rows = "".join(
        f"<tr><td>{e['sig']}</td><td>{e['test']}</td><td>{e['p_raw']:.3e}</td>"
        f"<td>{e['q']:.3e}</td><td>{e['z']:+.2f}</td><td>{e['stat']:.4f}</td><td>{e['verdict']}</td></tr>"
        for e in st["leaderboard"])

    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>双色球结构搜索 · 7x24 看板</title>
<style>body{{font-family:system-ui,'Microsoft YaHei',sans-serif;max-width:980px;margin:20px auto;color:#222;line-height:1.5}}
h1{{font-size:20px}}h2{{font-size:15px;margin-top:24px;border-left:4px solid #2b6cb0;padding-left:8px}}
.card{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;min-width:120px}}
.card .k{{font-size:12px;color:#718096}} .card .v{{font-size:20px;font-weight:700}} .card .s{{font-size:11px;color:#a0aec0}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #e2e8f0;padding:5px 8px;text-align:left}}
th{{background:#f5f7fa}} .box{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px}}
.refresh{{font-size:12px;color:#a0aec0}}</style></head><body>
<h1>双色球历史序列 · 自适应结构搜索 7x24 看板</h1>
<div class="refresh">更新于 {st['updated']} ｜ 自动刷新：请配合计划任务/nssm 周期调用 run_cycle.py</div>
{banner}
{cards}
<h2>候选算子 Leaderboard (Top 20, 按 p_raw)</h2>
<div class="box"><table><tr><th>信号映射</th><th>检验</th><th>p_raw</th><th>q(FDR)</th><th>z</th><th>stat</th><th>判定</th></tr>{rows}</table></div>
<h2>p 值分布（最近一轮全部算子）</h2>
<div class="box">{p_hist_svg(ps)}</div>
<h2>历史 best_q 趋势</h2>
<div class="box">{trend_svg(st.get('history', []))}</div>
<div class="box" style="color:#718096;font-size:12px">
说明：本看板监控的是"序列中是否存在可检测结构"的连续搜索。持续 null 是科学结果；仅当某算子经 FDR(q&lt;0.01) 且样本外复现才会触发警报。本系统不构成对时间是否存在的形而上学证明，亦不赋予任何预测权。
</div>
</body></html>"""
    return html


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    global PORT
    try:
        PORT = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))["http_port"]
    except Exception as _e:
        ssq_log.log_exception("serve", _e, "serve.py:158 silent-except")
    srv = HTTPServer(("0.0.0.0", PORT), H)
    print(f"[serve] dashboard on http://localhost:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
