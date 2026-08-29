# pre_commit_check.py
# Run BEFORE every `git commit`.
# Catches: Dockerfile missing new .py files, untracked critical files, etc.
# Exit 0 = OK to commit. Exit 1 = FIX FIRST.

import sys
import os
import re
import subprocess

PROJECT_DIR = r"D:\ssq_evo"

# 宿主/CI 专用脚本：本就不进容器，不应被「Dockerfile 必须 COPY 所有 .py」门禁拦截。
# 这些脚本运行在 GitHub Actions runner 或宿主机，不属 Docker 镜像的一部分。
HOST_ONLY_PY = {
    # 以下脚本只在宿主机/CI 跑，不进 Docker 引擎镜像——从 Dockerfile COPY 检查中豁免
    "evolve_predictor.py",   # 公式演进实验脚本（本机8核/CI runner 跑，不属 Docker 引擎镜像）
    "merge_evo_proposals.py",# 分布式 evolve 的 artifact 合并（workflow 跑，不进容器）
    "meta_audit.py",         # 只读元审计（宿主机跑，docker exec 读容器内状态，不进容器）
    "smoke_historical_fitness.py",  # Goodhart 陷阱对照实验（宿主跑，只读数据写 audit/，不进容器）
    "power_analysis.py",            # 理论边界/功率分析决策门（宿主跑，只读数据写 audit/，不进容器）
    "random_control_oot.py",        # OOT 随机数据对照闸门（宿主审计，证伪构造性伪信号）
    "gate_control_experiment.py",   # 新 OOT 闸门阴性/阳性对照（宿主审计）
    "gate_control_ev_a.py",         # EV_A 变换通带内阳性对照（宿主审计）
}


def run_git(args):
    r = subprocess.run(["git"] + args, cwd=PROJECT_DIR,
                       capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def check_dockerfile_covers_all_py():
    """CRITICAL: Every .py in project root MUST be in Dockerfile COPY."""
    dockerfile = os.path.join(PROJECT_DIR, "Dockerfile")
    if not os.path.exists(dockerfile):
        return False, "Dockerfile MISSING - cannot deploy without it"

    with open(dockerfile, encoding="utf-8-sig") as f:
        content = f.read()

    m = re.search(r"COPY\s+(.*?)\s+\./", content)
    if not m:
        return False, "Cannot find 'COPY ... ./' line in Dockerfile"

    copied = set(m.group(1).split())
    local_pys = set(f for f in os.listdir(PROJECT_DIR) if f.endswith(".py"))

    # 宿主/CI 专用脚本不要求进容器 COPY
    missing = sorted((local_pys - copied) - HOST_ONLY_PY)
    if missing:
        return (False,
                f"Dockerfile COPY is missing {len(missing)} .py file(s): {missing}\n"
                f"  Container will crash on: import {missing[0]}\n"
                f"  FIX: Add them to the COPY line in Dockerfile")
    return True, f"OK: all {len(local_pys)} .py files in COPY list"


def check_no_stale_deployment():
    """Warn if local commits are ahead of what container is running."""
    # Check if container has different file hashes than local for key files
    import hashlib

    key_files = ["run_cycle.py", "firewall.py", "proposer.py", "scoring.py",
                 "formula_viz.py", "predict_tonight.py"]
    stale = []
    for f in key_files:
        local_path = os.path.join(PROJECT_DIR, f)
        if not os.path.exists(local_path):
            continue
        h_local = hashlib.md5(open(local_path, "rb").read()).hexdigest()
        try:
            proc = subprocess.run(
                ["docker", "exec", "ssq-evo-engine", "python3", "-c",
                 f"import hashlib;print(hashlib.md5(open('/app/{f}','rb').read()).hexdigest())"],
                capture_output=True, text=True, timeout=10)
            rc, out = proc.returncode, proc.stdout.strip()
        except Exception:
            rc, out = -1, ""
        if rc == 0 and out.strip() != h_local:
            stale.append(f)

    if stale:
        return (False,
                f"WARNING: {len(stale)} file(s) differ from container:\n  "
                + ", ".join(stale) +
                "\n  You need: docker compose up -d --build  AFTER this commit")
    return True, "OK: container appears up-to-date (or not running)"


def check_untracked_critical():
    """Alert on untracked files that look like they should be committed."""
    rc, out, _ = run_git(["status", "--porcelain"])
    untracked = [l for l in out.splitlines()
                 if l.startswith("??") and l.endswith(".py")]
    if untracked:
        names = [l.split()[1] for l in untracked]
        return (False,
                f"{len(names)} untracked .py file(s) not staged:\n  "
                + "\n  ".join(names) +
                "\n  If these are new modules, git add them before committing")
    return True, "OK: no orphaned .py files"


def check_newline_at_eof():
    """Common issue: files missing trailing newline (git warns)."""
    rc, out, _ = run_git(["diff", "--cached", "--name-only"])
    staged = out.splitlines() if out else []
    no_nl = []
    for f in staged:
        fpath = os.path.join(PROJECT_DIR, f)
        if os.path.isfile(fpath):
            with open(fpath, "rb") as fh:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    no_nl.append(f)
    if no_nl:
        return False, f"No trailing newline: {no_nl}"
    return True, "OK"


def check_no_ghost_modules():
    """Catch import references to .py files that don't exist (ghost modules).
    These cause Docker build failure at the smoke-test step."""
    gh_path = os.path.join(PROJECT_DIR, "ghost_hunter.py")
    if not os.path.isfile(gh_path):
        return True, "OK: ghost_hunter.py not found (skip)"
    r = subprocess.run([sys.executable, gh_path], cwd=PROJECT_DIR,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, f"Ghost modules found:\n{r.stdout}"
    return True, r.stdout.strip()


def check_code_patterns():
    """Catch repeatable bad patterns (code-mode audit).

    Covers: flush() outside `with` block (silently downgrades atomic writes),
    silent `except: pass`, hardcoded drive paths in container-shipped files,
    bare except, mutable default args.
    """
    pa_path = os.path.join(PROJECT_DIR, "pattern_audit.py")
    if not os.path.isfile(pa_path):
        return True, "OK: pattern_audit.py not found (skip)"
    r = subprocess.run([sys.executable, pa_path, "--strict"], cwd=PROJECT_DIR,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, f"Bad code patterns found:\n{r.stdout}"
    return True, r.stdout.strip().replace("\n", " | ")


def check_import_smoke():
    """Import every container-shipped module — catches import-time crashes."""
    mods = ("paths ssq_log engine_core data store run_cycle daemon_loop frontier "
            "make_dashboard nonstationarity evaluator cache diff_formula "
            "positive_control redteam_audit representation_zoo layered_null "
            "run_axes firewall proposer scoring formula_viz predict_tonight "
            "ssq_health learning_contract failure_absorber ingest_candidates "
            "axis_proposer review_primitives bias_corrector formula_composer "
            "formula_research watchdog_mode seed_bridge verify_df_gen "
            "progress_gate blue_evolve changepoint_evolve gru_evolve seq_evolve "
            "novelty_search reflective_designer").split()
    r = subprocess.run(
        [sys.executable, "-c",
         "import " + ", ".join(mods) + "; print('IMPORT_OK')"],
        cwd=PROJECT_DIR, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        short = (r.stderr or r.stdout).strip().split("\n")[-6:]
        return False, "Import smoke FAILED:\n" + "\n".join(short)
    return True, f"OK: {len(mods)} modules import clean"


def main():
    print("=" * 55)
    print("PRE-COMMIT CHECK - run before git commit")
    print(f"Dir: {PROJECT_DIR}")
    print("=" * 55)

    # 阻塞项：提交前必须修好（Dockerfile 漏拷/孤儿文件/格式）。
    # 非阻塞项(WARN)：容器版本不匹配——这是 commit 后 rebuild 的预期中间态，
    #   文档化工作流为 commit → rebuild → verify，故只警告不阻断（否则僵死：
    #   不 commit 就不能 rebuild，不 rebuild 就过不了 hook）。
    WARN = {"Container version match"}

    checks = [
        ("Dockerfile covers all .py", check_dockerfile_covers_all_py),
        ("Container version match", check_no_stale_deployment),
        ("No untracked .py orphans", check_untracked_critical),
        ("File format sanity", check_newline_at_eof),
        ("No ghost module imports", check_no_ghost_modules),
        ("No bad code patterns", check_code_patterns),
        ("Import smoke test", check_import_smoke),
    ]

    results = []
    all_ok = True
    blocking_fail = False
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"EXCEPTION: {e}"
        if name in WARN and not ok:
            status = "WARN"
            all_ok = False  # 计入总 PASS 数但不阻断提交
        else:
            status = "PASS" if ok else "FAIL"
            if not ok:
                all_ok = False
                blocking_fail = True
        results.append((name, status, msg))
        print(f"\n[{status}] {name}")
        print(f"      {msg}")

    print("\n" + "=" * 55)
    n_pass = sum(1 for _, s, _ in results if s in ("PASS", "WARN"))
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"RESULT: {n_pass}/{len(results)} PASS, {n_fail} FAIL (WARN 不计入 FAIL)")

    if blocking_fail:
        print("STATUS: FIX BEFORE COMMITTING (阻塞项 FAIL)")
        return 1
    else:
        print("STATUS: OK to commit (容器版本不匹配为预期 WARN，commit 后 rebuild 即可)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
