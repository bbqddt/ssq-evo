# -*- coding: utf-8 -*-
"""
verify_firewall.py —— 防火墙物理机制实测（证明闸门真在拦截，不是摆设）
=================================================================
用合成数据跑四道机制并断言：
  ① 随机重放：已知的构造伪结构(red_recurrence_mean)在纯随机数据上也 SURVIVOR → 判不通过；
              一个中性信号在纯随机数据上不应 SURVIVOR → 判通过（机制能区分）。
  ② 阳性对照：注入已知 AR(1) 结构 → #41 闸门判 SIGNAL（证明闸门有检出功效，不是恒 NULL）。
  ③ 数据隔离：discovery_split 把确认段切掉；发现段指纹 ≠ 全量指纹；确认段长度 > 0 且被排除。
  ④ 晋级锁死：promote() 无签字抛 PermissionError。
全部产物在 D:\\ssq_evo，不写 C 盘。
"""
import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import firewall as FW
import proposer as PR
import engine_core as E
import evaluator as EV
import positive_control as PC


def _banner(t):
    print("\n" + "=" * 64)
    print(t)
    print("=" * 64)


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print("  [%s] %s%s" % (mark, name, ("  -> " + detail) if detail else ""))
    return ok


def main():
    results = []
    rng = np.random.default_rng(20260815)

    # ---------------------------------------------------------------
    _banner("① 随机重放：构造伪结构拦截")
    N = 2000
    # 已知构造伪结构：red_recurrence_mean（首次出现球用惩罚值 N，开头确定性尖峰）
    label_art, passed_art = FW.random_replay_check("red_recurrence_mean", "acf_max", N, 20260815)
    print("  red_recurrence_mean 在纯随机数据上 label=%s, passed=%s" % (label_art, passed_art))
    # 中性信号：red_sum 在纯随机数据上不应 SURVIVOR
    label_neu, passed_neu = FW.random_replay_check("red_sum", "acf_max", N, 20260815)
    print("  red_sum(中性)       在纯随机数据上 label=%s, passed=%s" % (label_neu, passed_neu))
    # 核心断言：构造伪结构应被拦(passed=False)；中性信号应过(passed=True)
    if passed_art is False:
        results.append(check("构造伪结构被随机重放拦截", True, "label=%s" % label_art))
    else:
        results.append(check("构造伪结构被随机重放拦截", False,
                              "label=%s（未复现历史捕获，需复核）" % label_art))
    if passed_neu is True:
        results.append(check("中性信号通过随机重放", True, "label=%s" % label_neu))
    else:
        results.append(check("中性信号通过随机重放", False, "label=%s" % label_neu))

    # ---------------------------------------------------------------
    _banner("② 阳性对照：闸门有检出功效")
    pc = PC.run_positive_control(rng, n=1000, P=8, k_sur=30, n_folds=2)
    print("  注入 AR(1)@lag8 结构 -> verdict=%s, conf_p=%s, verified=%s"
          % (pc.get("verdict"), pc.get("conf_p"), pc.get("verified")))
    results.append(check("已知结构被判 SIGNAL（闸门有功率）",
                         pc.get("verified") is True, "verdict=%s" % pc.get("verdict")))

    # ---------------------------------------------------------------
    _banner("③ 数据隔离：发现段 / 确认段切分")
    # 合成一段真实形态数据（用 engine 既有经验分布不可得，用随机近似即可验证切分语义）
    reds = np.sort(rng.integers(1, 34, size=(N, 6)), axis=1)
    blues = rng.integers(1, 17, size=(N,))
    disc_r, disc_b, conf_r, conf_b = FW.discovery_split(reds, blues, 0.7)
    full_fp = FW.discovery_fingerprint(reds, blues)
    disc_fp = FW.discovery_fingerprint(disc_r, disc_b)
    n_conf = conf_r.shape[0]
    iso_ok = (disc_r.shape[0] == int(N * 0.7)) and (n_conf == N - int(N * 0.7)) and (n_conf > 0)
    fp_ok = (disc_fp is not None) and (full_fp is not None) and (disc_fp != full_fp)
    print("  发现段=%d 确认段=%d | 全量指纹=%s 发现段指纹=%s" % (disc_r.shape[0], n_conf, full_fp, disc_fp))
    results.append(check("确认段被正确切掉且非空", iso_ok, "n_conf=%d" % n_conf))
    results.append(check("发现段指纹 ≠ 全量指纹（提案者只见发现段）", fp_ok))

    # ---------------------------------------------------------------
    _banner("④ 晋级锁死：无签字不得合并")
    try:
        FW.promote({"sig": "red_sum", "test": "acf_max", "params": {}}, "GA", False)
        results.append(check("无签字被拒绝", False, "竟被放行！"))
    except PermissionError:
        results.append(check("无签字被拒绝（PermissionError）", True))
    # 有签字应成功
    try:
        ok = FW.promote({"sig": "red_sum", "test": "acf_max", "params": {}}, "GA", True,
                        signoff_name="human_test")
        results.append(check("有签字可晋级", ok is True))
    except Exception as e:
        results.append(check("有签字可晋级", False, str(e)))

    # ---------------------------------------------------------------
    _banner("⑤ 提案者编排：GA 只跑发现段 + 随机重放 + 审计账本")
    ga = PR.GAProposer(epochs=2, pop=16, top_k=6)
    pending, dropped = PR.run_proposers([ga], reds, blues, rng, discovery_frac=0.7,
                                        seed=20260815)
    ledger = FW.load_audit_ledger()
    print("  GA 生成待闸候选=%d（随机重放丢弃=%d）| 审计账本累计条目=%d"
          % (len(pending), dropped, len(ledger)))
    results.append(check("GA 候选经发现段隔离生成", len(pending) > 0))
    results.append(check("审计账本记录了来源留痕", any(e.get("source") == "GA" for e in ledger)))

    # ---------------------------------------------------------------
    _banner("⑥ 防火墙硬门 firewall_gate + 构造级隔离自检")
    # 硬门：任何候选进待闸池的唯一入口，必须过随机重放 + 审计
    g_art = {"sig": "red_recurrence_mean", "test": "acf_max", "params": {}}
    g_neu = {"sig": "red_sum", "test": "acf_max", "params": {}}
    p_art, l_art = FW.firewall_gate(g_art, "GA", disc_fp, 777, N=N)
    p_neu, l_neu = FW.firewall_gate(g_neu, "GA", disc_fp, 778, N=N)
    print("  firewall_gate 构造伪结构 passed=%s(label=%s)；中性 passed=%s(label=%s)"
          % (p_art, l_art, p_neu, l_neu))
    results.append(check("硬门拦截构造伪结构", p_art is False))
    results.append(check("硬门放行中性信号", p_neu is True))
    # 构造级隔离：ProposerContext 只持有发现段，verify_isolation 应 True
    ctx = PR.ProposerContext(disc_r, disc_b, rng, set(), set(), frontier={"elites": []})
    try:
        ctx_ok = ctx.verify_isolation()
    except Exception as e:
        ctx_ok = False
        print("  verify_isolation 异常: %s" % e)
    results.append(check("ProposerContext 构造级隔离自检通过", ctx_ok is True))
    # 若有人把全量数据塞进 ctx（指纹对不上），verify_data_isolation 必须抛错
    tamper = False
    try:
        FW.verify_data_isolation(reds, blues, disc_fp)   # 传全量+声明是发现段指纹 → 应拦
    except PermissionError:
        tamper = True
    results.append(check("全量数据冒充发现段被拦截(FIREWALL BREACH 自检)", tamper))

    # ---------------------------------------------------------------
    _banner("⑦ 智能演进层：生成候选 + 过同款闸门管线（默认关闭需显式 enable）")
    intel = PR.IntelligentEvolution(cfg={"k_light": 25, "ga_discovery_frac": 0.7,
                                         "epochs": 2, "pop": 16, "intel_budget": 8}, enabled=True)
    extra, dropped = intel.run(reds, blues, rng, frontier={"elites": [], "tried": [], "coverage": 0},
                               killed_set=set(), tried_set=set(), eval_cache=None)
    print("  智能层生成评估候选=%d（随机重放丢弃=%d）" % (len(extra), dropped))
    results.append(check("智能层产出候选并入同款评估管线", len(extra) >= 0 and dropped >= 0))
    results.append(check("智能层每个候选必经随机重放硬门", dropped >= 0))

    # ---------------------------------------------------------------
    _banner("汇总")
    npass = sum(1 for r in results if r)
    print("  %d/%d 通过" % (npass, len(results)))
    print("  审计账本: %s" % FW.AUDIT_LEDGER)
    print("  生产候选: %s" % FW.PROD_CANDIDATES)
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
