"""图像处理工具：PDF/TIF 转 PNG + CPU 预处理（纠偏/增强等）。"""
import concurrent.futures
import logging
import math
import os
import re
from io import BytesIO
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger("genealogy.image")

VALID_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
VALID_DOC_EXTS = {".pdf"}


def _resize_to_max(img: Image.Image, max_long_edge: int) -> Image.Image:
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge:
        return img
    scale = max_long_edge / long_edge
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def _pdf_page_to_png(doc, page, page_no: int, out_dir: str, max_long_edge: int) -> str:
    """单页 PDF → 目标长边 PNG（在保清晰度的前提下走最快路径）。

    ① 整页单图（扫描 PDF 最常见形态）：直接抽取 PDF 内嵌原图，跳过 PDF 光栅化，
       保留原生分辨率后按需缩小，最清晰也最快；
    ② 其它页面：按目标长边一次性渲染到位，避免旧逻辑「先 300dpi 全尺寸渲染、
       再缩小丢弃」的双份开销（输出与旧逻辑一致：长边 = max_long_edge）。
    """
    import fitz  # PyMuPDF

    out = os.path.join(out_dir, f"page_{page_no:03d}.png")
    embedded = None
    try:
        infos = page.get_image_info(full=True)
        if len(infos) == 1:
            x0, y0, x1, y1 = infos[0]["bbox"]
            if (x1 - x0) >= page.rect.width * 0.8 and (y1 - y0) >= page.rect.height * 0.8:
                xref = infos[0].get("xref") or 0
                if xref > 0:
                    embedded = doc.extract_image(xref)["image"]
    except Exception:  # noqa: BLE001  探测失败不影响回退渲染
        embedded = None
    if embedded:
        img = Image.open(BytesIO(embedded)).convert("RGB")
        img = _resize_to_max(img, max_long_edge)
        img.save(out, "PNG", compress_level=4)
        logger.info("PDF 第 %d 页 → %s (%dx%d, 抽内嵌原图)", page_no, out, *img.size)
        return out

    base = 300.0 / 72.0
    w_pt, h_pt = page.rect.width, page.rect.height
    long_at_base = max(w_pt, h_pt) * base
    scale = base if long_at_base <= max_long_edge else base * (max_long_edge / long_at_base)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    del pix
    if max(img.size) > max_long_edge:  # 浮点误差兜底
        img = _resize_to_max(img, max_long_edge)
    img.save(out, "PNG", compress_level=4)
    logger.info("PDF 第 %d 页 → %s (%dx%d)", page_no, out, *img.size)
    return out


def _pdf_range_to_images(
    pdf_path: str, out_dir: str, start: int, end: int, max_long_edge: int
) -> List[str]:
    """单 worker 连续转换一段页（每 worker 独立打开 PDF，fitz 对象不跨线程共享）。"""
    import fitz  # PyMuPDF

    paths: List[str] = []
    with fitz.open(pdf_path) as doc:
        for pi in range(start, min(end, doc.page_count)):
            paths.append(_pdf_page_to_png(doc, doc[pi], pi + 1, out_dir, max_long_edge))
    return paths


def pdf_to_images(
    pdf_path: str, out_dir: str, max_long_edge: int, workers: int = 1
) -> List[str]:
    import fitz  # PyMuPDF

    with fitz.open(pdf_path) as doc:
        total = doc.page_count
    if total == 0:
        return []
    if workers <= 1:
        return _pdf_range_to_images(pdf_path, out_dir, 0, total, max_long_edge)
    step = math.ceil(total / workers)
    ranges = [(s, min(s + step, total)) for s in range(0, total, step)]
    paths: List[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ranges)) as ex:
        futures = [
            ex.submit(_pdf_range_to_images, pdf_path, out_dir, s, e, max_long_edge)
            for s, e in ranges
        ]
        for fut in futures:
            paths.extend(fut.result())
    # 多 worker 完成顺序不定，按页码归位
    paths.sort(key=lambda p: int(os.path.basename(p).split("_")[1].split(".")[0]))
    return paths


def tif_to_images(
    tif_path: str, out_dir: str, max_long_edge: int
) -> List[str]:
    img = Image.open(tif_path)
    paths: List[str] = []
    page_no = 0
    while True:
        page_no += 1
        frame = img.copy()
        if frame.mode not in ("RGB", "L"):
            frame = frame.convert("RGB")
        frame = _resize_to_max(frame, max_long_edge)
        out = os.path.join(out_dir, f"page_{page_no:03d}.png")
        frame.save(out, "PNG", compress_level=4)
        paths.append(out)
        logger.info("TIF 第 %d 页 → %s", page_no, out)
        try:
            img.seek(img.tell() + 1)
        except EOFError:
            break
    return paths


def count_pages(src_path: str) -> int:
    """快速获取文档总页数（只读取元数据，不渲染，毫秒级）。"""
    ext = os.path.splitext(src_path)[1].lower()
    try:
        if ext == ".pdf":
            import fitz  # PyMuPDF

            with fitz.open(src_path) as doc:
                return doc.page_count
        if ext in {".tif", ".tiff"}:
            with Image.open(src_path) as img:
                return int(getattr(img, "n_frames", 1) or 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取页数失败 %s: %s", src_path, exc)
    return 1


def make_thumbnail(src: str, dst: str, max_edge: int = 240, quality: int = 75) -> str:
    """生成审核页「小图条」用的缩略图（JPEG，单页约 5~15KB）。

    背景：小图条显示尺寸仅 58×78px，原先直接整页加载 MinIO 原图
    （古籍透印噪声页实测 1572×2400 / 7.4MB），一两百页即数百 MB，载入极慢。
    缩略图长边默认 240px（留高清余量，兼顾 Retina 与放大查看），JPEG q75
    → 实测 10KB，较原图降 99.9%。

    仅在转图阶段生成，不影响 AI 识别所用原图（识别仍用完整分辨率）。
    """
    img = Image.open(src)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_edge, max_edge), Image.LANCZOS)
    if dst.lower().endswith((".jpg", ".jpeg")):
        img.save(dst, "JPEG", quality=quality, optimize=True)
    else:
        img.save(dst, "PNG", compress_level=6)
    return dst


def is_blank_image(image_path: str, dark_frac_threshold: float = 0.005) -> bool:
    """粗略判断一页扫描图是否几乎无内容（空白/纯色底），避免白调一次 VLM。

    对灰度图抽样统计「暗像素（灰度 <200）占比」：低于阈值视为无内容页。
    阈值取极保守值 0.5%——实测真实内容页即使极稀疏（浅字/稀疏世系表）
    暗像素占比也在 2% 以上，空白扫描页约为 0~0.2%，不会误杀有字页。
    """
    try:
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return False
        h, w = img.shape
        # 抽样步长：采样点控制在几千到几万，足够代表整页，开销毫秒级
        stride = max(4, min(h, w) // 200)
        sub = img[::stride, ::stride]
        frac = float((sub < 200).sum()) / float(sub.size)
        return frac < dark_frac_threshold
    except Exception:  # noqa: BLE001
        return False


def estimate_text_density(image_path: str) -> float:
    """估算整页「文字密度」（0~1），用于判断是否值得主动切块识别。

    原理（09-16 实测标定）：刻本/印刷正文页的文字是**密集的横向墨迹行**，
    把暗像素按行投影后，行方向的「活跃行占比」能稳定区分正文页与稀疏页：

      - 高密度正文页（满页竖排小字）：活跃行占比 ≈ 0.55~0.85
      - 稀疏页（落款/遗像/单行标题） ：活跃行占比 ≈ 0.02~0.20
      - 空白页                        ：≈ 0

    返回 0~1 的活跃行占比。失败时返回 0（视为不密集，走保守的整页链路）。
    开销：cv2 读图 + 一次投影，毫秒级，不调 VLM。
    """
    try:
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        h, w = img.shape
        # 抽样降维：宽度抽到 ~600px，够表征行分布且更快
        stride = max(1, w // 600)
        sub = img[:, ::stride]
        dark = (sub < 200)
        # 行活跃度：该行暗像素占比 > 1% 视为「有字行」
        row_frac = dark.mean(axis=1)
        active = float((row_frac > 0.01).sum()) / float(h) if h else 0.0
        return max(0.0, min(1.0, active))
    except Exception:  # noqa: BLE001
        return 0.0


def convert_to_images(
    src_path: str,
    out_dir: str,
    max_long_edge: int,
    workers: Optional[int] = None,
) -> List[str]:
    """文档 → 页面 PNG（PDF 多 worker 并行转图；TIF/单图按需处理）。"""
    ext = os.path.splitext(src_path)[1].lower()
    os.makedirs(out_dir, exist_ok=True)
    if ext == ".pdf":
        return pdf_to_images(src_path, out_dir, max_long_edge, workers or 1)
    if ext in {".tif", ".tiff"}:
        return tif_to_images(src_path, out_dir, max_long_edge)
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        img = Image.open(src_path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img = _resize_to_max(img, max_long_edge)
        out = os.path.join(out_dir, "page_001.png")
        img.save(out, "PNG", compress_level=4)
        return [out]
    raise ValueError(f"不支持的文件类型: {ext}")


def _deskew_angle(gray: np.ndarray, max_angle: float = 10.0) -> float:
    """估算整页扫描的倾斜角（°），仅纠偏小角度。

    注意兼容不同 OpenCV 版本 minAreaRect 的角度语义（旧版返回 [0,90]，
    新版返回 [-90,0)，且会随矩形宽高朝向变化）：
    - 先按「矩形接近竖直 ⇔ 角度≈±90°」把朝向折算回 0°，避免把竖排
      版面的长边方向误判成 90° 倾斜（竖排古籍/谱书页常见，会整页转歪）；
    - 只在 ±max_angle 内的小角度才纠偏，更大的角度说明是内容主方向
      或误检，一律跳过。
    """
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(binary)
    if coords is None:
        return 0.0
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90
    if abs(angle) > max_angle:
        return 0.0
    return -angle


def deskew_image(img: np.ndarray) -> np.ndarray:
    gray = img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    angle = _deskew_angle(gray)
    if abs(angle) < 0.4:
        return img
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def preprocess_image(
    src: str,
    dst: str,
    deskew: bool = True,
    denoise: bool = False,
    enhance: bool = True,
    binarize: bool = False,
) -> str:
    """OpenCV CPU 预处理：纠偏 + 降噪 + 对比度增强（CLAHE）+ 可选二值化。"""
    img = cv2.imread(src, cv2.IMREAD_UNCHANGED)
    if img is None:
        img = cv2.imread(src)
    if img is None:
        raise ValueError(f"无法读取图片: {src}")
    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    if deskew:
        img = deskew_image(img)
    if denoise:
        img = cv2.fastNlMeansDenoisingColored(img, None, 7, 7, 7, 21)
    if enhance:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_ch = clahe.apply(l_ch)
        lab = cv2.merge((l_ch, a_ch, b_ch))
        img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    if binarize:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
        )
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    cv2.imwrite(dst, img)
    return dst


def sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", name)
    return name[:200]
