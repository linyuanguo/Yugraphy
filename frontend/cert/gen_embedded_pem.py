#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 rootCA.crt 重新生成 embedded_pem.h（C 字符串常量）。

当生产 server.crt 的根证书更新后，运行本脚本即可让 certtool 内嵌新证书，
然后重新构建两个二进制：
    python3 gen_embedded_pem.py
    ./build.sh
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "rootCA.crt")
OUT = os.path.join(HERE, "embedded_pem.h")


def main():
    with open(SRC, "r", encoding="utf-8") as f:
        raw = f.read()
    lines = [ln.rstrip("\n") for ln in raw.splitlines() if ln.strip()]
    body = "".join('    "%s\\n"\n' % ln for ln in lines)
    hdr = (
        "/* Embedded rootCA.crt (generated from rootCA.crt by gen_embedded_pem.py). */\n"
        "/* C string constant replacing windres RCDATA embedding. */\n"
        "static const char *EMBEDDED_PEM =\n"
        + body
        + '    "";\n'
    )
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(hdr)
    print("已生成 %s（%d 行）" % (OUT, len(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
