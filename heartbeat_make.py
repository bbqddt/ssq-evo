#!/usr/bin/env python3
# heartbeat_make.py - generate audit/heartbeat.json for the cloud watchdog.
# Run after each draw-day score phase (or on demand).
# Output: D:\ssq_evo_data\audit\heartbeat.json
import json, hashlib, time, datetime, os, sys

DATA = r"D:\ssq_evo_data"
AUDIT = os.path.join(DATA, "audit")
MASTER = os.path.join(DATA, "ssq_master.csv")
PREDS = os.path.join(DATA, "predictions.jsonl")
OBF = os.path.join(AUDIT, "preregistered_scores.jsonl")
ANCHOR = r"D:\ssq_evo\anchors\preregistered.sha256"
SCORER = os.path.join(DATA, "predict_cron.log")

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
    out = os.path.join(AUDIT, "heartbeat.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[heartbeat] wrote", out)
    print("[heartbeat] last_issue=", payload["last_issue"], " master_sha256=", (payload["master_sha256"] or "")[:12])

if __name__ == "__main__":
    main()
