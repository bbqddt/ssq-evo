# verify_automation_reachability.py
# Check: can each automation actually reach the paths/files it operates on?
# Cloud-sandbox automations that reference D:\ssq_evo_data will FAIL this check.
# Run after creating/modifying any automation.

import os
import sys

# Map of known automation IDs -> their claimed targets (from our records)
KNOWN_AUTOMATION_TARGETS = {
    # Format: automation_id -> list of (label, path_pattern, required_access)
    # required_access: "read" | "write" | "execute"
    "draw-day-register": [
        ("predictions file", r"D:\ssq_evo_data\predictions.jsonl", "write"),
        ("engine code", r"D:\ssq_evo\predict_tonight.py", "execute"),
        ("data dir", r"D:\ssq_evo_data", "read"),
    ],
    "draw-day-score": [
        ("predictions file", r"D:\ssq_evo_data\predictions.jsonl", "read+write"),
        ("data master", r"D:\ssq_evo_data\ssq_master.csv", "read"),
    ],
    "health-watchdog": [
        ("daemon log", r"D:\ssq_evo_data\daemon.log", "read"),
        ("state file", r"D:\ssq_evo_data\state.json", "read"),
    ],
}


def check_path_reachable(label, path, access):
    """Check if a path is reachable from current execution context."""
    if not os.path.exists(path):
        # Check if parent dir exists (path might not exist yet but should be creatable)
        parent = os.path.dirname(path)
        if os.path.exists(parent):
            return False, f"PATH NOT YET EXISTS (parent OK): {path}\n  Will be created at runtime - verify parent is writable"
        return False, f"UNREACHABLE: {path}\n  Parent dir does not exist either: {parent}\n  >>> This automation CANNOT run in cloud sandbox! <<<"

    # Path exists - check access
    if "write" in access:
        if not os.access(path, os.W_OK):
            return False, f"NO WRITE ACCESS: {path}"
    if "read" in access and not os.access(path, os.R_OK):
        return False, f"NO READ ACCESS: {path}"

    return True, f"OK ({access})"


def detect_execution_context():
    """Figure out where we're running."""
    indicators = {
        "D_DRIVE": os.path.exists(r"D:\ssq_evo"),
        "C_WORKBUDDY": os.path.exists(r"C:\Users\Administrator\.workbuddy"),
        "DOCKER": os.path.exists("/app/run_cycle.py") or os.environ.get("IN_DOCKER") == "1",
    }
    return indicators


def main():
    print("=" * 58)
    print("AUTOMATION REACHABILITY VERIFICATION")
    print("=" * 58)

    ctx = detect_execution_context()
    print("\nExecution context:")
    for name, present in ctx.items():
        print(f"  {'YES' if present else 'NO':>3}  {name}")

    if ctx["DOCKER"]:
        print("\n[INFO] Running inside Docker - local D: drive unreachable")
        print("  Any automation targeting D:\\ will FAIL from here")

    issues = []
    total = 0

    for auto_id, targets in KNOWN_AUTOMATION_TARGETS.items():
        print(f"\n--- Automation: {auto_id} ---")
        for label, path, access in targets:
            total += 1
            ok, msg = check_path_reachable(label, path, access)
            status = "OK" if ok else "FAIL"
            print(f"  [{status}] {label}: {msg}")
            if not ok:
                issues.append((auto_id, label, msg))

    print("\n" + "=" * 58)
    n_ok = total - len(issues)
    print(f"RESULT: {n_ok}/{total} reachable, {len(issues)} blocked")

    if issues:
        print("\nBLOCKED automations (will silently fail):")
        for auto_id, label, msg in issues:
            print(f"  ! {auto_id} / {label}")
            print(f"    {msg}")
        print("""
RECOMMENDATION:
- For D:\\ drive targets: use LOCAL tasks (PowerShell ScheduledTask)
  or run from a session with D: drive access (like WorkBuddy Bash).
- Cloud/sandbox automations CANNOT touch local Windows paths.
- See: install_predict_tasks.ps1 (local task installer)
""")
        return 1

    print("\nSTATUS: All automations can reach their targets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
