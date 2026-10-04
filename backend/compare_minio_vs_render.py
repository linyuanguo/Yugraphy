"""对比 MinIO 里 35f4b3e8b7e7 的页面图 vs 源 PDF 用生产参数渲染的结果。"""
import io
import os

import fitz
import numpy as np
from minio import Minio
from PIL import Image

mc = Minio(
    os.environ["MINIO_ENDPOINT"],
    access_key=os.environ["MINIO_USER"],
    secret_key=os.environ["MINIO_PASSWORD"],
    secure=False,
)
BUCKET = os.environ.get("MINIO_BUCKET", "genealogy")
TASK = "35f4b3e8b7e7"
PDF = "/app/uploads/J144-003-001-001.pdf"
ML = 1600
BASE = 300.0 / 72.0


def cb(img, label):
    g = np.asarray(img.convert("L"))
    dark = g < 235
    if not dark.any():
        print(f"  {label}: 全亮无内容")
        return None
    ys, xs = np.where(dark)
    h, w = g.shape
    box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    print(
        f"  {label}: 图={w}x{h} 内容bbox={box} "
        f"(占宽{100 * (box[2] - box[0]) / w:.0f}% 高{100 * (box[3] - box[1]) / h:.0f}%)"
    )
    return box


doc = fitz.open(PDF)
for n in range(4):
    p = doc[n]
    mb = None
    mimg = None
    try:
        data = mc.get_object(BUCKET, f"scans/{TASK}/page_{n + 1:03d}.png").read()
        mimg = Image.open(io.BytesIO(data)).convert("RGB")
        mb = cb(mimg, f"p{n + 1} MINIO")
    except Exception as e:  # noqa: BLE001
        print(f"p{n + 1} MINIO 下载失败: {e}")
    r = p.rect
    long_at_base = max(r.width, r.height) * BASE
    scale = BASE if long_at_base <= ML else BASE * (ML / long_at_base)
    pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    rimg = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    rb = cb(rimg, f"p{n + 1} 渲染")
    print(
        f"  p{n + 1} rect={r.width:.0f}x{r.height:.0f} rot={p.rotation} -> "
        f"render={rimg.size} minio={mimg.size if mimg else '?'}"
    )
    if mimg and mimg.size == rimg.size:
        a = np.asarray(rimg.resize((400, 400))).astype(int)
        b = np.asarray(mimg.resize((400, 400))).astype(int)
        print(f"  p{n + 1} 平均像素差(0-255): {abs(a - b).mean():.1f}")
doc.close()
print("DONE")
