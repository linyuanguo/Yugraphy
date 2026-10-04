"""诊断 J144-003-001-001.pdf 页面构成与转图内容损失（与生产 _pdf_page_to_png 同参数）。"""
import io
import os

import fitz
import numpy as np
from PIL import Image

PDF = "/app/uploads/J144-003-001-001.pdf"
ML = 1600  # 生产 MAX_IMAGE_LONG_EDGE
BASE = 300.0 / 72.0


def content_bbox(pil_img, label=""):
    """统计「内容」（比浅底暗的像素）在整图中的边界，判断内容是否铺满页面。"""
    g = np.asarray(pil_img.convert("L"))
    dark = g < 235
    if not dark.any():
        print(f"  {label}: 无内容(全亮)")
        return
    ys, xs = np.where(dark)
    h, w = g.shape
    print(
        f"  {label}: 图={w}x{h} 内容x:[{xs.min()}..{xs.max()}] y:[{ys.min()}..{ys.max()}] "
        f"内容占宽{100 * (xs.max() - xs.min()) / w:.0f}% 高{100 * (ys.max() - ys.min()) / h:.0f}%"
    )


doc = fitz.open(PDF)
print("PAGES:", doc.page_count, "encrypted:", doc.is_encrypted)
for n in range(min(8, doc.page_count)):
    p = doc[n]
    r = p.rect
    infos = p.get_image_info()
    txt = p.get_text("text")
    print("=" * 70)
    print(
        f"p{n + 1} rect={r.width:.1f}x{r.height:.1f} (ratio {r.width / r.height:.3f}) "
        f"rotation={p.rotation} cropbox={p.cropbox} images={len(infos)} textlen={len(txt.strip())}"
    )
    for i in infos:
        b = i["bbox"]
        print(
            f"   img bbox=({b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f}) "
            f"占页宽{(b[2] - b[0]) / r.width:.3f} 高{(b[3] - b[1]) / r.height:.3f} xref={i.get('xref')}"
        )
    if n >= 2:  # 前 2 页做渲染/抽取对比即可
        continue
    # —— 生产整页渲染（长边 1600）——
    w_pt, h_pt = r.width, r.height
    long_at_base = max(w_pt, h_pt) * BASE
    scale = BASE if long_at_base <= ML else BASE * (ML / long_at_base)
    pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    content_bbox(img, f"p{n + 1} 生产渲染(1600)")
    # —— 若满足抽内嵌原图条件，看抽出的图 ——
    if len(infos) == 1:
        x0, y0, x1, y1 = infos[0]["bbox"]
        if (x1 - x0) >= r.width * 0.8 and (y1 - y0) >= r.height * 0.8:
            xref = infos[0].get("xref") or 0
            if xref > 0:
                raw = doc.extract_image(xref)["image"]
                eimg = Image.open(io.BytesIO(raw)).convert("RGB")
                print(f"   → 会命中抽内嵌原图: 原图尺寸={eimg.size}")
                content_bbox(eimg, "   内嵌原图")
doc.close()
print("DONE")
