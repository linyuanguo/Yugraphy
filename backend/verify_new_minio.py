"""验证重传后的 MinIO 页面图：方向、内容占比与源 PDF 渲染一致。"""
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


def cb(img):
    g = np.asarray(img.convert("L"))
    dark = g < 235
    ys, xs = np.where(dark)
    if not dark.any():
        return None
    h, w = g.shape
    return (xs.min(), ys.min(), xs.max(), ys.max()), (w, h)


doc = fitz.open(PDF)
for n in (1, 2, 3):
    data = mc.get_object(BUCKET, f"scans/{TASK}/page_{n:03d}.png").read()
    mimg = Image.open(io.BytesIO(data)).convert("RGB")
    mbox, msize = cb(mimg)
    p = doc[n - 1]
    r = p.rect
    long = max(r.width, r.height) * BASE
    scale = BASE if long <= ML else BASE * (ML / long)
    pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    rimg = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    rbox, rsize = cb(rimg)
    same_size = msize == rsize
    print(f"p{n}: minio={msize} render={rsize} 同尺寸={same_size}")
    if same_size and mbox and rbox:
        # 内容框差几个像素内视为一致（CLAHE 不影响几何）
        ok = all(abs(a - b) <= 3 for a, b in zip(mbox, rbox))
        print(f"   minio内容bbox={mbox} render内容bbox={rbox} 内容一致={ok}")
doc.close()
print("DONE")
