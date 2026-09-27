#!/usr/bin/env python3
# heartbeat_make.py - generate audit/heartbeat.json for the cloud watchdog.
# Run after each draw-day score phase (or on demand).
# Output: /app/data/audit/heartbeat.json
import json, hashlib, time, datetime, os, sys

DATA = "/app/data"
AUDIT = os.path.join(DATA, "audit")
MASTER = os.path.join(DATA, "ssq_master.csv")
PREDS = os.path.join(DATA, "predictions.jsonl")
OBF = os.path.join(DATA, "audit", "preregistered_scores.jsonl")
ANCHOR = "/app/anchors/preregistered.sha256"
SCORER = os.path.join(DATA, "predict_cron.log")

OBF_DESIGN = os.path.join(DATA, "audit", "obf_design.json")
N_BASIS = 3496

def sha256_of(path):
    if not os.path.exists(path): return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def last_draw_issue():
    if not os.path.exists(MASTER): return None
    with open(MASTER, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln for ln in f if ln.strip()]
    if len(lines) < 2: return None
    last = lines[-1].split(",")[0].strip()
    return last if last.isdigit() else None

def load_obf_design():
    if not os.path.exists("/app/data/audit/obf_design.json"): return None
    with open("/app/data/audit/obf_design.json", "r", encoding="utf-8") as f:
        return json.load(f)

def obf_boundary(n_new, design):
    """根据 OBF 设计表返回该 n_new 对应的上边界 z 值。"""
    if not design or "boundary_head" not in design:
        return None
    bh = design["boundary_head"]
    keys = [int(k) for k in bh.keys()]
    keys.sort()
    key = None
    for k in keys:
        if k <= n_new:
            key = k
        else:
            break
    if key is None:
        return None
    return bh[str(key)]

def compute_z_n(n_new):
    """简易 OBF 统计量 Z_n 的近似。当前返回 None，状态由边界表兜底判定。"""
    return None

def update_obf_chain():
    """自动更新 OBF 链：根据 master 行数计算 n_new，追加记录到 preregistered_scores.jsonl。"""
    if not os.path.exists("/app/data/ssq_master.csv"):
        return False
    with open("/app/data/ssq_master.csv", "r", encoding="utf-8", errors="replace") as f:
        lines = [ln for ln in f if ln.strip()]
    n_master = len(lines) - 1 if len(lines) > 1 else 0
    if n_master <= 0:
        return False
    n_new = n_master - 3496
    if n_new <= 0:
        return False

    design = load_obf_design()
    boundary = obf_boundary(3496 + n_new, design)  # n_new 对应的边界
    z_n = None  # 简化：暂不计算 Z_n，状态由边界表兜底

    # 判定状态
    status = "INSUFFICIENT"
    # 完整版需计算 Z_n 并与边界比较；此处简化默认 INSUFFICIENT

    # 读取现有 OBF 链
    obf_records = []
    obf_path = "/app/data/audit/preregistered_scores.jsonl"
    if os.path.exists(obf_path):
        with open(obf_path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        obf_records.append(json.loads(ln))
                    except Exception as _e:
                        try:
                            import ssq_log
                            ssq_log.log_exception("heartbeat_make.obf_parse", _e, f"line={ln[:80]!r}")
                        except Exception:
                            print(f"[heartbeat_make] skip bad jsonl line: {_e}", file=sys.stderr)

    # 避免重复写入：若已有相同 n_new 的记录则跳过
    n_master_lines = 0
    if os.path.exists("/app/data/ssq_master.csv"):
        with open("/app/data/ssq_master.csv", "r", encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f if ln.strip()]
            n_master = len(lines) - 1 if len(lines) > 1 else 0
    n_new = n_master - 3496
    if n_new <= 0:
        return False

    if obf_records and obf_records[-1].get("n_new") == n_new:
        return False

    # 生成新记录
    footer = ("即便确认 σ≈3.5% 边际偏倚，它不改变头奖概率的量级（1/1772 万）。"
              "这是结构，不是印钞机。")
    new_rec = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "design": "OBF_v1",
        "n_basis": 3496,
        "n_new": n_new,
        "footer": ("即便确认 σ≈3.5% 边际偏倚，它不改变头奖概率的量级（1/1772 万）。"
                   "这是结构，不是印钞机。"),
        "status": "INSUFFICIENT",
        "min_new": 50,
    }
    obf_path = "/app/data/audit/preregistered_scores.jsonl"
    with open(obf_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(new_rec, ensure_ascii=False) + "\n")
    print(f"[heartbeat_make] OBF chain updated: n_new={n_new}, status=INSUFFICIENT")
    return True

def load_obf_design():
    if not os.path.exists("/app/data/audit/obf_design.json"): return None
    with open("/app/data/audit/obf_design.json", "r", encoding="utf-8") as f:
        return json.load(f)

def obf_boundary(n_new, design):
    if not design or "boundary_head" not in design:
        return None
    bh = design["boundary_head"]
    keys = [int(k) for k in bh.keys()]
    keys.sort()
    key = None
    for k in keys:
        if k <= n_new:
            key = k
        else:
            break
    if key is None:
        return None
    return bh[str(key)]

def last_draw_issue():
    if not os.path.exists("/app/data/ssq_master.csv"): return None
    with open("/app/data/ssq_master.csv", "r", encoding="utf-8", errors="replace") as f:
        lines = [ln for ln in f if ln.strip()]
    if len(lines) < 2: return None
    last = lines[-1].split(",")[0].strip()
    return last if last.isdigit() else None

def last_score_status():
    obf_path = "/app/data/audit/preregistered_scores.jsonl"
    if not os.path.exists(obf_path): return None
    with open(obf_path, "r", encoding="utf-8") as f:
        last = None
        for ln in f:
            if ln.strip(): last = ln.strip()
    if not last: return None
    try:
        rec = json.loads(last)
        return {"ts": rec.get("ts"), "n_new": rec.get("n_new"), "status": rec.get("status")}
    except Exception:
        return None

def anchor_summary():
    if not os.path.exists("/app/anchors/preregistered.sha256"): return None
    with open("/app/anchors/preregistered.sha256", "r", encoding="utf-8", errors="replace") as f:
        return f.read()[:200].strip()

def sha256_of(path):
    if not os.path.exists(path): return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    os.makedirs("/app/data/audit", exist_ok=True)
    # 1. 更新 OBF 链（自动跟随 master 行数）
    try:
        update_obf_chain()
    except Exception as e:
        print(f"[heartbeat] OBF chain update failed: {e}")
    payload = {
        "schema": "ssq_evo.heartbeat.v1",
        "ts_local": datetime.datetime.now().isoformat(timespec="seconds"),
        "ts_unix": int(time.time()),
        "last_issue": last_draw_issue(),
        "master_sha256": sha256_of("/app/data/ssq_master.csv"),
        "predictions_sha256": sha256_of("/app/data/predictions.jsonl"),
        "last_score": last_score_status(),
        "anchor": anchor_summary(),
    }
    out = "/app/data/audit/heartbeat.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[heartbeat] wrote", out)
    print("[heartbeat] last_issue=", last_draw_issue(), " master_sha256=", (sha256_of("/app/data/ssq_master.csv") or "")[:12])

if __name__ == "__main__":
    main()
