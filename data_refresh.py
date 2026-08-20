# -*- coding: utf-8 -*-
"""data_refresh.py —— 生成本地静态历史快照 data/ssq_history.csv

为什么需要它（架构必要性）：
  本地 Docker 引擎（驾1）跑 GA 进化靠 D:/ssq_evo_data/ssq_master.csv（真实开奖数据，
  落在 DATA_DIR 卷，不进仓库）。但 GitHub Actions（驾3）在云端、只能读仓库内容，
  摸不到本地卷。为了让驾3 能做【分布式 GA 进化】，仓库里放一份【固定参考快照】
  ——公开开奖史，非增长库。

性质说明（对"数据不出仓库"原则的有意例外，已评估安全）：
  - schema 与 data.py load_master 完全一致 (issue,r1..r6,b)，可被 ci_evolve.py 直接载入；
  - 它是「固定参考」，不是引擎状态；引擎状态仍在 D:/ssq_evo_data（唯一真相源）；
  - 手动刷新（不进 CI 自动提交，避免增长/与用户 push 撞车）：python data_refresh.py；
  - 刷新只重导出当前历史，保持静态参考性质。
"""
import os
import csv
import sys

DEFAULT_SRC = "D:/ssq_evo_data/ssq_master.csv"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(OUT_DIR, "ssq_history.csv")
FIELDS = ["issue", "r1", "r2", "r3", "r4", "r5", "r6", "b"]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    if not os.path.exists(src):
        print("[data_refresh] 源文件不存在:", src)
        return 1
    rows = []
    with open(src, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("issue"):
                continue
            try:
                int(row["issue"])
                for i in range(1, 7):
                    int(row["r%d" % i])
                int(row["b"])
            except Exception:
                continue
            rows.append({k: row[k] for k in FIELDS})
    rows.sort(key=lambda r: r["issue"])
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print("[data_refresh] 写入 %d 期 -> %s" % (len(rows), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
