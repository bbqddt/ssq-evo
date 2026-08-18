# verify_deployment.py
# Deployment post-build verification.
# Run AFTER every `docker compose up -d --build`.
# Exit 0 = ALL PASS, exit 1 = ANY FAIL.
# This is the HARD GATE that prevents "code written but container runs old version".

import sys
import os
import subprocess
import json
import time
import hashlib

PROJECT_DIR = r"D:\ssq_evo"
DATA_DIR = r"D:\ssq_evo_data"
CONTAINER_NAME = "ssq-evo-engine"

# Files that MUST exist inside container (Dockerfile COPY targets)
REQUIRED_CONTAINER_FILES = [
    "run_cycle.py",
    "engine_core.py",
    "data.py",
    "store.py",
    "daemon_loop.py",
    "frontier.py",
    "make_dashboard.py",
    "nonstationarity.py",
    "evaluator.py",
    "cache.py",
    "diff_formula.py",
    "positive_control.py",
    "redteam_audit.py",
    "representation_zoo.py",
    "layered_null.py",
    "run_axes.py",
    "config.json",
    # Added in later commits - these were MISSING before, caused crashes:
    "firewall.py",
    "proposer.py",
    "scoring.py",
    "formula_viz.py",
    "predict_tonight.py",
]

# Files where we check local==container content hash (critical path)
VERSION_CHECK_FILES = [
    "run_cycle.py",
    "firewall.py",
    "proposer.py",
    "scoring.py",
    "formula_viz.py",
    "run_axes.py",
    "predict_tonight.py",
]


def run_cmd(cmd, timeout=30):
    """Run command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def file_hash(path):
    """MD5 hex of file contents."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_container_running():
    """1. Is container Up?"""
    rc, out, _ = run_cmd(["docker", "ps", "--filter", f"name={CONTAINER_NAME}",
                          "--format", "{{.Status}}"])
    if rc != 0 or not out:
        return False, "FAIL: Container not running or docker error"
    if "Up" not in out:
        return False, f"FAIL: Container status = {out} (not Up)"
    return True, f"PASS: {out}"


def check_required_files():
    """2. All required .py files exist inside container."""
    missing = []
    for f in REQUIRED_CONTAINER_FILES:
        rc, out, _ = run_cmd(["docker", "exec", CONTAINER_NAME, "test", "-f", f"/app/{f}"])
        if rc != 0:
            missing.append(f)
    if missing:
        return False, f"FAIL: {len(missing)} missing in container: {missing}"
    return True, f"PASS: all {len(REQUIRED_CONTAINER_FILES)} files present"


def check_version_match():
    """3. Critical files: local hash == container hash."""
    mismatches = []
    for f in VERSION_CHECK_FILES:
        local_path = os.path.join(PROJECT_DIR, f)
        if not os.path.exists(local_path):
            mismatches.append(f"{f}: LOCAL FILE MISSING")
            continue
        local_h = file_hash(local_path)
        # Get container file hash
        rc, out, _ = run_cmd(["docker", "exec", CONTAINER_NAME,
                              "python3", "-c",
                              f"import hashlib; h=hashlib.md5(open('/app/{f}','rb').read()).hexdigest(); print(h)"])
        if rc != 0:
            mismatches.append(f"{f}: cannot read from container ({out[:60]})")
            continue
        if out != local_h:
            mismatches.append(f"{f}: LOCAL={local_h[:8]}.. != CONTAINER={out[:8]}..")
    if mismatches:
        return False, f"FAIL: {len(mismatches)} version mismatch:\n  " + "\n  ".join(mismatches)
    return True, f"PASS: {len(VERSION_CHECK_FILES)} files match"


def check_importable():
    """4. New modules can be imported inside container (no ImportError)."""
    modules_to_test = ["firewall", "proposer", "scoring", "formula_viz"]
    fail_imports = []
    for mod in modules_to_test:
        rc, out, err = run_cmd(["docker", "exec", CONTAINER_NAME,
                                "python3", "-c", f"import {mod}; print('OK')"])
        if rc != 0 or "OK" not in out:
            fail_imports.append(f"{mod}: {(err or out)[:80]}")
    if fail_imports:
        return False, f"FAIL: import errors:\n  " + "\n  ".join(fail_imports)
    return True, f"PASS: {len(modules_to_test)} modules importable"


def check_daemon_health():
    """5. daemon.log: no NEW tracebacks since last restart, state fresh."""
    log_path = os.path.join(DATA_DIR, "daemon.log")
    state_path = os.path.join(DATA_DIR, "state.json")

    issues = []

    # Check daemon.log exists and is recent
    if not os.path.exists(log_path):
        issues.append("daemon.log MISSING")
    else:
        mtime = os.path.getmtime(log_path)
        age_min = (time.time() - mtime) / 60
        if age_min > 120:  # 2 hours
            issues.append(f"daemon.log stale: {age_min:.0f}min old")

        # Check for recent Traceback (last 50 lines only - ignore historical)
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            recent = lines[-50:] if len(lines) > 50 else lines
            tb_count = sum(1 for l in recent if "Traceback" in l)
            if tb_count > 0:
                issues.append(f"{tb_count} Traceback(s) in last 50 lines of daemon.log")
        except Exception as e:
            issues.append(f"daemon.log read error: {e}")

    # Check state.json
    if not os.path.exists(state_path):
        issues.append("state.json MISSING")
    else:
        try:
            st_mtime = os.path.getmtime(state_path)
            state_age_min = (time.time() - st_mtime) / 60
            d = json.load(open(state_path))
            cid = d.get("cycle_id", "?")
            updated = d.get("updated", "?")
            if state_age_min > 120:
                issues.append(f"state.json stale: {state_age_min:.0f}min old (cycle={cid})")
            else:
                issues.append(f"INFO: cycle_id={cid}, updated={updated}, age={state_age_min:.0f}min")
        except Exception as e:
            issues.append(f"state.json parse error: {e}")

    fatal = [i for i in issues if not i.startswith("INFO")]
    if fatal:
        return False, "FAIL: " + "; ".join(fatal)
    return True, "PASS: " + "; ".join(issues)


def check_dockerfile_completeness():
    """6. Dockerfile COPY list includes all .py files in project root."""
    dockerfile_path = os.path.join(PROJECT_DIR, "Dockerfile")
    if not os.path.exists(dockerfile_path):
        return False, "FAIL: Dockerfile MISSING"

    with open(dockerfile_path, "r") as f:
        df_content = f.read()

    # Find COPY line with .py files
    import re
    copy_match = re.search(r"COPY\s+(.*?)\s+\./", df_content)
    if not copy_match:
        return False, "FAIL: Cannot find COPY line in Dockerfile"

    copied_files = copy_match.group(1).split()

    # Find all .py in project root
    local_pys = set(f for f in os.listdir(PROJECT_DIR) if f.endswith(".py"))
    copied_set = set(copied_files)

    missing_from_copy = local_pys - copied_set
    extra_in_copy = copied_set - local_pys

    msgs = []
    if missing_from_copy:
        msgs.append(f"MISSING from COPY: {sorted(missing_from_copy)}")
    if extra_in_copy:
        msgs.append(f"EXTRA in COPY (not found locally): {sorted(extra_in_copy)}")

    if missing_from_copy:
        return False, "FAIL: Dockerfile incomplete: " + "; ".join(msgs)
    detail = ", ".join(msgs) if msgs else "OK"
    return True, f"PASS: Dockerfile COPY complete ({detail})"


def main():
    print("=" * 60)
    print("DEPLOYMENT VERIFICATION - post build gate")
    print(f"Container: {CONTAINER_NAME}")
    print(f"Project:  {PROJECT_DIR}")
    print(f"Time:     {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    checks = [
        ("Container running", check_container_running),
        ("Required files present", check_required_files),
        ("Version match (local==container)", check_version_match),
        ("Modules importable", check_importable),
        ("Daemon health", check_daemon_health),
        ("Dockerfile completeness", check_dockerfile_completeness),
    ]

    results = []
    all_pass = True
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"EXCEPTION: {e}"
        status = "PASS" if ok else "FAIL"
        results.append((name, status, msg))
        if not ok:
            all_pass = False
        print(f"\n[{status}] {name}")
        print(f"      {msg}")

    print("\n" + "=" * 60)
    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"RESULT: {n_pass}/{len(results)} PASS, {n_fail} FAIL")

    if all_pass:
        print("STATUS: DEPLOYMENT VERIFIED - safe to proceed")
        return 0
    else:
        print("STATUS: DEPLOYMENT BROKEN - do NOT declare completion until fixed")
        print("\nFailed checks:")
        for name, status, msg in results:
            if status == "FAIL":
                print(f"  * {name}: {msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
