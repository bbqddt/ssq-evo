"""沙箱实测：智能演进「配合」(memetic 局部精修 + coalition 公式协作)。

仅验证引擎在开启 memetic/coalition 后能正常运行、候选被注入、种群不坍缩。
绝不涉及闸门 verdict / 自动合并。
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_core as E
import data as D

DATA_DIR = "D:/ssq_evo_data"


def main():
    master = os.path.join(DATA_DIR, "ssq_master.csv")
    if not os.path.exists(master):
        print("NO_DATA"); return
    rows = D.load_master(master)
    reds, blues, _ = D.to_arrays(rows)
    # 取末段切片加速（仅验证机制，不用于结论）
    reds, blues = reds[-400:], blues[-400:]
    rng = np.random.default_rng(12345)

    evo = E.Evolution(reds, blues, rng, k_light=15, k_heavy=8, epochs=5, pop=20,
                      novelty_enabled=True, memetic=True, coalition=True,
                      memetic_top_n=3, memetic_k=4)
    lb, all_evals = evo.run()

    uniq = len(lb)
    gkeys = set()
    for e in all_evals:
        gkeys.add(E.genome_key(e["sig"], e["test"], e["params"]))
    best_p = min((e.get("p_raw", 1.0) for e in lb.values()), default=1.0)
    # 行为多样性粗测：不同 (sig,test) 组合数
    st_pairs = len({(e.get("sig"), e.get("test")) for e in all_evals})
    print("=== smoke_smart_evo ===")
    print(f"all_evals={len(all_evals)}  unique_genomes(leaderboard)={uniq}  unique_gkeys={len(gkeys)}")
    print(f"memetic_injected={evo.memetic_injected}  coalition_injected={evo.coalition_injected}")
    print(f"distinct(sig,test) combos={st_pairs}")
    print(f"best_p_raw={best_p:.4f}  (红线圈: 仅机制验证, 不出口域结论)")
    # 基本健全性
    assert len(all_evals) > 0, "无评估产出"
    assert evo.memetic_injected > 0, "memetic 未注入候选"
    assert evo.coalition_injected > 0, "coalition 未注入候选"
    assert uniq >= 10, "种群坍缩"
    print("ASSERT PASS: 智能演进机制已激活且种群健康")


if __name__ == "__main__":
    main()
