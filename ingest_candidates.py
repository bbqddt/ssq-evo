# -*- coding: utf-8 -*-
"""ingest_candidates.py —— 驾1 摄入驾3 提案候选，过统一闸门后并入 frontier

流程：
  1. 取驾3 推到 ga-candidates 分支的 candidates.json：
     容器内无 git/credential，改用 GitHub Contents API（Python urllib 直连，
     api.github.com 已验证容器内可达，绕过 raw CDN 代理 TLS 失败）；
     也可用 --local <file> 做本地测试/干跑。
  2. 对每条候选，在【完整真实数据】上跑 run_axes.label_axis（分层 null）
     + random_control_label（构造伪结构拦截）。
  3. 只有 label==SURVIVOR 且未触发构造伪结构者，作为精英种子并入 frontier.json；
     驾1 下一轮 GA 会以这些种子起种群。

诚实护栏（不可协商）：
  - 候选只是「提案」，绝不绕过统一闸门直接进生产；
  - 若真实数据上无任何候选过闸门 → 诚实结论「提案未获确认」，不强行并入；
  - 这把云端免费算力的搜索广度/种子多样性，安全地嫁接进唯一真相源。
"""
import os
import sys
import json
import argparse
import datetime

import numpy as np
import data as D
import run_axes as RA
import frontier as FR
import engine_core as E

DATA_DIR = os.environ.get("DATA_DIR", "D:/ssq_evo_data")
CAND_FILE = "candidates.json"
GATE_SEED = 20260820
# 随机对照闸门 surrogate 数（构造伪结构拦截强度，不可弱化）。
RCL_K_SUR = 60
# label_axis 分层 null surrogate 数（足够判定 SURVIVOR，且主进程直跑不 OOM）。
LABEL_K_SUR = 40


def _eval_one(sig, test, params, master_path, N, seed):
    """主进程内评估单条候选（Windows multiprocessing 在本环境完全不可用，弃用）。
    优化：只有 label_axis 判 SURVIVOR 才跑 random_control_label（重型），
    其余候选直接跳过，大幅降内存/时间，避免 OOM 拖垮整轮摄入。
    返回 (label, p_shuffle, ctrl)；异常 -> ("ERROR", None, None)。"""
    import gc
    try:
        m = D.load_master(master_path)
        reds, blues, _ = D.to_arrays(m)
        rng = np.random.default_rng(seed)
        rec = RA.label_axis(sig, [test], reds, blues, rng, k_sur=LABEL_K_SUR, params=params)
        del m, reds, blues
        gc.collect()
        label = rec.get("label")
        # 仅 SURVIVOR 需要构造伪结构拦截（非 SURVIVOR 已被分层 null 杀，无需随机对照）。
        ctrl = None
        if label == "SURVIVOR":
            ctrl = RA.random_control_label(sig, [test], N, seed=seed, k_sur=RCL_K_SUR)
        return (label, rec.get("p_shuffle"), ctrl)
    except Exception as e:
        gc.collect()
        return ("ERROR", None, repr(e)[:200])


def fetch_candidates(local_path=None):
    if local_path:
        try:
            with open(local_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("[ingest] 本地文件读取失败:", e)
            return None
    # 容器内无 git/credential，改用 GitHub Contents API（Python urllib 直连，
    # 无需代理、无需 git）。raw.githubusercontent.com 经代理 TLS 握手失败，
    # 故走 api.github.com（已验证容器内可达 HTTP 200）。
    api_url = ("https://api.github.com/repos/bbqddt/ssq-evo/contents/"
               "candidates.json?ref=ga-candidates")
    try:
        import urllib.request, base64
        req = urllib.request.Request(api_url, headers={"User-Agent": "ssq-evo-ingest"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        if obj.get("encoding") == "base64" and obj.get("content"):
            raw = base64.b64decode("".join(obj["content"].split()))
            blob = json.loads(raw.decode("utf-8"))
            # 落盘审计（供主机/看板核对，也兼容旧 --local 下游）
            try:
                with open(os.path.join(DATA_DIR, CAND_FILE), "w", encoding="utf-8") as fp:
                    json.dump(blob, fp, ensure_ascii=False)
            except Exception:
                pass
            return blob
        print("[ingest] API 返回异常（无 base64 content）")
        return None
    except Exception as e:
        print("[ingest] GitHub API 拉取失败:", repr(e)[:200])
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default=None, help="本地候选文件（干跑/测试用）")
    ap.add_argument("--dry-run", action="store_true", help="只评估不写入 frontier")
    args = ap.parse_args()

    master = os.path.join(DATA_DIR, "ssq_master.csv")
    m = D.load_master(master)
    if not m:
        print("[ingest] 无真实数据:", master)
        return 2
    reds, blues, _ = D.to_arrays(m)
    N = int(reds.shape[0])
    rng = np.random.default_rng(GATE_SEED)

    blob = fetch_candidates(args.local)
    if not blob:
        print("[ingest] 无候选可摄入")
        return 0
    cands = blob.get("candidates", [])
    print("[ingest] 载入 %d 候选（云端提案），在 %d 期真实数据上过闸门"
          % (len(cands), N))

    f = FR.load_frontier(DATA_DIR)
    added = 0
    rejected = 0
    # 契约基石二：驾3 提案过闸的「存活/淘汰」必须结构化落盘，供 L1 失败吸收器消费。
    fate_path = os.path.join(DATA_DIR, "ingest_fate.jsonl")
    fate_fp = open(fate_path, "a", encoding="utf-8")
    master_path = os.path.join(DATA_DIR, "ssq_master.csv")
    # 驾3 提案是不可信的外部输入：主进程内逐条评估（Windows multiprocessing spawn
    # 连续/批量均不稳，弃用），单条异常 -> ERROR 跳过；每轮 gc 防累积 OOM。
    # 部分保存兜底：finally 无条件 save 已确认的存活精英，绝不丢结果。
    try:
        for c in cands:
            sig, test, params = c.get("sig"), c.get("test"), (c.get("params") or {})
            if sig is None or test is None:
                continue
            label, p_shuffle, ctrl = _eval_one(sig, test, params, master_path, N, GATE_SEED)
            # 构造伪结构拦截：仅当 label==SURVIVOR 且纯随机数据也 SURVIVOR 时成立。
            # 非 SURVIVOR 候选跳过 random_control_label(ctrl=None)，不计为 artifact。
            artifact = (label == "SURVIVOR" and ctrl == "SURVIVOR")
            fate_fp.write(json.dumps({
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "sig": sig, "test": test, "params": params,
                "label": label, "artifact": bool(artifact) if ctrl is not None else None,
                "p_shuffle": p_shuffle, "ctrl_label": ctrl,
            }, ensure_ascii=False) + "\n")
            if label == "ERROR":
                rejected += 1
                print("  [评估异常-跳过] sig=%s test=%s" % (sig, test))
                continue
            if label == "SURVIVOR" and not artifact:
                g = {"sig": sig, "test": test, "params": params}
                gkey = E.genome_key(sig, test, params)
                # 保留已存在精英的 q/verdict/z（绝不因并入提案而剥除评估数据）
                for ex in f["elites"]:
                    if E.genome_key(ex.get("sig"), ex.get("test"), ex.get("params", {})) == gkey:
                        for _k in ("q", "verdict", "z"):
                            if _k in ex:
                                g[_k] = ex[_k]
                        break
                if g not in f["elites"]:
                    f["elites"].append(g)
                    f["tried"].append(gkey)
                    added += 1
                    print("  [并入精英] sig=%s test=%s p_shuffle=%s"
                          % (sig, test, p_shuffle))
                else:
                    print("  [已存在] sig=%s test=%s" % (sig, test))
            else:
                rejected += 1
                print("  [拒绝] sig=%s test=%s label=%s artifact=%s"
                      % (sig, test, label, artifact))
    except Exception as e:
        print("[ingest] 循环异常(兜底部分保存): %s" % repr(e)[:200])
    finally:
        fate_fp.close()
        # 无论如何都保存已收集的存活精英（部分保存），避免进程级崩溃丢结果。
        if added and not args.dry_run:
            try:
                FR.save_frontier(DATA_DIR, f)
                print("[ingest] 已写入 frontier（精英种子=%d）" % len(f["elites"]))
            except Exception as e:
                print("[ingest] 保存 frontier 失败: %s" % repr(e)[:200])

    print("[ingest] 通过闸门=%d  拒绝=%d  新增精英=%d" % (added, rejected, added))
    if not added:
        print("[ingest] 本轮无候选通过闸门（诚实结论：提案未获确认）")
    elif args.dry_run:
        print("[ingest] DRY-RUN：未写入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
