#!/usr/bin/env bash
# 交叉编译 家谱管理系统 证书安装工具（win + linux 两个二进制），输出到 ../nginx/cert/。
#   - Linux  : gcc:12-bookworm 静态编译（-static，规避 glibc 版本不匹配）
#   - Windows: ubuntu + mingw-w64 交叉编译（GUI .exe，原生嵌 comctl32 v6 manifest）
#
# 前置：本机已安装 docker 且能拉取镜像。
# 用法：./build.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/../nginx/cert"
mkdir -p "$OUT"

echo "==> [1/2] 编译 Linux 版（静态）"
docker run --rm -v "$HERE:/src" -w /src gcc:12-bookworm \
  bash -lc 'gcc -static -O2 -o /src/certtool_linux certtool_linux.c'
cp "$HERE/certtool_linux" "$OUT/cert-tool-linux"
chmod +x "$OUT/cert-tool-linux"

echo "==> [2/2] 编译 Windows 版（mingw-w64, GUI .exe）"
# Ubuntu 的 mingw gcc 驱动不自动处理 .rc：先用 windres 转成 COFF 目标再链接。
docker run --rm -v "$HERE:/src" -w /src ubuntu:22.04 \
  bash -lc '
    set -e
    apt-get update
    apt-get install -y --no-install-recommends gcc-mingw-w64-x86-64
    x86_64-w64-mingw32-windres -O coff -i certtool_dlg.rc -o certtool_dlg.res.o
    x86_64-w64-mingw32-gcc -O2 -o /src/certtool_dlg.exe certtool_dlg.c certtool_dlg.res.o \
      -mwindows -lcomctl32 -lcrypt32 -lshell32 -luser32 -lgdi32 -lole32
  '
cp "$HERE/certtool_dlg.exe" "$OUT/cert-tool-windows.exe"

# 根证书也放一份，供手动安装 / 排查
cp "$HERE/rootCA.crt" "$OUT/rootCA.crt"

echo "==> 完成，输出目录："
ls -la "$OUT"
