#!/usr/bin/env bash
# 构建前端 dist（无需宿主机安装 Node，用 node:20 容器）
set -e
cd "$(dirname "$0")/../frontend"

echo "==> 用 node:20 容器构建前端"
docker run --rm \
  -v "$(pwd)":/app \
  -w /app \
  node:20-alpine \
  sh -c "npm install --registry=https://registry.npmmirror.com && npm run build"

echo "==> 构建完成: $(pwd)/dist"
