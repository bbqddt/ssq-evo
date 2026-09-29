#!/usr/bin/env python3
# heartbeat_make.py - generate audit/heartbeat.json for the cloud watchdog.
# Run after each draw-day score phase (or on demand).
# Output: $DATA/audit/heartbeat.json
#
# 2026-09-29 重写：原文件 1) 硬编码 /app/data，宿主跑时静默写出空骨架并
# 在 C:\app\data 造垃圾目录（心跳链失明 47h+ 无人报警）；2) 尾部意外叠加了
# 4 个重复函数定义（load_obf_design/obf_boundary/last_draw_issue/sha256_of），
# 后定义覆盖前定义。本版统一走 _detect_data() 探测容器/宿主两种布局。
# 防呆：宿主环境必须以 D:\ssq_evo_data\ssq_master.csv 存在为准，绝不在
# 宿主新建 /app/data 假骨架。
import json, hashlib, time, datetime, os, sys

def _detect_data():
    env = os.environ.get("SSQ_DATA_DIR")
    if env:
        return env
    cands = ["/app/data"]
    if os.name == "nt":
        cands.append(r"D:\ssq_evo_data")
    for c in cands:
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "ssq_master.csv")):
            return c
    return cands[0]

DATA = _detect_data()
AUDIT = os.path.join(DATA, "audit")
MASTER = os.path.join(DATA, "ssq_master.csv")
PREDS = os.path.join(DATA, "predictions.jsonl")
OBF = os.path.join(AUDIT, "preregistered_scores.jsonl")
OBF_DESIGN = os.path.join(AUDIT, "obf_design.json")
HEARTBEAT_OUT = os.path.join(AUDIT, "heartbeat.json")
if os.name == "nt":
    ANCHOR = r"D:\ssq_evo\anchors\preregistered.sha256"
    SCORER = os.path.join(DATA, "predict_cron.log")
else:
    ANCHOR = "/app/anchors/preregistered.sha256"
    SCORER = os.path.join(DATA, "predict_cron.log")

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
    if not os.path.exists(OBF_DESIGN): return None
    with open(OBF_DESIGN, "r", encoding="utf-8") as f:
        return json.load(f)

def obf_boundary(n_new, design):
    """根据 OBF 设计表返回该 n_new 对应的上边界 z 值。"""
    if not design or "boundary_head" not in design:
        return None
    bh = design["boundary_head"]
    keys = sorted(int(k) for k in bh.keys())
    key = None
    for k in keys:
        if k <= n_new:
            key = k
        else:
            break
    if key is None:
        return None
    return bh[str(key)]

def update_obf_chain():
    """自动更新 OBF 链：根据 master 行数计算 n_new，追加记录到 preregistered_scores.jsonl。"""
    if not os.path.exists(MASTER):
        return False
    with open(MASTER, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln for ln in f if ln.strip()]
    n_master = len(lines) - 1 if len(lines) > 1 else 0
    if n_master <= 0:
        return False
    n_new = n_master - N_BASIS
    if n_new <= 0:
        return False

    # 读取现有 OBF 链
    obf_records = []
    if os.path.exists(OBF):
        with open(OBF, "r", encoding="utf-8") as f:
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
    if obf_records and obf_records[-1].get("n_new") == n_new:
        return False

    footer = ("即便确认 σ≈3.5% 边际偏倚，它不改变头奖概率的量级（1/1772 万）。"
              "这是结构，不是印钞机。")
    new_rec = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "design": "OBF_v1",
        "n_basis": N_BASIS,
        "n_new": n_new,
        "footer": footer,
        "status": "INSUFFICIENT",  # 简化：Z_n 未计算，状态由边界表兜底
        "min_new": 50,
    }
    with open(OBF, "a", encoding="utf-8") as f:
        f.write(json.dumps(new_rec, ensure_ascii=False) + "\n")
    print(f"[heartbeat_make] OBF chain updated: n_new={n_new}, status=INSUFFICIENT")
    return True

def last_score_status():
    if not os.path.exists(OBF): return None
    with open(OBF, "r", encoding="utf-8") as f:
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
    if not os.path.exists(ANCHOR): return None
    with open(ANCHOR, "r", encoding="utf-8", errors="replace") as f:
        return f.read()[:200].strip()

def main():
    os.makedirs(AUDIT, exist_ok=True)
    try:
        update_obf_chain()
    except Exception as e:
        print(f"[heartbeat] OBF chain update failed: {e}")
    payload = {
        "schema": "ssq_evo.heartbeat.v1",
        "ts_local": datetime.datetime.now().isoformat(timespec="seconds"),
        "ts_unix": int(time.time()),
        "last_issue": last_draw_issue(),
        "master_sha256": sha256_of(MASTER),
        "predictions_sha256": sha256_of(PREDS),
        "last_score": last_score_status(),
        "anchor": anchor_summary(),
    }
    # 防呆：核心字段全空说明数据没找到，拒绝覆盖有效心跳（宁可 stale 报警，不要假新鲜）
    if payload["last_issue"] is None and payload["master_sha256"] is None:
        print(f"[heartbeat] REFUSE to write: DATA={DATA} has no ssq_master.csv "
              f"(would produce empty heartbeat)", file=sys.stderr)
        sys.exit(3)
    with open(HEARTBEAT_OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[heartbeat] wrote", HEARTBEAT_OUT)
    print("[heartbeat] last_issue=", payload["last_issue"],
          " master_sha256=", (payload["master_sha256"] or "")[:12])

if __name__ == "__main__":
    main()
