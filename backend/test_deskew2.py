"""验证修复后的 _deskew_angle：竖排页面不误转 90°，小倾斜仍能纠偏。"""
import cv2
import fitz
import numpy as np
from PIL import Image

from app.utils.image_utils import _deskew_angle

PDF = "/app/uploads/J144-003-001-001.pdf"
ML = 1600
BASE = 300.0 / 72.0


def render_png(n):
    doc = fitz.open(PDF)
    p = doc[n - 1]
    r = p.rect
    long = max(r.width, r.height) * BASE
    scale = BASE if long <= ML else BASE * (ML / long)
    pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)


# 1) 真实谱书页：期望返回 ≈0（不再转 90°）
for n in (1, 2, 3):
    bgr = render_png(n)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    a = _deskew_angle(gray)
    print(f"p{n}: deskew_angle = {a:.2f}  (期望 ~0)")

# 2) 合成横向文本图：本身水平 → 0；人为歪斜 +3° → 应纠偏（返回值 ~±3）
text = np.full((900, 1200), 255, np.uint8)
cv2.putText(text, "Genealogy test line one two three four", (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
cv2.putText(text, "Second line for skew checking only", (60, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
cv2.putText(text, "Third line end of the sample", (60, 600), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
print("水平图:", _deskew_angle(text))
for deg in (3, -4):
    m = cv2.getRotationMatrix2D((600, 450), deg, 1.0)
    tilted = cv2.warpAffine(text, m, (text.shape[1], text.shape[0]), flags=cv2.INTER_CUBIC, borderValue=255)
    print(f"歪斜{deg:+d}°:", _deskew_angle(tilted), "(期望 ≈{:+d})".format(-deg))
