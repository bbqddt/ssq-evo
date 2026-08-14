#!/usr/bin/env bash
# 恢复脚本：把备份里的修复文件覆盖到 clone 目录，并校验
set -euo pipefail
TMP="${1:-D:/ssq_evo_clone_tmp}"
BAK="${2:-D:/ssq_evo_backup_20260814_152001}"

for f in engine_core.py run_cycle.py daemon_loop.py make_dashboard.py Dockerfile docker-compose.yml deploy_devtop.sh config.json; do
  if [ -f "$BAK/$f" ]; then
    cp "$BAK/$f" "$TMP/$f"
    echo "overwrote $f"
  else
    echo "skip (not in backup): $f"
  fi
done

echo "=== verify osc fix ==="
grep -q 'max(2, int(k)) if read == "osc"' "$TMP/engine_core.py" && echo "OSC_FIX_PRESENT" || echo "OSC_FIX_MISSING"
echo "=== verify transfer_entropy ==="
grep -q 'transfer_entropy' "$TMP/engine_core.py" && echo "TE_PRESENT" || echo "TE_MISSING"
echo "=== modified files (git) ==="
git -C "$TMP" status --short
