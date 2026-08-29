"""沙箱实测：守夜人「连续 N 周期全 NULL 即暂停空转、只留监测待命」。

验证：
1) 全 NULL 计数器超阈值 -> is_watchdog_paused=True；
2) effective_cfg 在暂停时降为最小待命(pop=1, comp_breed_n=0)，保证 GA 空转停止；
3) 引擎在暂停配置下仍能跑完一轮不崩溃（待命心跳）。
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_core as E
import data as D
import watchdog_mode as WD

DATA_DIR = "D:/ssq_evo_data"


def main():
    base_cfg = {"pop": 30, "epochs": 8, "comp_breed_n": 8,
                "k_light": 25, "k_heavy": 10,
                "watchdog_mode_enabled": True, "watchdog_stagnation_cycles": 30,
                "watchdog_force_resume": False}

    # 1) 全 NULL 累计 35 周期 -> 应暂停
    fr_null = {"cycles_since_signal": 35, "baseline_base_signals": len(E.BASE_SIGNALS)}
    assert WD.is_watchdog_paused(fr_null, base_cfg), "全 NULL 超阈值未暂停"
    c, paused = WD.effective_cfg(base_cfg, fr_null)
    assert paused and c["pop"] == 1 and c["comp_breed_n"] == 0, "暂停配置未降为最小待命"
    print("[1] 全 NULL x35 -> paused=True, pop=1, comp_breed_n=0  ✅")

    # 2) 有信号(cycles_since_signal=0) -> 不暂停
    fr_live = {"cycles_since_signal": 0, "baseline_base_signals": len(E.BASE_SIGNALS)}
    assert not WD.is_watchdog_paused(fr_live, base_cfg), "有信号却误暂停"
    print("[2] cycles_since_signal=0 -> paused=False  ✅")

    # 3) 强制恢复 -> 不暂停
    fr_force = {"cycles_since_signal": 99, "baseline_base_signals": len(E.BASE_SIGNALS),
                "force_resume": True}
    assert not WD.is_watchdog_paused(fr_force, base_cfg), "force_resume 未生效"
    print("[3] force_resume -> paused=False  ✅")

    # 4) 待命心跳：引擎在 pop=1/epochs=1 下跑完不崩溃
    master = os.path.join(DATA_DIR, "ssq_master.csv")
    if os.path.exists(master):
        rows = D.load_master(master)
        reds, blues, _ = D.to_arrays(rows)
        reds, blues = reds[-300:], blues[-300:]
        rng = np.random.default_rng(777)
        evo = E.Evolution(reds, blues, rng, k_light=c["k_light"], k_heavy=c["k_heavy"],
                          epochs=c["epochs"], pop=c["pop"], novelty_enabled=True,
                          memetic=True, coalition=True)
        lb, all_evals = evo.run()
        print(f"[4] 待命心跳跑完: all_evals={len(all_evals)}  unique={len(lb)}  (pop={c['pop']})  ✅")
    else:
        print("[4] 无数据，跳过引擎心跳实测")

    print("ASSERT PASS: 守夜人「全 NULL 即停转、只留监测待命」已生效")


if __name__ == "__main__":
    main()
