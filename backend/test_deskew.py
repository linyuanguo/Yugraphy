"""测试 image_utils.deskew_image 对 PDF 渲染图的影响。"""
import os
import cv2
import fitz
import numpy as np
from PIL import Image

PDF = "/app/uploads/J144-003-001-001.pdf"
OUT = "/app/diag_out"
os.makedirs(OUT, exist_ok=True)
ML = 1600
BASE = 300.0 / 72.0


def render(p):
    r = p.rect
    long = max(r.width, r.height) * BASE
    scale = BASE if long <= ML else BASE * (ML / long)
    pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def deskew(img: Image.Image) -> Image.Image:
    arr = np.asarray(img.convert("RGB"))
    arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(binary)
    if coords is None:
        return img
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    print("  minAreaRect angle raw:", angle)
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    print("  corrected angle:", angle)
    if abs(angle) < 0.4:
        return img
    h, w = arr.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    out = cv2.warpAffine(
        arr, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    out = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
    return Image.fromarray(out)


doc = fitz.open(PDF)
for n in (1, 2, 3):
    p = doc[n - 1]
    img = render(p)
    print(f"p{n} original size={img.size}")
    img.save(f"{OUT}/p{n}_render.jpg", "JPEG", quality=80)
    dimg = deskew(img)
    print(f"   deskew size={dimg.size}")
    dimg.save(f"{OUT}/p{n}_deskew.jpg", "JPEG", quality=80)
print("DONE")
