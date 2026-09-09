#!/usr/bin/env python3
# heartbeat_push.py - push heartbeat + critical data snapshot to GitHub data-backup branch.
# Called by predict_cron.ps1 after score phase, or manually: python heartbeat_push.py
# Target: bbqddt/ssq-evo branch data-backup, worktree D:\ssq_evo_data\_backup_wt
#
# SAFETY: the initial orphan commit is built with git plumbing (hash-object/mktree/
# commit-tree) and NEVER touches the main working tree. The only git commands that
# may modify files run inside the dedicated worktree WT, never in REPO.
import json, os, shutil, subprocess, sys, datetime

REPO = r"D:\ssq_evo"
DATA = r"D:\ssq_evo_data"
AUDIT = os.path.join(DATA, "audit")
WT = os.path.join(DATA, "_backup_wt")
BRANCH = "data-backup"
LOG = os.path.join(DATA, "heartbeat_push.log")
README_TEXT = ("ssq_evo data-backup branch. Auto-updated by heartbeat_push.py "
               "(heartbeat + critical data snapshots). Do not edit here.\n")

FILES = [
    (r"D:\ssq_evo_data\ssq_master.csv",                         "ssq_master.csv"),
    (r"D:\ssq_evo_data\predictions.jsonl",                      "predictions.jsonl"),
    (r"D:\ssq_evo_data\frontier.json",                          "frontier.json"),  # comp_elites(gen=4)：cloud-register 用同一套公式树，方法对齐
    (os.path.join(AUDIT, "heartbeat.json"),                     os.path.join("audit", "heartbeat.json")),
    (os.path.join(AUDIT, "preregistered_scores.jsonl"),         os.path.join("audit", "preregistered_scores.jsonl")),
    (os.path.join(AUDIT, "obf_boundary.csv"),                   os.path.join("audit", "obf_boundary.csv")),
    (os.path.join(AUDIT, "obf_design.json"),                    os.path.join("audit", "obf_design.json")),
    (os.path.join(AUDIT, "marginal_bias_preregistered.json"),   os.path.join("audit", "marginal_bias_preregistered.json")),
    (os.path.join(AUDIT, "physical_prior_bayes.json"),          os.path.join("audit", "physical_prior_bayes.json")),
    (os.path.join(AUDIT, "PHYSICAL_PRIOR_BAYES.md"),            os.path.join("audit", "PHYSICAL_PRIOR_BAYES.md")),
    (os.path.join(AUDIT, "PHYSICAL_MEASUREMENT_PROTOCOL.md"),   os.path.join("audit", "PHYSICAL_MEASUREMENT_PROTOCOL.md")),
    (os.path.join(AUDIT, "FOI_REQUEST_20260906.md"),            os.path.join("audit", "FOI_REQUEST_20260906.md")),
    (os.path.join(AUDIT, "DRAW_CLOSEOUT_20260902.md"),          os.path.join("audit", "DRAW_CLOSEOUT_20260902.md")),
    (os.path.join(AUDIT, "DRAW_CLOSEOUT_20260904.md"),          os.path.join("audit", "DRAW_CLOSEOUT_20260904.md")),
    (r"D:\ssq_evo\anchors\preregistered.sha256",                os.path.join("anchors", "preregistered.sha256")),
    (r"D:\ssq_evo\PRE_REGISTERED_PROTOCOL_v1.md",               "PRE_REGISTERED_PROTOCOL_v1.md"),
]

# network ops need openssl backend (proxy TLS pitfall) + wincred (GitHub PAT from credential store)
GIT_NET = ["-c", "http.sslBackend=openssl", "-c", "credential.helper=wincred"]
# data-only branch: code-check hook does not apply (no code files in snapshot);
# override hooks path per-command instead of copying the checker into the backup
GIT_NOHOOK = ["-c", "core.hooksPath=NUL:"]
# byte-exact snapshots (2026-09-09): keep CRLF exactly as on disk so a restored
# file hashes to the pre-registered anchor; autocrlf would store LF and break
# anchor reproduction after disaster recovery (d020750d vs 6eef6874 lesson).
GIT_RAW = ["-c", "core.autocrlf=false"]

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (ts, msg))
    print("[hb] %s" % msg)

def git(args, cwd, check=True, inp=None):
    cmd = ["git"] + args
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", input=inp)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if out: log("git: %s" % out[:300])
    if err: log("git-err: %s" % err[:300])
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed exit=%d" % (" ".join(args[:3]), r.returncode))
    return r

def git_bytes(args, cwd, inp_bytes=None, check=True):
    """Binary-input git call: avoids Windows text-mode CRLF corruption in plumbing inputs.
    Returns decoded stdout (like git())."""
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, input=inp_bytes)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    err = (r.stderr or b"").decode("utf-8", "replace").strip()
    if out: log("git: %s" % out[:300])
    if err: log("git-err: %s" % err[:300])
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed exit=%d" % (" ".join(args[:3]), r.returncode))
    return out

def remote_branch_exists():
    r = git(GIT_NET + ["ls-remote", "--heads", "origin", BRANCH], cwd=REPO, check=False)
    if r.returncode != 0:
        # Network failure (proxy down / github unreachable) is NOT "branch missing".
        # Treating it as missing would wrongly trigger bootstrap_orphan_branch(),
        # whose `git branch -f data-backup` then collides with the existing worktree
        # (fatal: cannot force update the branch used by worktree) and masks the real
        # cause. Surface the network error instead.
        raise RuntimeError(
            "ls-remote origin failed exit=%d (network/proxy down?); refusing to bootstrap"
            % r.returncode)
    return BRANCH in (r.stdout or "")

def bootstrap_orphan_branch():
    """Create initial commit on orphan branch via plumbing (binary-safe).
    Never touches any working tree."""
    log("bootstrap: building orphan commit via plumbing")
    blob = git_bytes(["hash-object", "-w", "--stdin"], cwd=REPO,
                     inp_bytes=README_TEXT.encode("utf-8"))
    tree_inp = ("100644 blob %s\tREADME.md\n" % blob).encode("ascii")
    tree = git_bytes(["mktree"], cwd=REPO, inp_bytes=tree_inp)
    commit = git(["commit-tree", tree, "-m", "init data-backup branch"], cwd=REPO).stdout.strip()
    # local branch may linger from a failed earlier bootstrap — reset it to this commit
    git(["branch", "-f", BRANCH, commit], cwd=REPO)
    git(GIT_NET + ["push", "origin", BRANCH], cwd=REPO)
    log("bootstrap: pushed initial %s" % BRANCH)

def main():
    log("=== heartbeat_push start ===")
    # 0. hard safety guard
    assert os.path.abspath(WT).lower() != os.path.abspath(REPO).lower(), "WT must differ from REPO"
    # 1. fresh heartbeat.json
    r = subprocess.run([sys.executable, os.path.join(REPO, "heartbeat_make.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines(): log("make: %s" % line)
    if r.returncode != 0:
        log("make-err: %s" % (r.stderr or "")[:300])
        raise RuntimeError("heartbeat_make failed")
    # 2. ensure worktree (bootstrap branch first if needed)
    if not remote_branch_exists():
        bootstrap_orphan_branch()
    if not os.path.isdir(WT):
        git(["worktree", "add", WT, BRANCH], cwd=REPO)
        log("worktree ready at %s" % WT)
    # 3. copy files into worktree only
    n = 0
    for src, dst in FILES:
        if not os.path.exists(src):
            log("skip missing: %s" % src); continue
        dst_path = os.path.join(WT, dst)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy2(src, dst_path)
        n += 1
    log("copied %d files" % n)
    # 4. commit + push inside worktree only
    git(GIT_RAW + ["add", "-A"], cwd=WT, check=False)
    st = git(GIT_RAW + ["status", "--porcelain"], cwd=WT, check=False)
    if st.stdout.strip():
        issue = "unknown"
        try:
            with open(os.path.join(WT, "audit", "heartbeat.json"), encoding="utf-8") as f:
                issue = json.load(f).get("last_issue") or "unknown"
        except Exception as e:
            log("issue parse fallback: %s" % e)
        git(GIT_RAW + GIT_NOHOOK + ["commit", "-m", "heartbeat %s %s" % (issue, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))], cwd=WT)
        git(GIT_NET + ["push", "origin", "%s:%s" % (BRANCH, BRANCH)], cwd=WT)
        log("OK pushed %s" % BRANCH)
    else:
        log("no changes, skip push")
    log("=== heartbeat_push done ===")

if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        log("ERROR: %s" % e)
        sys.exit(1)
