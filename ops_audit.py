# -*- coding: utf-8 -*-
"""ops_audit.py —— 运维层自动体检（自我发现问题的机器化，2026-09-25）

背景（用户批评）：9/25 全天发现的问题——26106 污染、26107 缺口、cloud-register
七连败、双任务互踩、watchdog L2 盲区——全部是"用户一问我才查"挖出来的。
监督系统不能只盯引擎，还得盯"登记链 + 云端腿 + 任务层"，且必须自己会叫。

纪律（与项目方法论同源）：每个探测器必须先通过阳性自检（注入已知坏状态，
必须报 CRIT/WARN）才配声称"OK"。--selftest 跑全套阳性对照，不过就不配当探测器。

用法：
    python ops_audit.py            # 全量体检，报告写 audit/ops_audit/
    python ops_audit.py --selftest # 探测器阳性对照（无需网络/docker）
退出码：0=全绿 1=有WARN 2=有CRIT（自测失败=3）
"""
import argparse, csv, datetime, json, os, re, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths

DATA = paths.DATA_DIR
AUD = os.path.join(DATA, "audit")
OUT = os.path.join(AUD, "ops_audit")
PRED_FILE = os.path.join(DATA, "predictions.jsonl")
MASTER = os.path.join(DATA, "ssq_master.csv")
CRON_LOG = os.path.join(DATA, "predict_cron.log")
HB = os.path.join(AUD, "heartbeat.json")
WD_LOG = os.path.join(DATA, "watchdog.log")
REPO_START = "26094"           # 项目预注册链起点
REVIEWED_GAPS = {"26106", "26107", "26111"}   # 已复盘作废的期（新缺口必须不在列=CRIT）
GITHUB_REPO = "bbqddt/ssq-evo"
DRAW_WEEKDAYS = (1, 3, 6)      # 周二/四/日


def findings_add(f, name, sev, msg):
    f.append({"check": name, "sev": sev, "msg": msg})
    print("[%s] %-24s %s" % (sev, name, msg))


# ---------- 纯函数探测器（可注入数据 → selftest 复用同一实现） ----------

def check_chain_gaps(drawn_issues, preds_by_issue, today):
    """登记链完整性：每个已开奖期都必须有有效预注册，且登记时刻早于开奖(21:15)。"""
    out = []
    for issue in sorted(drawn_issues):
        if issue < REPO_START:
            continue
        preds = preds_by_issue.get(issue, [])
        valid = [p for p in preds if not p.get("invalid")]
        if not preds:
            sev = "WARN" if issue in REVIEWED_GAPS else "CRIT"
            out.append((sev, issue, "无预测记录" + ("（已复盘作废）" if issue in REVIEWED_GAPS else "——预注册链断裂!")))
        elif not valid:
            out.append(("WARN", issue, "预测记录全部标记 invalid（已作废样本）"))
        else:
            p = valid[0]
            reg = p.get("registered_ts", "")[:10]
            target = p.get("target_date", "")
            hhmm = p.get("registered_ts", "")[11:16]
            if target and reg > target:
                out.append(("CRIT", issue, "污染：登记日 %s 晚于开奖日 %s（事后选号）" % (reg, target)))
            elif target and reg == target and hhmm >= "21:15":
                out.append(("CRIT", issue, "污染：开奖时刻后登记（%s）" % hhmm))
    return out


def check_master_fresh(n_master, prev_state, today, is_draw_day_passed):
    """主表新鲜度：跨两次 ops_audit 的"行数是否随开奖增长"，比日期联查可靠
    （主表无日期列、ssq_sales 滞后到 26098，都不适合判最新期新鲜度）。
    prev_state = {"n_master":int,"ts":str} 或 None（首次运行无基线 → INFO）。
    is_draw_day_passed 由调用方判断：上次体检 ts 之后是否已跨过开奖日 22:00。"""
    prev_n = (prev_state or {}).get("n_master")
    if prev_n is None:
        return [("INFO", "master_fresh", "首次体检，行数基线=%d（下次起可判增长）" % n_master)]
    if not is_draw_day_passed:
        return []  # 没跨过开奖点，不检查
    if n_master <= prev_n:
        return [("WARN", "master_fresh",
                 "距上次体检(%s)主表行数未增长(%d→%d)，期间已过开奖点——数据摄取疑断"
                 % ((prev_state or {}).get("ts", "?"), prev_n, n_master))]
    return []


def check_cron_log(lines, days=7, seen_errors=None):
    """cron 日志：ERROR（新=CRIT/已知=INFO）、START 无 DONE（崩在半路）、双发。
    seen_errors = 上次体检已报过的 ERROR 键集合 → 新错才升级，旧错转 INFO 不刷屏。"""
    out = []
    seen = set(seen_errors or [])
    new_seen = []
    pat = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (START|DONE|ERROR) (?:phase=(\w+))?")
    events = []
    for ln in lines:
        m = pat.search(ln)
        if m:
            events.append((datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"),
                           m.group(2), m.group(3) or ""))
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    events = [e for e in events if e[0] >= cutoff]
    open_starts = {}
    for ts, kind, ph in events:
        if kind == "START":
            for oph, ots in open_starts.items():
                if oph != ph and abs((ts - ots).total_seconds()) < 10:
                    out.append(("WARN", "cron_double_fire",
                                "%s 与 %s 在 %.0fs 内双发（互斥锁上线后不应再现）" % (oph, ph, (ts - ots).total_seconds())))
            open_starts[ph] = ts
        elif kind == "DONE":
            open_starts.pop(ph, None)
        elif kind == "ERROR":
            key = "%s %s" % (ts, ph)
            new_seen.append(key)
            if key in seen:
                out.append(("INFO", "cron_error", "%s 历史 ERROR（已知已复盘）：%s" % (ts, ph)))
            else:
                out.append(("CRIT", "cron_error", "%s 新 ERROR：%s" % (ts, ph)))
    for ph, ts in open_starts.items():
        if (datetime.datetime.now() - ts).total_seconds() > 600:
            out.append(("CRIT", "cron_no_done", "%s 的 %s 相 START 后无 DONE（疑似中途崩溃）" % (ts, ph)))
    return out, new_seen


def check_log_garble(lines):
    """GBK/UTF-8 混淆乱码探针（已知观察项，报 WARN 不拦截）。"""
    garbled = sum(1 for ln in lines[-200:] if "浜" in ln or "鈥" in ln or "锛" in ln)
    if garbled:
        return [("WARN", "log_garble", "cron 日志最近 200 行含 %d 行编码混淆（观察项）" % garbled)]
    return []


def check_container(image_sha, git_sha):
    if not image_sha or not git_sha:
        return [("WARN", "container_sha", "L2 盲区：镜像或 git SHA 取不到（%r/%r）——盲区必须报警" % (image_sha, git_sha))]
    if image_sha != git_sha:
        return [("CRIT", "container_sha", "STALE：容器=%s 本地=%s（需 rebuild）" % (image_sha[:7], git_sha[:7]))]
    return []


def check_watchdog_alive(mtime_age_min):
    if mtime_age_min is None:
        return [("CRIT", "watchdog_alive", "watchdog.log 不存在——看门狗失踪")]
    if mtime_age_min > 45:
        return [("CRIT", "watchdog_alive", "watchdog.log %.1f 小时未更新（应 30 分钟一轮）" % (mtime_age_min / 60))]
    return []


def check_cloud_runs(runs_by_wf):
    """云端腿：workflow 连续 schedule 失败 = 兜底失效（cloud-register 七连败教训）。
    降级规则：最后一次 schedule 之后若有更新的 dispatch run 且 success，
    说明修复已人工验证、待下次 schedule 确认 → WARN 而非 CRIT。"""
    out = []
    for name, runs in runs_by_wf.items():
        if runs is None:
            out.append(("WARN", "cloud_" + name, "API 取不到运行记录（盲区）"))
            continue
        if not runs:
            out.append(("WARN", "cloud_" + name, "无任何运行记录（从未启用?）"))
            continue
        sched = [r for r in runs if r.get("event") == "schedule"]
        fails = sum(1 for r in sched[:3] if r.get("conclusion") == "failure")
        if fails == 3:
            last_sched_ts = sched[0].get("created_at", "")
            later_ok = any(r.get("event") == "workflow_dispatch"
                           and r.get("conclusion") == "success"
                           and (r.get("created_at") or "") > last_sched_ts
                           for r in runs)
            if later_ok:
                out.append(("WARN", "cloud_" + name,
                            "schedule 历史 3 连败，但之后有 dispatch 验证 success——待下次 schedule 确认修复"))
            else:
                out.append(("CRIT", "cloud_" + name, "最近 3 次 schedule 全部失败且无修复验证——云端腿已死"))
        elif fails:
            out.append(("WARN", "cloud_" + name, "最近 3 次 schedule 有 %d 次失败" % fails))
    return out


def check_heartbeat_age(mtime_age_h):
    if mtime_age_h is None:
        return [("WARN", "heartbeat", "heartbeat.json 不存在")]
    if mtime_age_h > 72:
        return [("WARN", "heartbeat", "心跳 %.0f 小时未更新" % mtime_age_h)]
    return []


# ---------- 数据采集（IO 边界，selftest 不走这里） ----------

def load_drawn():
    """ssq_master.csv 无日期列；开奖日期从 ssq_sales.csv(code5,date) 联查。"""
    out = {}
    if not os.path.exists(MASTER):
        return out
    dates = {}
    sales = os.path.join(DATA, "ssq_sales.csv")
    if os.path.exists(sales):
        with open(sales, encoding="utf-8", errors="replace") as fh:
            for i, row in enumerate(csv.reader(fh)):
                if i and row and len(row) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", row[1] or ""):
                    dates[row[0].strip()] = row[1].strip()
    with open(MASTER, encoding="utf-8", errors="replace") as f:
        for row in csv.reader(f):
            if row and row[0].isdigit():
                out[row[0]] = dates.get(row[0], "")
    return out


def load_preds():
    by = {}
    if os.path.exists(PRED_FILE):
        for line in open(PRED_FILE, encoding="utf-8"):
            line = line.strip()
            if line:
                p = json.loads(line)
                by.setdefault(p["issue"], []).append(p)
    return by


def mtime_age_minutes(path):
    if not os.path.exists(path):
        return None
    return (datetime.datetime.now().timestamp() - os.path.getmtime(path)) / 60.0


def git_head():
    try:
        r = subprocess.run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def docker_image_sha():
    """取容器实际运行的镜像短 SHA（前7位），用 docker inspect 避免 Git Bash 路径转换 bug。"""
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", "ssq-evo-engine"],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            # 返回 sha256:<full> → 截取前 7 位与 git rev-parse --short 对齐
            img = r.stdout.strip()
            return img.replace("sha256:", "")[:7]
    except Exception as e:
        sys.stderr.write("docker_image_sha failed: %s\n" % e)
    return ""


def github_cloud_runs():
    runs = {}
    try:
        with urllib.request.urlopen(
                "https://api.github.com/repos/%s/actions/workflows" % GITHUB_REPO, timeout=30) as resp:
            wfs = json.load(resp)["workflows"]
        for wf in wfs:
            short = wf["name"].replace(".github/workflows/", "").replace(".yml", "")
            if short not in ("cloud-register", "heartbeat-watchdog"):
                continue
            try:
                with urllib.request.urlopen(
                        "https://api.github.com/repos/%s/actions/workflows/%d/runs?per_page=6"
                        % (GITHUB_REPO, wf["id"]), timeout=30) as r2:
                    runs[short] = json.load(r2)["workflow_runs"]
            except Exception:
                runs[short] = None
    except Exception:
        runs["api"] = None
    return runs


def run_all():
    os.makedirs(OUT, exist_ok=True)
    f = []
    today = datetime.date.today().isoformat()
    drawn = load_drawn()
    preds = load_preds()

    # 上次体检状态（行数基线 + 已报 ERROR），本次结果落盘供下次对比
    state_p = os.path.join(OUT, "state.json")
    try:
        prev = json.load(open(state_p, encoding="utf-8"))
    except Exception:
        prev = {}

    for sev, issue, msg in check_chain_gaps(set(drawn), preds, today):
        findings_add(f, "chain_" + issue, sev, msg)

        # 已跨过开奖时刻：上次体检 ts 之后是否经过了至少一个开奖日 22:00（score 相完成）
    now = datetime.datetime.now()
    prev_ts_str = (prev or {}).get("ts", "")
    crossed = False
    if prev_ts_str:
        try:
            prev_dt = datetime.datetime.fromisoformat(prev_ts_str)
            probe = prev_dt.date() + datetime.timedelta(days=1)
            while probe <= now.date():
                if probe.weekday() in DRAW_WEEKDAYS:
                    draw_close = datetime.datetime.combine(probe, datetime.time(22, 0))
                    if prev_dt < draw_close <= now:
                        crossed = True
                        break
                probe += datetime.timedelta(days=1)
        except Exception as e:
            sys.stderr.write("master_fresh state load failed: %s\n" % e)
    n_master = len(drawn)
    for sev, name, msg in check_master_fresh(n_master, prev or None, today, crossed):
        findings_add(f, name, sev, msg)

    cron_lines = []
    if os.path.exists(CRON_LOG):
        cron_lines = open(CRON_LOG, encoding="utf-8", errors="replace").read().splitlines()
    cron_findings, new_seen = check_cron_log(cron_lines, seen_errors=set(prev.get("seen_errors", [])))
    for sev, name, msg in cron_findings:
        findings_add(f, name, sev, msg)
    for sev, name, msg in check_log_garble(cron_lines):
        findings_add(f, name, sev, msg)

    for sev, name, msg in check_container(docker_image_sha(), git_head()):
        findings_add(f, name, sev, msg)

    for sev, name, msg in check_watchdog_alive(mtime_age_minutes(WD_LOG)):
        findings_add(f, name, sev, msg)

    for sev, name, msg in check_heartbeat_age(
            None if (mtime_age_minutes(HB) is None) else mtime_age_minutes(HB) / 60.0):
        findings_add(f, name, sev, msg)

    for sev, name, msg in check_cloud_runs(github_cloud_runs()):
        findings_add(f, name, sev, msg)

    worst = max(({"INFO": 0, "OK": 0, "WARN": 1, "CRIT": 2}[x["sev"]] for x in f), default=0)
    report = {"ts": now.isoformat(timespec="seconds"),
              "worst": ["OK", "WARN", "CRIT"][worst], "findings": f}
    with open(os.path.join(OUT, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    with open(state_p, "w", encoding="utf-8") as fh:
        json.dump({"n_master": n_master, "ts": report["ts"],
                   "seen_errors": sorted(set(new_seen) | set(prev.get("seen_errors", [])))[-50:]},
                  fh, ensure_ascii=False, indent=1)
    crit_n = sum(1 for x in f if x["sev"] == "CRIT")
    warn_n = sum(1 for x in f if x["sev"] == "WARN")
    info_n = sum(1 for x in f if x["sev"] == "INFO")
    print("OPS-AUDIT: %s (CRIT %d / WARN %d / INFO %d)" % (report["worst"], crit_n, warn_n, info_n))
    return worst


# ---------- 阳性对照自测（探测器必须先证明自己抓得住坏状态） ----------

def selftest():
    """每个探测器注入已知坏状态 → 必须报对应级别。全过才配声称 OK。"""
    passed, failed = 0, 0
    def case(name, got, want):
        nonlocal passed, failed
        if any(sev == want for sev, _, _ in got):
            passed += 1; print("[selftest] PASS  %-28s 抓到注入的 %s" % (name, want))
        else:
            failed += 1; print("[selftest] FAIL  %-28s 对注入的 %s 无反应——探测器无功效!" % (name, want))

    pred_ok = {"registered_ts": "2026-09-20 18:00:00", "target_date": "2026-09-20"}
    # 缺口：注入了但没登记的期必须被抓
    case("gap", check_chain_gaps({"26200"}, {}, "2026-09-26"), "CRIT")
    # 污染：登记晚于开奖
    late = {"26201": [{"registered_ts": "2026-09-22 10:00:00", "target_date": "2026-09-21"}]}
    case("contamination", check_chain_gaps({"26201"}, late, "2026-09-26"), "CRIT")
    # 开奖后 21:15 补登
    post = {"26202": [{"registered_ts": "2026-09-21 21:30:00", "target_date": "2026-09-21"}]}
    case("post_draw", check_chain_gaps({"26202"}, post, "2026-09-26"), "CRIT")
    # 阴性：正常链必须 NOT 报警
    norm = check_chain_gaps({"26203"}, {"26203": [dict(pred_ok, target_date="2026-09-20",
                                                       registered_ts="2026-09-20 18:00:00")]}, "2026-09-26")
    if norm: failed += 1; print("[selftest] FAIL  chain_normal 正常链被误报:", norm)
    else: passed += 1; print("[selftest] PASS  chain_normal 正常链零误报")
    # 主表行数不增长（跨过开奖点）→ WARN；正常增长 → 零误报
    case("master_stall", check_master_fresh(3507, {"n_master": 3507, "ts": "x"}, "2026-09-26", True), "WARN")
    if check_master_fresh(3508, {"n_master": 3507, "ts": "x"}, "2026-09-26", True):
        failed += 1; print("[selftest] FAIL  master_grow 正常增长被误报")
    else:
        passed += 1; print("[selftest] PASS  master_grow 正常增长零误报")
    # cron ERROR / 无 DONE / 双发
    # cron ERROR（新=CRIT；已复盘过的=INFO）/ 无 DONE / 双发
    got, _seen = check_cron_log(["[2026-09-25 10:00:00] ERROR phase=register : boom"])
    case("cron_error", got, "CRIT")
    got2, _ = check_cron_log(["[2026-09-25 10:00:00] ERROR phase=register : boom"],
                             seen_errors={"2026-09-25 10:00:00 register"})
    case("cron_error_known", got2, "INFO")
    past = (datetime.datetime.now() - datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    got3, _ = check_cron_log(["[%s] START phase=register" % past])
    case("cron_no_done", got3, "CRIT")
    dbl = ["[2026-09-25 10:49:57] START phase=register", "[2026-09-25 10:49:58] START phase=score"]
    got4, _ = check_cron_log(dbl)
    case("cron_double", got4, "WARN")
    # 容器 SHA 不一致 + 盲区
    case("stale_image", check_container("a" * 40, "b" * 40), "CRIT")
    case("sha_blind", check_container("", "b" * 40), "WARN")
    # 看门狗死亡 / 心跳过期 / 云端连败
    case("wd_dead", check_watchdog_alive(180.0), "CRIT")
    case("hb_stale", check_heartbeat_age(90.0), "WARN")
    case("cloud_dead", check_cloud_runs({"cloud-register": [
        {"event": "schedule", "conclusion": "failure"}] * 3}), "CRIT")
    # 阴性：云端全绿不得误报
    if check_cloud_runs({"cloud-register": [{"event": "schedule", "conclusion": "success"}] * 3}):
        failed += 1; print("[selftest] FAIL  cloud_green 全绿被误报")
    else:
        passed += 1; print("[selftest] PASS  cloud_green 全绿零误报")
    print("SELFTEST: %d pass, %d fail" % (passed, failed))
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    sys.exit(selftest() if a.selftest else run_all())
