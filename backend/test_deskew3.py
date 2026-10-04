"""端到端验证 deskew_image：合成图倾斜 ±3/±5° → 纠偏后应接近水平。"""
import cv2
import numpy as np

from app.utils.image_utils import _deskew_angle, deskew_image


def text_img():
    img = np.full((900, 1200), 255, np.uint8)
    cv2.putText(img, "Genealogy test line one two three four", (60, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
    cv2.putText(img, "Second line for skew checking only", (60, 420), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
    cv2.putText(img, "Third line end of the sample", (60, 620), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
    return img


base = text_img()
for deg in (3, -4, 5, -6):
    m = cv2.getRotationMatrix2D((600, 450), deg, 1.0)
    tilted = cv2.warpAffine(base, m, (base.shape[1], base.shape[0]), flags=cv2.INTER_CUBIC, borderValue=255)
    restored = deskew_image(tilted)
    after = _deskew_angle(cv2.cvtColor(restored, cv2.COLOR_BGR2GRAY) if len(restored.shape) == 3 else restored)
    print(f"倾斜{deg:+d}° → deskew 后残余角 {after:+.2f}° (≈0 即成功)")
