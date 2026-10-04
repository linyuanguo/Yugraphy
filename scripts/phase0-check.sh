#!/usr/bin/env bash
# 阶段0：目标机前置检查
set -e
echo "========== [1] Docker 版本 =========="
docker --version || echo "!! docker 未安装"
docker compose version 2>/dev/null || docker-compose --version || echo "!! compose 未安装"

echo
echo "========== [2] 16 → 206 SGLang 连通性 =========="
curl -s -m 10 http://172.16.199.206:30000/health || echo "!! /health 不通"
echo
echo "--- /v1/models ---"
curl -s -m 15 http://172.16.199.206:30000/v1/models | python -m json.tool 2>/dev/null || echo "!! /v1/models 不可用"

echo
echo "========== [3] 磁盘空间 =========="
df -h / /opt /var/lib/docker 2>/dev/null | head -10

echo
echo "========== [4] 内存 =========="
free -h

echo
echo "========== [5] Docker 拉镜像测试 =========="
docker pull nginx:1.25-alpine && echo "✓ 镜像拉取正常" || echo "✗ 拉取失败，需配置 registry-mirrors"

echo
echo "========== [6] 防火墙 80/443 =========="
command -v firewall-cmd >/dev/null && {
  firewall-cmd --list-ports
  firewall-cmd --add-service=http --add-service=https --permanent
  firewall-cmd --reload
} || echo "无 firewalld（可能是其他防火墙方案）"

echo
echo "========== [7] 端口占用检查 =========="
ss -tlnp | grep -E ':(80|443|5432|7687|9000|8000)\b' || echo "端口干净"

echo "========== 阶段0 完成 =========="
