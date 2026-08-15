"""红队自审单元测试：验证审计器能抓过度声称、退化统计、null 多重比较噪声、确认段缺失。"""
import json
import os
import tempfile
import redteam_audit as RA


def _base_state():
    """一个干净的 null 状态：未达 FDR、z 正常、#41 已确认、阳性对照已验证。"""
    return {
        "cycle_id": 1,
        "best_q": 0.5, "best_p": 0.4, "best_z": 3.2,
        "fdr_q": 0.05, "alert": False,
        "n_eval": 100,
        "wf_verdict": "SIGNAL", "wf_n_confirm": 3,
        "best_z_history": [3.1, 3.2, 2.9, 3.0],
        "positive_control": {"verified": True, "verdict": "SIGNAL",
                              "conf_p": 0.001, "disc_p": 0.002, "n_confirm": 2},
    }


def test_clean_state_ok():
    """干净状态应 verdict=OK 且无发现。"""
    rep = RA.audit_cycle(_base_state())
    assert rep["verdict"] == "OK", rep
    assert rep["n_findings"] == 0, rep["findings"]


def test_overclaim_alert():
    """绝对化措辞且无对冲 => ALERT。"""
    st = _base_state()
    rep = RA.audit_cycle(st, summary_text="本轮发现结构，下一期可预测。")
    assert rep["verdict"] == "ALERT"
    assert any("绝对化措辞" in f for f in rep["findings"]), rep["findings"]


def test_absurd_z_flagged():
    """退化统计（荒谬 z）必须报警。"""
    st = _base_state()
    st["best_z_history"] = [3.1, 1.15e9, 2.9]   # 注入荒谬离群
    rep = RA.audit_cycle(st)
    assert any("荒谬离群" in f for f in rep["findings"]), rep["findings"]


def test_multiple_comparison_null_noise():
    """best_p 落在 null 期望范围内 => 点破为噪声（REVIEW）。"""
    st = _base_state()
    st["best_q"] = 0.04          # 过了 FDR
    st["best_p"] = 0.0099
    st["n_eval"] = 243           # null 最小 p 期望≈1/244≈0.004 < 0.0099
    st["wf_verdict"] = "SIGNAL"
    st["wf_n_confirm"] = 3
    rep = RA.audit_cycle(st)
    assert any("完全落在随机波动范围内" in f for f in rep["findings"]), rep["findings"]
    assert rep["verdict"] in ("REVIEW", "ALERT")


def test_missing_confirmation_flagged():
    """声称 SIGNAL 但无 #41 verdict => 报警。"""
    st = _base_state()
    st["best_q"] = 0.03
    st.pop("wf_verdict", None)   # 删掉确认证据
    rep = RA.audit_cycle(st)
    assert any("未记录 #41 发现/确认分离" in f for f in rep["findings"]), rep["findings"]


def test_positive_control_failure_alert():
    """持续阳性对照失败（已知结构未被闸门检出）=> ALERT。"""
    st = _base_state()
    st["positive_control"] = {"verified": False, "verdict": "UNCONFIRMED",
                              "conf_p": 0.4, "disc_p": 0.3, "n_confirm": 0}
    rep = RA.audit_cycle(st)
    assert rep["verdict"] == "ALERT"
    assert any("持续阳性对照失败" in f for f in rep["findings"]), rep["findings"]


def test_write_report_files():
    """report.json / report.md 正确写出。"""
    st = _base_state()
    st["best_z_history"] = [3.1, 1e12, 2.9]
    rep = RA.audit_cycle(st, summary_text="发现结构")
    d = tempfile.mkdtemp()
    jp, mp = RA.write_report(rep, d)
    assert os.path.exists(jp) and os.path.exists(mp)
    with open(jp, encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["verdict"] == "ALERT"
    print("  [redteam] write_report OK ->", mp)
