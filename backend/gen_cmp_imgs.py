"""生成 MINIO 图 vs 源PDF渲染 的对比拼图（上=MINIO，下=渲染），落到 /app/diag_out/。"""
import io
import os

import fitz
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
OUT = "/app/diag_out"
os.makedirs(OUT, exist_ok=True)
BASE = 300.0 / 72.0
ML = 1600


def down(page_no):
    data = mc.get_object(BUCKET, f"scans/{TASK}/page_{page_no:03d}.png").read()
    return Image.open(io.BytesIO(data)).convert("RGB")


def render(page_no):
    doc = fitz.open(PDF)
    try:
        p = doc[page_no - 1]
        r = p.rect
        long = max(r.width, r.height) * BASE
        scale = BASE if long <= ML else BASE * (ML / long)
        pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def fit(img, long_edge=260):
    w, h = img.size
    s = long_edge / max(w, h)
    return img.resize((max(1, int(w * s)), max(1, int(h * s))))


for pn in (1, 2, 3):
    a = fit(down(pn))
    b = fit(render(pn))
    W = max(a.width, b.width)
    H = a.height + b.height + 24
    canvas = Image.new("RGB", (W, H), "white")
    canvas.paste(a, (0, 0))
    canvas.paste(b, (0, a.height + 24))
    canvas.save(os.path.join(OUT, f"cmp_p{pn}.jpg"), "JPEG", quality=78)
    print(f"cmp_p{pn}.jpg saved {canvas.size}")
print("DONE")
