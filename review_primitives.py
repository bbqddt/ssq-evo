# -*- coding: utf-8 -*-
"""
review_primitives.py —— 学习模块 L4：人类复核与回馈闭环（基石四落地工具）
==========================================================================

L2 原语扩张器把通过 discovery 的新原语写进 `pending_primitives.json`（待复核池），
**绝不自动 merge 进生产 SIGMAPS**。本脚本是人工复核入口：

  1. 列出待复核池所有候选（名称/类别/discovery 段证据/是否伪结构拦截）；
  2. 用户逐一确认（y）或拒绝（n）；
  3. 确认者：写回 representation_zoo.NEW_SIGNALS 风格的注入清单 → 下次驾1/驾3 在新空间搜；
  4. 拒绝者：从待复核池移除（记录拒绝原因）；
  5. 任何 merge 后的新原语，驾1/驾3 首次用到须再过 #41 confirm 段复验（基石三）。

红线（不可绕过）：
  - 本脚本是**唯一**能把学习产出注入 SIGMAPS 的入口；axis_proposer 绝不自动 merge。
  - 默认所有候选待复核；不运行本脚本 = 永不注入（保守但诚实）。

用法：
  python review_primitives.py --data-dir D:/ssq_evo_data [--auto-list]   # 只看不 merge
  python review_primitives.py --data-dir D:/ssq_evo_data --interactive   # 交互确认
"""
import os
import sys
import json
import argparse

DATA_DIR = os.environ.get("DATA_DIR", "D:/ssq_evo_data")
PENDING_FILE = "pending_primitives.json"
APPROVED_FILE = "approved_primitives.json"


def load_pending(data_dir):
    path = os.path.join(data_dir, PENDING_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_approved(data_dir):
    path = os.path.join(data_dir, APPROVED_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_pending(data_dir, pool):
    path = os.path.join(data_dir, PENDING_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


def save_approved(data_dir, approved):
    path = os.path.join(data_dir, APPROVED_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(approved, f, ensure_ascii=False, indent=2)


def list_pending(pool):
    if not pool:
        print("待复核池为空（无任何学习产出等待人类确认）。")
        return
    print("=== 待复核池（学习模块 L2 产出，待人类否决权行使）===")
    for i, e in enumerate(pool):
        p = e.get("primitive", {})
        ev = e.get("feedback_evidence", {})
        print("%2d. %s [类别=%s] learned=%s" % (i + 1, p.get("name"), p.get("kind"), p.get("learned")))
        print("     discovery 证据: zero_hypothesis_cross=%s, random_control_label=%s"
              % (ev.get("zero_hypothesis_cross"), ev.get("random_control_label")))
        print("      staged_at=%s" % e.get("staged_at"))
    print("（运行 --interactive 逐条确认；确认后写回 approved_primitives.json，下次驾1/驾3 在新空间搜）")


def approve(pool, idx, data_dir):
    """人类确认一条 → 移入 approved_primitives.json（基石四：仅此入口可 merge）。"""
    if idx < 0 or idx >= len(pool):
        print("无效序号")
        return
    e = pool.pop(idx)
    e["primitive"]["human_confirmed"] = True
    approved = load_approved(data_dir)
    approved.append(e)
    save_approved(data_dir, approved)
    save_pending(data_dir, pool)
    print("已确认 %s → approved_primitives.json（下次驾1/驾3 在新空间搜，首次用到须过 #41）"
          % e.get("primitive", {}).get("name"))


def reject(pool, idx, reason, data_dir):
    """人类拒绝一条 → 从待复核池移除。"""
    if idx < 0 or idx >= len(pool):
        print("无效序号")
        return
    e = pool.pop(idx)
    save_pending(data_dir, pool)
    print("已拒绝 %s（原因=%s）" % (e.get("primitive", {}).get("name"), reason))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=DATA_DIR)
    ap.add_argument("--auto-list", action="store_true", help="只列出待复核，不交互")
    ap.add_argument("--interactive", action="store_true", help="逐条 y/n 确认")
    ap.add_argument("--approve-all", action="store_true", help="全部确认（慎用：放弃人工把关）")
    a = ap.parse_args()

    pool = load_pending(a.data_dir)
    if not pool:
        list_pending(pool)
        return

    if a.auto_list or (not a.interactive and not a.approve_all):
        list_pending(pool)
        return

    if a.approve_all:
        for _ in range(len(pool)):
            approve(pool, 0, a.data_dir)
        return

    if a.interactive:
        list_pending(pool)
        for i in range(len(pool)):
            cur = pool[0] if pool else None
            if cur is None:
                break
            name = cur.get("primitive", {}).get("name")
            ans = input("确认 %s 进 SIGMAPS? [y/n]: " % name).strip().lower()
            if ans == "y":
                approve(pool, 0, a.data_dir)
            else:
                reject(pool, 0, "人工拒绝", a.data_dir)


if __name__ == "__main__":
    main()
