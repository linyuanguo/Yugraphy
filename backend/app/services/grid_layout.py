# -*- coding: utf-8 -*-
"""世系格子页「版式切格」版式分析（生产模块，供 grid_extract 使用）。

来源：docs/内部技术笔记/grid_模型对比.md 所述切格实验的 grid_layout v2（两轮 6 页实测后迁入生产）。
  · 掩膜 = 严格红色(HSV 格线/吊线) ∪ 暗色(OTSU 黑字)
  · 横线双路检测取并集：投影法（闭运算接断点 + 横向开运算 + 行覆盖率）
    + HoughLinesP（容忍扫描倾斜）→ 互补，细线/微倾斜/断续线也能检到
  · 分带：相邻横线之间 = 一个「格带」（≈ 一条世系记录）
  · 带内切列：带内 ink 的 x 投影（覆盖率阈值，红线也算）找空白缝 → 文字列；
    再按「间距明显大于常态」断开 → 列组（≈ 一人的记录块）
  · 阅读顺序：格带上→下 × 列组右→左（竖排）

依赖 cv2/numpy 均为函数内局部 import，避免拖慢服务启动。
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

Cell = Tuple[int, int, int, int, int, int]  # x0, y0, x1, y1, band_idx, col_idx


def red_mask(img, s_min: int = 60, v_min: int = 60):
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    m = ((h <= 12) | (h >= 165)) & (s >= s_min) & (v >= v_min)
    return m.astype(np.uint8) * 255


def dark_mask(img):
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return bw


def group_coords(idx, gap: int = 8):
    """邻近坐标聚类 → [(中心, 起点, 终点)]。"""
    out = []
    if len(idx) == 0:
        return out
    s = p = int(idx[0])
    for v in idx[1:]:
        v = int(v)
        if v - p <= gap:
            p = v
        else:
            out.append(((s + p) // 2, s, p))
            s = p = v
    out.append(((s + p) // 2, s, p))
    return out


def h_lines_proj(mask, W: int, min_len_ratio=0.22, cov_ratio=0.22):
    import cv2
    import numpy as np

    m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 1), "uint8"))
    k = max(6, int(W * min_len_ratio))
    hor = cv2.morphologyEx(
        m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    )
    cov = hor.sum(axis=1) / 255.0
    ys = np.where(cov > W * cov_ratio)[0]
    return group_coords(ys, gap=10)


def h_lines_hough(mask, W: int):
    import cv2
    import numpy as np

    lines = cv2.HoughLinesP(
        mask, 1, np.pi / 720,
        threshold=max(20, int(W * 0.12)),
        minLineLength=max(40, int(W * 0.22)),
        maxLineGap=max(10, int(W * 0.02)),
    )
    if lines is None:
        return []
    ys = []
    for x1, y1, x2, y2 in lines[:, 0]:
        if abs(int(y2) - int(y1)) <= 8 and abs(int(x2) - int(x1)) >= W * 0.22:
            ys.append((int(y1) + int(y2)) // 2)
    ys.sort()
    return group_coords(np.array(ys), gap=12)


def merge_ys(a, b, gap=14):
    ys = sorted([c for c, _, _ in a] + [c for c, _, _ in b])
    if not ys:
        return []
    out = [ys[0]]
    for y in ys[1:]:
        if y - out[-1] > gap:
            out.append(y)
        else:
            out[-1] = (out[-1] + y) // 2
    return out


def body_x_range(mask, H: int, W: int):
    """找页面左右两条长竖线（版框/书口），正文 = 该 x 区间；找不到回退整页。"""
    import cv2

    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, int(H * 0.5))))
    ver = cv2.morphologyEx(mask, cv2.MORPH_OPEN, vk)
    cov = ver.sum(axis=0) / 255.0
    xs = group_coords((cov > H * 0.5).nonzero()[0], gap=10)
    if len(xs) >= 2:
        a, b = xs[0][0], xs[-1][0]
        if b - a > W * 0.5:
            return a, b
    return 0, W


def columns_in_band(mask, y0: int, y1: int, W: int, x_lo: int, x_hi: int,
                    gap_ratio=0.008, cov_ratio=0.10):
    """带内 x 投影找宽空白缝 → 逐「文字列」区间。

    用覆盖率（该 x 上墨像素占带高比例）而不是「>0」判有字：
    红色吊线、残点会让「>0」处处为真，切不出缝。
    """
    import numpy as np

    sub = mask[y0:y1, x_lo:x_hi]
    bh = max(1, sub.shape[0])
    cov = (sub > 0).sum(axis=0).astype(float) / bh
    ink = cov > cov_ratio
    min_gap = max(4, int(W * gap_ratio))
    cols = []
    i, n = 0, len(ink)
    seg_start = None
    while i < n:
        if ink[i]:
            if seg_start is None:
                seg_start = i
            i += 1
        else:
            j = i
            while j < n and not ink[j]:
                j += 1
            if seg_start is not None and j - i >= min_gap:
                cols.append((seg_start, i - 1))
                seg_start = None
            i = j
    if seg_start is not None:
        cols.append((seg_start, n - 1))
    out = []
    for (a, b) in cols:
        if b - a + 1 >= max(6, int(W * 0.02)):
            out.append((a + x_lo, b + x_lo))
    return out


def group_columns(cols, W: int, k=2.6, min_abs_ratio=0.045):
    """把逐「文字列」并成「格」：列间距明显大于常态间距处断开。

    阈值 = max(常态间距中位数 × k, 页宽 × min_abs_ratio)。
    过高会把一条记录切碎（实测 p266 曾把「長適以宅王氏」切成独立单元），
    故 k 取 2.6 且绝对值下限抬到 4.5% 页宽。
    """
    if len(cols) <= 1:
        return list(cols)
    gaps = [cols[i + 1][0] - cols[i][1] for i in range(len(cols) - 1)]
    med = sorted(gaps)[len(gaps) // 2]
    thr = max(med * k, W * min_abs_ratio)
    groups = [list(cols[0])]
    for i, g in enumerate(gaps):
        if g > thr:
            groups.append(list(cols[i + 1]))
        else:
            groups[-1][1] = cols[i + 1][1]
    return [(a, b) for a, b in groups]


def detect(img, debug: bool = False):
    """返回 (cells, bands, h_line_ys)。cell = (x0, y0, x1, y1, band_idx, col_idx)。"""
    import cv2

    H, W = img.shape[:2]
    m = cv2.bitwise_or(red_mask(img), dark_mask(img))
    ys = merge_ys(h_lines_proj(m, W), h_lines_hough(m, W))
    if debug:
        print(f"  横线 {len(ys)}: {ys}")

    # 去掉紧贴页面边缘的框线（书口/装订线），保留内部线
    inner = [y for y in ys if 0.02 * H < y < 0.985 * H]
    bounds = [0] + inner + [H]
    bands = []
    for i in range(len(bounds) - 1):
        y0, y1 = bounds[i], bounds[i + 1]
        if y1 - y0 < max(24, H * 0.02):
            continue
        bands.append((y0, y1))

    x_lo, x_hi = body_x_range(dark_mask(img), H, W)
    cells: List[Cell] = []
    for bi, (y0, y1) in enumerate(bands):
        # 切列用「红∪黑」掩膜：红线（吊线/框线）也是版式的一部分；
        # 若只看黑色，红线处会形成假空白缝，把一条记录误切成两半。
        cols = columns_in_band(m, y0, y1, W, x_lo, x_hi)
        groups = group_columns(cols, W)
        # 阅读顺序：竖排右→左，故 col 序号 0 = 本带最右的格
        groups = sorted(groups, key=lambda g: -g[0])
        if debug:
            print(f"  带{bi} y{y0}-{y1} 列 {len(cols)} → 格 {len(groups)}")
        if not groups:
            cells.append((x_lo, y0, x_hi, y1, bi, 0))
            continue
        for ci, (a, b) in enumerate(groups):
            cells.append((a, y0, b, y1, bi, ci))
    return cells, bands, ys


def ink_ratio(img, box: Sequence[int]) -> float:
    """单元内墨占比（用于丢掉页边留白等空单元）。"""
    x0, y0, x1, y1 = box[0], box[1], box[2], box[3]
    sub = img[y0:y1, x0:x1]
    if sub.size == 0:
        return 0.0
    return float((sub.mean(axis=2) < 160).mean())


def is_grid_page(n_bands: int, n_units: int, n_h_lines: int, min_bands: int,
                 min_units: int, max_units: int, min_h_lines: int) -> bool:
    """页型分流判据：格线页 = 有足够多**内部横线** + 切出足够多单元，且不至于碎到失控。

    判据来自两轮 6 页实测（横线数 = 去掉页边框线后的内部横线）：
      · 世系格页：p195 8带/12单元/8线、p235 5/13/6、p255 6/11/7、p266 6/11/7 → 命中
      · 正文页 p138：3带/4单元/**仅 2 条内部横线**（版心上下框，非格线）→ 不命中
      · p67：3带/3单元/2线 → 不命中（切了也没收益，实测 G 仅 164→169）
    故 **min_h_lines 是区分"真格线"与"只有版面框线"的关键**：仅靠带数/单元数
    会把正文页误判成格线页（正文被切成 3 块后质量下降）。
    max_units 兜底：切得太碎说明格线检测误判（会把正文切烂），宁可回退整页。
    """
    return (
        n_h_lines >= min_h_lines
        and n_bands >= min_bands
        and min_units <= n_units <= max_units
    )
