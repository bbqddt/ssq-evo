#!/usr/bin/env bash
# ============================================================
# ssq_evo — DevTop (腾讯云) 一键接车脚本
# 用法：在 DevTop 云桌面的终端里执行
#   bash <(curl -fsSL https://raw.githubusercontent.com/bbqddt/ssq-evo/main/deploy_devtop.sh)
# 或：把本文件下载到 DevTop 后  bash deploy_devtop.sh
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/app/ssq_evo}"
DATA_DIR="${DATA_DIR:-/app/ssq_evo_data}"
REPO="https://ghp.ci/https://github.com/bbqddt/ssq-evo.git"
IMG="ssq-evo-cloud"
CTN="ssq-evo-cloud"

echo "=== ssq_evo DevTop 一键接车 ==="
echo "APP_DIR=$APP_DIR  DATA_DIR=$DATA_DIR"

# 1. 工具预检
command -v git  >/dev/null || { echo "✗ git 未安装，请先: apt-get update && apt-get install -y git"; exit 1; }
command -v docker >/dev/null || { echo "✗ docker 未安装（DevTop 应已预装 docker-in-docker），退出"; exit 1; }
echo "✓ git / docker 就绪"

# 2. 克隆或更新
if [ -d "$APP_DIR/.git" ]; then
  echo "→ 仓库已存在，拉取最新..."
  git -C "$APP_DIR" pull --ff-only
else
  echo "→ 克隆仓库到 $APP_DIR ..."
  git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

# 2.5 版本闸门：确认克隆到的是"带修复的引擎"，否则拒绝跑旧代码
echo "→ 校验引擎是否含关键修复（防跑旧漏洞版本）..."
if ! grep -q 'max(2, int(k)) if read == "osc"' engine_core.py 2>/dev/null; then
  echo "✗ 危险：克隆到的 engine_core.py 不含 osc k>=2 修复（这是旧漏洞版本）。"
  echo "  请先确认仓库默认分支已包含修复提交（847886f 及之后）后再运行本脚本。"
  echo "  中止。"
  exit 1
fi
if ! grep -q 'transfer_entropy' engine_core.py 2>/dev/null; then
  echo "✗ 危险：克隆到的 engine_core.py 不含转移熵检验（旧版本）。中止。"
  exit 1
fi
if ! grep -q 'def twin_surrogate' engine_core.py 2>/dev/null; then
  echo "✗ 危险：克隆到的 engine_core.py 不含 twin surrogates 金标准零假设（旧版本）。中止。"
  exit 1
fi
echo "✓ 引擎版本校验通过（含 osc 修复 + 转移熵 + twin surrogates）"

# 3. 构建镜像（DevTop 的 docker 即宿主 docker，直接可用）
echo "→ 构建镜像 $IMG ..."
docker build -t "$IMG" .

# 4. 数据目录
mkdir -p "$DATA_DIR"

# 5. 清理旧锁 + 旧容器后启动
rm -f "$DATA_DIR/daemon.lock" || true
if docker ps -a --format '{{.Names}}' | grep -q "^${CTN}$"; then
  echo "→ 容器已存在，重建..."
  docker rm -f "$CTN"
fi
echo "→ 启动容器 $CTN ..."
docker run -d \
  --name "$CTN" \
  --restart unless-stopped \
  -v "$DATA_DIR":/app/data \
  -e CYCLE_MINUTES=0 \
  -p 8088:8088 \
  "$IMG"
rm -f "$DATA_DIR/daemon.lock" || true   # 启动后再清一次，防止构建期残留

# 6. 健康检查
sleep 6
echo "=== 容器状态 ==="
docker ps --filter "name=$CTN" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo "=== 最近日志 (tail 25) ==="
docker logs --tail 25 "$CTN" || true
echo
echo "✓ DevTop 第三辆车已上线。看板: http://<DevTop公网IP>:8088"
