"""调用 172.16.199.206 的 SGLang 多模态服务（Qwen3-VL）做扫描件信息提取。"""
import asyncio
import base64
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

import httpx

from app.core.config import settings
from app.utils import image_utils

logger = logging.getLogger("genealogy.vision")

# 识别提示词可在「系统设置 → 识别参数」覆盖（AppSetting.scan_vision_prompt），
# 留空/未配置时使用下方代码默认模板。修改后对之后执行的识别生效
# （新导入、单页补扫/整卷重扫），已识别页不会自动重跑。
VISION_PROMPT_KEY = "scan_vision_prompt"


# ============ 版式自适应：列边界对齐竖切（09-17 新增）============
# 背景与实测（14 页人工真 GT）：
#   规则版式（刻本正文/多栏/表格，6 页）整页 53.3% → 列边界对齐竖切 col3 **85.5%**
#     （+32.2pt，读序 96.2%→100%，耗时 19.1s→7.5s）；逐页全胜 +8.6~+54.5pt。
#   不规则版式（世系表，8 页）切列会崩（p21 −47.9pt）：小注是**无空白的密集竖排短列**，
#     列间没有可投影的边界，任何几何切分都会越界串栏。世系表保持整页。
# 与「无脑均分竖切」的本质区别：切点落在**检测到的列间空白**上，绝不切穿一列。
COL_TILE_MIN_QUALITY = 0.6      # 列检测可信度门槛（环境变量可覆盖）
COL_TILE_GROUPS = 3             # 合并成几组（每组一次 VLM 调用）


def _env_int(name: str, default: int) -> int:
    """读取整型环境变量，空串/非法值/0 一律回落默认值。

    ⚠ 必须这样读：`int(os.getenv(name, "0") or default)` 是**错的** ——
    环境变量未设置时 `getenv` 返回字符串 "0"，而 `"0" or 3` 里 "0" 是真值，
    结果恒为 0（实测导致 groups=0 → 12 列合并成 1 组 → 列切退化成整页，白跑）。
    """
    raw = os.getenv(name, "")
    try:
        v = int(str(raw).strip() or 0)
    except (TypeError, ValueError):
        v = 0
    return v if v > 0 else default


def _colgrid_enabled() -> bool:
    """列切是否启用。默认开（OCR_LAYOUT_MODE=auto）；设 whole 可一键关闭回到旧行为。"""
    return str(
        os.getenv("OCR_LAYOUT_MODE", getattr(settings, "OCR_LAYOUT_MODE", "auto")) or "auto"
    ).lower() != "whole"


def _detect_columns_sync(img, min_gap_ratio: float = 0.008, cov_ratio: float = 0.08):
    """在整页上做 x 投影，找出「文字列」区间。纯 PIL 手写，无 cv2 依赖。

    返回 [(x0, x1), ...]，按从左到右排列。
    """
    try:
        g = img.convert("L")
        gw, gh = g.size
        px = g.load()
        cov = [0] * gw
        for x in range(gw):
            c = 0
            for y in range(0, gh, 2):
                if px[x, y] < 170:
                    c += 1
            cov[x] = c / max(1, gh / 2)
        ink = [c > cov_ratio for c in cov]
        min_gap = max(3, int(gw * min_gap_ratio))
        cols, i, n, s = [], 0, len(ink), None
        while i < n:
            if ink[i]:
                if s is None:
                    s = i
                i += 1
            else:
                j = i
                while j < n and not ink[j]:
                    j += 1
                if s is not None and (j - i) >= min_gap:
                    cols.append((s, i - 1))
                    s = None
                i = j
        if s is not None:
            cols.append((s, n - 1))
        return [(a, b) for a, b in cols if (b - a + 1) >= max(5, int(gw * 0.012))]
    except Exception:  # noqa: BLE001
        return []


def _cols_quality_sync(cols, img_w: int) -> float:
    """列检测可信度 0~1（无 GT，纯几何）：列宽越均匀 / 列数越合理 / 覆盖率越高 → 越可信。

    ⚠ 注意：该判据在两批实测中**区间重叠**（规则 0.674~0.768，世系表 0.500~0.807），
    单靠它无法精确判型。故生产采用「类别优先，几何兜底」：
    先看模型 notes 的页型判断，拿不准时才用本分数决定是否切列。
    """
    if not cols or len(cols) < 3:
        return 0.0
    ws = [b - a + 1 for a, b in cols]
    mean = sum(ws) / len(ws)
    if mean <= 0:
        return 0.0
    var = sum((x - mean) ** 2 for x in ws) / len(ws)
    s_uniform = max(0.0, 1.0 - (var ** 0.5) / mean)
    n = len(cols)
    s_count = 1.0 if 5 <= n <= 25 else (0.5 if 3 <= n <= 40 else 0.2)
    s_cover = min(1.0, sum(ws) / max(1, img_w) * 1.2)
    return round(0.5 * s_uniform + 0.2 * s_count + 0.3 * s_cover, 3)


def _group_cols(cols, k: int):
    """把相邻列均分成 k 组（组内可能含多列），保持组间不切穿单列。

    ⚠ 修复（09-17 实测 bug）：旧实现用 `per = ceil(n/k)` 分块，当 n <= k 时会退化成
    **1 组**（整页宽度），等于没切——现象是日志里「列组1(x86~1051)」横跨整页。
    必须保证：n > k 时产出恰好 k 组；n <= k 时退化为「一列一组」（绝不合组）。
    """
    if not cols:
        return []
    n = len(cols)
    if n <= k:
        return [[c] for c in cols]      # 列数不比组数多 → 一列一组，绝不并成整页
    if k <= 1:
        return [list(cols)]
    # 按列数尽量均分成 k 组（余数摊到前几组）
    base, rem = divmod(n, k)
    out, i = [], 0
    for gi in range(k):
        take = base + (1 if gi < rem else 0)
        out.append(list(cols[i:i + take]))
        i += take
    return out


def classify_layout_sync(image_path: str, notes: str = "") -> str:
    """判断本页该走哪条识别路线，返回 'col_grid' 或 'whole'。

    策略（**类别优先，几何兜底**）：
    1. 模型 notes 已明确判为「世系页」→ whole（切列在实测中 6/8 页更差，最多 −47.9pt）；
    2. notes 判为「正文页/功德名单页」→ col_grid（实测 6/6 页更优）；
    3. 拿不准 → 用列检测几何质量判：达标走 col_grid，否则 whole。
    """
    mode = str(
        os.getenv("OCR_LAYOUT_MODE", getattr(settings, "OCR_LAYOUT_MODE", "auto")) or "auto"
    ).lower()
    if mode == "whole":
        return "whole"
    if mode == "col_grid":
        return "col_grid"
    n = notes or ""
    if "世系" in n:
        return "whole"
    if "正文" in n or "功德" in n or "名册" in n or "表" in n:
        return "col_grid"
    # 拿不准：几何判据兜底
    try:
        from PIL import Image  # noqa: PLC0415

        img = Image.open(image_path)
        img.thumbnail((1600, 1600))
        cols = _detect_columns_sync(img)
        q = _cols_quality_sync(cols, img.size[0])
        thr = float(os.getenv("OCR_COL_QUALITY_MIN") or COL_TILE_MIN_QUALITY)
        if len(cols) >= 3 and q >= thr:
            return "col_grid"
    except Exception as exc:  # noqa: BLE001
        logger.warning("版式判型失败（回落整页）: %s", exc)
    return "whole"


def get_configured_vision_prompt() -> Optional[str]:
    """返回管理员在系统设置里自定义的识别提示词；未配置则返回 None（用代码默认）。"""
    try:
        from app.core.database import SessionLocal
        from app.models.orm import AppSetting

        with SessionLocal() as db:
            row = db.query(AppSetting).filter(AppSetting.key == VISION_PROMPT_KEY).first()
            val = (row.value or "").strip() if row and row.value else ""
        return val or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取识别提示词配置失败（使用默认模板）: %s", exc)
        return None

VISION_PROMPT = """你是一个专业的古籍族谱多模态OCR与信息抽取助手。输入是一页族谱扫描图片，可能为手写体世系、印刷体表格、或纯图像/版画。

第一步：先判断本页类型（据此执行下面不同规则），并在 notes 开头写明类型词：
- 「世系页」：血缘人丁/行传正文（横排或竖排），页面常密集出现「世/代」等字样；
- 「功德名单页」：乐助/捐资/功德/芳名榜等非血缘登记表格；
- 「纯图页」：纯图/空白/无字版式；
- 「正文页」：其他谱书正文（源流、迁徙、传记、家规、祠祭、字辈、序跋、坟茔、凡例、艺文等）。

通用版式与阅读顺序规则（先于下面各类型细则执行）：
1. 先判断本页是横排还是竖排：页面主要文字列/行明显竖向排列 → 竖排；明显横向从左到右排列 → 横排。
2. 若无法明确判断、或本页无明显横排证据，一律默认按族谱常见竖排处理：从右到左逐列/逐栏读取，每列/每栏内从上到下。竖排时严禁从左到右横读，严禁把相邻两列文字并成一行。
   （经验：家谱正文/世系绝大多数是竖排；只有明确看到现代横排印刷或横向表格时才判为横排。拿不准就按竖排。）
3. 登记/名册/分存类版式（如宗谱分存记、房谱分存记、芳名榜、功德册）往往每栏内每行格式高度重复（如「地名 + 一部/一本/若干圆」），遇到这种版式必须逐栏、逐行完整照录：每一行独立一条 content，禁止因句式重复而省略、合并或只取前几条；同一句式重复行必须一条不少地输出。

一、功德名单页（多栏表格）规则：
1. 逐栏独立读取计数：按版面从右到左（或表格题名所示顺序）一栏一栏读，栏内从上到下，
   相邻栏之间文字严禁跨栏错位/串行。
2. 严禁跨栏同名合并去重：同一姓名出现在不同栏（不同笔/不同捐款）必须照录为多行；
   persons 数组逐人列出，同页同名不同人一律加「(二)(三)…」区分，禁止合并成一行。
3. 金额与备注严格分列照录：
   - 金额列逐字照录原文（壹貳參肆伍陸柒捌玖拾佰仟萬億兩圓及「零」，以及「不計」「隨緣」等字样），
     严禁改写为阿拉伯数字、严禁补全、严禁四舍五入；
   - 备注/事由（如「樂助道路」「石獅」「修祠」「合家」）单独照录成独立部分/括注，
     绝不并入金额串、禁省略；
   - 禁止把整栏/整页归纳成「共 X 名 計 X 圓」之类的汇总行。
4. 名单中的姓名进 persons（gender=unknown），金额与备注只照录进 content，严禁写进 name/biography。
5. content 按栏逐行照录原文（姓名、金额、备注以顿号/空格分隔、不得把备注拼进金额），一栏一段。

二、世系页（竖排为主）规则：
1. 阅读顺序：严格从右到左逐列读取，每列从上到下读完整一列，再转其左侧下一列；
   相邻两列的正文/小注/人名严禁跨列拼接、严禁把两列并排横读，行与行的归属不得错位。
2. content 以「列」为单位组织：多列版面请一列至少一条 content（列内较长再按语义拆多条），
   严禁把一列的开头与另一列的结尾拼成同一条——按列切条能最大限度避免串列、倒序。
3. 行传短句化：一人的行传按语义拆成多条独立短句逐句照录——生、卒、配、葬、墳、附葬、
   適、坐向分金各成短句，句间以标点分隔；严禁把数句搅成一长句、严禁跨人并句、严禁跨列补字。
3. 生卒日期四格照录（最高优先级，严禁臆造）：日期按「{年}＋{月}＋{日}＋{時}」四格顺序照录
   （如「光緒丁丑年四月廿八日未時」「庚寅年十月廿八日未時」）：
   - 年、月、日、時逐格照录原文；任一格原字模糊/破损/褪色/只剩残形读不准 → 该格单独写「□」
     （如「□日」「□時」），严禁填入任何具体数字、严禁用常见干支凑格、严禁挪用同页他人日期、
     严禁把残字脑补成别的字、严禁看残留笔划猜全数字；
   - **矛盾自检（强制）**：若你在 notes 里写「X 日期疑缺/不清/存疑/缺干支/缺日」等
     描述，则该日期里相应位置必须已用「□」占位，禁止出现「一边声称存疑、一边仍输出具体
     数字」（例如写「卒於道光乙未月廿五日」又注「日期疑缺」＝臆造，应改「卒於道光乙未年
     □月□日□時」这类带 □ 写法，宁多留空不猜数字）；
   - 公元换算仅限同句同时写明「朝代帝号＋年号干支」时（如「生於光緒辛丑」→ birth_year=1901）；
     只写干支而页面未署帝号（如仅「辛丑年生」）→ 严禁补帝号（禁扩为「光緒辛丑」）、
     严禁推演换算，只照录进 biography、birth_year 留空。
4. 坟地方位句式：记作「葬{山}山{向}向」或「坐{山}{向}兼{分金}」等；任何一项缺失/看不清，
   对应位置写「□」，即「□山□向□分金」，占位符不可省略、不许换词搪塞。
5. 人名提取：按列读序把本页出现的所有人名（含仅有名字、无任何小注者）逐一提取进 persons：
   - name 只取姓名本体，去「公/翁/祖/世/號/行第/諱/嗣/繼」等称谓/前缀（「賢翁」「紹德公嗣」
     之类称呼只作小注归属，不拆进名字）；
   - 同页同名不同人加「(二)(三)」；
   - 出嫁去向句（形如「長女適白灣煙墩腳梁術」「次適□」，即「適/適配＋地名→人名」）：
     仅当句中确含独立明确人名（姓＋名或某氏，如「梁術」）时，才把该人名提取为独立
     person、gender=unknown，biography 照录去向小注；
     若仅有地名/宅名（如「適本地董宅」）或残缺（「長適□」）→ 只照录文字、绝不建人，
     严禁把地名/宅名/単姓截字当人名。
   - 单纯「配某氏/配林氏」等配偶记载（无地名去向、非獨立名諱）只作所属者小注照录，
     不另行提取 person。
   - 配偶/婚嫁记载按原文断句照录，姓名严禁拼接：若原文是不同对象/不同句（如「配李國藩」
     与「吳氏」分属不同句/不同人），各归各句照录；严禁把「姓名＋某氏」「適對象＋生母某氏」
     揉成一个 name（name 形如「李國藩吳氏」即属捏造）；name 只能放本人名讳或句中明确独立的
     人名本身，绝不把一截行文当名字。
   - 配偶自带的小注/生卒（如「配李國藩」之后紧接「吳氏，生於乾隆乙丑年四月十三日辰時」）
     归属该配偶自己、独立成句照录，严禁并入前一行主人（漢昌 等）的小注，更严禁把两处
     不同人的日期粘成一串（「配李國藩吳氏，生於乾隆乙丑年四月十三日辰時」并入主人小注
     即属错位拼接，须断句归位）。
6. 风水/坐向/分金/山向类术语独立成短句（按第 4 条句式），严禁并入生卒小注、严禁拆进年份
   字段；子嗣句（如「生子三、女一」）单独成短句，绝不与坟地坐向句粘连成「坐乙向辛生子三女」
   这类畸形串。
7. 残句过滤：能进 persons 的必须是**明确人名**（本人名讳，或婚嫁/配偶句里独立完整的人名）。
   无明确人名且无「生/卒/配/葬/墳/適」等语义的行文碎片（如「四月初一日」「西□□一」、
   「兼德」「兼承」「坐向辛生子三女」）严禁提取为 person、也不单独断成 content 段落，
   按上下文并入所属小注完整照录；拿不准是否人名时宁可不建人（漏名由补漏复查/人工兜住）。

三、人名与置信度（所有页通用）：
1. confidence 量级（严格按条、逐人独立判定，严禁虚高；严禁偷懒把整页人名统一标成同一个值，
   尤其严禁整页一律 0.4——清晰的人必须给 0.85~0.95，模糊的人单独压，互不影响）：
   - 字迹清晰、笔划完整、毫无疑点的照录 → 0.85~0.95（最高不超过 0.95）；
   - 姓名或随行小注中带「□」占位，或该人名在 notes 被点名 疑似/模糊/不清/磨损/褪色/
     殘缺 → 最高 ≤0.6；
   - 读不准却仍按猜测填入具体数字（含阿拉伯数字/自行补全年份）→ ≤0.4，
     且须在该人名 biography 与 notes 注明「疑为补位」；
   - 备注只笼统写「部分字跡模糊」未点名时，只把真正带□/确实模糊的那几个人压 ≤0.6，
     其余清晰人名照常 0.85~0.95。
2. 整姓名无法辨认（无字形线索）→ 不编造名字，在 notes 里注明该处疑似；
   拿不准的字须在 notes 注明是哪个人名/字（如「第X欄董氏疑」）。

四、暗字/磨损/残损区：逐字判读，禁止整块跳过；拿不准也照录最可能字形并注 notes；
完全无字形线索的字符在 content 中该位置用 □ 占位，严禁凭上下文猜字。

五、繁体原字：人名、地名、金额、正文一律按原字形照录，禁止转简体。

六、content 照录整页可读文字（任何有字页不得为空；条目不带类型/标题，只保留纯文本）：
- 段落类正文（源流/迁徙/家规家训/凡例/传记/艺文/祠祭/字辈/序跋/坟茔等）整段照录不改写；
- 世系行文页 → 行文与小注按列序完整照录、不省略；功德名单页 → 见「一」；
- 一条不超 500 字，超长拆多条；条数不限，必须完整照录所有可读文字，禁止因内容重复、行数多或句式相似而省略、合并或只取前几条；
- 多栏/多列版面按版面实际分栏逐栏独立读取，严禁把不同栏/列的残字串成一句、严禁跨栏错位拼接；
- 页边/书口/版心/栏外/鱼尾等处的可读文字（如「共和戊午年修」等刻印题署、页码、堂号、卷题）
  属本页可读文字，照录进 content，严禁漏掉；
- 仅当整页无任何可读文字（纯图/空白/无字版式）content 才给空数组 []。

七、notes：开头先写类型词（世系页/功德名单页/纯图页/正文页），再写疑点
（如「第2欄某字疑似」「某人名疑」），最长 30 字；无疑点可只写类型词。

八、绝对不编造内容；绝对不输出 relations（亲属关系由后续整卷整理推断，本步不做）。

九、严格只输出如下 JSON，不输出任何其他文字：
{
  "persons": [{"name": "姓名", "gender": "male/female/unknown", "confidence": 0.6, "birth_year": null, "death_year": null, "birth_place": null, "biography": "小注原文照录（≤120字）"}],
  "content": ["整页原文照录段落一（≤500字）", "段落二…"],
  "notes": "世系页；某字疑似"
}
"""

# 第二轮「世系补漏复查」专用提示词：整页识别成功后对世系页的横向分块放大复查。
# 只抓「漏人」（首轮整页可能漏读的人名），正文照录仍以首轮整页结果为准；
# 因此 output 只要求 persons，content 可省略（不重复照录全文），控制复查耗时。
# 放大图更易编造，故防幻觉要求更严。
VISION_VERIFY_PROMPT = """你在对一张族谱扫描页的局部（放大图）做第二轮「补漏复查」。图内通常是一段竖排世系谱文，也可能是多栏名单的一部分。

要求：
1. 看图内实际版面读取：竖排从右到左逐列、列内从上到下；横排从左到右；多栏先右栏后左栏。
   严禁跨列拼接文字，严禁凭印象输出图里没有的字。
2. 找出并输出图内出现的**一切人名**，尽量一个不漏（漏报是本轮最大失误；已见过的人名照常输出，不做去重决策）：
   - name 只取姓名本体，去「公/翁/祖/世/號/行第/諱/嗣/繼」称谓/前缀；同页同名不同人加「(二)(三)」；
   - 出嫁去向句（適/適配＋地名→人名）若出现明确人名 → 提取为 person、gender=unknown；
     僅地名/宅名或殘缺則只照录不建人（严禁把地名/宅名当人名）；單純「配某氏」只照录不另建；
   - 生僻/模糊/疑似的人名仍输出最可能字形，confidence 必须压到 ≤0.6（一般 0.3~0.5）；
   - 只有字迹清晰、毫无疑点的姓名才允许 0.85~0.95（不超过 0.95）；
   - 姓名或随行小注带「□」占位 → confidence ≤0.6；
   - 按猜测填入具体数字 → ≤0.4，并注明「疑为补位」；
   - 严禁偷懒把整块/整页人名统一标成同一数值（尤其严禁整页一律 0.4），逐人独立判定；
   - 完全无法辨认、毫无字形线索的姓名不要编造，也不许给高置信虚高。
3. 每个姓名如有生卒/小注短句随行出现，一并照录（原字不换算干支、不补帝号；日期按年月日時
   四格照录，模糊格写「□」，严禁挪用相邻人名日期、严禁猜测填具体数字）。
4. 能输出的必须是明确人名：无明确人名且无生卒语义的碎片（「兼德」「坐向辛生子三女」等）
   不建人、只照录并入上下文；配偶/婚嫁姓名严禁拼接（禁止把「姓名＋某氏」揉成一个 name）。
5. 严禁编造图内不存在的人名或文字。
6. 只输出如下 JSON，不输出其他任何文字（content 可省略）：
{"persons": [{"name": "", "gender": "male/female/unknown", "confidence": 0.6, "birth_year": null, "death_year": null, "birth_place": null, "biography": ""}], "content": [], "notes": ""}
"""

# 谱书文字内容类型白名单（content_entries.type：illustration/人工补录/历史数据用；
# AI 识别输出已不再携带 type——正文整页以通用值入库，见 import_service.sync_task_entries）
ENTRY_TYPES = (
    "源流",
    "迁徙",
    "家规家训",
    "凡例",
    "传记",
    "艺文",
    "祠祭",
    "字辈",
    "序跋",
    "坟茔",
    "世系",
    "功德名单",
    "插图",
    "其他",
)

MAX_ENTRIES_PER_PAGE = 100
MAX_ENTRY_TITLE = 80
MAX_ENTRY_TEXT = 1600


def _assert_no_repeat_loop(content: str) -> None:
    """结构级死循环熔断（优先于总长度熔断）。

    用「人物块指纹」检测整段复读：把 JSON 里每个 {"name":..,"gender":..} 块裁成指纹，
    同一指纹「相邻连续重复 ≥ VISION_REPEAT_BLOCK」或「累计出现 ≥ 2×该值」即判死循环。
    旧 p241 死循环输出 40605 字符全是同一个「董翁」对象——rep penalty 抑制后仍偶发的
    长名单/透印页可在内容还没涨到 25000 总长阈值前就提前拦下，少白占 206 槽位。
    真实名单页的同页同名最多几例（带(二)），不会误伤；全非结构化输出时无指纹、走总长熔断。
    """
    threshold = max(2, int(settings.VISION_REPEAT_BLOCK))
    segs = re.findall(r'"name"\s*:\s*"[^"]{1,24}"\s*,\s*"gender"\s*:\s*"[^"]{1,16}"', content)
    if not segs:
        return
    run = 1
    for prev, cur in zip(segs, segs[1:]):
        run = run + 1 if cur == prev else 1
        if run >= threshold:
            raise ValueError(
                f"输出结构重复（同一人物块连续 {run} 次），疑似重复生成死循环"
            )
    from collections import Counter  # noqa: PLC0415

    top = Counter(segs).most_common(1)[0]
    if top[1] >= threshold * 2:
        raise ValueError(
            f"输出结构重复（同一人物块累计 {top[1]} 次），疑似重复生成死循环"
        )


def parse_json_from_llm(content: str) -> Dict:
    """处理 ```json 包裹等模型常见输出格式。

    注意：原实现用闭合围栏正则匹配；模型输出被 max_tokens 截断时常只有开头
    ```json、没有结尾 ```，正则匹配不到 → 整段无法解析（表现为 char 0 报错）。
    现改为只剥开头围栏标记 + 尾部残余围栏，再截取 {…} 区间，兼容截断输出。
    """
    text = content.strip()
    # 剥掉开头围栏标记（``` 或 ```json），不要求存在闭合围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    # 剥掉尾部残余围栏（若有）
    text = re.sub(r"```\s*$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        v = json.loads(text)
    except json.JSONDecodeError as exc:
        # 容错修复（09-17）：模型常漏写字段间的逗号 / 多写尾逗号，导致整块被判为坏 JSON
        #   （实测 p23「"content": [...] "notes": ...」缺一个逗号 → 整组识别白费）。
        #   这些都是**标点级**小错，字符本身是好的，修一下即可全额回收。
        fixed = _repair_json_text(text)
        if fixed is not None:
            logger.warning("LLM 输出 JSON 有标点缺陷，已容错修复后解析成功")
            return fixed
        logger.error("LLM 输出无法解析为 JSON: %s", text[:500])
        raise ValueError(f"模型输出不是合法 JSON: {exc}") from exc
    if not isinstance(v, dict):
        # 09-18 实测（ec901f31d23e p404）：模型偶尔退化成顶层数组输出（只吐段落数组/
        # 空数组等），json.loads 成功返回 list → 下游 normalize_extraction 调 .get
        # 报 'list' object has no attribute 'get' 且发生在重试/分块兜底之外 → 整页白失败。
        # 按坏输出处理 → 抛出后由调用方走重试 / 重识别兜底（长超时+分块）。
        logger.error("LLM 输出顶层不是对象(%s): %s", type(v).__name__, text[:200])
        raise ValueError(f"模型输出顶层不是对象（{type(v).__name__}）")
    return v


def _repair_json_text(text: str) -> Optional[Dict]:
    """修复模型输出里**纯标点级**的 JSON 缺陷；修不好返回 None（不猜内容）。

    处理三类（均为实测常见）：
      1. 对象/数组元素之间漏逗号（`"a" "b"` → `"a", "b"`；`} {` → `}, {`）
      2. 尾随逗号（`,"}` / `,]`）
      3. 单引号当字符串定界符（`'a'` → `"a"`）
    只在「修复后能解析成功」时才返回，否则判为真坏。
    """
    import re as _re  # noqa: PLC0415

    for cand in (
        # 1) 漏逗号：引号/括号之后紧跟引号或括号
        _re.sub(r'(["\}\]\d])\s*\n?\s*(")', r"\1, \2", text),
        # 2) 尾随逗号
        _re.sub(r",\s*([}\]])", r"\1", _re.sub(r'(["\}\]\d])\s*\n?\s*(")', r"\1, \2", text)),
        # 3) 尾随逗号（单独）
        _re.sub(r",\s*([}\]])", r"\1", text),
        # 4) 单引号定界
        text.replace("'", '"'),
    ):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except Exception:  # noqa: BLE001
            continue
    return None


def image_file_to_b64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def _call_vlm(
    b64: str, prompt: str = VISION_PROMPT, max_tokens: Optional[int] = None
) -> str:
    payload = {
        "model": settings.QWEN_MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        # 贪心解码（temperature=0）：谱页提取是感知+结构化任务，不需要采样多样性；
        # 0.1 时同页多次识别结果 token 级波动（漏字/错字随机），批量与单页观感差异
        # 主要来自此。改 0 后结果可复现、更稳，批量/单页表现一致。
        "temperature": 0,
        "max_tokens": max_tokens or settings.VISION_MAX_TOKENS,
        # 抑制「同一对象无限重复」死循环：透印/噪声页会让模型反复输出同一姓名直到
        # max_tokens 截断（实测输出 40605 字符 / 338s），既失败又白占 206 槽位。
        # 只惩罚高度重复 token，正常谱页几乎不受影响（1.0 = 关闭，见 VISION_REPETITION_PENALTY）。
        "repetition_penalty": settings.VISION_REPETITION_PENALTY,
        # qwen3-vl 服务端默认开启思考(reasoning)模式：会先输出大段 reasoning_content，
        # 可能吃光 max_tokens 导致 content 为空/截断（表现为连续「JSON 解析失败」重试，
        # 每页白白多跑 2~3 遍），且推理 token 让单页明显变慢。
        # 谱页提取属感知+结构化任务、不需要长推理，显式关闭思考。
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Authorization": f"Bearer {settings.SGLANG_API_KEY}"}
    async with httpx.AsyncClient(timeout=settings.VLM_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.SGLANG_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def extract_page(
    image_path: str,
    retries: int = 1,
    hard_timeout: Optional[int] = None,
    prompt_override: Optional[str] = None,
    auto_verify: bool = True,
) -> Dict:
    """把一页扫描件图片交给多模态模型，提取人物与关系。失败自动重试。

    使用系统设置里自定义的识别提示词（见 get_configured_vision_prompt），
    未配置时退回代码内置默认 VISION_PROMPT。
    近空白/无内容页先本机预检直接跳过（不调 AI），避免白等一次 206 调用；
    重试上限 1（总 2 次尝试）——还失败就由上层记失败页跳过，可后续补扫，
    不再让坏页反复重试拖住整本进度（此前 3 次重试 × 206 慢响应 = 单页卡几分钟）。

    整页识别（含重试）用 asyncio.wait_for 硬限超时秒：
    VLM_TIMEOUT 只是 httpx 的 read 超时，206 慢速滴答返回/挂起时会不断重置计时、
    单页可能卡远超 60s 甚至挂死；而逐页是滑动窗口（默认并发 1），一页卡住=整本卡住。
    此硬超时保证顽固页最多卡 N 秒即抛错，由上层记失败跳过、其余页继续。

    hard_timeout：可覆盖默认 VLM_PAGE_TIMEOUT。审核端「重新识别本页/批量重识别」
    主动重试时走更长超时（settings.REDO_PAGE_TIMEOUT），导入流水线保持原值不动。

    prompt_override：覆盖识别提示词（默认为系统设置/内置模板）；auto_verify：是否启用
    整页成功后的自动复查（空结果分块放大补扫）。两者默认值保持生产链路不变，仅对比采集
    等旁路场景使用。
    """
    budget = hard_timeout or settings.VLM_PAGE_TIMEOUT
    if image_utils.is_blank_image(image_path):
        logger.info(
            "页面近乎空白/无文字内容，跳过 AI 识别: %s", os.path.basename(image_path)
        )
        return {
            "persons": [],
            "relations": [],
            "content": [],
            "notes": "纯图页；近空白/无内容页，已自动跳过（未调用 AI）",
        }
    prompt = prompt_override or get_configured_vision_prompt() or VISION_PROMPT
    b64 = image_file_to_b64(image_path)

    async def _do_extract() -> Dict:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                content = await _call_vlm(b64, prompt=prompt)
                # 熔断：透印/噪声严重的页会让模型陷入「同一对象无限重复」死循环
                # （实测 J144-003-003-002 第241页输出 40605 字符、耗时 334s，
                # 远超 VLM_PAGE_TIMEOUT=180s 硬超时，白占 206 并发槽）。
                # 正常页 JSON 最长约 1.5 万字符（长名单/功德碑页），阈值 25000
                # 只拦死循环、不误伤长页；判败后走重试，仍失败则记失败页跳过。
                # 结构级重复熔断优先于总长：同一人物块复读无需等到 25000 字符。
                _assert_no_repeat_loop(content)
                if len(content) > 25000:
                    raise ValueError(
                        f"输出 {len(content)} 字符异常超长，疑似重复生成死循环"
                    )
                result = parse_json_from_llm(content)
                if auto_verify:
                    return await _maybe_auto_verify(image_path, result, prompt, budget)
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "VLM 调用第 %d 次失败: %s", attempt + 1, exc
                )
                if attempt < retries:
                    await asyncio.sleep(2 * (attempt + 1))
        # 带上异常类型：httpx 超时异常 str 为空（历史上失败页 error 只剩「多模态识别失败: 」，
        # 无法区分是超时还是输出熔断/坏 JSON），带类型后可直接在审核页定位根因。
        raise RuntimeError(f"多模态识别失败: {type(last_exc).__name__}: {last_exc}")

    try:
        return await asyncio.wait_for(_do_extract(), timeout=budget)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"多模态识别超时（单页 >{budget}s），已跳过本页"
        )


# ============ 审核端重识别：整页失败后「横向切块」兜底 ============
# 背景：透印/噪声严重的页会让 Qwen 整页死循环/超时（如 J144-003-003-002 第241页
# 整页输出 4 万字符仍停不下来）。整页重试加长超时仍失败时，把页面切成 N 条横向带
# （相邻带重叠 8% 防切行漏字），每带单独识别。切块不能消除透印噪声本身，但能把每块
# 的视觉 token 与待解对象减到约 1/N：模型上下文小 → 不易整页死循环，块失败也可逐块
# 容错。竖排长段落跨带会被拆成上下两段（读序略乱、质量约同 OCR 草稿级），因此只作为
# 兜底，整页能成功就绝不走分块。

async def redo_extract_page(
    image_path: str,
    hard_timeout: Optional[int] = None,
) -> Dict:
    """重识别主入口（导入流水线兜底与审核端「重新识别本页」共用，返回已 normalize 的结果）：

    1. 整页用更长超时（REDO_PAGE_TIMEOUT，默认 300s）识别，成功即返回；
    2. 整页失败（超时/熔断/坏 JSON）且 REDO_TILED=True 时，自动横向分块兜底：
       各块成功即合并返回（notes 注明「分块识别」）；
    3. 常规分块（默认 3 带）仍全失败时，再自动「加密分块」二次兜底
       （REDO_TILED_RETRY_STRIPS，默认 6 带）——块更小更不易死循环。
       仅在常规分块全败时才用：竖排长段落会被切得更碎，读序更乱。
    """
    budget = hard_timeout or settings.REDO_PAGE_TIMEOUT
    raw = None
    try:
        raw = await extract_page(image_path, hard_timeout=budget)
    except Exception as exc:  # noqa: BLE001
        logger.warning("整页识别失败，尝试版式自适应兜底: %s", exc)
    if raw is not None:
        norm = normalize_extraction(raw)
        # ★版式自适应：整页成功后，若判为「规则版式」则改用列边界对齐竖切重跑一次。
        #   实测规则版式整页 53.3% → 列切 85.5%（+32.2pt）；世系表切列会崩故不切。
        if _colgrid_enabled():
            layout = classify_layout_sync(image_path, str(norm.get("notes") or ""))
            if layout == "col_grid":
                try:
                    alt = await extract_page_colgrid_norm(
                        image_path,
                        hard_timeout=budget,
                        groups=_env_int("OCR_COL_TILE_GROUPS", COL_TILE_GROUPS),
                        max_entries=settings.REDO_TILED_MAX_ENTRIES,
                    )
                    if alt.get("content") or alt.get("persons"):
                        alt["_redo_tiled"] = True
                        alt["_layout"] = "col_grid"
                        logger.info("版式自适应：判定规则版式，已采用列边界对齐竖切")
                        return alt
                except Exception as exc:  # noqa: BLE001
                    logger.warning("列切识别失败，回落到整页结果: %s", exc)
        norm["_redo_tiled"] = False
        norm["_layout"] = "whole"
        return norm
    if not settings.REDO_TILED:
        raise RuntimeError("整页识别失败且未启用分块兜底")

    async def _tiled(strips: int, vertical: bool = False) -> Dict:
        return await extract_page_tiled_norm(
            image_path,
            hard_timeout=budget,
            strips=strips,
            overlap_frac=settings.REDO_TILED_OVERLAP,
            max_entries=settings.REDO_TILED_MAX_ENTRIES,
            vertical=vertical,
        )

    # 竖排古籍（本项目绝大多数）：按列切竖条比横向切带读序更准；
    # 若纵向切条整页无产出（可能是横排版面），自动回退横向切带再兜一次
    vertical = (
        str(getattr(settings, "REDO_TILED_DIRECTION", "horizontal") or "horizontal").lower()
        == "vertical"
    )
    strips = (
        settings.REDO_TILED_VERTICAL_STRIPS if vertical else settings.REDO_TILED_STRIPS
    )
    retry = settings.REDO_TILED_RETRY_STRIPS
    if vertical:
        try:
            norm = await _tiled(strips, vertical=True)
            norm["_redo_tiled"] = True
            if norm.get("persons") or norm.get("content"):
                return norm
            logger.warning("纵向切条兜底无产出，疑似横排版面，回退横向切带")
        except Exception as exc:  # noqa: BLE001
            logger.warning("纵向切条兜底失败，回退横向切带: %s", exc)
        strips = settings.REDO_TILED_STRIPS
    try:
        norm = await _tiled(strips)
        norm["_redo_tiled"] = True
        return norm
    except Exception as exc:  # noqa: BLE001
        if not retry or retry <= strips:
            raise
        logger.warning(
            "分块兜底 %d 带仍全部失败，转加密分块 %d 带再试: %s",
            strips, retry, exc,
        )
    norm = await _tiled(retry)
    norm["_redo_tiled"] = True
    return norm


async def extract_page_colgrid_norm(
    image_path: str,
    hard_timeout: Optional[int] = None,
    groups: int = COL_TILE_GROUPS,
    max_entries: int = 24,
    prompt_override: Optional[str] = None,
) -> Dict:
    """**列边界对齐竖切**识别（09-17 新增，规则版式专用）。

    与 extract_page_tiled_norm(vertical=True) 的本质区别：
      后者按**像素宽度均分**，会切穿文字列（实测世系表条字数 [28,59,110,150] 极不均，
      条0 抄走邻栏表头）；本函数切点落在**检测到的列间空白**上，绝不切穿一列。

    实测（6 页规则版式）：整页 53.3% → 本方案 **85.5%**（+32.2pt），读序 100%，7.5s/页。
    调用顺序：竖排从右到左逐组读取（最右组最先）。
    """
    from PIL import Image  # noqa: PLC0415

    import tempfile

    budget = hard_timeout or settings.REDO_PAGE_TIMEOUT
    prompt = prompt_override or get_configured_vision_prompt() or VISION_PROMPT
    # 时间预算（09-17 修）：列切单组内容远少于整页，不该按整页 budget 等。
    #   旧实现用整页 budget → 某组超时时白等 180s+（实测 p23 合计 183.8s、p32 192.1s），
    #   而整页结果本来已拿到，纯属浪费。改为**单组上限 + 整页总预算**双约束。
    per_col = _env_int(
        "OCR_COL_TILE_TIMEOUT",
        min(budget, int(getattr(settings, "REDO_TILED_BLOCK_TIMEOUT", 0) or 0) or 90),
    )
    total_col = _env_int("OCR_COL_TILE_TOTAL_TIMEOUT", per_col * max(2, min(groups, 4)))
    deadline = time.monotonic() + total_col
    try:
        img = Image.open(image_path)
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"列切识别无法读取页面图: {exc}") from exc
    _long = _env_int("OCR_COL_TILE_LONG", 1600)
    img.thumbnail((_long, _long))
    cols = _detect_columns_sync(img)
    if len(cols) < 3:
        raise RuntimeError(f"列检测不足（{len(cols)} 列），无法列切")
    grouped = list(reversed(_group_cols(cols, groups)))   # 竖排：最右组先读
    W0, H0 = img.size
    tmp_dir = tempfile.mkdtemp(prefix="col_grid_")
    merged: Dict = {"persons": [], "relations": [], "content": [], "notes": ""}
    errors: List[str] = []
    try:
        for gi, grp in enumerate(grouped):
            x0 = max(0, min(a for a, _ in grp) - max(2, int(W0 * 0.004)))
            x1 = min(W0, max(b for _, b in grp) + max(2, int(W0 * 0.004)))
            # 剩余预算不足 15s 直接放弃余下组，避免拖垮整页（已拿到的组仍会合并返回）
            remain = deadline - time.monotonic()
            if remain < 15:
                errors.append(f"列组{gi + 1}: 总预算 {total_col}s 已耗尽")
                break
            fname = os.path.join(tmp_dir, f"col_{gi}.png")
            img.crop((x0, 0, x1, H0)).save(fname, "PNG")
            try:
                b64 = image_file_to_b64(fname)
                content = await asyncio.wait_for(
                    _call_vlm(b64, prompt=prompt), timeout=min(per_col, remain)
                )
                _assert_no_repeat_loop(content)
                if len(content) > 25000:
                    raise ValueError(f"列块输出 {len(content)} 字符异常超长，疑似死循环")
                band = normalize_extraction(parse_json_from_llm(content))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"列组{gi + 1}(x{x0}~{x1}): {str(exc)[:150]}")
                logger.warning("列切 第 %d/%d 块失败: %s", gi + 1, len(grouped), exc)
                continue
            for p in band["persons"]:
                if not p.get("name"):
                    continue
                key = p["name"].replace(" ", "").replace("\u3000", "")
                exist = next(
                    (x for x in merged["persons"]
                     if x["name"].replace(" ", "").replace("\u3000", "") == key), None
                )
                if exist:
                    for k, v in p.items():
                        if v and not exist.get(k):
                            exist[k] = v
                else:
                    merged["persons"].append(p)
            merged["content"].extend(band["content"])
            if band.get("notes"):
                merged["notes"] = (
                    (merged["notes"] + "；" + band["notes"])
                    if merged["notes"] else band["notes"]
                )
        if not merged["persons"] and not merged["content"]:
            raise RuntimeError(f"列切识别全部失败：{' | '.join(errors) if errors else '无产出'}")
        merged["content"] = merged["content"][:max_entries]
        way = f"列边界对齐竖切（{len(grouped)} 组）"
        merged["notes"] = (f"本页按{way}识别" + (
            f"，{len(errors)} 组失败" if errors else ""
        )) + (f"；{merged['notes']}" if merged.get("notes") else "")
        return merged
    finally:
        for g in os.listdir(tmp_dir):
            try:
                os.remove(os.path.join(tmp_dir, g))
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


async def extract_page_tiled_norm(
    image_path: str,
    hard_timeout: Optional[int] = None,
    strips: int = 3,
    overlap_frac: float = 0.08,
    max_entries: int = 24,
    upscale: Optional[float] = None,
    prompt_override: Optional[str] = None,
    verify_mode: bool = False,
    vertical: bool = False,
) -> Dict:
    """切块兜底识别：逐块调 VLM，块结果先各自 normalize，再合并成一份 norm。

    vertical=False（历史默认）：沿纵向切「横带」，适合横排版式。
    vertical=True：沿横向切「竖条」（一列或几列一条），专为竖排古籍设计——每块
    只含少数几列，模型只需判断列内自上而下顺序，不必在整页里找列，可减少串列/倒序；
    合并时按「从右到左」回填块序（最右竖条最先读），输出仍是正确阅读顺序。
    

    upscale>1：每块 crop 先等比放大该倍数再送 VLM（复查路径用于暗字/磨损小字，
    默认失败兜底不放大）。verify_mode=True：不做「整页识别失败」文案（复查用，
    页并非失败）。prompt_override：复查时沿用整页调用同款提示词。
    """
    from PIL import Image  # noqa: PLC0415  延迟导入降低模块启动依赖

    import tempfile

    strips = max(2, int(strips))
    overlap_frac = min(max(overlap_frac, 0.0), 0.3)
    budget = hard_timeout or settings.REDO_PAGE_TIMEOUT
    # 单块上限与整页总预算分离（09-16 修）：
    # 旧实现给「每一块」都发完整 budget，切 N 条时最坏 N×budget（生产 6 条 × 300s
    # = 1800s），远超前端 900s 易直接超时且 206 槽位被长期占用。
    # 现在改为：整函数受 deadline 总预算约束，每块取 min(单块上限, 剩余时间)；
    # 剩余不足的块直接记为失败（已通过前置块拿到部分内容），不再拖垮整页。
    per_block = int(getattr(settings, "REDO_TILED_BLOCK_TIMEOUT", 0) or 0) or budget
    total_budget = int(getattr(settings, "REDO_TILED_TOTAL_TIMEOUT", 0) or 0) or (
        per_block * max(2, min(strips, 4))
    )
    deadline = time.monotonic() + total_budget
    prompt = prompt_override or get_configured_vision_prompt() or VISION_PROMPT

    try:
        img = Image.open(image_path)
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"分块兜底无法读取页面图: {exc}") from exc
    w, h = img.size
    if vertical:
        ov = int(w * overlap_frac)
        step = max(1, (w + ov * (strips - 1)) // strips)
        xs = [(max(0, i * (step - ov)), min(w, i * (step - ov) + step)) for i in range(strips)]
        xs = [(x0, x1) for x0, x1 in xs if x1 > x0]
        if len(xs) <= 1:
            raise RuntimeError("分块兜底切块异常（图片过窄），无法纵向分块")
        ys = [(0, h)] * len(xs)
    else:
        ov = int(h * overlap_frac)
        band_h = max(1, (h + ov * (strips - 1)) // strips)
        ys = [(max(0, i * (band_h - ov)), min(h, i * (band_h - ov) + band_h)) for i in range(strips)]
        ys = [(y0, y1) for y0, y1 in ys if y1 > y0]
        xs = [(0, w)] * len(ys)
        if len(ys) <= 1:
            raise RuntimeError("分块兜底切块异常（图片过小），无法分块")

    tmp_dir = tempfile.mkdtemp(prefix="redo_tiled_")
    band_files: List[str] = []
    errors: List[str] = []
    merged: Dict = {"persons": [], "relations": [], "content": [], "notes": ""}
    try:
        for i, (y0, y1) in enumerate(ys):
            x0, x1 = xs[i]
            fname = os.path.join(tmp_dir, f"band_{i}.png")
            crop = img.crop((x0, y0, x1, y1))
            if upscale and upscale > 1:
                crop = crop.resize(
                    (max(1, int(crop.size[0] * upscale)), max(1, int(crop.size[1] * upscale))),
                    Image.LANCZOS,
                )
            crop.save(fname, "PNG")
            band_files.append(fname)
        band_norms: List[Optional[Dict]] = [None] * len(band_files)
        for i, fname in enumerate(band_files):
            x0, x1 = xs[i]
            y0, y1 = ys[i]
            try:
                b64 = image_file_to_b64(fname)
                # 每块超时 = min(单块上限, 剩余总预算)；剩余不足 15s 直接放弃该块，
                # 避免把整页拖过前端超时（已拿到的块内容仍会合并返回）。
                remain = deadline - time.monotonic()
                block_to = min(per_block, remain)
                if block_to < 15:
                    raise TimeoutError(
                        f"总预算 {total_budget}s 已耗尽（剩余 {remain:.0f}s），跳过余下块"
                    )
                content = await asyncio.wait_for(
                    _call_vlm(b64, prompt=prompt), timeout=block_to
                )
                _assert_no_repeat_loop(content)
                if len(content) > 25000:
                    raise ValueError(f"分块输出 {len(content)} 字符异常超长，疑似死循环")
                band_raw = parse_json_from_llm(content)
                band_norms[i] = normalize_extraction(band_raw)
                logger.info("分块兜底 第 %d/%d 块成功（%s）", i + 1, len(band_files), os.path.basename(fname))
            except Exception as exc:  # noqa: BLE001
                rng = f"x{x0}~{x1}" if vertical else f"y{y0}~{y1}"
                errors.append(f"块{i + 1}({rng}): {str(exc)[:150]}")
                logger.warning("分块兜底 第 %d 块失败: %s", i + 1, exc)
        # 合并顺序：竖排按列切条时最右侧竖条最先读（从右到左）
        order = list(range(len(band_norms)))
        if vertical:
            order.reverse()
        for i in order:
            band_norm = band_norms[i]
            if not band_norm:
                continue
            # 合并：人物按姓名（去空格）去重、属性以首次出现为准；
            # content 段落按块顺序拼接后统一截断；备注并集。
            for p in band_norm["persons"]:
                if not p.get("name"):
                    continue
                key = p["name"].replace(" ", "").replace("\u3000", "")
                exist = next(
                    (x for x in merged["persons"] if x["name"].replace(" ", "").replace("\u3000", "") == key),
                    None,
                )
                if exist:
                    for k, v in p.items():
                        if v and not exist.get(k):
                            exist[k] = v
                else:
                    merged["persons"].append(p)
            merged["content"].extend(band_norm["content"])
            if band_norm.get("notes"):
                merged["notes"] = (
                    (merged["notes"] + "；" + band_norm["notes"])
                    if merged["notes"]
                    else band_norm["notes"]
                )
        if not merged["persons"] and not merged["content"] and errors:
            raise RuntimeError(f"分块兜底全部失败：{' | '.join(errors)}")
        merged["content"] = merged["content"][:max_entries]
        if errors:
            merged["notes"] = (
                (merged["notes"] + "；" if merged["notes"] else "")
                + f"分块部分失败({len(errors)}/{len(band_files)})"
            )
        note = merged.get("notes") or ""
        if verify_mode:
            merged["notes"] = note
        else:
            way = "纵向切条" if vertical else "横向切带"
            merged["notes"] = (
                f"本页整页识别失败，已自动{way}识别兜底（{len(ys)} 块，{len(errors)} 块失败）"
            ) + (
                f"；{note}" if note else ""
            )
        return merged
    finally:
        for f in band_files:
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


def _classify_page_type(norm: Dict) -> str:
    """按首轮（整页）结果把页面粗分类，供自动复查门控：世系 / 功德名单 / 其他。

    notes 开头按提示词规则先写类型词（世系页/功德名单页/纯图页/正文页），
    据此稳定判定；首轮空结果（无任何 content）时为「其他」，走空结果复查兜底。
    """
    head = str(norm.get("notes") or "")[:20]
    if "世系" in head:
        return "世系"
    if "功德" in head:
        return "功德名单"
    return "其他"


_DOUBT_WORDS = (
    "疑似",
    "模糊",
    "不清",
    "看不清",
    "难辨",
    "難辨",
    "可疑",
    "無法確認",
    "无法确认",
    "待考",
    "存疑",
    "猜想",
    "磨损",
    "磨損",
    "褪色",
    "殘損",
    "残损",
    "殘缺",
    "残缺",
    "破損",
    "破损",
    "侵蚀",
    "侵蝕",
)

# 残句锚点：含这些语义词的文本视为有语义内容，不当作残句移除（规则17c）
_FRAG_ANCHOR_RE = re.compile(
    r"[生卒殁歿配適娶葬墳墳穴坐向分金兼巽龍祖妣世翁公氏繼嗣子孫男婦]"
    r"|[年月日時]|[一二三四五六七八九十廿卅初]"
)


def _shi_result_doubtful(norm: Dict) -> bool:
    """世系页首轮结果是否「存疑」→ 决定要不要补漏复查（只查疑点页，不白烧 AI）。

    命中任一即补跑：
    1. notes 声明疑似/模糊/不清/难辨/可疑等疑点；
    2. persons 存在 confidence < 0.6（提示词规则：模糊/磨损人名强制标 ≤0.6 低置信）；
    3. content 正文「□」占位 ≥ 2 个（有读不出的字，可能存在漏读）。

    结果清爽（无疑点）的世系页直接返回，由人工审核把关（用户决策，规则4）。
    """
    notes = str(norm.get("notes") or "")
    if any(w in notes for w in _DOUBT_WORDS):
        return True
    if any((p.get("confidence") or 1.0) < 0.6 for p in norm.get("persons") or []):
        return True
    text = "".join(
        str(c.get("text") if isinstance(c, dict) else c or "")
        for c in (norm.get("content") or [])
    )
    return text.count("□") >= 2


async def _maybe_auto_verify(
    image_path: str, raw: Dict, prompt: str, budget: int
) -> Dict:
    """整页识别成功后的自动复查（页面类型门控 + 补漏，规则4）:

    - 世系页（notes 开头类型词含「世系」）：只在「结果存疑」时才做一轮「横向分块放大复查」
      （疑点判据见 _shi_result_doubtful：notes 疑点 / 人名低置信 <0.6 / 正文 □≥2），
      把首轮漏读的人补入 persons 并在 notes 注明补入人数；补入者强制低置信 ≤0.5，
      方便审核界面「忽略低置信 <0.6」先筛掉、人工复核后再确认。结果清爽无疑点的世系页
      直接返回不复查——后面还有人工审核把关。（VISION_AUTO_VERIFY_SHI 开关）
    - 功德名单页/其它正文页：一律不复查，直接返回（功德名单直接返回）。
    - 空结果页（非空白、首轮什么都没读出）：仍横向分块复查兜底一次
      （VISION_AUTO_VERIFY_EMPTY；沿用整页同款提示词，尽量把全文/人名补回来）。

    复查失败/超时一律回退整页原结果，绝不把本来成功的页拖成失败页。
    """
    try:
        base = normalize_extraction(raw)
        if image_utils.is_blank_image(image_path):
            return raw
        is_empty = not base["persons"] and not base["content"]
        kind = _classify_page_type(base)
        if kind == "世系":
            if not settings.VISION_AUTO_VERIFY_SHI:
                return raw
            if not _shi_result_doubtful(base):
                return raw  # 首轮结果无疑点：不补跑（省 AI），后续人工审核把关
            verify_prompt = VISION_VERIFY_PROMPT  # 世系补漏：只抓人名，不重复照录全文
            tag = "世系补漏复查"
            whole_prompt_mode = False
        elif is_empty:
            if not settings.VISION_AUTO_VERIFY_EMPTY:
                return raw
            verify_prompt = prompt  # 空结果兜底沿用整页同款提示词，尽量补回全文
            tag = "空结果自动复查"
            whole_prompt_mode = True
        else:
            return raw  # 功德名单/其它页：不复查

        async def _run() -> Optional[Dict]:
            tiled = await extract_page_tiled_norm(
                image_path,
                hard_timeout=budget,
                strips=settings.REDO_TILED_STRIPS,
                overlap_frac=settings.REDO_TILED_OVERLAP,
                max_entries=settings.REDO_TILED_MAX_ENTRIES,
                upscale=settings.VISION_VERIFY_UPSCALE,
                prompt_override=verify_prompt,
                verify_mode=True,
            )
            if not tiled["persons"] and not tiled["content"]:
                return None
            return tiled

        tiled = await asyncio.wait_for(_run(), timeout=min(budget // 2, 240))
        if not tiled:
            return raw
        if whole_prompt_mode:
            merged = normalize_extraction(tiled)
            add_n = len(merged["persons"])
        else:
            base_n = len(base["persons"])
            merged = _merge_verify(base, tiled)
            add_n = len(merged["persons"]) - base_n
        prev = (merged.get("notes") or "").strip()
        note = tag if not add_n else f"{tag}（补入 {add_n} 人，低置信待核）"
        merged["notes"] = ((prev + "；") if prev else "") + note
        return merged
    except Exception as exc:  # noqa: BLE001
        logger.warning("整页结果自动复查失败（保留原结果）: %s", exc)
        return raw


def _merge_verify(base: Dict, tiled: Dict) -> Dict:
    """复查合并：以整页(base，新版主结果)为准；复查里「首轮没有的人」补入 persons 尾部。

    补入者 confidence 强制压到 ≤0.5（标低置信）——复查是放大图易「看着像」就冒 0.9，
    低置信标记让审核界面「忽略低置信 <0.6」先筛掉这批，人工复核通过前不进图谱。
    同名同字不去重差异：带(二)(三)后缀视为不同人（各成独立条目）。
    """
    merged = {
        "persons": list(base["persons"]),
        "relations": [],
        "content": list(base["content"]) or list(tiled["content"]),
        "notes": base.get("notes") or tiled.get("notes") or "",
    }
    seen = {p["name"].replace(" ", "").replace("\u3000", "") for p in merged["persons"]}
    for p in tiled.get("persons") or []:
        key = (p.get("name") or "").replace(" ", "").replace("\u3000", "")
        if key and key not in seen:
            np_ = dict(p)
            try:
                conf = float(np_.get("confidence")) if np_.get("confidence") is not None else 0.5
            except (TypeError, ValueError):
                conf = 0.5
            np_["confidence"] = round(min(conf, 0.5), 2)
            merged["persons"].append(np_)
            seen.add(key)
    return merged


def _is_frag_name(name: str, bio: str, conf: float) -> bool:
    """残字/残句被模型误当人名的启发式识别（规则6/17c，用户指令2/3）。

    命中 → 不作为 person 入库，文字并入 notes「残句照录」保留。
    只在低置信（≤0.6）下生效，且只收明确像日期/干支/行文/结构词/拼接产物的形态，
    不做激进删除（防误伤真名）。主要拦截：
    - 单字残字（董/焦/十/生/卒…）；纯干支或纯数字的两字组合（壬寅/初四…）；
    - 兼祧/承继残句被当名字（「兼德」「兼承」「承德」类，无小注支撑）；
    - 行文结构词开头的多字碎片（「坐向辛生」「葬坐…」——真实人名不以生卒配葬坐向开头）；
    - 「姓名＋某氏」揉成一个名字的拼接产物（如「李國藩吳氏」）。
    """
    if conf > 0.6 or len(name) < 1:
        return False
    numerals = "一二三四五六七八九十廿卅初零"
    ganzhi = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
    date_unit = "年月日時"
    if len(name) == 1:
        ch = name
        if ch in "生卒殁歿配適葬墳妣氏長次男女子" + date_unit + "坐向分金兼立":
            return True
        if bio and all(c in numerals + "□" for c in bio):
            return True
        # 形如「適本地董宅」被截成单姓 → 其所在小注是婚/配语境即判碎片
        if ch == "董" and bio and ("適" in bio or "配" in bio or "宅" in bio):
            return True
        return False
    # 兼祧/承继残句（「兼德」「承德」等）：低置信且无足够小注支撑 → 判碎片
    if conf <= 0.5 and name[0] in "兼承" and not (bio and len(bio) >= 4):
        return True
    if len(name) == 2:
        if all(c in numerals for c in name) or all(c in ganzhi for c in name):
            return True
        return False
    # 三字及以上
    if conf <= 0.5:
        # 行文结构词开头（坐向辛生/葬坐…）——真实人名不以这些字起头
        if name[0] in "生卒殁歿配適娶葬墳妣坐向分金":
            return True
        # 拼接人名：真实名讳不含「氏」字
        if len(name) >= 4 and "氏" in name:
            return True
    return False


def normalize_extraction(raw: Dict) -> Dict:
    """把模型返回规整成统一扁平结构，并在代码层做「置信度审计 + 残句过滤」：

    返回页级顶层：persons / relations（恒空）/ content（纯文本段落）/ notes。
    结构契约（扁平化，取消篇目分类嵌套，见用户需求4）：
    - content：整页原文纯文本段落，条目不带 type/title/类别；
    - notes：页备注（疑点/类型词）；
    - ai_meta 统计（人数/条数/字数/耗时/低置信数）由 import_service 落盘时按页面计算，
      模型本身不输出（无法自报耗时）。

    - 置信度上限 0.95（规则11：清晰照录 0.85~0.95，绝不 0.95 以上）；
    - 姓名/小注含「□」→ 压到 ≤0.6（规则12：模糊占位即存疑）；
    - 小注出现阿拉伯数字（疑模型换算/自填年份）→ 压到 ≤0.4 并注「疑为补位」（规则13）；
    - notes 点名疑似/模糊/磨损等 的姓名 → 压到 ≤0.6（规则14）；
    - 无主名、无生卒语义的碎片残句（≤12字）被模型误当人名 → 剔出 persons、并入 notes
      保留照录（规则17c）。
    - 注意：不做「页级整体压置信」（曾压整页 0.4 引发清晰条目被误伤，用户规则严禁整页统一值）；
    模糊只对带 □ / 被 notes 点名的具体条目生效，清晰条目保持 0.85~0.95。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"识别结果不是对象（{type(raw).__name__}），无法规整")
    persons = []
    for p in raw.get("persons") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        conf = p.get("confidence")
        try:
            conf = float(conf) if conf is not None else 0.5
        except (TypeError, ValueError):
            conf = 0.5
        gender = str(p.get("gender", "unknown")).lower()
        if gender not in ("male", "female", "unknown"):
            gender = "unknown"
        persons.append(
            {
                "name": name,
                "gender": gender,
                "birth_year": p.get("birth_year"),
                "death_year": p.get("death_year"),
                "birth_place": p.get("birth_place") or None,
                "biography": p.get("biography") or None,
                "confidence": round(min(max(conf, 0.0), 1.0), 2),
            }
        )
    _notes = str(raw.get("notes") or raw.get("page_notes") or "")
    notes_have_doubt = bool(_notes) and any(t in _notes for t in _DOUBT_WORDS)
    audit_marks: List[str] = []
    for p in persons:
        conf = p["confidence"]
        seg = p["name"] + " " + (p.get("biography") or "")
        if "□" in seg:
            conf = min(conf, 0.6)  # 规则12：带□（模糊/破损/缺字占位）→ ≤0.6
        if re.search(r"[0-9]", p.get("biography") or ""):
            conf = min(conf, 0.4)  # 规则13
            if "疑为补位" not in audit_marks:
                audit_marks.append("疑为补位")
        if notes_have_doubt and len(p["name"]) >= 2 and p["name"] in _notes:
            conf = min(conf, 0.6)  # 规则14：notes 点名疑似/模糊的姓名 → ≤0.6
        p["confidence"] = round(min(conf, 0.95), 2)  # 规则11 上限 0.95
    # 规则6/17c：残字/残句被模型误当人名（董/焦/十/初四 等）→ 剔除并并入备注照录
    _frag_names: List[str] = []
    _kept_persons: List[dict] = []
    for _p in persons:
        if _is_frag_name(_p["name"], _p.get("biography") or "", _p["confidence"]):
            _frag_names.append(_p["name"])
        else:
            _kept_persons.append(_p)
    persons = _kept_persons
    # 第一段只做读字提人：亲属关系全部交给第二段（卷级整理）统一推断，这里恒为空。
    relations: list = []
    # 扁平纯文本段落：模型输出 content 为字符串数组或 [{"text": ...}] 数组。
    content_raw = raw.get("content")
    if isinstance(content_raw, str):
        content_raw = [content_raw]
    content: List[Dict] = []
    for c in content_raw or []:
        if len(content) >= MAX_ENTRIES_PER_PAGE:
            break
        text = (
            str(c.get("text") if isinstance(c, dict) else c or "").strip()
        )
        if not text:
            continue
        content.append({"text": text[:MAX_ENTRY_TEXT]})
    if _frag_names:
        audit_marks.append("残句照录：" + "；".join(list(dict.fromkeys(_frag_names))[:6]))
    # 审计注记去重：notes 里已含相同短语（如复查链重复 normalize）则不重复追加
    parts = list(dict.fromkeys(m for m in audit_marks if m not in _notes))
    notes_out = _notes
    if parts:
        notes_out = (notes_out + "；" if notes_out else "") + "；".join(parts)
    return {
        "persons": persons,
        "relations": relations,
        "content": content,
        "notes": notes_out,
    }


# ============ 插图页内容识别（正文极少页的图注条目） ============
# 插图页（照片/图画/画像/地图/宗祠/祖宅等）几乎无正文，纯文字检索搜不到；识别流程对
# 「正文极少」的候选页调用 describe_illustrations 生成图注，apply 时以 type=插图 的
# content_entries 入库并被 RAG 检索到。用户借此也能搜到谱书里的图像类内容。
ILLUSTRATION_PROMPT = """你是古籍族谱扫描页的图像内容助手。请看这一页扫描件：
如果页面包含**图像类内容**（照片、图画、画像、地图、宗祠/祖宅/坟山/器物/合影等，区别于纯文字版面、表格、名单），请逐一对每张图给出：
- title：≤24 字的一句话图名（如「某氏宗祠老照片」「始祖容像」「谱系地图」）；
- text：≤120 字描述画面内容与可见细节（建筑形制/人物姿态/环境/构图等）；若图内可见题字、匾额、铭文、印章等文字，请照录在「」中。
若页面是纯文字/名单/表格，或图只是版式纹饰，没有可描述的图像内容，则 images 给空数组。

严格只输出如下 JSON，不要输出任何其他文字：
{"images": [{"title": "...", "text": "..."}]}"""


async def describe_illustrations(image_path: str) -> List[Dict]:
    """对扫描页做插图内容识别，返回 type=插图的条目列表（无图为 []）。

    供识别流程的「插图页补充识别」（import_service.ensure_illustration_pass）对正文极少
    的候选页调用。失败不抛致命异常（返回 [] 跳过），不影响识别主流程。
    """
    try:
        if image_utils.is_blank_image(image_path):
            return []
        b64 = image_file_to_b64(image_path)

        async def _do() -> List[Dict]:
            content = await _call_vlm(
                b64, prompt=ILLUSTRATION_PROMPT, max_tokens=900
            )
            data = parse_json_from_llm(content)
            out: List[Dict] = []
            for im in data.get("images") or []:
                if not isinstance(im, dict):
                    continue
                title = str(im.get("title") or "").strip()[:40]
                text = str(im.get("text") or "").strip()[:500]
                if not title and not text:
                    continue
                out.append({"type": "插图", "title": title, "text": text})
            return out[:4]

        return await asyncio.wait_for(
            _do(), timeout=min(settings.VLM_PAGE_TIMEOUT, 150)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("插图识别失败（跳过本页）: %s", exc)
        return []
