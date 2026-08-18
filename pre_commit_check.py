# pre_commit_check.py
# Run BEFORE every `git commit`.
# Catches: Dockerfile missing new .py files, untracked critical files, etc.
# Exit 0 = OK to commit. Exit 1 = FIX FIRST.

import sys
import os
import re
import subprocess

PROJECT_DIR = r"D:\ssq_evo"


def run_git(args):
    r = subprocess.run(["git"] + args, cwd=PROJECT_DIR,
                       capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def check_dockerfile_covers_all_py():
    """CRITICAL: Every .py in project root MUST be in Dockerfile COPY."""
    dockerfile = os.path.join(PROJECT_DIR, "Dockerfile")
    if not os.path.exists(dockerfile):
        return False, "Dockerfile MISSING - cannot deploy without it"

    with open(dockerfile) as f:
        content = f.read()

    m = re.search(r"COPY\s+(.*?)\s+\./", content)
    if not m:
        return False, "Cannot find 'COPY ... ./' line in Dockerfile"

    copied = set(m.group(1).split())
    local_pys = set(f for f in os.listdir(PROJECT_DIR) if f.endswith(".py"))

    missing = sorted(local_pys - copied)
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


def main():
    print("=" * 55)
    print("PRE-COMMIT CHECK - run before git commit")
    print(f"Dir: {PROJECT_DIR}")
    print("=" * 55)

    checks = [
        ("Dockerfile covers all .py", check_dockerfile_covers_all_py),
        ("Container version match", check_no_stale_deployment),
        ("No untracked .py orphans", check_untracked_critical),
        ("File format sanity", check_newline_at_eof),
    ]

    results = []
    all_ok = True
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"EXCEPTION: {e}"
        status = "PASS" if ok else "FAIL"
        results.append((name, status, msg))
        if not ok:
            all_ok = False
        print(f"\n[{status}] {name}")
        print(f"      {msg}")

    print("\n" + "=" * 55)
    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"RESULT: {n_pass}/{len(results)} PASS, {n_fail} FAIL")

    if all_ok:
        print("STATUS: OK to commit")
        return 0
    else:
        print("STATUS: FIX BEFORE COMMITTING")
        return 1


if __name__ == "__main__":
    sys.exit(main())
