# -*- coding: utf-8 -*-
"""
run_cycle.py —— 一次完整演化周期
流程：抓取/合并数据 -> 跑演化搜索若干代 -> BH-FDR 校正 -> 样本外验证
      -> 写 SQLite + state.json（供看板读取）
用法：python run_cycle.py            # 抓取最新数据并跑一轮
      python run_cycle.py --no-fetch # 不联网，用本地主表重跑（断网/离线也持续产出）
"""
import os, sys, json, argparse, datetime, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR: 优先用环境变量(Docker 部署传 /app/data)；未设置时默认 D:\ssq_evo_data(本机全在 D 盘，不写 C)
DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    _d = r"D:\ssq_evo_data"
    DATA_DIR = _d if os.path.isdir(_d) else HERE
os.makedirs(DATA_DIR, exist_ok=True)
sys.path.insert(0, HERE)

import engine_core as E
import data as D
import store as S
import frontier as F
import nonstationarity as NS
import evaluator as EV
import cache as C
import positive_control as PC

MASTER = os.path.join(DATA_DIR, "ssq_master.csv")
DB = os.path.join(DATA_DIR, "ssq_evo.db")
STATE = os.path.join(DATA_DIR, "state.json")

# ---- 可调参数（部署时可改 config.json）----
DEFAULT_CFG = {
    "epochs": 6, "pop": 24, "k_light": 25, "k_heavy": 10,
    "k_causal": 50,   # 因果扫描专用 surrogate 数：8 个方向×检验下, k=25 的 p 地板 1/26≈0.038
                      # 经 BH-FDR(8检验) 后 q 仅 0.051(勉强不过 0.05)。提到 50 使 p 地板 1/51≈0.0196,
                      # 强耦合可被判 q<0.05, 既保留"真实数据仍稳 null"又证明闸门有检出功效。
    "seed": 20260813, "oos_frac": 0.2, "alert_q": 0.01, "alert_oos_p": 0.01,
    "wf_n_folds": 3, "wf_disc_frac": 0.7,   # 发现/确认分离闸门 (#41)：折数 / 发现段占比
    "cache_enabled": True,                  # #40 增量评估缓存：同(基因组,数据集)复用，不改统计
    "cache_path": "eval_cache.json",        # 缓存文件（DATA_DIR 下）
    # 持续阳性对照（闸门功率监控）：每 positive_control_every 轮注入已知结构验闸门还灵不灵
    "positive_control_enabled": True,
    "positive_control_every": 1,            # 1=每轮；>1 每 K 轮一次（省算力）
    "positive_control_n": 1000, "positive_control_lag": 8,
    "positive_control_k_sur": 30, "positive_control_folds": 2,
}
FDR_Q = 0.05          # 结构显著的 FDR 门槛（与看板一致）


# ============================================================
# 嵌入式自检机制 —— 每轮 cycle 自动运行，主动暴露问题
# 不等用户截图发现。覆盖：OOS 平凡解 / 配置漂移 /
# Dockerfile 漏拷 / best_q 漂移 / state 一致性
# ============================================================
def self_check(state, acc, cfg):
    """返回 dict: {warnings: [str], score: int(0=干净, 越大问题越多)}。
    结果写入 state['self_check'] 并打印到日志。"""
    warns = []
    score = 0

    # 1) OOS 平凡解检测（osc k=1 类 bug 的通用指纹）
    if acc:
        if (acc.get("hit_rate") == 1.0 and acc.get("sur_mean", 0) >= 0.99
                and acc.get("p_random", 1) >= 0.95):
            warns.append(
                "OOS 平凡解: hit_rate=1.0 & sur_mean≈1.0 & p≈1.0"
                " —— 可能是 osc k=1 或类似退化规则导致的伪完美准确率")
            score += 3
        if acc.get("n", 0) < 30:
            warns.append(f"OOS 样本量过小(n={acc['n']}<30)，统计量不足")
            score += 1
        # 新增：osc k=1 直接从 params 里抓
        top_params = (state.get("leaderboard") or [{}])[0].get("params") or {}
        comp = top_params.get("_comp")
        if isinstance(comp, dict) and comp.get("read") == "osc" and int(comp.get("k", 1)) <= 1:
            warns.append("基因组参数异常: read=osc 且 k<=1(平凡解)，已强制 k>=2")
            score += 3

    # 1b) OOT 盲测平凡解检测（同样防数据泄露指纹）
    oot = None
    oot_p = state.get("oot_p")
    oot_hit = state.get("oot_hit")
    oot_n = state.get("oot_n") or 0
    if oot_p is not None and oot_hit is not None:
        if oot_hit >= 0.99 and oot_p >= 0.95:
            warns.append(
                "OOT 盲测平凡解: hit_rate≈1.0 & p≈1.0 —— 检查是否在盲测段泄露了未来信息")
            score += 3
        if oot_n < 30:
            warns.append(f"OOT 盲测样本量过小(n={oot_n}<30)，统计量不足")
            score += 1

    # 2) 配置一致性（canonical YAML 源 vs 运行时合并值）
    try:
        sys.path.insert(0, HERE)
        from configs import load_engine_config
        canon = load_engine_config()
        for key in ("k_light", "k_heavy", "epochs", "pop", "k_causal", "oos_frac"):
            cv, rv = canon.get(key), cfg.get(key)
            if cv is not None and rv is not None and cv != rv:
                warns.append(f"配置漂移: {key} YAML源={cv} vs 运行时={rv}(config.json 可能覆盖了 YAML)")
                score += 2
    except Exception:
        pass

    # 3) Dockerfile COPY 完整性（只检查本项目 .py 文件是否漏拷）
    df = os.path.join(HERE, "Dockerfile")
    if os.path.exists(df):
        try:
            df_text = open(df, encoding="utf-8").read()
            # 本项目的 .py 文件（排除 __pycache__ / venv / .git）
            proj_py = set()
            for f in os.listdir(HERE):
                fp = os.path.join(HERE, f)
                if f.endswith(".py") and os.path.isfile(fp):
                    # 排除纯本地开发工具（benchmark/smoke_test 等）
                    if f.startswith(("benchmark_", "smoke_", "test_")):
                        continue
                    proj_py.add(f)
            missing_prod = [f for f in proj_py if f not in df_text]
            if missing_prod:
                warns.append(f"Dockerfile 漏拷生产文件: {missing_prod}")
                score += len(missing_prod) * 2
        except Exception:
            pass

    # 4) best_q 漂移检测（读最近快照）
    try:
        snaps = sorted([f for f in os.listdir(DATA_DIR)
                         if f.startswith("state.") and f.endswith(".json")])
        if len(snaps) >= 5:
            recent_qs = []
            for sn in snaps[-5:]:
                sp = json.load(open(os.path.join(DATA_DIR, sn), encoding="utf-8"))
                q = sp.get("best_q")
                if q is not None:
                    recent_qs.append(q)
            if len(recent_qs) >= 3:
                mq = np.mean(recent_qs)
                cq = state.get("best_q", 1)
                if abs(cq - mq) > 0.15:
                    warns.append(
                        f"best_q 剧烈漂移: 当前={cq:.4f}, 近5轮均值={mq:.4f}"
                        f"(搜索前沿未收敛或过拟合)")
                    score += 1
    except Exception:
        pass

    # 5) 结构 vs OOS 矛盾检测
    bq = state.get("best_q", 1)
    cross_ok = state.get("oos_cross_consistent", False)
    if bq < 0.05 and cross_ok and acc and not acc.get("above_random"):
        warns.append(
            "结构显著(best_q<0.05, 交叉一致)但 OOS 不高于随机"
            " —— 可能是训练集过拟合或 OOS 规则退化")
        score += 2

    # 6) 演化漏检 vs 谱扫描独立检出（仅当谱扫描 q 极强<0.01 才提示，避免偶发 surrogate 地板命中误报）
    evo_q = state.get("best_q", 1)
    spec_q = state.get("spectral_q", 1)
    if evo_q >= 0.05 and spec_q < 0.01:
        warns.append(
            f"演化漏检: 主搜索 best_q={evo_q:.4f}≥0.05 但谱扫描独立检出结构 "
            f"(q={spec_q:.4f}, {state.get('spectral_best_sig')}/{state.get('spectral_best_test')})"
            " —— 演化覆盖不足，谱扫描兜底生效")
        score += 2

    result = {"warnings": warns, "score": score, "ts": state.get("updated")}
    # 打印摘要
    if warns:
        print(f"[self_check] ⚠️  发现 {len(warns)} 个问题(score={score}):")
        for w in warns:
            print(f"  - {w}")
    else:
        print("[self_check] ✅ 全部通过")
    return result


# ============================================================
# 随机数据对照闸门（构造伪结构拦截） —— 接进常驻引擎主干
# ------------------------------------------------------------
# 任何基信号若在「纯随机双色球」上也 SURVIVOR，说明其显著来自信号构造本身
# （如 red_recurrence_mean 的 N 惩罚尖峰），而非彩票结构。此类信号在 FDR / 最优
# 选择 / 谱报警中一律降级，绝不计入真实候选。结果按 N 缓存（确定性，跨 cycle 复用）。
# ============================================================
def artifact_prone_signals(N, cfg, candidate_sigs=None):
    """返回在纯随机数据上仍 SURVIVOR 的基信号集合（构造伪结构）。

    关键性能修正：只对「真实数据上本身显著」的候选信号做随机对照，
    而非遍历全部基信号——其它信号反正不会当选，无需查。
    否则 33 信号 × 60 surrogate × 4 重型检验 的首跑会堵死整个 cycle
    （实测连续模式 60s 一轮根本跑不完，daemon 卡死）。

    - k_sur 降至 25（粗检足以识别「随机也显著」）
    - 90s 墙钟预算，超时即停（剩余信号下轮补算），绝不拖垮 cycle
    - 按 N 缓存已知的 prone 信号，跨 cycle 累积，越跑越快
    """
    cache_path = os.path.join(DATA_DIR, "artifact_prone.json")
    known_prone = set()
    try:
        if os.path.exists(cache_path):
            d = json.load(open(cache_path, encoding="utf-8"))
            if d.get("N") == N:
                known_prone = set(d.get("signals", []))
    except Exception:
        pass
    import representation_zoo as RZ
    import run_axes as RA
    RZ.register()
    seed = int(cfg.get("seed", 20260813))
    tests = ["acf_max", "mi_max", "perm_entropy", "fft_peak"]
    k_sur = int(cfg.get("artifact_k_sur", 25))
    budget = float(cfg.get("artifact_budget_sec", 90))
    t0 = time.time()
    sigs_to_check = list(candidate_sigs) if candidate_sigs else list(E.SIGMAPS.keys())
    n_checked = 0
    for sig in sigs_to_check:
        if sig in known_prone:
            continue
        try:
            ctrl = RA.random_control_label(sig, tests, N, seed=seed + 1, k_sur=k_sur)
        except Exception:
            continue
        n_checked += 1
        if ctrl == "SURVIVOR":
            known_prone.add(sig)
        if time.time() - t0 > budget:
            print(f"[cycle] 随机对照闸门: 超预算({budget:.0f}s)，已查{n_checked}个，剩余下轮补算")
            break
    try:
        json.dump({"N": N, "signals": list(known_prone)},
                  open(cache_path, "w", encoding="utf-8"))
    except Exception:
        pass
    return known_prone


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    # 1) 外部化 YAML（configs/engine.yaml）作为引擎参数的 canonical 源
    try:
        sys.path.insert(0, HERE)
        from configs import load_engine_config
        cfg.update(load_engine_config())
    except Exception:
        pass
    # 2) 兼容旧 config.json（仅部署键如 http_port/schedule_hours 应留在此；
    #    引擎键若仍存在会覆盖 YAML，便于平滑迁移）
    p = os.path.join(DATA_DIR, "config.json")
    if not os.path.exists(p):
        p = os.path.join(HERE, "config.json")   # 回退到代码目录
    if os.path.exists(p):
        cfg.update(json.load(open(p, encoding="utf-8")))
    # 3) YAML 扁平化后 cache 段键为 enabled/path，映射到本项目命名
    if "enabled" in cfg:
        cfg["cache_enabled"] = cfg.pop("enabled")
    if "path" in cfg:
        cfg["cache_path"] = cfg.pop("path")
    return cfg


# ============================================================
# #39 可微 Formula 集成 —— 额外候选源（实验性，默认关闭）
# ============================================================
def run_diff_formula_candidates(reds, blues, rng, cfg, seen_keys, all_evals):
    """#39 集成：在发现段上数值优化连续超参生成候选，冻结后并入统一诚信闸门池。

    纪律（与 #39 模块一致，且对接生产管线）：
      - 优化只在发现段（discovery_frac 前缀）；确认段优化器从未见过。
      - 候选经 E.evaluate 在全量数据评估 p_raw 后，与演化/谱/因果候选一起进入
        同一个 BH-FDR 池，并随后接受 OOT 盲测 + 多零假设交叉 + #41 发现/确认分离闸门
        的 unified 裁决——绝不绕过任何诚信闸门（与演化候选完全相同的待遇）。
    返回 (n_generated, n_added_to_pool)。disabled 时返回 (0, 0)。
    """
    if not cfg.get("diff_formula_enabled"):
        return 0, 0
    try:
        import diff_formula as DF
    except Exception as e:
        print(f"[cycle] #39 可微Formula 模块导入失败(跳过): {e}")
        return 0, 0
    try:
        rng_df = np.random.default_rng(int(cfg.get("seed", 20260813)) + len(reds) + 99)
        recs = DF.run_diff_search(
            reds, blues, rng_df,
            n_candidates=cfg.get("diff_formula_candidates", 6),
            discovery_frac=0.7,
            k_sur_opt=cfg.get("diff_formula_k_sur", cfg.get("k_light", 25)),
            n_steps=cfg.get("diff_formula_n_steps", 12),
            wf_n_folds=cfg.get("wf_n_folds", 3),
            wf_disc_frac=cfg.get("wf_disc_frac", 0.7),
            wf_k_sur=cfg.get("diff_formula_wf_k_sur", 25),
            confirm=False)  # 统一闸门在下方生产管线重判，避免双重确认开销
        added = 0
        for r in recs:
            g = {"sig": r["sig"], "test": r["test"], "params": r["params"]}
            k = E.genome_key(g["sig"], g["test"], g.get("params", {}))
            if k in seen_keys:
                continue
            ev = E.evaluate(g["sig"], g["test"], reds, blues, rng_df,
                           cfg.get("k_light", 25), params=g.get("params"))
            if ev is None:
                continue
            ev["diff_formula"] = True          # 标记来源，供看板/审计区分
            ev["df_disc_p"] = r.get("disc_p")
            seen_keys.add(k)
            all_evals.append(ev)
            added += 1
        print(f"[cycle] #39 可微Formula: 生成 {len(recs)} 候选, 入池 {added} "
              f"(经统一诚信闸门重判, 不绕过)")
        return len(recs), added
    except Exception as e:
        print(f"[cycle] #39 可微Formula 运行失败(不影响主流程): {e}")
        return 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    cfg = load_cfg()

    # 1. 数据
    master = D.load_master(MASTER)
    added = 0
    if not args.no_fetch:
        fresh = D.fetch_recent()
        if fresh is None:
            print("[cycle] 抓取失败，使用本地主表继续。")
        else:
            master, added = D.update_master(master, fresh)
            D.save_master(master, MASTER)
    else:
        if master:
            pass
    if not master:
        print("[cycle] 无数据，退出。"); return
    reds, blues, issues = D.to_arrays(master)
    N = len(reds)
    print(f"[cycle] N={N} 期, 新增 {added}, 末期 {issues[-1]}")
    sys.stdout.flush()  # 确保 daemon 日志即时可见

    # 2. 演化（接入跨轮 frontier：精英 seed + 参数 hill-climbing + 去重）
    rng = np.random.default_rng(cfg["seed"] + N)  # 随样本量变化种子，避免每轮完全相同
    # #40 增量评估缓存：同(基因组,数据集指纹)复用上次完整评估（严格等价，不改统计）
    ec = None
    if cfg.get("cache_enabled"):
        try:
            ec = C.EvalCache(os.path.join(DATA_DIR, cfg.get("cache_path", "eval_cache.json")))
        except Exception as e:
            print(f"[cycle] 缓存初始化失败(降级为无缓存): {e}")
            ec = None
    fr = F.load_frontier(DATA_DIR)
    elite_seeds = fr.get("elites", [])
    print(f"[cycle] frontier: 历史覆盖度={fr.get('coverage',0)}, 精英种子={len(elite_seeds)}, "
          f"z历史长度={len(fr.get('best_z_history',[]))}")
    evo = E.Evolution(reds, blues, rng, k_light=cfg["k_light"], k_heavy=cfg["k_heavy"],
                      epochs=cfg["epochs"], pop=cfg["pop"],
                      elites=elite_seeds, frontier=fr, eval_cache=ec)
    leaderboard, all_evals = evo.run()
    print(f"[cycle] 评估算子数(含重复): {len(all_evals)}, 唯一基因组: {len(leaderboard)}")

    # 2b. 直接谱扫描兜底闸门（独立于演化搜索）
    #     枚举全部基信号 × {fft_peak,acf_max,dfa_alpha,mi_max}，用 evaluate() 跑 shuffle
    #     零假设，对全部组合做 BH-FDR。无论演化覆盖如何，周期/自相关结构都被独立测试一次，
    #     补全"演化因随机搜索漏掉某具体 (信号,检验) 组合"这一盲区（阳性对照中周期17即此例）。
    rng_scan = np.random.default_rng(cfg["seed"] + N + 7)  # 独立种子，避免与演化相关
    spec = E.spectral_scan(reds, blues, rng_scan, k_sur=cfg["k_light"])
    # 独立因果耦合扫描（双向 4 配对 × {CCM,Granger}；用更高 k_causal 保证检出功效）
    caus = E.causal_scan(reds, blues, rng_scan, k_sur=cfg["k_causal"])
    # 合并进主 FDR 池（按基因组去重），使 best_q 反映"演化 + 扫描"的最强证据
    seen_keys = set()
    for e in all_evals:
        seen_keys.add(E.genome_key(e["sig"], e["test"], e.get("params", {})))
    for e in spec["evals"]:
        k = E.genome_key(e["sig"], e["test"], e.get("params", {}))
        if k not in seen_keys:
            seen_keys.add(k)
            all_evals.append(e)
    print(f"[cycle] 谱扫描: 测试 {spec['n']} 组合, q_min={spec['q_min']:.4g}, "
          f"最强={spec['best_sig']}/{spec['best_test']} (p={spec['p_min']:.4g}, {spec['verdict']})")
    for e in caus["evals"]:
        k = E.genome_key(e["sig"], e["test"], e.get("params", {}))
        if k not in seen_keys:
            seen_keys.add(k)
            all_evals.append(e)
    print(f"[cycle] 因果扫描: 测试 {caus['n']} 组合, q_min={caus['q_min']:.4g}, "
          f"最强={caus['best_sig']}/{caus['best_test']} (p={caus['p_min']:.4g}, {caus['verdict']})")

    # 2c. #39 可微 Formula 候选（实验性，默认关闭）：发现段数值优化连续超参生成候选，
    #     冻结后并入统一诚信闸门池（BH-FDR + OOT + 交叉零假设 + #41 发现/确认分离闸门），
    #     与演化/谱/因果候选完全相同的待遇，绝不绕过任何闸门。
    df_gen, df_added = run_diff_formula_candidates(reds, blues, rng, cfg, seen_keys, all_evals)
    if df_gen:
        print(f"[cycle] #39 可微Formula: 生成 {df_gen}, 入池 {df_added}")

    # 3b. 非平稳性 / 物理磨损监控闸门（方向1）：每球频率随时间漂移 + 短期动量
    #     与演化/谱/因果 FDR 池分离——它测的是"单球边际频率的时间非平稳"，
    #     单位不同，混入主池会污染 BH-FDR。独立成门，单独报告 verdict。
    rng_ns = np.random.default_rng(cfg["seed"] + N + 13)
    ns = NS.ball_drift_scan(reds, blues, rng_ns, k_sur=cfg.get("k_nonstat", 300))
    bd = ns["best_drift"]; bm = ns["best_mom"]
    print(f"[cycle] 非平稳监控: 漂移显著 {ns['n_sig_drift']} 球 | 动量 {ns['n_sig_mom']} 球"
          f" | 最强漂移 {bd[0]}{bd[1]} q={ns['best_q_drift']:.4g}"
          f" | 最强动量 {bm[0]}{bm[1]} q={ns['best_q_mom']:.4g}")

    # 3a. 随机数据对照闸门（构造伪结构拦截）：只对「真实数据上本身显著」的候选信号
    #     做纯随机对照——若随机数据上也 SURVIVOR，说明显著来自信号构造本身
    #     （如 red_recurrence_mean 的 N 惩罚尖峰），在 FDR / 最优 / 谱报警中降级。
    #     只查候选信号 => 工作量从 33 信号降至个位数，首跑不再堵死 cycle。
    cand_sigs = {e["sig"] for e in all_evals if e["p_raw"] < 0.2}
    prone = artifact_prone_signals(N, cfg, candidate_sigs=cand_sigs)
    if prone:
        print(f"[cycle] 随机对照闸门: {len(prone)} 个基信号判为构造伪结构 {sorted(prone)}")
    else:
        print("[cycle] 随机对照闸门: 无构造伪结构信号")

    # 3. FDR (跨全部评估)
    pvals = np.array([e["p_raw"] for e in all_evals])
    qs = E.bh_fdr(pvals)
    for e, q in zip(all_evals, qs):
        e["q"] = float(q)
        if e["sig"] in prone:
            # 构造伪结构：随机数据上也显著 => 降级，不计入 FDR 显著 / 最优 / 报警
            e["artifact_prone"] = True
            e["q"] = 1.0
            e["verdict"] = "构造伪结构[随机对照闸门]"
            continue
        if q < 0.05:
            e["verdict"] = "显著(<0.05)"
        elif q < 0.2:
            e["verdict"] = "边缘"
        else:
            e["verdict"] = "随机区间"

    # 4. 本论最优（按 q）
    order = sorted(all_evals, key=lambda e: e["q"])
    best = order[0]
    best_q = best["q"]

    # 5. 样本外验证（针对唯一 leaderboard 最优）
    oos_p = None
    lb_items = sorted(leaderboard.values(), key=lambda e: e["p_raw"])
    if lb_items:
        # OOS/方向准确率/交叉零假设/OOT 验证只针对"单变量结构"候选(演化+谱扫描)，
        # 不含双变量因果耦合检验(CCM/Granger 不是可外推的方向预测公式, 且无单变量信号构造器)。
        # 这样谱扫描独立检出的结构仍会被同样严格的样本外闸门裁决, 而因果检验只参与 FDR 池。
        _causal = {"ccm", "granger"}
        _sv = [e for e in all_evals if e["test"] not in _causal]
        top = min(_sv, key=lambda e: e["q"]) if _sv else best
        oos = E.out_of_sample(top, reds, blues, rng, frac=cfg["oos_frac"], k_sur=cfg["k_light"])
        oos_p = oos

    # 5b. 诚实的"高于随机"方向准确率（准确率由公式读取规则决定 + AAFT 替代分布零假设）
    acc = None
    if lb_items:
        acc = E.oos_accuracy(top, reds, blues, rng, frac=cfg["oos_frac"], k_sur=cfg["k_light"])

    # 5c. 多零假设交叉验证（AAFT vs IAAFT vs TWIN）：最优候选是否在三套零假设下都显著？
    cross = None
    if lb_items:
        cross = E.cross_validate_null(top, reds, blues, rng, frac=cfg["oos_frac"], k_sur=cfg["k_light"])

    # 5d. Out-of-Time (OOT) 盲测：候选来自训练段(前85%)，用冻结规则盲打真正未来(末段)
    # 这是进化搜索 10000+ 公式的最终诚信闸门——直接回答"公式能预测真实未来吗"。
    oot = None
    if lb_items:
        oot = E.out_of_time(top, reds, blues, rng, train_frac=0.85, k_sur=cfg["k_light"])

    # 5e 前置：随机对照闸门作用于谱扫描——若最强谱组合来自构造伪结构信号，
    # 降级 spec 并抑制报警（避免如 red_recurrence_mean 的 N 惩罚尖峰被误报为谱信号）。
    if spec.get("best_sig") in prone:
        spec["q_min"] = 1.0
        spec["verdict"] = "构造伪结构[随机对照闸门]"
        print(f"[cycle] 谱扫描最强信号 {spec['best_sig']} 被判构造伪结构(随机对照闸门)，已降级")

    # 5e. 谱扫描独立闸门（灵敏度探测器）：若 z-FDR 显著，对最强谱组合跑 OOT 盲测验证，
    # 防止 z 偶然尖峰误报。这是演化搜索的兜底——演化漏掉的周期/自相关结构在此独立裁决。
    spectral_oot = None
    spectral_alert = False
    if spec["q_min"] < FDR_Q and spec.get("best_eval") is not None:
        try:
            spectral_oot = E.out_of_time(spec["best_eval"], reds, blues, rng,
                                         train_frac=0.85, k_sur=cfg["k_light"])
            # 严格闸门：仅当 OOT 盲测 p 极小(<0.001)才确认报警。
            # 理由：谱扫描用大 k surrogate 会偶发"地板命中"(真实值仅压过所有 surrogate，
            # 概率 1/(k+1)≈1/2501)，这种偶然尖峰的 OOT p 通常~0.005，会被 0.001 阈值挡下；
            # 真实强周期(如 z=100)的 OOT p≈0，照常通过。此阈值把假阳性率压到≈1e-5/轮。
            spectral_alert = bool(spectral_oot and spectral_oot["above_random"]
                                  and spectral_oot["p_random"] < 0.001)
        except Exception as e:
            print(f"[cycle] 谱扫描 OOT 验证失败(不影响主流程): {e}")

    # 5f. 发现/确认分离闸门 (#41) —— 把"在发现段挖出的候选"在独立确认段上跨折裁决，
    # 彻底切断"候选在全量数据上被挑出→再在尾部验证"的选择性偏差（自演进系统最易翻车处）。
    # 候选一旦选定即冻结，在发现阶段从未见过的滚动未来段上一次性独立验证，跨折 Fisher 合并
    # 并要求多数折确认。verdict=SIGNAL 才是"结构在独立未来复现"的唯一诚实证据。
    wf = None
    wf_signal = False
    if lb_items:
        try:
            wf = EV.confirm_candidate(top, reds, blues, rng,
                                      n_folds=cfg.get("wf_n_folds", 3),
                                      discovery_frac=cfg.get("wf_disc_frac", 0.7),
                                      k_sur=cfg["k_light"])
            wf_signal = bool(wf and wf["verdict"] == "SIGNAL")
        except Exception as e:
            print(f"[cycle] 发现/确认分离闸门失败(不影响主流程): {e}")

    alert = ((best_q < cfg["alert_q"]) and (oos_p is not None) and (oos_p < cfg["alert_oos_p"])) \
            or spectral_alert or wf_signal
    # 诚实闸门：方向准确率"高于随机"必须先过结构 FDR 显著(best_q<0.05)，否则只是
    # 从大量候选中挑最优再测的"选择性偏差"产物，不能作为结论。
    oos_above = bool(acc and acc["above_random"] and best_q < 0.05)

    # 6. 更新并持久化演化前沿（跨轮累积迭代）
    prev_tried = len(fr.get("tried", []))
    fr = F.update_frontier(fr, leaderboard, evo.tried, elite_k=12)
    if acc:
        hist = fr.setdefault("acc_history", [])
        hist.append({"hr": round(acc["hit_rate"], 4),
                     "sur_mean": round(acc["sur_mean"], 4),
                     "p_random": round(acc["p_random"], 4),
                     "above": bool(acc["above_random"]),
                     "n": int(acc["n"])})
        fr["acc_history"] = hist[-200:]
    F.save_frontier(DATA_DIR, fr)
    z_hist = fr["best_z_history"]
    newly = fr["coverage"] - prev_tried
    print(f"[cycle] 迭代进度: 覆盖度={fr['coverage']} (本论新增 {newly}), "
          f"精英={len(fr['elites'])}, z轨迹末值={z_hist[-1] if z_hist else 'NA'}")
    # #40 增量缓存：汇报命中率并落盘
    if ec is not None:
        cs = ec.stats()
        print(f"[cycle] 增量缓存: 命中率={cs['rate']*100:.0f}% (命中{cs['hits']}/"
              f"总{cs['hits']+cs['misses']}, 条目{cs['entries']})")
        ec.flush()

    # 7. 持久化
    con = S.open_db(DB)
    pc = None  # 持续阳性对照结果占位；8.5 节在 state 写入之后重算（依赖 rid），此处先绑定避免 UnboundLocalError
    run = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_issues": N, "added": added, "n_eval": len(all_evals),
        "best_q": best_q, "best_sig": best["sig"], "best_test": best["test"],
        "best_p": best["p_raw"], "oos_p": (oos_p if oos_p is not None else -1.0),
        "oos_acc": (round(acc["hit_rate"], 4) if acc else None),
        "oos_acc_sur": (round(acc["sur_mean"], 4) if acc else None),
        "oos_acc_p": (round(acc["p_random"], 4) if acc else None),
        "oos_acc_above": oos_above,
        "oos_acc_n": (acc["n"] if acc else 0),
        "oos_cross_aaft": (round(cross["aaft"], 4) if cross and cross.get("aaft") is not None else None),
        "oos_cross_iaaft": (round(cross["iaaft"], 4) if cross and cross.get("iaaft") is not None else None),
        "oos_cross_consistent": (bool(cross["consistent"]) if cross else False),
        "oot_hit": (round(oot["hit_rate"], 4) if oot else None),
        "oot_p": (round(oot["p_random"], 4) if oot else None),
        "oot_above": bool(oot and oot["above_random"]),
        "oot_n": (oot["n"] if oot else 0),
        "oot_rule": (oot["best_rule"] if oot else None),
        "spectral_q": spec["q_min"], "spectral_q_rank": spec["q_rank"], "spectral_p": spec["p_min"],
        "spectral_best_sig": spec["best_sig"], "spectral_best_test": spec["best_test"],
        "spectral_best_z": spec["best_z"], "spectral_z_min": spec["z_min"],
        "spectral_n": spec["n"], "spectral_verdict": spec["verdict"],
        "spectral_oot_hit": (round(spectral_oot["hit_rate"], 4) if spectral_oot else None),
        "spectral_oot_p": (round(spectral_oot["p_random"], 4) if spectral_oot else None),
        "spectral_oot_above": bool(spectral_oot and spectral_oot["above_random"]),
        "spectral_oot_n": (spectral_oot["n"] if spectral_oot else 0),
        "spectral_alert": spectral_alert,
        "wf_verdict": (wf["verdict"] if wf else None),
        "wf_conf_p": (round(wf["conf_combined_p"], 4) if wf else None),
        "wf_disc_p": (round(wf["disc_combined_p"], 4) if wf else None),
        "wf_n_confirm": (wf["n_confirm"] if wf else None),
        "wf_n_folds": (wf["n_folds"] if wf else None),
        "df_enabled": bool(cfg.get("diff_formula_enabled")),
        "df_gen": df_gen, "df_added": df_added,
        "positive_control": pc,   # 持续阳性对照：闸门功率监控（None=本轮未跑）
        "alert": alert, "coverage": fr["coverage"],
        "artifact_prone_n": len(prone), "artifact_prone": sorted(prone)[:12],
        "note": ("候选结构! 需人工复核" if alert else "无超越随机的可提取结构 (null)"),
    }
    rid = S.insert_run(con, run)
    S.insert_evals(con, rid, all_evals)
    con.close()

    # 8.5 持续阳性对照（闸门功率监控）：每 positive_control_every 轮注入已知结构，
    # 验证统一诚信闸门仍灵敏；若判不出 SIGNAL 说明闸门功率退化，redteam_audit 会 ALERT。
    pc = None
    pc_every = int(cfg.get("positive_control_every", 1))
    if cfg.get("positive_control_enabled", True) and (rid % pc_every == 0):
        try:
            pc_rng = np.random.default_rng(int(cfg.get("seed", 20260813)) + rid + 777)
            pc = PC.run_positive_control(
                pc_rng,
                n=int(cfg.get("positive_control_n", 1000)),
                P=int(cfg.get("positive_control_lag", 8)),
                k_sur=int(cfg.get("positive_control_k_sur", 30)),
                n_folds=int(cfg.get("positive_control_folds", 2)),
                discovery_frac=float(cfg.get("discovery_frac", 0.7)))
        except Exception as e:
            pc = {"verified": False, "verdict": None, "conf_p": None,
                  "disc_p": None, "n_confirm": None,
                  "note": "positive_control error: %s" % e}

    # 8. 写 state.json
    lb_top = sorted(leaderboard.values(), key=lambda e: e["p_raw"])[:20]
    history = S.recent_runs(S.open_db(DB), 200)
    state = {
        "updated": run["ts"], "n_issues": N, "last_issue": issues[-1], "added": added,
        "cycle_id": rid, "best_q": best_q, "best_sig": best["sig"], "best_test": best["test"],
        "best_p": best["p_raw"], "best_stat": best["stat"], "best_z": best["z"],
        "oos_p": (oos_p if oos_p is not None else None),
        "oos_acc": (round(acc["hit_rate"], 4) if acc else None),
        "oos_acc_sur": (round(acc["sur_mean"], 4) if acc else None),
        "oos_acc_p": (round(acc["p_random"], 4) if acc else None),
        "oos_acc_above": oos_above,
        "oos_acc_n": (acc["n"] if acc else 0),
        "oos_cross_aaft": (round(cross["aaft"], 4) if cross and cross.get("aaft") is not None else None),
        "oos_cross_iaaft": (round(cross["iaaft"], 4) if cross and cross.get("iaaft") is not None else None),
        "oos_cross_primary_type": (cross.get("primary_type") if cross else None),
        "oos_cross_primary": (round(cross["primary"], 4) if cross and cross.get("primary") is not None else None),
        "oos_cross_consistent": (bool(cross["consistent"]) if cross else False),
        "oot_hit": (round(oot["hit_rate"], 4) if oot else None),
        "oot_p": (round(oot["p_random"], 4) if oot else None),
        "oot_above": bool(oot and oot["above_random"]),
        "oot_n": (oot["n"] if oot else 0),
        "oot_rule": (oot["best_rule"] if oot else None),
        "spectral_q": spec["q_min"], "spectral_q_rank": spec["q_rank"], "spectral_p": spec["p_min"],
        "spectral_best_sig": spec["best_sig"], "spectral_best_test": spec["best_test"],
        "spectral_best_z": spec["best_z"], "spectral_z_min": spec["z_min"],
        "spectral_n": spec["n"], "spectral_verdict": spec["verdict"],
        "spectral_oot_hit": (round(spectral_oot["hit_rate"], 4) if spectral_oot else None),
        "spectral_oot_p": (round(spectral_oot["p_random"], 4) if spectral_oot else None),
        "spectral_oot_above": bool(spectral_oot and spectral_oot["above_random"]),
        "spectral_oot_n": (spectral_oot["n"] if spectral_oot else 0),
        "spectral_alert": spectral_alert,
        # 发现/确认分离闸门 (#41)：候选冻结后在独立确认段跨折裁决
        "wf_verdict": (wf["verdict"] if wf else None),
        "wf_conf_p": (round(wf["conf_combined_p"], 4) if wf else None),
        "wf_disc_p": (round(wf["disc_combined_p"], 4) if wf else None),
        "wf_n_confirm": (wf["n_confirm"] if wf else None),
        "wf_n_folds": (wf["n_folds"] if wf else None),
        # 因果耦合（独立轻量扫描）
        "causal_q_min": caus["q_min"], "causal_p_min": caus["p_min"],
        "causal_best_sig": caus["best_sig"], "causal_best_test": caus["best_test"],
        "ccm_rho_max": caus.get("ccm_rho_max"), "granger_f_max": caus.get("granger_f_max"),
        # 非平稳 / 物理磨损监控闸门（独立成门，不混入主 FDR 池）
        "ns_n_sig_drift": ns["n_sig_drift"], "ns_n_sig_mom": ns["n_sig_mom"],
        "ns_best_drift_sig": f"{bd[0]}{bd[1]}", "ns_best_drift_val": round(bd[2], 4),
        "ns_best_drift_q": round(ns["best_q_drift"], 4),
        "ns_best_mom_sig": f"{bm[0]}{bm[1]}", "ns_best_mom_val": round(bm[4], 4),
        "ns_best_mom_q": round(ns["best_q_mom"], 4),
        "ns_verdict": ns["verdict"], "ns_k_sur": ns["k_sur"],
        "alert": bool(alert),
        "positive_control": pc,   # 持续阳性对照结果（None=本轮未跑；dict=闸门功率核验）
        "artifact_prone_n": len(prone), "artifact_prone": sorted(prone),
        "n_eval": len(all_evals), "n_unique": len(leaderboard),
        "coverage": fr["coverage"], "elite_count": len(fr["elites"]),
        "best_z_history": z_hist,
        "params": cfg,
        "leaderboard": [
            {"sig": e["sig"], "test": e["test"],
             "params": e.get("params", {"_sig": {}, "_test": {}}),
             "p_raw": e["p_raw"], "q": e.get("q", 1.0),
             "z": e["z"], "stat": e["stat"], "verdict": e["verdict"]} for e in lb_top],
        "history": [{"ts": h[1], "best_q": h[4], "alert": bool(h[9]),
                     "coverage": h[10] if len(h) > 10 else None} for h in history],
    }
    # 7.5 嵌入式自检（主动暴露问题，不等用户发现）
    sc_result = self_check(state, acc, cfg)
    state["self_check"] = {
        "score": sc_result["score"],
        "warnings": sc_result["warnings"],
        "ts": sc_result["ts"],
    }

    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 8b. 滚动快照（防坏 state 丢历史；保留最近 20 个，避免磁盘膨胀）
    import shutil
    snap = os.path.join(DATA_DIR, f"state.{state['cycle_id']}.json")
    shutil.copy2(STATE, snap)
    # 清理旧快照（保留最近 20）
    snaps = sorted([f for f in os.listdir(DATA_DIR) if f.startswith("state.") and f.endswith(".json")])
    for old in snaps[:-20]:
        try:
            os.remove(os.path.join(DATA_DIR, old))
        except OSError:
            pass

    print(f"[cycle] best_q={best_q:.4g}  best={best['sig']}/{best['test']}  "
          f"oos_p={oos_p if oos_p is None else round(oos_p,4)}  alert={alert}")
    if acc:
        if oos_above:
            tag = "高于随机(且结构FDR显著)!"
            note = ""
        elif acc["above_random"]:
            tag = "探索性高于随机"
            note = "（结构未达FDR显著，系从大量候选挑最优再测的选择性偏差，非结论）"
        else:
            tag = "未高于随机(=随机基线)"
            note = ""
        print(f"[cycle] 方向准确率={acc['hit_rate']:.3f}  随机基线={acc['sur_mean']:.3f}  "
              f"p_random={acc['p_random']:.3f}  => {tag}  最优读法={acc.get('best_rule')}{note}")
    if cross:
        print(f"[cycle] 零假设交叉验证: primary({cross.get('primary_type')}) p={cross.get('primary')}  "
              f"AAFT p={cross.get('aaft')}  IAAFT p={cross.get('iaaft')}  "
              f"三零假设一致显著={cross.get('consistent')}")
    print(f"[cycle] state -> {STATE}")
    sys.stdout.flush()

    # 8c. 生成监控看板 (自包含 HTML，供 CloudStudio 部署到腾讯云作为第三辆车的可视化/分享层)
    try:
        import make_dashboard
        make_dashboard.main()
    except Exception as de:
        print(f"[cycle] dashboard 生成失败(不影响主流程): {de}")

    # 8c2. 周期摘要持久化 (追加式 JSONL; 供 CloudStudio 看板/外部直接解读结论,
    #      不依赖解析 daemon.log 黑洞, 也不依赖易过期的 state.json)
    _digest_path = os.path.join(DATA_DIR, "daily_digest.jsonl")
    try:
        # 完整结论载荷：直接复用 run 字典(已含 oos/谱/因果/非平稳/确认分离闸门等),
        # 仅补三处: ① 头条 verdict 文本 ② leaderboard 紧凑表 ③ positive_control 真实值
        # (pc 在 run 字典生成之后才重算, run 内为 None 占位, 此处用真实 pc 覆盖)。
        def _clean_digest(o):
            if isinstance(o, dict):
                return {k: _clean_digest(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_clean_digest(v) for v in o]
            if isinstance(o, np.floating):
                return float(o)
            if isinstance(o, np.integer):
                return int(o)
            return o
        _lb_compact = [
            {"sig": e["sig"], "test": e["test"],
             "params": e.get("params", {"_sig": {}, "_test": {}}),
             "p_raw": round(float(e["p_raw"]), 6),
             "q": round(float(e.get("q", 1.0)), 6),
             "z": (round(float(e["z"]), 3) if e.get("z") is not None else None),
             "stat": (round(float(e["stat"]), 4) if e.get("stat") is not None else None),
             "verdict": e.get("verdict", "—")}
            for e in lb_top[:8]
        ]
        _digest_entry = _clean_digest(dict(run))
        _digest_entry["verdict"] = best.get("verdict", "?")
        _digest_entry["leaderboard"] = _lb_compact
        _digest_entry["positive_control"] = pc   # 真实阳性对照结果(run 内为 None 占位)
        with open(_digest_path, "a", encoding="utf-8") as _df:
            _df.write(json.dumps(_digest_entry, ensure_ascii=False) + "\n")
        print(f"[cycle] 摘要(完整结论载荷)已写入 {_digest_path}")
    except Exception as _de:
        print(f"[cycle] 摘要写入失败(不影响主流程): {_de}")
    sys.stdout.flush()

    # 8d. 红队自审（诚实守护，只读；默认关闭，不扰生产）
    if cfg.get("redteam_audit_enabled", False):
        try:
            import redteam_audit as RA
            with open(STATE, "r", encoding="utf-8") as _f:
                _st = json.load(_f)
            _rep = RA.audit_cycle(_st, summary_text=None)
            _jp, _mp = RA.write_report(_rep, os.path.join(DATA_DIR, cfg.get("redteam_out", "audit")))
            print(f"[cycle] 红队自审 verdict={_rep['verdict']} findings={_rep['n_findings']} -> {_mp}")
        except Exception as _ae:
            print(f"[cycle] 红队自审失败(不影响主流程): {_ae}")


if __name__ == "__main__":
    main()
