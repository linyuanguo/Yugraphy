#!/usr/bin/env bash
# 生成内网 HTTPS 自签证书（根 CA + 服务器证书），输出到 certs/。
# 实现见 scripts/gen_certs.py（用 cryptography 精确按 UTF-8 写入中文 CN，
# 规避 openssl req -subj 的字符集二次编码坑）。
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$DIR/scripts/gen_certs.py"
echo "✓ 完成：请把 certs/ca.crt 作为 rootCA.crt 分发/嵌入到客户端工具（必须与签发 server.crt 的 CA 是同一张）"
