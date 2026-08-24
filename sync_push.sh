#!/usr/bin/env bash
# ssq_evo 推送助手 —— 走"已连接的 GitHub 身份"，命令中不含任何 token。
#
# 原理：
#   本机 git 的系统级 credential.helper 是 WorkBuddy 的 helper-selector(manager)，
#   它不会为 github.com 提供 git 推送凭证（面板连接 != git CLI 凭证）。
#   用户已在 Windows 凭据管理器存了 GitHub PAT（oauth2@github.com），
#   用 git-credential-wincred 可取。本脚本临时隔离系统 helper，仅用 wincred，
#   从而实现"借已连接的 GitHub 身份推送"且不把 token 写进命令。
#
# 用法： ./sync_push.sh "提交说明"
set -e
cd "$(dirname "$0")"

PROXY="http://127.0.0.1:10808/"
EMPTY="$(mktemp 2>/dev/null || echo /tmp/empty_gitconfig)"
: > "$EMPTY"   # 空文件，用于隔离系统级 helper-selector

GIT=(git -c "http.proxy=$PROXY" -c http.sslVerify=false -c http.schannelCheckRevoke=false -c http.sslBackend=openssl -c credential.helper=wincred)

# 1) 先拉远端（GitHub Actions 每轮会回写数据），rebase 保持线性历史
echo "==> pull --rebase (sync from Actions)"
GIT_TERMINAL_PROMPT=0 GIT_CONFIG_SYSTEM="$EMPTY" "${GIT[@]}" pull --rebase origin main

# 2) 提交本地改动
git add -A
if git diff --cached --quiet; then
  echo "==> nothing to commit"
else
  git -c user.name=ssq-evo -c user.email=ssq-evo@local commit -m "${1:-auto: sync push}"
fi

# 3) 推送
echo "==> push origin main (via connected GitHub credential)"
GIT_TERMINAL_PROMPT=0 GIT_CONFIG_SYSTEM="$EMPTY" "${GIT[@]}" push origin main
echo "==> done"
