#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meta_audit.py — ssq_evo 只读元审计（架构/分层/诚实红线自检）

定位：红队 + 架构自检 + 提议。
  - 只读：除追加 meta_audit.jsonl 报告外，绝不修改任何代码/状态文件；
  - 只提议：每条发现带 SUGGEST，绝不自动改代码或合并进生产；
  - 受数值闸约束：每条发现必须引用可核验证据（路径/行号/持久化真值），
    不凭空声称"改进"。

它能提前抓出我们 N 次翻车的那类坑：
  - 新 .py 未进 Dockerfile COPY → 重建后容器 import 崩溃（头号部署故障源）；
  - 网络/fetch 职责放错层（应在容器 ingest，不该在主机 watchdog）；
  - 持久化真值(frontier.json) 与内存打印(daemon.log) 分叉 → transient 假进展；
  - 诚实红线：阳性对照功率 / 闸门绕过 / 空壳回归；
  - 汇报诚实：best_q 改善但选号仍 NULL、df_gen 上长但零确认 → 不得称"突破"。
"""

import os
import sys
import json
import glob
import datetime
import subprocess
import ssq_log

REPO = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", "D:/ssq_evo_data")
CONTAINER = "ssq-evo-engine"


# ---------- 只读 IO 助手（运行时状态一律走 docker exec，规避沙箱陈旧缓存） ----------
def docker_exec(cmd):
    try:
        r = subprocess.run(["docker", "exec", CONTAINER] + cmd,
                           capture_output=True, text=True, timeout=60)
        return r.stdout, (r.returncode == 0)
    except Exception as e:
        return "", False


def read_container_json(path):
    out, ok = docker_exec(["python", "-c",
                           "import json;print(json.dumps(json.load(open('%s'))))" % path])
    if ok:
        try:
            return json.loads(out)
        except Exception:
            return None
    return None


def read_container_text(path):
    out, ok = docker_exec(["cat", path])
    return out if ok else ""


def read_repo(path):
    try:
        with open(os.path.join(REPO, path), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ---------- 检查项（每个返回 [(severity, category, detail, evidence, line, suggestion)]）----------

def _incontainer_needed_modules():
    """收集「容器内真正需要」的本地模块名集合：
       入口模块 + Dockerfile 冒烟测试 import 列表 + 各 .py 实际 import/import_module。
       主机编排脚本（不被任何容器内模块 import）自然不在此集合 -> 不误报。"""
    import re
    needed = {"daemon_loop", "run_cycle", "serve"}  # 入口
    df = read_repo("Dockerfile")
    # 冒烟测试 import 列表（Dockerfile RUN python -c "import a,b,c"）
    for line in df.splitlines():
        if "python -c" in line and "import " in line:
            for m in re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*,", line):
                needed.add(m.strip())
    # 扫描所有仓库根 .py 的 import（捕获本地模块依赖）
    for py in glob.glob(os.path.join(REPO, "*.py")):
        src = read_repo(os.path.basename(py))
        if not src:
            continue
        for m in re.findall(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", src, re.M):
            needed.add(m)
        for m in re.findall(r'import_module\(\s*["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']', src):
            needed.add(m)
    return needed


def check_dockerfile_copy():
    """需要的 .py 是否都在 Dockerfile COPY 列表（头号部署故障源）。
       解析 COPY 行 + 用容器内实际 import 集合判定，避免把整目录复制误判为漏拷。"""
    findings = []
    df = read_repo("Dockerfile")
    if not df:
        return findings
    # 解析 COPY 行：源文件 tokens + 目标（最后一个 token）
    copied = set()
    whole_dir = False
    for line in df.splitlines():
        s = line.strip()
        if not s.startswith("COPY "):
            continue
        toks = s[5:].split()
        if len(toks) < 2:
            continue
        dest = toks[-1]
        srcs = toks[:-1]
        if dest in ("./", "/app", ".", "/app/"):
            if "." in srcs or any("*" in t for t in srcs):
                whole_dir = True
            copied.update(t for t in srcs if not t.startswith("-"))
    if whole_dir:  # 整目录复制 -> 无需逐文件核对
        return findings
    # 明确在主机跑、不进容器的编排脚本（不被容器内模块 import）
    HOST_ONLY = {"meta_audit.py"}
    needed = _incontainer_needed_modules()
    repo_pys = set(os.path.basename(p) for p in glob.glob(os.path.join(REPO, "*.py")))
    for f in sorted(repo_pys):
        stem = f[:-3] if f.endswith(".py") else f
        if f in HOST_ONLY:
            continue
        if stem not in needed:   # 容器内不需要 -> 不必进容器
            continue
        if f in copied:          # 需要且在 COPY 列表 -> OK
            continue
        findings.append((
            "BLOCK", "dockerfile_copy",
            "容器内需要的 %s 未被 Dockerfile COPY 覆盖 -> 重建后容器内 import 崩溃" % f,
            "Dockerfile", 0,
            "SUGGEST: 在 Dockerfile COPY 列表加入 %s（或改用整目录 COPY . /app）" % f,
        ))
    return findings


def check_host_fetch():
    """watchdog.ps1 是否仍在主机做 GitHub 网络 fetch（fetch 应已在容器 ingest）"""
    findings = []
    wd = read_repo("watchdog.ps1")
    if not wd:
        return findings
    has_github = ("api.github.com" in wd) or ("raw.githubusercontent" in wd)
    writes_candidates = ("candidates.json" in wd) and (
        ("Invoke-WebRequest" in wd) or ("curl" in wd.lower()))
    if has_github and writes_candidates:
        findings.append((
            "WARN", "layering",
            "watchdog.ps1 仍含 GitHub fetch 并写入 candidates.json（候选应由容器内 ingest 直连拉取）",
            "watchdog.ps1", 0,
            "SUGGEST: 移除主机 fetch 逻辑，仅保留健康监控+重启",
        ))
    return findings


def check_ingest_git():
    """ingest 是否仍用 git（容器无 git 二进制 -> fetch 必崩）"""
    findings = []
    ing = read_repo("ingest_candidates.py")
    if ('subprocess.run(["git"' in ing) or ('["git", "fetch"' in ing) or ("git show" in ing):
        findings.append((
            "BLOCK", "layering",
            "ingest_candidates.py 仍调用 git（容器内无 git 二进制，fetch 必失败）",
            "ingest_candidates.py", 0,
            "SUGGEST: 改用 urllib 直连 GitHub API（若已实现则忽略此条）",
        ))
    return findings


def check_positive_control(state):
    findings = []
    pc = (state or {}).get("positive_control", {})
    if pc.get("verified") is not True:
        findings.append((
            "BLOCK", "honesty_redline",
            "positive_control.verified != True -> 统一闸门失去分辨功率，下游所有 NULL/SIGNAL 结论不可信",
            "state.json:positive_control", 0,
            "SUGGEST: 立即排查闸门功率；恢复前任何'无结构'结论不得出口",
        ))
    return findings


def check_bypass(frontier):
    findings = []
    el = (frontier or {}).get("elites", [])
    bypass = [e for e in el if e.get("verdict") in ("NULL", "ARTIFACT")]
    if bypass:
        findings.append((
            "BLOCK", "honesty_redline",
            "frontier 含 %d 个显式 NULL/ARTIFACT 却仍作精英 -> 疑似绕过统一闸门自动合并" % len(bypass),
            "frontier.json:elites", 0,
            "SUGGEST: 审查并入路径，禁止无监督自演进以过闸为目标合并（红线 #1）",
        ))
    return findings


def check_empty_shell(frontier):
    findings = []
    el = (frontier or {}).get("elites", [])
    comp = [e for e in el if e.get("sig") == "comp"]
    comp_none = [e for e in comp if e.get("q") is None]
    if comp and comp_none:
        findings.append((
            "BLOCK", "honesty_redline",
            "空壳回归: %d/%d comp 精英 q=None（GA 未赋 q）-> df_gen 无法真实上长" % (len(comp_none), len(comp)),
            "frontier.json:elites(comp)", 0,
            "SUGGEST: 确认 run_cycle 全局 BH-FDR 赋 q 在生产生效",
        ))
    return findings


def check_df_gen_divergence(frontier, daemon_log):
    import re
    findings = []
    pf = (frontier or {}).get("df_gen")
    if pf is None:
        return findings
    m = re.findall(r"df_gen=\s*(\d+)", daemon_log or "")
    if m:
        dl = m[-1]
        try:
            if int(dl) > int(pf):
                findings.append((
                    "WARN", "reporting_honesty",
                    "daemon.log 末次 df_gen=%s > frontier 持久 df_gen=%s -> transient 尖峰，勿据此报进展" % (dl, pf),
                    "daemon.log / frontier.json", 0,
                    "SUGGEST: 以 frontier.json 持久值为准",
                ))
        except Exception as _e:
            ssq_log.log_exception("meta_audit", _e, "meta_audit.py:229 silent-except")
    return findings


def check_metric_drift(state):
    findings = []
    pick_p = (state or {}).get("pick_p")
    try:
        if pick_p is not None and float(pick_p) >= 0.99:
            findings.append((
                "INFO", "reporting_honesty",
                "pick_p=%.2f（选号准确率不优于随机）-> bottom-line 未变，任何 best_q 改善不得表述为'准确率提升'" % float(pick_p),
                "state.json:pick_p", 0,
                "SUGGEST: 汇报时严格区分'边缘样本外信号'与'确认准确率'",
            ))
    except Exception as _e:
        ssq_log.log_exception("meta_audit", _e, "meta_audit.py:245 silent-except")
    return findings


def check_evolution_no_confirm(frontier, state):
    findings = []
    df = (frontier or {}).get("df_gen") or 1
    bv = (state or {}).get("best_verdict")
    if int(df) >= 2 and bv in (None, "UNCONFIRMED"):
        findings.append((
            "INFO", "reporting_honesty",
            "df_gen=%d 但 best_verdict=%s -> 进化在转但零确认结构，不得称'突破'" % (int(df), bv),
            "frontier.json:df_gen / state.json:best_verdict", 0,
            "SUGGEST: 报'代际进展'须同时标注'未确认'",
        ))
    return findings


def check_df_gen_source(digest_last):
    findings = []
    src = (digest_last or {}).get("df_gen_source")
    if src is None:
        findings.append((
            "INFO", "known_debt",
            "df_gen_source 未记录（元数据 bug，不影响 df_gen 真值）",
            "run_cycle.py / daily_digest", 0,
            "SUGGEST: 修复 _gen_source 写入路径",
        ))
    return findings


SEV_ORDER = {"BLOCK": 0, "WARN": 1, "INFO": 2}


def main():
    frontier = read_container_json("/app/data/frontier.json")
    state = read_container_json("/app/data/state.json")
    digest_last = None
    dlog = read_container_text("/app/data/daily_digest.jsonl")
    if dlog.strip():
        try:
            digest_last = json.loads(dlog.strip().splitlines()[-1])
        except Exception as _e:
            ssq_log.log_exception("meta_audit", _e, "meta_audit.py:288 silent-except")
    daemon_log = read_container_text("/app/data/daemon.log")

    checks = [
        check_dockerfile_copy,
        check_host_fetch,
        check_ingest_git,
        lambda: check_positive_control(state),
        lambda: check_bypass(frontier),
        lambda: check_empty_shell(frontier),
        lambda: check_df_gen_divergence(frontier, daemon_log),
        lambda: check_metric_drift(state),
        lambda: check_evolution_no_confirm(frontier, state),
        lambda: check_df_gen_source(digest_last),
    ]
    findings = []
    for c in checks:
        findings += c()
    findings.sort(key=lambda x: SEV_ORDER.get(x[0], 9))

    report = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "n_block": sum(1 for x in findings if x[0] == "BLOCK"),
        "n_warn": sum(1 for x in findings if x[0] == "WARN"),
        "n_info": sum(1 for x in findings if x[0] == "INFO"),
        "findings": [
            {"severity": s, "category": c, "detail": d,
             "evidence": e, "line": l, "suggestion": sg}
            for (s, c, d, e, l, sg) in findings
        ],
    }

    print("=== ssq_evo 只读元审计 ===")
    print("BLOCK=%d  WARN=%d  INFO=%d" % (report["n_block"], report["n_warn"], report["n_info"]))
    for s, c, d, e, l, sg in findings:
        print("[%s] %s | %s" % (s, c, d))
        print("    证据: %s" % e)
        print("    建议: %s" % sg)

    # 只读：仅追加报告文件，绝不改动代码/状态
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "meta_audit.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    except Exception as ex:
        print("报告写入失败(不影响审计):", ex)

    # 退出码：有 BLOCK 则非 0（供巡检/CI 感知严重异常）
    return 1 if report["n_block"] else 0


if __name__ == "__main__":
    sys.exit(main())
