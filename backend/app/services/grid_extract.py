# -*- coding: utf-8 -*-
"""格线页「切格识别」链路（生产）：切格逐格 OCR → 文本结构化抽取 → 与整页同构输出。

为什么需要它（两轮 6 页 A/C/G/Gb 实测结论，见 docs/内部技术笔记/grid_模型对比.md）：
  · 世系格子页整页喂 VLM 会跨格横拼/跳行/丢字段（p235 整页仅 90 字 vs 切格 196 字）；
  · 切格后逐格 OCR 可把不同人的生/卒/配/葬各自归位，字数与字段命中全面领先；
  · 但正文页（序跋/家规等无格线页）切格有害（p138 崩到 18 字）→ 必须页型分流。

链路：
  1) grid_layout.detect 检横线 → 分带 → 带内切列 → 单元（上→下 × 右→左）
  2) 页型判定：带数/单元数达标才走本链路，否则返回 None（调用方回退整页 extract_page）
  3) 逐格调 VLM 纯 OCR（GRID_OCR_PROMPT），得到该格原文
  4) 全页原文按格序拼成 page_text，再调一次**纯文本** LLM 做结构化抽取
     （GRID_STRUCT_PROMPT）→ {persons, content, notes}
  5) 走与整页完全相同的 normalize_extraction 规整 → 下游 consolidate 无感

与整页链路的关系：本函数**只替代"看图"那一步**，输出结构与生产 extract_page 一致
（含 persons/relations/content），不做任何下游格式变更。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Dict, List, Optional, Sequence, Tuple

import httpx

from app.core.config import settings
from app.services import grid_layout
from app.services import vision_service

logger = logging.getLogger("genealogy.vision")

# ============ 逐格 OCR 提示词（纯认字，不做语义理解） ============
# 来源 ocr_ab 实验包 FUSED_OCR_PROMPT（fused-ocr-v2，两轮实测版），措辞由「栏」改为「格」。
GRID_OCR_PROMPT = """你是专业的古籍族谱视觉 OCR 引擎。你的任务是忠实识别图片中的原始文字，并保证版式归属正确。

【认字原则】
1. 只输出图中实际存在的字。严禁补字、严禁按语义改写、严禁总结/解释/翻译/公元换算。
2. 按"形"认字，不按"义"联想：繁体字必须照录原字形，严禁写成简体
   （「節」不可作「节」、「譜」不可作「谱」、「張」不可作「张」、「書」不可作「书」）。
3. 形近字按笔画判定，不因"读起来通顺"而换字。已实测易错对照，务必逐字看清再定：
   節/莭、葬/葵/藝、曰/日、己/已/巳、戌/戍/戊、光/未、午/干/牛、壬/王/玉、
   子/于/干、丙/兩、寅/黃、辰/晨、緒/續/績、道/首、塟/葬/墳。
   **特别注意：年号「道光」「光緒」中的「光」字极易被误认成「未」**（正确：「卒道光」「卒光緒」）。
4. 年号、干支、数字照录，不做任何换算或"合理化"。
5. 看不清且无法确认时用 □，绝不猜字。

【版式与顺序】
6. 竖排严格从上到下读本格；同一格内若有多列，按右→左列序，列间用换行分隔，严禁跨列拼接。
7. 本图是世系页里的**一个格**：只识别这一格内的文字，格内通常是一个人的生、卒、配、葬、
   子嗣、分金等字段，请按原版式逐字段照录。
8. 页眉、卷次、页码、「第X號」、年款等独立块：一律保持它在图中的原位置输出。

【输出格式】
9. 按原版式换行：原图一行输出一行，格内一个字段输出一行。
10. 原图没有标点就不要添加标点；原图本身有标点/句读则照录。
11. 不做人物抽取、不做人物合并、不做亲属关系推断。

只输出：
{
  "text": "原始文字（保留换行）",
  "confidence": 0.0
}
只输出 JSON。"""

# ============ 结构化抽取提示词（纯文本 → persons/content） ============
# 输入已是切格 OCR 得到的准确原文，本步只做「分条 + 抽人」，不再看图：
# 既省一次大图视觉 token，也避免模型边认字边理解导致的串行丢字。
GRID_STRUCT_PROMPT = """你是古籍族谱信息抽取助手。下面给出的是**某一页族谱已按版式切格 OCR 得到的原文**，
原文按版面顺序排列：格带从上到下、每带内从右到左，格与格之间用空行分隔。

===== 原文开始 =====
{page_text}
===== 原文结束 =====

请完成两件事：

一、把原文整理成 content（**照录成句**，不改写用字）：
1. 一个「格」（≈一个人的记录）一条 content：把该人的生、卒、配、葬、子嗣、分金等字段
   按原文顺序**连成完整句子**（句间用逗号/句号分隔），与人工点读的观感一致。
2. **严禁按原图逐行断成碎片**：「係德紹公次 / 子生於同治 / 戊辰年正月」这类半句一行
   不可用（人工审核已明确否决），必须连成「係德紹公次子，生於同治戊辰年正月…」。
3. 只做「连句 + 加标点」：原文用字一律照录，繁体严禁转简体；
   严禁补字、严禁省略、严禁改写、严禁干支/公元换算、严禁合并不同人的句子。
4. 单条不超 500 字，超长拆多条；条数不限，必须完整覆盖全部原文。
5. 页眉/卷次/页码/年款等独立块单独成一条，保持它在原文中的位置。

二、从原文中抽取 persons（人名与随行小注）：
1. name 只取姓名本体，去「公/翁/祖/世/號/行第/諱/嗣/繼」等称谓/前缀；同页同名不同人加「(二)(三)」。
2. 出嫁去向句（適/適配＋地名→人名）仅当含独立明确人名（如「梁術」）才建 person、gender=unknown；
   仅有地名/宅名（「適本地董宅」）或残缺（「長適□」）→ 只照录不建人。
3. 单纯「配某氏」只作所属者小注照录，不另建 person。
4. 姓名严禁拼接（禁止把「姓名＋某氏」揉成一个 name，如「李國藩吳氏」属捏造）。
5. 生卒日期只在原文同句同时写明「朝代帝号＋年号干支」时才换算 birth_year/death_year；
   只写干支无帝号 → 严禁补帝号、birth_year 留空，照录进 biography。
6. 无明确人名且无「生/卒/配/葬/墳/適」语义的碎片（「四月初一日」「兼德」等）不建人。
7. confidence 逐人独立判定：清晰 → 0.85~0.95；带「□」或小注模糊 → ≤0.6；
   猜填具体数字 → ≤0.4 并在 biography 注明「疑为补位」。严禁整页统一标同一个值。
8. biography 照录该人随行小注原文（≤120 字）。
9. **严禁把残片当人名**：「某公女」「某氏」「某女」「適某」「配某」的截段不是人名
   （如「葉女」「鳳公女」实为「…公女」残片，「小華」「一洋」这类无姓残字也多半是切分残片），
   只有完整独立的姓名（姓＋名，或明确的某氏全稱）才建 person，宁漏勿滥。
10. 严禁编造原文中没有的人名或文字。

三、notes：开头写类型词（世系页/功德名单页/正文页），再写疑点，最长 30 字。

四、绝对不输出 relations（亲属关系由后续整卷整理推断）。

只输出如下 JSON，不输出其他任何文字：
{{
  "persons": [{{"name": "姓名", "gender": "male/female/unknown", "confidence": 0.6, "birth_year": null, "death_year": null, "birth_place": null, "biography": "小注原文照录（≤120字）"}}],
  "content": ["段落一（≤500字）", "段落二…"],
  "notes": "世系页；某字疑似"
}}
"""


def _setting(name: str, default):
    """读配置；config 未同步时（滚动部署中间态）用默认值兜底，避免 import 期报错。"""
    return getattr(settings, name, default)


def _crop_pad_b64(img, box: Sequence[int], min_short: int = 256,
                  max_long: int = 1600, pad: int = 8, max_scale: float = 2.0) -> str:
    """裁一格 → 补白边 → 短边补到 min_short（同时长边不超过 max_long）→ PNG base64。

    小格直接送 VLM 会因分辨率过低丢笔画，故短边补到 min_short；补白边是让
    模型看到"纸边"，避免贴边字被当成噪声抹掉。

    **必须限制长边**：页眉/页脚这类"宽而扁"的带（如 1047×52）若只按短边放大到 256，
    长边会被放大到 5000+ px，视觉 token 暴涨 → 单次调用直接撞 VLM_TIMEOUT
    （实测 p255 第 0 格因此 180s 超时失败）。故放大系数取
    min(短边补足所需, 长边不超上限所需)，与整页预处理 MAX_IMAGE_LONG_EDGE 对齐。
    """
    import cv2

    x0, y0, x1, y1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    H, W = img.shape[:2]
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(W, x1 + pad), min(H, y1 + pad)
    sub = img[y0:y1, x0:x1]
    if sub.size == 0:
        return ""
    h, w = sub.shape[:2]
    short, long_ = min(h, w), max(h, w)
    scale = 1.0
    if short:
        scale = max(scale, min_short / float(short))
    # 放大上限：页眉/页脚这类扁带（如 1047×52）按短边补 256 要放大 ~5 倍，
    # 长边被推到 5000+ px，视觉 token 暴涨（实测单格 180s 超时失败）。
    # 字本身已有 40+ px 高，放大 2 倍足够，再大只增加 token 不增加信息。
    scale = min(scale, max_scale)
    if long_ and long_ * scale > max_long:
        scale = min(scale, max_long / float(long_))
    if abs(scale - 1.0) > 1e-3:
        sub = cv2.resize(sub, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        h, w = sub.shape[:2]
        short, long_ = min(h, w), max(h, w)
    if short and short < min_short:  # 极端细长条：短边两侧补白到 min_short
        need = (min_short - short) // 2 + 1
        if long_ + 2 * need > max_long:
            need = max(0, (max_long - long_) // 2)
        if need:
            sub = cv2.copyMakeBorder(
                sub,
                need if h >= w else 0, need if h >= w else 0,
                need if w >= h else 0, need if w >= h else 0,
                cv2.BORDER_CONSTANT, value=(255, 255, 255),
            )
    ok, buf = cv2.imencode(".png", sub)
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("utf-8")


async def _llm_text(prompt: str, max_tokens: Optional[int] = None) -> str:
    """纯文本对话调用（无图，关思考，temperature=0 保证可复现）。"""
    payload = {
        "model": settings.QWEN_MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens or settings.VISION_MAX_TOKENS,
        "repetition_penalty": settings.VISION_REPETITION_PENALTY,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Authorization": f"Bearer {settings.SGLANG_API_KEY}"}
    async with httpx.AsyncClient(timeout=settings.VLM_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.SGLANG_URL}/v1/chat/completions", json=payload, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_ocr_text(raw: str) -> str:
    """解析单格 OCR 输出；解析失败/空则回退去围栏后的原文。

    注意：模型对空白格返回 `{"text": ""}` 时不能把整段 JSON 当正文拼进结果
    （实测 p35 顶部空带曾漏出 ```json {...}）。
    """
    obj = None
    try:
        obj = vision_service.parse_json_from_llm(raw)
    except Exception:  # noqa: BLE001
        obj = None
    if isinstance(obj, dict) and "text" in obj:
        text = str(obj.get("text") or "").strip()
    else:
        text = (raw or "").strip()
        if text.lstrip().startswith(("```", "{", "[")):
            return ""
    # 空白/无字格：模型会输出一长串「□」占位（实测整格 300+ 个 □），
    # 若拼进 page_text 会污染后续结构化抽取（模型把 □ 当内容反复照录）。
    # 判定：□ 占比 > 60% 或去掉 □ 后无实质内容 → 视为空。
    if text:
        solid = text.replace("□", "").strip()
        if not solid or text.count("□") / max(1, len(text)) > 0.6:
            return ""
    return text


async def extract_page_grid_norm(
    image_path: str,
    hard_timeout: Optional[int] = None,
) -> Optional[Dict]:
    """格线页走「切格识别」；非格线页返回 None（调用方请回退整页 extract_page）。

    返回已 normalize 的结果（与 extract_page + normalize_extraction 同构），
    并带 `_grid=True` / `_grid_meta` 便于排查。任何环节抛异常由调用方兜底回退。
    """
    import cv2

    if not _setting("OCR_GRID_ENABLED", True):
        return None

    budget = hard_timeout or int(_setting("OCR_GRID_TIMEOUT", 420))
    img = cv2.imread(image_path)
    if img is None:
        return None

    cells, bands, ys = grid_layout.detect(img)
    H, W = img.shape[:2]
    min_ink = float(_setting("OCR_GRID_MIN_INK", 0.004))
    cells = [c for c in cells if grid_layout.ink_ratio(img, c) > min_ink]
    cells.sort(key=lambda c: (c[4], c[5]))

    # 内部横线数（去掉页边上下框线）：区分「真格线」与「只有版框的正文页」的关键指标
    n_h_lines = len([y for y in ys if 0.02 * H < y < 0.985 * H])
    if not grid_layout.is_grid_page(
        len(bands), len(cells), n_h_lines,
        int(_setting("OCR_GRID_MIN_BANDS", 3)),
        int(_setting("OCR_GRID_MIN_UNITS", 4)),
        int(_setting("OCR_GRID_MAX_UNITS", 16)),
        int(_setting("OCR_GRID_MIN_H_LINES", 4)),
    ):
        logger.info(
            "页型判定=非格线页（横线%d/带%d/单元%d），走整页链路: %s",
            n_h_lines, len(bands), len(cells), os.path.basename(image_path),
        )
        return None

    logger.info(
        "页型判定=格线页（横线%d/带%d/单元%d/%dx%d），走切格链路: %s",
        n_h_lines, len(bands), len(cells), W, H, os.path.basename(image_path),
    )

    unit_tokens = int(_setting("OCR_GRID_UNIT_MAX_TOKENS", 1024))
    unit_timeout = int(_setting("OCR_GRID_UNIT_TIMEOUT", 90))

    # 逐格 OCR **页内并发**：206 的 max_running_requests=4，串行跑 11 格要 40~70s，
    # 4 路并发后可压到 1/3（单页 75~123s → 约 30~45s）。上限由 206 槽位决定，不要超过 4。
    sem = asyncio.Semaphore(max(1, int(_setting("OCR_GRID_UNIT_CONCURRENCY", 4))))

    async def _ocr_unit(bi: int, ci: int, box: Sequence[int]) -> Dict:
        x0, y0, x1, y1 = box
        text = ""
        b64 = _crop_pad_b64(img, box)
        if b64:
            async with sem:
                try:
                    # 单格只做 OCR：max_tokens 封 1024（一格最多几百字）+ 单格 90s 硬超时。
                    # 不封顶时模糊/空白格会让模型一路生成到 16384 token（实测 180s 超时、
                    # 整页被拖到 410s），封顶后既快又能防复读。
                    raw = await asyncio.wait_for(
                        vision_service._call_vlm(
                            b64, prompt=GRID_OCR_PROMPT, max_tokens=unit_tokens
                        ),
                        timeout=unit_timeout,
                    )
                    text = _parse_ocr_text(raw)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "切格 %d.%d OCR 失败: %s: %s", bi, ci, type(exc).__name__, exc
                    )
        return {"band": bi, "col": ci, "box": [x0, y0, x1, y1], "text": text}

    async def _run() -> Dict:
        # gather 保序：结果仍按「带上→下 × 列右→左」排列
        per: List[Dict] = await asyncio.gather(
            *[_ocr_unit(bi, ci, (x0, y0, x1, y1))
              for (x0, y0, x1, y1, bi, ci) in cells]
        )

        page_text = "\n\n".join(p["text"] for p in per if p["text"])
        if not page_text.strip():
            raise RuntimeError("切格 OCR 全页无产出")

        raw2 = await _llm_text(
            GRID_STRUCT_PROMPT.format(page_text=page_text),
            max_tokens=int(_setting("OCR_GRID_STRUCT_MAX_TOKENS", 8192)),
        )
        vision_service._assert_no_repeat_loop(raw2)
        if len(raw2) > 25000:
            raise ValueError(f"结构化输出 {len(raw2)} 字符异常超长，疑似死循环")
        obj = vision_service.parse_json_from_llm(raw2)
        if not isinstance(obj, dict):
            raise ValueError("结构化输出非 JSON 对象")

        norm = vision_service.normalize_extraction(obj)
        notes = norm.get("notes") or ""
        norm["notes"] = ((notes + "；") if notes else "") + "切格识别"
        norm["_grid"] = True
        norm["_grid_meta"] = {
            "bands": len(bands), "units": len(cells), "h_lines": n_h_lines,
            "h_lines_all": len(ys),
            "ocr_units": len([p for p in per if p["text"]]),
        }
        return norm

    return await asyncio.wait_for(_run(), timeout=budget)
