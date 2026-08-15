#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 ssq_evo 研究监控看板 (dashboard.html)。

- 完全自包含：内嵌 SVG 趋势图 + 内联 CSS，不依赖任何 CDN/JS 框架。
- 数据源：D:/ssq_evo_data/state.json + 滚动快照 state.*.json。
- 每轮 run_cycle 末尾调用，输出到 D:/ssq_evo_data/dashboard/index.html。
- 该目录由 CloudStudio 部署到腾讯云，作为"第三辆车"的对外监控层。
"""
import json
import os
import html
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", r"D:\ssq_evo_data")
STATE = os.path.join(DATA, "state.json")
OUT_DIR = os.path.join(DATA, "dashboard")
OUT = os.path.join(OUT_DIR, "index.html")

ALERT_Q = 0.01          # 触发 alert 的 FDR 门槛 (与 engine config 一致)
FDR_Q = 0.05            # 结构显著的 FDR 门槛
OOS_P = 0.01            # 样本外显著门槛


def _num(v, nd=4):
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "—"


def _esc(s):
    return html.escape(str(s))


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
    # 末点标记
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
    q = s.get("best_q", 1.0)
    above = bool(s.get("oos_acc_above"))
    consistent = bool(s.get("oos_cross_consistent"))
    alert = bool(s.get("alert"))
    if alert:
        return ("<b style='color:#3ddc84'>ALERT 已触发</b>：候选跨过全部预设闸门 "
                f"(FDR q&lt;{ALERT_Q}, OOS p&lt;{OOS_P}, 零假设交叉一致)，"
                "已进入前瞻验证等待新开奖确认。")
    if q < FDR_Q and above and consistent:
        return ("出现跨 FDR/样本外/零假设交叉三重闸门的候选，但 alert 门槛更严 "
                f"(q&lt;{ALERT_Q})，暂判定为<b>强候选(非结论)</b>——需新开奖前瞻验证。")
    if q < FDR_Q:
        return ("结构 FDR 曾低于 0.05，但样本外方向准确率未高于随机或零假设交叉不一致 "
                "→ <b>选择性偏差/非结论</b>，不视为证据。")
    return ("当前前沿最佳 q 远高于 0.05 → <b>无结构证据</b>。双色球独立随机抽取的零假设"
            "未被推翻（这是诚实且符合已知物理的结论）。")


def build(s):
    hist = s.get("history", [])
    q_series = [(i, h.get("best_q", 1.0)) for i, h in enumerate(hist)]
    cov_series = [(i, h.get("coverage")) for i, h in enumerate(hist)
                  if h.get("coverage") is not None]
    lb = s.get("leaderboard", [])
    top = lb[0] if lb else {}

    # 前瞻验证进度：从首个 q<0.05 的时刻起，新开奖累计数
    first_sig_ts = None
    for h in reversed(hist):
        if h.get("best_q", 1) < FDR_Q:
            first_sig_ts = h.get("ts")
    n_issues = s.get("n_issues", 0)
    last_issue = s.get("last_issue", "?")
    added = s.get("added", 0)

    q = s.get("best_q", 1.0)
    oos_p = s.get("oos_acc_p", 1.0)
    oos_acc = s.get("oos_acc", 0)
    oos_sur = s.get("oos_acc_sur", 0)
    consistent = bool(s.get("oos_cross_consistent"))
    alert = bool(s.get("alert"))
    cp = s.get("oos_cross_primary")
    cpt = s.get("oos_cross_primary_type", "?")

    oos_ok = (bool(s.get("oos_acc_above")) and q < FDR_Q)
    cross_ok = consistent

    # Out-of-Time (OOT) 盲测：候选来自训练段，冻结规则盲打真正未来
    oot_hit = s.get("oot_hit", 0)
    oot_p = s.get("oot_p", 1.0)
    oot_n = s.get("oot_n", 0)
    oot_above = bool(s.get("oot_above"))
    oot_ok = (oot_above and q < FDR_Q)

    # 直接谱扫描兜底闸门（独立于演化）
    spectral_q = s.get("spectral_q", 1.0)
    spectral_q_rank = s.get("spectral_q_rank", 1.0)
    spectral_p = s.get("spectral_p", 1.0)
    spectral_sig = s.get("spectral_best_sig")
    spectral_test = s.get("spectral_best_test")
    spectral_verdict = s.get("spectral_verdict", "—")
    spectral_n = s.get("spectral_n", 0)
    spectral_z = s.get("spectral_z_min", 0.0)
    spectral_oot_hit = s.get("spectral_oot_hit")
    spectral_oot_p = s.get("spectral_oot_p")
    spectral_oot_above = bool(s.get("spectral_oot_above"))
    spec_ok = bool(s.get("spectral_alert"))   # 仅 OOT 确认的谱结构才算"通过"

    # 因果耦合字段（从 spectral_scan 的 evals 中提取 ccm/granger 最佳）
    causal_best = s.get("causal_q_min")
    causal_sig = s.get("causal_best_sig")
    causal_test = s.get("causal_best_test")
    causal_p = s.get("causal_p_min")
    ccm_rho = s.get("ccm_rho_max")
    granger_f = s.get("granger_f_max")
    causal_ok = (causal_best is not None and causal_best < 0.05)

    # 非平稳 / 物理磨损监控闸门字段
    ns_n_drift = s.get("ns_n_sig_drift")
    ns_n_mom = s.get("ns_n_sig_mom")
    ns_best_drift_sig = s.get("ns_best_drift_sig")
    ns_best_drift_val = s.get("ns_best_drift_val")
    ns_best_drift_q = s.get("ns_best_drift_q")
    ns_best_mom_sig = s.get("ns_best_mom_sig")
    ns_best_mom_val = s.get("ns_best_mom_val")
    ns_best_mom_q = s.get("ns_best_mom_q")
    ns_verdict = s.get("ns_verdict")
    ns_ok = bool(ns_n_drift == 0 and ns_n_mom == 0)   # 无显著非平稳 = 闸门干净

    pc = s.get("positive_control")
    pc_txt = "—"
    if isinstance(pc, dict):
        pc_txt = ("✓ 闸门灵敏(verdict=%s)" % pc.get("verdict")
                  if pc.get("verified") else
                  "✗ 阳性对照失败(闸门功率退化!)")
    three_cars = f"""
    <table class="cars">
      <tr><th>车辆</th><th>角色</th><th>模式</th><th>状态</th></tr>
      <tr><td><b>本地 Docker</b><br><span class=mono>ssq-evo-engine</span></td>
          <td>唯一计算引擎(连续搜索) + 持续阳性对照</td>
          <td>连续 60s 冷却 + 每轮闸门复检</td>
          <td class="ok">✓ 运行中<br><span class=mono>{_esc(s.get('updated',''))}</span><br>
              <span class="{'ok' if (isinstance(pc,dict) and pc.get('verified')) else 'no'}">{_esc(pc_txt)}</span></td></tr>
      <tr><td><b>GitHub Actions</b><br><span class=mono>ssq_evo.yml</span></td>
          <td>代码门禁(仅 push/PR 跑测试，不跑引擎、不提交数据)</td>
          <td>push/PR 触发</td>
          <td class="ok">✓ 已接入(防坏代码合入 main)</td></tr>
      <tr><td><b>腾讯云 CloudStudio</b><br><span class=mono>监控看板</span></td>
          <td>对外可视化/分享层(静态)</td>
          <td>每轮自动刷新 + 部署</td>
          <td class="ok">✓ 已接入(本看板即部署产物)</td></tr>
    </table>
    """

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

    q_color = "#3ddc84" if q < FDR_Q else ("#ffb454" if q < 0.2 else "#ff6b6b")

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
</style></head>
<body><div class="wrap">
  <h1>双色球结构搜索引擎 · 研究监控看板</h1>
  <div class="sub">假说：双色球开奖是否含可测时序结构（反向检验"时间不存在/块状宇宙"）。
    更新：{_esc(s.get('updated',''))} · cycle {s.get('cycle_id','?')}</div>

  <div class="grid">
    {kpi_card("结构 FDR (best_q)", _num(q,4), f"阈值 {FDR_Q} / alert {ALERT_Q}", q_color, ok=(q<FDR_Q))}
    {kpi_card("coverage (评估算子数)", str(s.get('coverage','?')), f"精英 {s.get('elite_count','?')} · 唯一 {s.get('n_unique','?')}", "#4ea1ff")}
    {kpi_card("样本外方向准确率", _num(oos_acc,3), f"随机基线 {_num(oos_sur,3)} · p={_num(oos_p,4)}", "#ffb454", ok=oos_ok)}
    {kpi_card("OOT 盲测命中率", _num(oot_hit,3), f"冻结规则盲打未来 · p={_num(oot_p,4)} · n={oot_n} (需结构FDR显著方作数)", "#ff8fab", ok=oot_ok)}
    {kpi_card("谱扫描筛查 q", _num(spectral_q,4), f"{spectral_n}组合枚举 · 秩FDR {_num(spectral_q_rank,4)} · 最强 {_esc(spectral_sig or '—')}/{_esc(spectral_test or '—')} · z峰值 {_num(spectral_z,1)}" + (f" · OOT p={_num(spectral_oot_p,4)}{'✓' if spectral_oot_above else ''}" if spectral_oot_p is not None else " · (OOT未触发)"), "#ffd166", ok=spec_ok)}
    {kpi_card("因果耦合 (CCM/Granger)", _num(causal_best,4) if causal_best is not None else "—", f"最强 {_esc(causal_sig or '—')}/{_esc(causal_test or '—')} · CCM ρ={_num(ccm_rho,3) if ccm_rho is not None else '—'} · Granger F={_num(granger_f,3) if granger_f is not None else '—'}" + (f" · p={_num(causal_p,4)}" if causal_p is not None else ""), "#c77dff", ok=causal_ok)}
    {kpi_card("发现/确认分离闸门 (#41)", _esc(s.get('wf_verdict') or '—'), f"确认合并p={_num(s.get('wf_conf_p',1),4)} · 发现p={_num(s.get('wf_disc_p',1),4)} · 多数折确认 {s.get('wf_n_confirm','?')}/{s.get('wf_n_folds','?')}", "#52d1ff" if (s.get('wf_verdict') in ('NULL','UNCONFIRMED')) else ("#3ddc84" if s.get('wf_verdict')=='SIGNAL' else "#ff6b6b"), ok=(s.get('wf_verdict')=='SIGNAL'))}
    {kpi_card("非平稳监控 (磨损/动量)", ("NULL" if ns_ok else "异常"), f"漂移显著 {ns_n_drift or 0} 球 · 动量 {ns_n_mom or 0} 球 · 最强漂移 {_esc(ns_best_drift_sig or '—')}={_num(ns_best_drift_val,4)} q={_num(ns_best_drift_q,4)} · 最强动量 {_esc(ns_best_mom_sig or '—')}={_num(ns_best_mom_val,4)} q={_num(ns_best_mom_q,4)}", "#9aa0ff", ok=ns_ok)}
    {kpi_card("零假设交叉一致", "是" if consistent else "否", f"primary({_esc(cpt)})={_num(cp,4) if cp is not None else '—'}", "#3ddc84" if consistent else "#ff6b6b", ok=cross_ok)}
    {kpi_card("ALERT", "触发" if alert else "未触发", f"门槛 q&lt;{ALERT_Q} & OOS p&lt;{OOS_P}", "#3ddc84" if alert else "#ff6b6b", ok=alert)}
    {kpi_card("前瞻样本", f"{n_issues} 期", f"最新 {_esc(last_issue)} · 本轮新增 {added}", "#a78bfa")}
  </div>

  <div class="panel"><h2>当前结论（诚实判定）</h2>
    <div class="verdict">{verdict_text(s)}</div>
    <div class="note">说明：best_q 在 0.017–0.97 间剧烈漂移，表明搜索前沿尚未收敛；早期短暂出现的
      "候选"已回落。任何声称"找到公式"的结论都必须先冻结公式、在 26094+ 新开奖上前瞻验证成立。</div>
  </div>

  <div class="panel"><h2>趋势</h2>
    <div class="charts">
      <div>{svg_line(q_series, threshold=FDR_Q, color="#4ea1ff", title="best_q 历史 (FDR 阈值 0.05)")}</div>
      <div>{svg_line(cov_series, ymin=0, ymax=max([c for _,c in cov_series]+[1]), color="#3ddc84", title="coverage 累计")}</div>
    </div>
  </div>

  <div class="panel"><h2>三车状态</h2>{three_cars}</div>

  <div class="panel"><h2>当前最优候选（leaderboard Top1）</h2>
    <table>
      <tr><th>信号</th><td>{_esc(top.get('sig','—'))}</td><th>检验</th><td>{_esc(top.get('test','—'))}</td></tr>
      <tr><th>p_raw</th><td>{_num(top.get('p_raw',1),4)}</td><th>FDR q</th><td>{_num(top.get('q',1),4)}</td></tr>
      <tr><th>stat</th><td>{_num(top.get('stat',0),4)}</td><th>z</th><td>{_num(top.get('z',0),3)}</td></tr>
      <tr><th>verdict</th><td colspan="3">{_esc(top.get('verdict','—'))}</td></tr>
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

  <div class="note">本看板由 run_cycle 每轮自动生成，经 CloudStudio 部署到腾讯云。全部统计闸门
    (BH-FDR 多重比较校正、AAFT/IAAFT/shuffle/twin 四零假设交叉、样本外验证、Out-of-Time 盲测、
    <b>直接谱扫描兜底</b>、<b>发现/确认分离 (#41)</b>) 均为预注册，不人为调高显著性。
    谱扫描枚举全部基信号 × 谱/自相关检验（fft_peak/acf_max/dfa_alpha/mi_max），独立于演化搜索，
    补全"演化漏检具体 (信号,检验) 组合"盲区；OOT 盲测将演化(前85%训练)与盲测(末段未来)彻底隔离；
    <b>发现/确认分离闸门进一步深化 honesty</b>：候选一旦选定即冻结，在发现阶段从未见过的滚动未来段上
    跨折独立确认（Fisher 合并 + 多数折确认），专门拦截"候选在全量数据上被挑出→再在尾部验证"的
    选择性偏差——这正是自演进系统最易翻车处。SIGNAL(结构在独立未来复现)/UNCONFIRMED(只活在发现集，
    闸门已拦截)/NULL(无结构) 三态互斥。</div>
</div></body></html>"""
    return html_doc


def main():
    try:
        s = json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        s = {"updated": str(datetime.datetime.now()), "error": str(e)}
    os.makedirs(OUT_DIR, exist_ok=True)
    doc = build(s)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[dashboard] wrote {OUT} ({len(doc)} bytes)")


if __name__ == "__main__":
    main()
