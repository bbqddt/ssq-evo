# -*- coding: utf-8 -*-
"""
seed_bridge.py —— 外部数学框架 (gplearn 符号回归) 桥接进公式代数演进
=====================================================================

用户诉求：找更多数学框架/自我演进来「参与公式研发突破」，前人公式不可行，
只借机制、不借结论。

本模块做什么：
  用 gplearn 在「严格无泄露」的真实数据上做符号回归，搜索以「原创基元」为
  字母表的符号公式（输入=本期基元值，目标=下期基元值）。把 gplearn 发现的
  最优符号公式翻译成 engine_core 的复合公式树(comp)，作为 GA 代数演进的
  「外部框架种子」注入 composer，让 GA 在更大的、框架指引的起点附近继续
  长出选号公式。

诚实护栏（红线，不可协商）：
  - gplearn 只产出「候选种子」，绝不自动合并进 frontier/确认集。
  - 注入的种子最终仍须过 engine_core 统一闸门（run_axes.label_axis:
    shuffle + AAFT + subset_marginal + 随机对照 + BH-FDR）。
  - 优化器绝不以「过闸」为目标自动合并（Goodhart 假阳性头号红线）。
  - 特征严格只用 t 期之前/当期信息预测 t+1 期，杜绝目标泄漏。

与 formula_research 的关系：
  formula_research 把「新基元字母表」交给了 GA；本模块把「外部框架发现的
  具体公式组合」作为这些字母表上的种子交给 GA。两者都过同一套闸门。
"""
import os
import re
import json
import copy
import warnings

import numpy as np

import engine_core as E
import formula_research as FR   # 注册原创基元 + 提供基元函数
import run_axes as RA

warnings.filterwarnings("ignore")

FR.register()  # 确保 BASE_SIGNALS 含原创基元（翻译后的变量名必须存在）

DATA = "D:/ssq_evo_data/ssq_master.csv"
SEED_JSON = "D:/ssq_evo_data/gplearn_seeds.json"

CONST = object()  # 常量叶哨兵（符号回归的加性/乘性常数，对选号方向无影响，吸收丢弃）


# ---------------------------------------------------------------------------
# 1. 无泄露特征：在「基元空间」做符号回归（输入=本期基元值，目标=下期基元值）
# ---------------------------------------------------------------------------
def build_base_signal_features(reds, blues):
    """把每期原始开奖映射到一组「每期标量基元」，构造符号回归的 X/y。

    严格无泄露：X 用第 t 期基元值，y 用第 t+1 期基元值（目标在将来，不前瞻）。
    返回 (X, y, feat_names)，其中 feat_names[i] 即 comp 基元名（翻译后变量直接是它）。
    """
    N = reds.shape[0]
    feats = {
        "red_digit_sum": FR.red_digit_sum(reds, blues),
        "red_digit_root9": FR.red_digit_root9(reds, blues),
        "red_qr_count": FR.red_qr_count(reds, blues),
        "red_fib_count": FR.red_fib_count(reds, blues),
        "red_pairwise_prod": FR.red_pairwise_prod(reds, blues),
        "red_gap_var": FR.red_gap_var(reds, blues),
        "red_lz_complexity": FR.red_lz_complexity(reds, blues),
        "red_sum": reds.sum(axis=1).astype(float),
    }
    g = np.diff(np.sort(reds, axis=1), axis=1)
    feats["red_gap_mean"] = g.mean(axis=1).astype(float)
    names = list(feats.keys())
    Xmat = np.column_stack([feats[n] for n in names])   # (N, K)
    # 目标：下一期和值（red_sum 的滞后一期）
    y = np.roll(feats["red_sum"], -1)
    X = Xmat[:-1]
    y = y[:-1]
    return X, y, names


# ---------------------------------------------------------------------------
# 2. gplearn 符号回归：在基元空间搜索非线性符号公式
# ---------------------------------------------------------------------------
def run_gplearn(X, y, n_runs=6, pop=150, gen=15):
    """跑 n_runs 个不同随机种子的 SymbolicRegressor，每个取最优程序。
    返回 [(expr_str, program), ...]（未翻译的原始程序列表）。"""
    from gplearn.genetic import SymbolicRegressor
    programs = []
    for sd in range(n_runs):
        sr = SymbolicRegressor(
            population_size=pop, generations=gen,
            function_set=["add", "sub", "mul", "div", "sin", "cos"],
            parsimony_coefficient=0.01, random_state=sd, verbose=0,
            metric="mse",
        )
        try:
            sr.fit(X, y)
            prog = sr._program
            programs.append((str(prog), prog))
        except Exception as e:   # 单跑失败不致命
            print("[seed_bridge] gplearn run sd=%d 失败: %s" % (sd, e))
    return programs


# ---------------------------------------------------------------------------
# 3. 翻译：gplearn 前缀字符串 -> engine_core comp 树 (dict)
# ---------------------------------------------------------------------------
def _split_top(s):
    """按顶层逗号拆分参数（括号深度为 0 时才切分）。"""
    depth = 0
    args = []
    cur = ""
    for ch in s:
        if ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            args.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip() != "":
        args.append(cur)
    return [a.strip() for a in args]


def _parse(s, feat):
    """递归解析 gplearn 程序字符串为 comp 树（变量=基元名）。
    返回：str(基元名) / dict(子树) / CONST(常量叶) / None(不可翻译)。"""
    s = s.strip()
    m = re.match(r"^([A-Za-z_]+)\s*\((.*)\)$", s, re.S)
    if m:
        fn = m.group(1)
        inner = m.group(2)
        args = _split_top(inner)
        if fn in ("sin", "cos"):
            a = _parse(args[0], feat)
            if a is CONST or a is None:
                return None
            return {"op": fn, "a": a, "read": "cont"}
        if fn in ("add", "sub", "mul", "div"):
            a = _parse(args[0], feat)
            b = _parse(args[1], feat)
            # 常量吸收：加/减/乘/除的常数偏移/缩放对「方向预测」(read 基于符号/z)
            # 无影响，吸收为另一操作数；两个都是常量则整棵退化，丢弃。
            if a is CONST and b is CONST:
                return None
            if a is CONST:
                return b
            if b is CONST:
                return a
            if a is None or b is None:
                return None
            return {"op": fn, "a": a, "b": b, "read": "cont"}
        return None  # 不支持的函数（sqrt/log/inv/neg 等，gplearn function_set 已限制不会出现）
    # 叶：变量 Xn 或常量
    if re.match(r"^X\d+$", s):
        idx = int(s[1:])
        return feat[idx] if 0 <= idx < len(feat) else None
    try:
        float(s)
        return CONST
    except ValueError:
        return None


def translate(prog_str, feat_names):
    """把 gplearn 程序字符串翻译为 comp 树。返回 dict 或 None。"""
    tree = _parse(prog_str, feat_names)
    if isinstance(tree, dict):
        return tree
    # 单基元(str) 或 CONST/None：非「复合」种子，丢弃（GA 随机已能采样单基元）
    return None


# ---------------------------------------------------------------------------
# 4. 编译校验 + 去重 + 写 JSON
# ---------------------------------------------------------------------------
def compile_check(tree, reds, blues):
    """编译校验：comp 树必须可编译且非退化（std>0）。返回 (ok, reason)。"""
    try:
        x = E._build_comp(tree, reds, blues)
    except Exception as e:
        return False, "compile_error:%s" % e
    if x is None:
        return False, "returns_none"
    if not np.all(np.isfinite(x)):
        return False, "nonfinite"
    if np.std(x) < 1e-9:
        return False, "degenerate_constant"
    return True, "ok"


def dedupe(trees):
    """按 JSON 序列化去重。"""
    seen = set()
    out = []
    for t in trees:
        key = json.dumps(t, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def build_seeds(reds, blues, n_runs=6, pop=150, gen=15, max_seeds=12):
    """完整流程：特征 -> gplearn -> 翻译 -> 校验 -> 去重 -> 写 JSON。
    返回 (seeds, stats)。"""
    X, y, names = build_base_signal_features(reds, blues)
    programs = run_gplearn(X, y, n_runs=n_runs, pop=pop, gen=gen)
    seeds = []
    n_translated = 0
    n_degenerate = 0
    for expr, _prog in programs:
        tree = translate(expr, names)
        if tree is None:
            continue
        n_translated += 1
        ok, reason = compile_check(tree, reds, blues)
        if not ok:
            n_degenerate += 1
            continue
        seeds.append({"expr": expr, "comp": tree})
    seeds = dedupe(seeds)[:max_seeds]
    stats = {
        "gplearn_runs": n_runs,
        "programs_collected": len(programs),
        "non_trivial_translated": n_translated,
        "degenerate_skipped": n_degenerate,
        "valid_seeds": len(seeds),
    }
    with open(SEED_JSON, "w") as f:
        json.dump({"stats": stats, "seeds": seeds}, f, indent=2, ensure_ascii=False)
    return seeds, stats


# ---------------------------------------------------------------------------
# 5. 诚实闸门：对每棵种子过 run_axes 统一闸门（与引擎完全一致）
# ---------------------------------------------------------------------------
def honest_gate(reds, blues, seeds, k_sur=20):
    """对每棵种子调 RA.label_axis("comp", tests, reds, blues, rng, params={"_comp": tree})。
    返回带 verdict 的记录列表。"""
    tests = ["mi_max", "acf_max", "perm_entropy"]
    recs = []
    for i, s in enumerate(seeds):
        rng = np.random.default_rng(20260826 + i * 7)
        try:
            rec = RA.label_axis("comp", tests, reds, blues, rng, k_sur,
                                params={"_comp": s["comp"]})
        except Exception as e:
            rec = {"label": "ERROR", "note": "gate_error:%s" % e}
        rec["expr"] = s["expr"]
        recs.append(rec)
    return recs


# ---------------------------------------------------------------------------
# 6. 注入钩子：composer 消费式读取（读后删除，避免重复霸占 GA 种群）
# ---------------------------------------------------------------------------
def load_seeds_consume(path=SEED_JSON):
    """读取种子 JSON 返回 comp 树列表，并删除文件（消费式）。无文件返回 []。"""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            d = json.load(f)
        trees = [s["comp"] for s in d.get("seeds", []) if s.get("comp")]
    except Exception:
        trees = []
    try:
        os.remove(path)
    except OSError:
        pass
    return trees


# ---------------------------------------------------------------------------
# 7. 命令行入口
# ---------------------------------------------------------------------------
def main():
    import data as D
    m = D.load_master(DATA)
    if not m:
        print("[seed_bridge] 未找到真实数据，退出")
        return
    reds, blues, _ = D.to_arrays(m)
    print("[seed_bridge] 载入真实数据 %d 期" % len(reds))

    seeds, stats = build_seeds(reds, blues, n_runs=6, pop=150, gen=15)
    print("[seed_bridge] 统计: %s" % json.dumps(stats, ensure_ascii=False))

    if not seeds:
        print("[seed_bridge] gplearn 未产出非平凡种子（真实数据无跨期基元结构，符合 NULL 立场）")
        return

    recs = honest_gate(reds, blues, seeds, k_sur=20)
    n_surv = sum(1 for r in recs if r.get("label") == "SURVIVOR")
    n_art = sum(1 for r in recs if r.get("label") == "ARTIFACT_BY_CONSTRUCTION")
    print("\n================ gplearn 种子 · 统一闸门评估 ================")
    print("%-42s %-9s %-8s %-8s %-8s" % ("expr", "label", "p_shuf", "p_aaft", "p_marg"))
    print("-" * 100)
    for r in recs:
        def fmt(v):
            return ("%.4g" % v) if isinstance(v, float) else "-"
        print("%-42s %-9s %-8s %-8s %-8s" % (
            (r.get("expr", "")[:42]), r.get("label", "-"),
            fmt(r.get("p_shuffle")), fmt(r.get("p_aaft")), fmt(r.get("p_marg"))))
    print("汇总: SURVIVOR=%d  ARTIFACT=%d  (其余 NULL)" % (n_surv, n_art))
    print("\n种子已写入 %s，composer 在下一轮 breed 时消费式注入 GA。" % SEED_JSON)


if __name__ == "__main__":
    main()
