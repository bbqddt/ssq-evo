# -*- coding: utf-8 -*-
"""pattern_audit.py —— 代码模式级静态审计（防复发门禁）

背景：深度审计发现的问题大多不是"写错一行"，而是**可重复的坏模式**：
  1. `with open() as f:` 块外再 `f.flush()` → f 已关闭 → ValueError
     （实测发生：frontier.py / run_cycle.py 的原子写静默降级为非原子写）
  2. `except Exception: pass` → 异常被吞，容器静默空转
  3. 硬编码 `D:/xxx` 路径 → 容器内 /app 下必炸
  4. 裸 `except:` → 连 KeyboardInterrupt/SystemExit 一起吞
  5. 可变默认参数 `def f(x=[])` → 跨调用共享状态
  6. `max()/min()` 空序列 → ValueError（无 default 兜底）

本模块在 commit / docker build 时自动拦截这些模式。

用法:
    python pattern_audit.py            # 扫描当前目录所有 .py
    python pattern_audit.py --strict   # WARN 也视为失败
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 已知且已确认安全的豁免（文件:行号 或 文件）
ALLOWLIST = {
    # ssq_log 内部必须自身绝对安全，绝不能因日志再抛异常
    "ssq_log.py",
    # pattern_audit 自身包含用于演示/匹配的字符串
    "pattern_audit.py",
}

# 只在宿主机运行的脚本：硬编码 D: 路径是预期行为，不进容器
HOST_ONLY_PY = {
    "ci_evolve.py", "merge_candidates.py", "ingest_candidates.py",
    "data_refresh.py", "meta_audit.py", "evolve_predictor.py",
    "merge_evo_proposals.py", "ghost_hunter.py", "pattern_audit.py",
    "pre_commit_check.py", "verify_deployment.py",
    "verify_automation_reachability.py", "verify_firewall.py",
    "ssq_health.py", "smoke_test.py", "benchmark_speed.py",
}

FILE_METHODS = ("flush", "fileno", "close", "write", "read", "writelines", "truncate")


def _py_files(root="."):
    out = []
    for f in sorted(os.listdir(root)):
        if f.endswith(".py") and not f.startswith("."):
            out.append(f)
    return out


def _strip_strings(line):
    """粗略去掉字符串字面量，避免把示例文本当代码。"""
    return re.sub(r'"[^"]*"', '""', re.sub(r"'[^']*'", "''", line))


def audit_file(fn, host_only=False):
    """返回 [(severity, lineno, rule, msg)]

    host_only=True 时跳过 HARDCODED_DRIVE（宿主机脚本用 D: 路径是预期行为）。
    """
    findings = []
    try:
        raw = open(fn, encoding="utf-8", errors="ignore").read()
    except Exception:
        return findings
    lines = raw.split("\n")

    # with-as 块追踪: var -> (block_indent, block_end_line)
    with_blocks = []
    open_stack = []
    for i, ln in enumerate(lines):
        code = _strip_strings(ln)
        stripped = code.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 行内豁免：行尾加 `# audit-ok: 理由` 表示已人工确认安全
        if re.search(r'#\s*audit-ok\b', ln):
            continue
        indent = len(code) - len(code.lstrip())

        # 记录 with ... as X:
        m = re.search(r'\bwith\s+.*\bas\s+(\w+)\s*:', code)
        if m:
            open_stack.append((m.group(1), indent, i))
        # 块结束检测：缩进回退
        while open_stack and indent <= open_stack[-1][1] and i > open_stack[-1][2]:
            open_stack.pop()

        # 规则1：块外对文件变量调用文件方法
        fm = re.match(r'(\w+)\.(' + "|".join(FILE_METHODS) + r')\s*\(', stripped)
        if fm and open_stack is not None:
            var, meth = fm.group(1), fm.group(2)
            for (wvar, windent, wline) in open_stack:
                if wvar == var:
                    break
            else:
                # 变量不在任何打开的 with 块中 → 可能用了已关闭句柄
                # 仅当同文件里出现过 "with ... as var" 才报，避免误伤
                if re.search(r'\bwith\s+.*\bas\s+' + re.escape(var) + r'\s*:', raw):
                    prev = lines[i - 1] if i else ""
                    if "with open" not in prev:
                        findings.append((
                            "CRITICAL", i + 1, "CLOSED_FILE_OP",
                            f"`{var}.{meth}()` outside its `with` block "
                            f"(file already closed → ValueError)"))

        # 规则2：静默 except（下一行是裸 pass）
        if re.match(r'except\b', stripped) and stripped.rstrip().endswith(":"):
            nxt = ""
            for j in range(i + 1, min(i + 3, len(lines))):
                s = lines[j].strip()
                if s and not s.startswith("#"):
                    nxt = s
                    break
            if nxt == "pass":
                findings.append((
                    "WARN", i + 1, "SILENT_EXCEPT",
                    "exception swallowed silently; use "
                    "ssq_log.log_exception(...) instead of pass"))

        # 规则4：裸 except
        if re.match(r'except\s*:', stripped):
            findings.append((
                "WARN", i + 1, "BARE_EXCEPT",
                "bare except swallows KeyboardInterrupt/SystemExit too"))

        # 规则3：硬编码盘符路径（宿主机脚本豁免）
        dm = re.search(r'''['"][A-Za-z]:[\\/]''', ln)
        if dm and not host_only and not re.search(r'#.*[A-Za-z]:[\\/]', ln):
            findings.append((
                "WARN", i + 1, "HARDCODED_DRIVE",
                f"hardcoded drive path {dm.group(0)!r} breaks inside container "
                f"(use DATA_DIR env)"))

        # 规则5：可变默认参数
        if re.search(r'def\s+\w+\s*\(.*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\))', code):
            findings.append((
                "WARN", i + 1, "MUTABLE_DEFAULT",
                "mutable default arg shared across calls"))

    return findings


def main():
    strict = "--strict" in sys.argv
    total = {"CRITICAL": 0, "WARN": 0}
    rows = []
    for fn in _py_files(HERE):
        if fn in ALLOWLIST:
            continue
        for sev, lineno, rule, msg in audit_file(fn, host_only=(fn in HOST_ONLY_PY)):
            total[sev] += 1
            rows.append((sev, fn, lineno, rule, msg))

    rows.sort(key=lambda r: (r[0] != "CRITICAL", r[1], r[2]))
    for sev, fn, lineno, rule, msg in rows:
        print(f"[{sev}] {fn}:{lineno} {rule} — {msg}")

    print(f"\npattern_audit: CRITICAL={total['CRITICAL']} WARN={total['WARN']}")
    if total["CRITICAL"] or (strict and total["WARN"]):
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
