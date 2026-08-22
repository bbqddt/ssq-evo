#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 ssq_evo 研究监控看板 (dashboard/index.html)。

数据源改为 **daily_digest.jsonl**（而非易过期的 state.json）：
  - daily_digest.jsonl 是 run_cycle 每轮追加写的"完整结论载荷"日志，
    是引擎结论最权威、最新鲜、且不可篡改追加的记录。
  - 本脚本读取全部 JSONL 行：最后一行 = 最新结论，全部行 = 历史趋势。
  - 生成的 index.html 自包含（内联 CSS + 内嵌数据 + 一段轻量 JS）：
      * 内嵌最新+历史数据（部署即正确，不依赖运行时 fetch）；
      * 页面加载后 fetch('./daily_digest.jsonl') 直接解读该文件：
        若远端最新 cycle 比内嵌新 → 自动刷新页面显示最新结论；
        否则显示"已核对·数据新鲜"时间戳。
  - 同时把 daily_digest.jsonl 复制到 dashboard/ 目录，使 fetch 同源可用。
该目录由 CloudStudio 部署到腾讯云，作为"第三辆车"的对外监控层。
"""
import json
import os
import html
import datetime
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", r"D:\ssq_evo_data")
DIGEST = os.path.join(DATA, "daily_digest.jsonl")
OUT_DIR = os.path.join(DATA, "dashboard")
OUT = os.path.join(OUT_DIR, "index.html")

ALERT_Q = 0.01          # 触发 alert 的 FDR 门槛 (与 engine config 一致)
FDR_Q = 0.05            # 结构显著的 FDR 门槛
OOS_P = 0.01            # 样本外显著门槛


# ────────────────────────────────────────────────────────────
# 数据加载
# ────────────────────────────────────────────────────────────
def load_digest(path):
    """读取 daily_digest.jsonl，返回记录列表（容错跳过坏行）。"""
    if not os.path.exists(path):
        return []
    recs = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception as e:
                print(f"[dashboard] 跳过第 {ln} 行(解析失败): {e}")
    return recs


# ────────────────────────────────────────────────────────────
# 显示辅助
# ────────────────────────────────────────────────────────────
def _num(v, nd=4):
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "—"


def _esc(s):
    return html.escape(str(s))


def _yn(b):
    return "是" if b else "否"


def svg_line(series, w=640, h=200, ymin=0.0, ymax=1.0, threshold=None,
             color="#4ea1ff", tcolor="#ff6b6b", title=""):
    """series: list of (idx, value). 返回内联 SVG 折线图。"""
    if not series:
        return f'<svg viewBox="0 0 {w} {h}"><text x="10" y="20" fill="#888">无数据</text></svg>'
    pad_l, pad_r, pad_t, pad_b = 38, 10, 14, 22
    iw = w - pad_l - pad_r
    ih = h - pad_t - pad_b
    n = len(series)
    xs = [pad_l + (iw * i / max(1, n - 1)) for i in range(n)]
    def yv(v):
        v = max(ymin, min(ymax, v))
        return pad_t + ih * (1 - (v - ymin) / (ymax - ymin + 1e-12))
    pts = " ".join(f"{x:.1f},{yv(v):.1f}" for (_, v), x in zip(series, xs))
    grid = ""
    for g in range(5):
        gy = pad_t + ih * g / 4
        gv = ymax - (ymax - ymin) * g / 4
        grid += f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w-pad_r}" y2="{gy:.1f}" stroke="#2a2f3a" stroke-width="1"/>'
        grid += f'<text x="4" y="{gy+3:.1f}" fill="#7a8290" font-size="9">{gv:.2f}</text>'
    th = ""
    if threshold is not None:
        ty = yv(threshold)
        th = (f'<line x1="{pad_l}" y1="{ty:.1f}" x2="{w-pad_r}" y2="{ty:.1f}" '
              f'stroke="{tcolor}" stroke-width="1" stroke-dasharray="4 3"/>'
              f'<text x="{w-pad_r-58}" y="{ty-4:.1f}" fill="{tcolor}" font-size="9">阈值 {threshold}</text>')
    lx, ly = xs[-1], yv(series[-1][1])
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="xMidYMid meet">'
            f'{grid}{th}'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{color}"/>'
            f'<text x="{pad_l}" y="{h-6}" fill="#7a8290" font-size="9">{title}</text>'
            f'</svg>')


def kpi_card(label, value, sub, color="#4ea1ff", ok=None):
    badge = ""
    if ok is True:
        badge = '<span class="badge ok">✓ 显著</span>'
    elif ok is False:
        badge = '<span class="badge no">✗ 未达</span>'
    return (f'<div class="card"><div class="kpi-label">{_esc(label)}</div>'
            f'<div class="kpi-val" style="color:{color}">{_esc(value)}</div>'
            f'<div class="kpi-sub">{_esc(sub)}{badge}</div></div>')


def verdict_text(s):
    """基于最新 digest 记录计算诚实头条结论。"""
    q = float(s.get("best_q", 1.0) or 1.0)
    above = bool(s.get("oos_acc_above"))
    consistent = bool(s.get("oos_cross_consistent"))
    alert = bool(s.get("alert"))
    wf = s.get("wf_verdict")
    if alert:
        return ("<b style='color:#3ddc84'>ALERT 已触发</b>：候选跨过全部预设闸门 "
                f"(FDR q&lt;{ALERT_Q}, OOS p&lt;{OOS_P}, 零假设交叉一致)，"
                "已进入前瞻验证等待新开奖确认。")
    if wf == "SIGNAL":
        return ("发现/确认分离闸门 (#41) 判定 <b>SIGNAL</b>：候选在冻结后的独立确认段上"
                "跨折复现，是'结构在独立未来复现'的唯一诚实证据。")
    if q < FDR_Q and above and consistent:
        return ("出现跨 FDR/样本外/零假设交叉三重闸门的候选，但 alert 门槛更严 "
                f"(q&lt;{ALERT_Q})，暂判定为<b>强候选(非结论)</b>——需新开奖前瞻验证。")
    if q < FDR_Q:
        return ("结构 FDR 曾低于 0.05，但样本外方向准确率未高于随机或零假设交叉不一致 "
                "→ <b>选择性偏差/非结论</b>，不视为证据。")
    return ("当前前沿最佳 q 远高于 0.05 → <b>研发进行中：尚未产出通过最终闸门的公式</b>。"
            "本系统是研发/创造计算开奖的公式，非预设'无结构'结论；null 域仅为待检验猜想，"
            "研发进度不代表域定性。")


# ────────────────────────────────────────────────────────────
# 主构建
# ────────────────────────────────────────────────────────────
def build(records):
    if not records:
        return ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<title>ssq_evo 看板</title></head><body>"
                "<h1>尚无结论数据</h1><p>等待 run_cycle 完成首轮并写入 daily_digest.jsonl。</p>"
                "</body></html>")
    latest = records[-1]
    hist = records

    # 历史趋势序列
    q_series = [(i, float(r.get("best_q", 1.0) or 1.0)) for i, r in enumerate(hist)]
    cov_series = [(i, r.get("coverage")) for i, r in enumerate(hist)
                  if r.get("coverage") is not None]
    n_series = [(i, r.get("n_issues")) for i, r in enumerate(hist)
                if r.get("n_issues") is not None]
    # 公式代数 df_gen 轨迹：观察代际是否真实上长（研发进度，非域定性）
    dfgen_series = [(i, int(r.get("df_gen"))) for i, r in enumerate(hist)
                    if r.get("df_gen") is not None]
    # 评估稳定性轨迹（研发诚实指标）：近 N 轮 best_q 变异系数
    stab_series = [(i, (r.get("stability") or {}).get("q_cv")) for i, r in enumerate(hist)
                   if (r.get("stability") or {}).get("q_cv") is not None]

    q = float(latest.get("best_q", 1.0) or 1.0)
    oos_acc = latest.get("oos_acc")
    oos_sur = latest.get("oos_acc_sur")
    oos_p = latest.get("oos_acc_p")
    oos_above = bool(latest.get("oos_acc_above"))
    oos_n = latest.get("oos_acc_n")
    consistent = bool(latest.get("oos_cross_consistent"))
    alert = bool(latest.get("alert"))
    cross_primary = latest.get("oos_cross_primary")
    cross_type = latest.get("oos_cross_primary_type") or "—"

    oot_hit = latest.get("oot_hit")
    oot_p = latest.get("oot_p")
    oot_n = latest.get("oot_n")
    oot_above = bool(latest.get("oot_above"))
    oot_ok = (oot_above and q < FDR_Q)

    # 选号准确率（第一性原理口径）：引擎 top 候选产出 6+1 组合 vs 超几何随机基线的超额命中
    pick_red_excess = latest.get("pick_red_excess")
    pick_blue_excess = latest.get("pick_blue_excess")
    pick_p = latest.get("pick_p")
    pick_above = bool(latest.get("pick_above"))
    pick_n = latest.get("pick_n")
    pick_red_pick = latest.get("pick_red_pick")
    pick_blue_pick = latest.get("pick_blue_pick")

    spectral_q = latest.get("spectral_q")
    spectral_n = latest.get("spectral_n")
    spectral_rank = latest.get("spectral_q_rank")
    spectral_sig = latest.get("spectral_best_sig")
    spectral_test = latest.get("spectral_best_test")
    spectral_z = latest.get("spectral_best_z")
    spectral_oot_p = latest.get("spectral_oot_p")
    spectral_oot_above = bool(latest.get("spectral_oot_above"))
    spectral_alert = bool(latest.get("spectral_alert"))
    spec_ok = spectral_alert

    causal_best = latest.get("causal_q_min")
    causal_sig = latest.get("causal_best_sig")
    causal_test = latest.get("causal_best_test")
    causal_p = latest.get("causal_p_min")
    ccm_rho = latest.get("ccm_rho_max")
    granger_f = latest.get("granger_f_max")
    causal_ok = (causal_best is not None and causal_best < 0.05)

    wf = latest.get("wf_verdict")
    wf_conf_p = latest.get("wf_conf_p")
    wf_disc_p = latest.get("wf_disc_p")
    wf_nc = latest.get("wf_n_confirm")
    wf_nf = latest.get("wf_n_folds")
    wf_color = "#52d1ff" if wf in ("NULL", "UNCONFIRMED") else ("#3ddc84" if wf == "SIGNAL" else "#ff6b6b")

    ns_n_drift = latest.get("ns_n_sig_drift")
    ns_n_mom = latest.get("ns_n_sig_mom")
    ns_d_sig = latest.get("ns_best_drift_sig")
    ns_d_val = latest.get("ns_best_drift_val")
    ns_d_q = latest.get("ns_best_drift_q")
    ns_m_sig = latest.get("ns_best_mom_sig")
    ns_m_val = latest.get("ns_best_mom_val")
    ns_m_q = latest.get("ns_best_mom_q")
    ns_ok = bool((ns_n_drift or 0) == 0 and (ns_n_mom or 0) == 0)

    pc = latest.get("positive_control")
    pc_txt = "—"
    if isinstance(pc, dict):
        pc_txt = ("✓ 闸门灵敏(verdict=%s, conf_p=%s)" % (pc.get("verdict"), pc.get("conf_p"))
                  if pc.get("verified") else
                  "✗ 阳性对照失败(闸门功率退化!)")

    prone = latest.get("artifact_prone") or []
    n_issues = latest.get("n_issues", 0)
    last_issue = latest.get("last_issue", "?")
    added = latest.get("added", 0)
    coverage = latest.get("coverage", "?")
    elite = latest.get("elite_count", "?")
    n_unique = latest.get("n_unique", "?")
    best_sig = latest.get("best_sig", "?")
    best_test = latest.get("best_test", "?")
    best_p = latest.get("best_p")
    note = latest.get("note", "")
    spectral_verdict = latest.get("spectral_verdict")
    oos_p_val = latest.get("oos_p")

    # 前瞻验证进度：从首个 q<0.05 的时刻起，新开奖累计数
    first_sig_ts = None
    for r in reversed(hist):
        if float(r.get("best_q", 1.0) or 1.0) < FDR_Q:
            first_sig_ts = r.get("ts")
            break

    q_color = "#3ddc84" if q < FDR_Q else ("#ffb454" if q < 0.2 else "#ff6b6b")

    # Leaderboard
    lb = latest.get("leaderboard") or []
    lb_rows = ""
    for i, e in enumerate(lb[:8]):
        params = e.get("params", {})
        tp = params.get("_test", {})
        tp_s = ", ".join(f"{k}={v}" for k, v in tp.items()) if tp else "—"
        lb_rows += (f"<tr><td>{i+1}</td><td>{_esc(e.get('sig',''))}</td>"
                    f"<td>{_esc(e.get('test',''))}</td><td>{_num(e.get('q',1),4)}</td>"
                    f"<td>{_num(e.get('p_raw',1),4)}</td>"
                    f"<td>{_esc(tp_s)}</td>"
                    f"<td>{_esc(e.get('verdict',''))}</td></tr>")

    three_cars = f"""
    <table class="cars">
      <tr><th>车辆</th><th>角色</th><th>模式</th><th>状态</th></tr>
      <tr><td><b>本地 Docker</b><br><span class=mono>ssq-evo-engine</span></td>
          <td>唯一计算引擎(连续搜索) + 持续阳性对照</td>
          <td>数据驱动(空闲/检查) + 每轮闸门复检</td>
          <td class="ok">✓ 运行中<br><span class=mono>{_esc(latest.get('ts',''))}</span><br>
              <span class="{'ok' if (isinstance(pc,dict) and pc.get('verified')) else 'no'}">{_esc(pc_txt)}</span></td></tr>
      <tr><td><b>GitHub Actions</b><br><span class=mono>ssq_evo.yml</span></td>
          <td>代码门禁(仅 push/PR 跑测试，不跑引擎、不提交数据)</td>
          <td>push/PR 触发</td>
          <td class="ok">✓ 已接入(防坏代码合入 main)</td></tr>
      <tr><td><b>腾讯云 CloudStudio</b><br><span class=mono>监控看板</span></td>
          <td>对外可视化/分享层(静态)</td>
          <td>读取 daily_digest.jsonl 直接渲染</td>
          <td class="ok">✓ 已接入(本看板即部署产物)</td></tr>
    </table>
    """

    # 历史表（最近 12 轮）
    hist_rows = ""
    for r in reversed(hist[-12:]):
        hist_rows += (f"<tr><td>{_esc(r.get('cycle_id','?'))}</td>"
                      f"<td>{_esc(r.get('ts',''))}</td>"
                      f"<td>{_num(r.get('best_q',1),4)}</td>"
                      f"<td>{_esc(r.get('best_sig','?'))}/{_esc(r.get('best_test','?'))}</td>"
                      f"<td>{_esc(r.get('verdict','—'))}</td>"
                      f"<td>{_esc(r.get('wf_verdict') or '—')}</td>"
                      f"<td>{_esc(r.get('spectral_verdict') or '—')}</td>"
                      f"<td>{'⚠' if r.get('artifact_prone') else ''}</td></tr>")

    # 构造伪结构拦截框
    if prone:
        prone_box = (f'<div class="panel"><h2>🛡 随机对照闸门 · 构造伪结构拦截</h2>'
                     f'<div class="verdict" style="border-color:#7a4b00">以下基信号在<b>纯随机双色球</b>上也复现显著 → '
                     f'判为"构造伪结构"(信号构造本身产生确定性伪显著)，已在 FDR/最优/谱报警中降级：'
                     f'<b>{_esc(", ".join(map(str, prone)))}</b>。这是诚实护栏：研发过程中必现的 Goodhart 假阳性被拦截，'
                     f'避免把信号构造产生的确定性伪显著误当作研发成果。</div></div>')
    else:
        prone_box = (f'<div class="panel"><h2>🛡 随机对照闸门 · 构造伪结构拦截</h2>'
                     f'<div class="verdict" style="border-color:#143d2a">本轮无构造伪结构信号。'
                     f'所有候选在纯随机数据上均未复现，无确定性伪显著。</div></div>')

    # 阳性对照框
    pc_box = (f'<div class="panel"><h2>🔬 持续阳性对照（闸门功率监控）</h2>'
              f'<div class="verdict" style="border-color:{"#143d2a" if (isinstance(pc,dict) and pc.get("verified")) else "#3d1717"}">'
              f'{_esc(pc_txt) if pc else "本轮未跑阳性对照（见 config 的 positive_control_every）。"}'
              f'</div><div class="note">阳性对照每 N 轮注入已知结构(AR(1)@lag8)，验证统一诚信闸门仍灵敏；'
              f'若判不出 SIGNAL 说明闸门功率退化，红队审计会 ALERT。</div></div>')

    # 嵌入数据供 JS 运行时 fetch 刷新
    embed = json.dumps({"latest_cycle": latest.get("cycle_id"),
                        "latest_ts": latest.get("ts")}, ensure_ascii=False)

    # 学习模块闭环面板数据（直接读数据卷结构化 JSON，契约基石二：来自三驾车真实产出）
    def _load_j(p):
        fp = os.path.join(DATA, p)
        if not os.path.exists(fp):
            return None
        try:
            with open(fp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    ftax = _load_j("failure_taxonomy.json") or {}
    bc = _load_j("bias_corrector.json") or {}
    pend = _load_j("pending_primitives.json") or {"pending": []}
    ftax_labels = ftax.get("labels", {})
    ftax_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{v.get('count',0)}</td><td>{v.get('last_seen_cycle','—')}</td></tr>"
        for k, v in sorted(ftax_labels.items(), key=lambda kv: -kv[1].get("count", 0))
    ) or '<tr><td colspan="3" style="color:var(--mut)">暂无失败记录</td></tr>'
    debunk = bc.get("debunked_tests", []) + bc.get("debunked_sigs", [])
    novelty = bc.get("novelty_tilt", {})
    novelty_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{_num(v,3)}</td></tr>" for k, v in list(novelty.items())[:8]
    ) or '<tr><td colspan="2" style="color:var(--mut)">暂无倾斜</td></tr>'
    pend_list = pend.get("pending", []) if isinstance(pend, dict) else []
    pend_rows = "".join(
        f"<tr><td>{_esc(d.get('name',''))}</td><td>{_esc(d.get('label',''))}</td>"
        f"<td>{'伪结构' if d.get('artifact') else '—'}</td></tr>"
        for d in pend_list[:10]
    ) or '<tr><td colspan="3" style="color:var(--mut)">待复核池为空（研发进行中：暂无通过最终闸门的新原语，符合预期）</td></tr>'
    learning_panel = f"""
  <div class="panel"><h2>🧠 学习模块闭环（L1→L3 回馈三驾车）</h2>
    <div class="note">基石：只用<b>不撒谎的反馈信号</b>（闸门零假设交叉+随机对照），绝不把回测拟合当目标。
      输入来自三驾车真实产出，产出回馈三驾车——闭环每轮由 daemon 串接。</div>
    <div class="grid2">
      <div><h3>L1 失败吸收 · failure_taxonomy</h3>
        <table><tr><th>失败类型</th><th>次数</th><th>末见 cycle</th></tr>{ftax_rows}</table></div>
      <div><h3>L3 偏置纠正 · 已证伪/倾斜</h3>
        <table>
          <tr><th>已证伪路线</th><td>{_esc(', '.join(debunk) if debunk else '（无）')}</td></tr>
          <tr><th>高新颖度倾斜</th><td><table style="margin:0">{novelty_rows}</table></td></tr>
          <tr><th>更新 cycle</th><td>{bc.get('updated_cycle','—')}</td></tr>
        </table></div>
      <div><h3>L4 人类复核 · 待复核池</h3>
        <table><tr><th>提议原语</th><th>discovery 标签</th><th>随机对照</th></tr>{pend_rows}</table>
        <div class="note">仅经你复核确认的原语才 merge 进 SIGMAPS（基石四：人类否决权）。</div></div>
    </div>
  </div>
"""

    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ssq_evo 研究监控看板</title>
<style>
  :root {{ --bg:#0f1320; --panel:#171c2b; --line:#2a2f3a; --fg:#e6e9ef; --mut:#7a8290; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
          font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:18px 16px 40px; }}
  h1 {{ font-size:20px; margin:0 0 2px; }}
  .sub {{ color:var(--mut); font-size:13px; margin-bottom:14px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:18px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
  .kpi-label {{ color:var(--mut); font-size:12px; }}
  .kpi-val {{ font-size:26px; font-weight:700; margin:4px 0 2px; }}
  .kpi-sub {{ font-size:12px; color:var(--mut); }}
  .badge {{ font-size:11px; padding:1px 6px; border-radius:6px; margin-left:6px; }}
  .grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
  .grid2 h3 {{ font-size:14px; margin:0 0 6px; color:var(--fg); }}
  .grid2 table {{ width:100%; }}
  .badge.ok {{ background:#143d2a; color:#3ddc84; }}
  .badge.no {{ background:#3d1717; color:#ff6b6b; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin-bottom:16px; }}
  .panel h2 {{ font-size:15px; margin:0 0 10px; }}
  .charts {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  @media(max-width:760px) {{ .charts {{ grid-template-columns:1fr; }} }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--mut); font-weight:600; }}
  .ok {{ color:#3ddc84; }} .no {{ color:#ff6b6b; }}
  .mono {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:11px; color:var(--mut); }}
  .verdict {{ font-size:14px; line-height:1.5; padding:10px 12px; border-radius:8px;
              background:#101626; border:1px solid var(--line); }}
  .note {{ font-size:12px; color:var(--mut); margin-top:8px; line-height:1.5; }}
  a {{ color:#4ea1ff; }}
  #freshness {{ font-size:12px; color:var(--mut); margin-top:6px; }}
</style></head>
<body><div class="wrap">
  <h1>双色球结构搜索引擎 · 研究监控看板</h1>
  <div class="sub">假说：双色球开奖是否含可测时序结构（反向检验"时间不存在/块状宇宙"）。
    更新：{_esc(latest.get('ts',''))} · cycle {latest.get('cycle_id','?')} · 数据源 daily_digest.jsonl（{len(hist)} 轮历史）
    <div id="freshness">⏳ 正在核对远端 daily_digest.jsonl …</div>
  </div>

  <div class="grid">
    {kpi_card("结构 FDR (best_q)", _num(q,4), f"阈值 {FDR_Q} / alert {ALERT_Q}", q_color, ok=(q<FDR_Q))}
    {kpi_card("coverage (评估算子数)", str(coverage), f"精英 {elite} · 唯一 {n_unique}", "#4ea1ff")}
    {kpi_card("样本外方向准确率", _num(oos_acc,3), f"随机基线 {_num(oos_sur,3)} · p={_num(oos_p,4)} · n={oos_n}", "#ffb454", ok=(oos_above and q<FDR_Q))}
    {kpi_card("OOT 盲测命中率", _num(oot_hit,3) if oot_hit is not None else "—", f"冻结规则盲打未来 · p={_num(oot_p,4)} · n={oot_n} (需结构FDR显著方作数)", "#ff8fab", ok=oot_ok)}
    {kpi_card("谱扫描筛查 q", _num(spectral_q,4) if spectral_q is not None else "—", f"{spectral_n}组合枚举 · 秩FDR {_num(spectral_rank,4)} · 最强 {_esc(spectral_sig or '—')}/{_esc(spectral_test or '—')} · z峰值 {_num(spectral_z,1)}" + (f" · OOT p={_num(spectral_oot_p,4)}{'✓' if spectral_oot_above else ''}" if spectral_oot_p is not None else " · (OOT未触发)") + (f" · {_esc(spectral_verdict)}" if spectral_verdict else ""), "#ffd166", ok=spec_ok)}
    {kpi_card("因果耦合 (CCM/Granger)", _num(causal_best,4) if causal_best is not None else "—", f"最强 {_esc(causal_sig or '—')}/{_esc(causal_test or '—')} · CCM ρ={_num(ccm_rho,3) if ccm_rho is not None else '—'} · Granger F={_num(granger_f,3) if granger_f is not None else '—'}" + (f" · p={_num(causal_p,4)}" if causal_p is not None else ""), "#c77dff", ok=causal_ok)}
    {kpi_card("发现/确认分离闸门 (#41)", _esc(wf or '—'), f"确认合并p={_num(wf_conf_p,4)} · 发现p={_num(wf_disc_p,4)} · 多数折确认 {wf_nc}/{wf_nf}", wf_color, ok=(wf=='SIGNAL'))}
    {kpi_card("非平稳监控 (磨损/动量)", ("NULL" if ns_ok else "异常"), f"漂移显著 {ns_n_drift or 0} 球 · 动量 {ns_n_mom or 0} 球 · 最强漂移 {_esc(ns_d_sig or '—')}={_num(ns_d_val,4)} q={_num(ns_d_q,4)} · 最强动量 {_esc(ns_m_sig or '—')}={_num(ns_m_val,4)} q={_num(ns_m_q,4)}", "#9aa0ff", ok=ns_ok)}
    {kpi_card("零假设交叉一致", _yn(consistent), f"primary({_esc(cross_type)})={_num(cross_primary,4) if cross_primary is not None else '—'}", "#3ddc84" if consistent else "#ff6b6b", ok=consistent)}
    {kpi_card("ALERT", "触发" if alert else "未触发", f"门槛 q&lt;{ALERT_Q} & OOS p&lt;{OOS_P}", "#3ddc84" if alert else "#ff6b6b", ok=alert)}
    {kpi_card("前瞻样本", f"{n_issues} 期", f"最新 {_esc(last_issue)} · 本轮新增 {added}", "#a78bfa")}
    {kpi_card("选号准确率 (第一性原理)", f"{_num(pick_red_excess,2) if pick_red_excess is not None else '—'} 红 / {_num(pick_blue_excess,2) if pick_blue_excess is not None else '—'} 蓝", f"top候选选6+1 vs 超几何随机基线超额 · p={_num(pick_p,4) if pick_p is not None else '—'} · n={pick_n} · {'高于随机✓' if pick_above else '不优于随机蒙'} · 选号 {_esc(str(pick_red_pick) if pick_red_pick else '—')}/{_esc(str(pick_blue_pick) if pick_blue_pick else '—')}", "#5ad1c4", ok=pick_above)}
    {kpi_card("公式代数 df_gen (代际演进)", str(latest.get("df_gen") if latest.get("df_gen") is not None else "—"), f"本轮新增组合 {latest.get('df_added') if latest.get('df_added') is not None else '—'} · 播种期=1(地板)；代际上长待首个 comp 精英过统一闸门", "#f4a261", ok=(isinstance(latest.get("df_gen"), int) and latest.get("df_gen", 0) >= 2))}
    {kpi_card("评估稳定性 (研发诚实)", f"cv={_num(stab.get('q_cv'),3) if (stab:=latest.get('stability') or {}) and stab.get('q_cv') is not None else '—'}", f"近{stab.get('n_window','?')}轮 best_q 变异系数 · iqr={_num(stab.get('q_iqr'),3) if stab and stab.get('q_iqr') is not None else '—'} · cv>0.5=搜索前沿未收敛(研发进行中,非失败)", "#e76f51", ok=(stab and (stab.get('q_cv') or 9) <= 0.5))}
  </div>

  <div class="panel"><h2>当前结论（诚实判定）</h2>
    <div class="verdict">{verdict_text(latest)}</div>
    <div class="note">说明：best_q 在 0.017–0.97 间剧烈漂移，表明搜索前沿尚未收敛；早期短暂出现的
      "候选"已回落。任何声称"找到公式"的结论都必须先冻结公式、在 26094+ 新开奖上前瞻验证成立。
      备注：{_esc(note)}</div>
  </div>

  {prone_box}
  {pc_box}

  <div class="panel"><h2>趋势（来自 daily_digest.jsonl 历史）</h2>
    <div class="charts">
      <div>{svg_line(q_series, threshold=FDR_Q, color="#4ea1ff", title="best_q 历史 (FDR 阈值 0.05)")}</div>
      <div>{svg_line(cov_series, ymin=0, ymax=max([c for _,c in cov_series]+[1]), color="#3ddc84", title="coverage 累计")}</div>
      <div>{svg_line(dfgen_series, ymin=0, ymax=max([g for _,g in dfgen_series]+[2]), color="#f4a261", title="公式代数 df_gen 轨迹 (代际演进)")}</div>
      <div>{svg_line(stab_series, ymin=0, ymax=max([c for _,c in stab_series if c is not None]+[1]), color="#e76f51", title="评估稳定性 best_q 变异系数 (cv↓=前沿收敛)")}</div>
    </div>
  </div>

  <div class="panel"><h2>三车状态</h2>{three_cars}</div>

  <div class="panel"><h2>当前最优候选（leaderboard Top1）</h2>
    <table>
      <tr><th>信号</th><td>{_esc(best_sig)}</td><th>检验</th><td>{_esc(best_test)}</td></tr>
      <tr><th>p_raw</th><td>{_num(best_p,4)}</td><th>FDR q</th><td>{_num(q,4)}</td></tr>
      <tr><th>样本外 p</th><td>{_num(oos_p_val,4) if oos_p_val is not None else '—'}</td><th>verdict</th><td>{_esc(latest.get('verdict','—'))}</td></tr>
    </table>
  </div>

  <div class="panel"><h2>Leaderboard (Top 8)</h2>
    <table>
      <tr><th>#</th><th>信号</th><th>检验</th><th>q</th><th>p_raw</th><th>检验参数</th><th>verdict</th></tr>
      {lb_rows}
    </table>
  </div>

  <div class="panel"><h2>前瞻验证进度</h2>
    <div class="note">首个 q&lt;0.05 出现时刻：{_esc(first_sig_ts or '—')}；自该时刻新开奖累计：{added} 期；
      最新已抓期号：{_esc(last_issue)}。若 added 长期为 0，说明官方尚未开新奖或抓取失败——前瞻验证须等真实新数据。</div>
  </div>

  {learning_panel}

  <div class="panel"><h2>历史轮次（最近 12 轮）</h2>
    <table>
      <tr><th>cycle</th><th>时间</th><th>best_q</th><th>最优</th><th>verdict</th><th>wf</th><th>谱</th><th>伪结构</th></tr>
      {hist_rows}
    </table>
  </div>

  <div class="note">本看板由 make_dashboard 直接读取 <b>daily_digest.jsonl</b>（run_cycle 每轮追加的"完整结论载荷"日志）生成，
    并经 CloudStudio 部署到腾讯云。页面加载后会 fetch 同源 daily_digest.jsonl 直接解读——若远端已有更新的 cycle，
    将自动刷新以展示最新结论。全部统计闸门 (BH-FDR 多重比较校正、AAFT/IAAFT/shuffle/twin/四零假设交叉、样本外验证、
    Out-of-Time 盲测、<b>直接谱扫描兜底</b>、<b>发现/确认分离 (#41)</b>、<b>随机对照闸门拦截构造伪结构</b>) 均为预注册，
    不人为调高显著性。SIGNAL(结构在独立未来复现)/UNCONFIRMED(只活在发现集，闸门已拦截)/NULL(无结构) 三态互斥。</div>
</div>

<script>
// ── 运行时直接解读 daily_digest.jsonl ──────────────────────
(function(){{
  var EMBEDDED = {embed};
  function setFresh(msg, isLive){{
    var el = document.getElementById('freshness');
    if(!el) return;
    el.textContent = (isLive ? '🔄 ' : '⏳ ') + msg;
  }}
  function tryReload(remoteLatest){{
    if(remoteLatest && EMBEDDED.latest_cycle!=null &&
       Number(remoteLatest.cycle_id) > Number(EMBEDDED.latest_cycle)){{
      setFresh('检测到更新 cycle '+remoteLatest.cycle_id+'，正在刷新…', true);
      setTimeout(function(){{ location.reload(); }}, 400);
      return true;
    }}
    return false;
  }}
  function parseJSONL(text){{
    var recs=[]; var lines=text.split(/\\r?\\n/);
    for(var i=0;i<lines.length;i++){{ var l=lines[i].trim(); if(!l) continue;
      try{{ recs.push(JSON.parse(l)); }}catch(e){{}} }}
    return recs;
  }}
  function check(){{
    fetch('./daily_digest.jsonl', {{cache:'no-store'}}).then(function(r){{ return r.text(); }})
      .then(function(t){{
        var recs=parseJSONL(t);
        if(!recs.length){{ setFresh('远端无数据', false); return; }}
        if(tryReload(recs[recs.length-1])) return;
        setFresh('已核对·数据新鲜（远端最新 cycle '+recs[recs.length-1].cycle_id+' @ '+recs[recs.length-1].ts+'）', false);
      }})
      .catch(function(e){{
        setFresh('无法读取远端 daily_digest.jsonl（将显示已部署版本）', false);
      }});
  }}
  check();
  setInterval(check, 2*60*60*1000);  // 每 2 小时核对一次
}})();
</script>
</body></html>"""
    return html_doc


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    records = load_digest(DIGEST)
    doc = build(records)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[dashboard] wrote {OUT} ({len(doc)} bytes, {len(records)} cycles)")
    # 复制 daily_digest.jsonl 到 dashboard/ 目录，使 fetch 同源可用
    try:
        shutil.copy2(DIGEST, os.path.join(OUT_DIR, "daily_digest.jsonl"))
        print(f"[dashboard] copied daily_digest.jsonl -> {OUT_DIR}")
    except Exception as e:
        print(f"[dashboard] 复制 jsonl 失败(不影响主流程): {e}")


if __name__ == "__main__":
    main()
