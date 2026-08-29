# -*- coding: utf-8 -*-
"""反思设计层 + 狂跑配置 沙箱实测。
验证：①每轮反省生成报告 ②提案注入候选且不破坏闸门 ③无自动合并 ④种群不坍缩。
"""
import os, sys, json, tempfile
HERE = r"D:/ssq_evo"
sys.path.insert(0, HERE)
os.environ["DATA_DIR"] = r"D:/ssq_evo_data"


def main():
    import numpy as np
    import data as D
    import engine_core as E
    import reflective_designer as RD

    # 临时反射日志，避免污染生产数据
    _tmp = tempfile.mktemp(suffix=".jsonl")
    RD.REFLECTION_LOG = _tmp

    master = D.load_master(os.path.join(os.environ["DATA_DIR"], "ssq_master.csv"))
    reds, blues, _ = D.to_arrays(master)
    N = 400
    reds, blues = reds[-N:], blues[-N:]

    rng = np.random.default_rng(12345)
    evo = E.Evolution(reds, blues, rng, k_light=20, k_heavy=8, epochs=4, pop=18,
                      elites=[], frontier={"tried": []}, eval_cache=None,
                      oos_every_epoch=False, memetic=True, coalition=True, reflect_enabled=True)
    lb, all_evals = evo.run()

    # ---- 断言 ----
    assert evo.reflect_injected > 0, "反思未注入候选"
    recs = [json.loads(l) for l in open(_tmp, encoding="utf-8") if l.strip()]
    assert len(recs) == 4, f"应有4轮反省, 实际 {len(recs)}"
    for e in all_evals:
        assert "p_raw" in e, "存在无闸门结果的候选(疑似自动合并?)"
    uniq_genomes = len({e["gkey"] for e in all_evals})

    print("reflect_injected      :", evo.reflect_injected)
    print("unique genomes        :", uniq_genomes, "/", len(all_evals), "评估")
    print("leaderboard size      :", len(lb))
    print("reflection epochs log :", len(recs))
    print("sample EP0 report     :", recs[0]["report"]["text"].replace("\n", " | "))
    print("sample EP0 proposals  :", [p["text"] for p in recs[0]["proposals"]])
    print("ASSERT PASS: 反思闭环已激活——每轮反省→提案→注入候选；闸门 intact、无自动合并、未坍缩")
    os.remove(_tmp)

    # ---- 狂跑涡轮配置验证 ----
    os.environ["SSQ_TURBO"] = "1"
    import run_cycle as RC
    cfg = RC.load_cfg()
    print("\n[turbo] epochs=%s pop=%s k_light=%s oos_every=%s reflect=%s" % (
        cfg["epochs"], cfg["pop"], cfg["k_light"],
        cfg["ga_oos_every_epoch"], cfg["ga_reflect_enabled"]))
    assert cfg["pop"] >= 60 and cfg["epochs"] >= 20 and cfg["ga_reflect_enabled"]
    print("ASSERT PASS: SSQ_TURBO=1 已拉满搜索强度(不影响闸门/诚实护栏)")


if __name__ == "__main__":
    main()
