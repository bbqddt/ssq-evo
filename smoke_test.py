# -*- coding: utf-8 -*-
import sys, time, os, numpy as np
sys.path.insert(0, ".")
import engine_core as E
import data as D
import frontier as F

DATA = "D:/ssq_evo_data" if os.path.exists("D:/ssq_evo_data") else "."
master = D.load_master(os.path.join(DATA, "ssq_master.csv"))
reds, blues, issues = D.to_arrays(master)
print("N=", len(reds))
rng = np.random.default_rng(12345)
fr = F.load_frontier(".")
t0 = time.time()
evo = E.Evolution(reds, blues, rng, k_light=8, k_heavy=4, epochs=3, pop=10,
                  elites=fr.get("elites", []), frontier=fr)
lb, allv = evo.run()
print("smoke: evals=%d unique=%d  time=%.1fs" % (len(allv), len(lb), time.time() - t0))
for e in list(lb.values())[:3]:
    print("  ", e["sig"], e["test"], e["params"], "p=%.3f z=%.2f" % (e["p_raw"], e["z"]))
fr2 = F.update_frontier(fr, lb, evo.tried, elite_k=12)
print("frontier coverage=", fr2["coverage"], "elites=", len(fr2["elites"]), "z_hist=", fr2["best_z_history"])
print("SMOKE_OK")
