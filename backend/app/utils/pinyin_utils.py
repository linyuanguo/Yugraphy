"""同音字/模糊搜索：基于 pypinyin 的姓名匹配工具（访客搜索、问答人名识别）。"""
from functools import lru_cache
from typing import List, Optional, Tuple

from pypinyin import Style, lazy_pinyin

# 避免加载非精简词典：pypinyin 默认 dict 足够
_phone_cache: dict = {}


def _phones(name: str) -> List[str]:
    """全拼（无声调、无空格）。"""
    key = name
    if key not in _phone_cache:
        _phone_cache[key] = lazy_pinyin(name, style=Style.NORMAL, errors="ignore")
    return _phone_cache[key]


def _initials(name: str) -> str:
    """首字母缩写。"""
    return "".join(lazy_pinyin(name, style=Style.FIRST_LETTER, errors="ignore"))


@lru_cache(maxsize=4096)
def pinyin_key(name: str) -> Tuple[str, str]:
    """返回 (全拼指纹, 首字母指纹)。"""
    full = "".join(_phones(name)).lower()
    return full, _initials(name).lower()


def is_homophone(a: str, b: str) -> bool:
    """两个名字是否同音（忽略声调）。"""
    return pinyin_key(a)[0] == pinyin_key(b)[0]


def edit_distance(a: str, b: str, max_d: int = 1) -> int:
    """Levenshtein 距离（超阈值提前返回，节省计算）。"""
    if abs(len(a) - len(b)) > max_d:
        return max_d + 1
    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev = dp[0]
        dp[0] = i
        for j, cb in enumerate(b, 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (0 if ca == cb else 1))
            prev = cur
    return dp[-1]


def fuzzy_match(query: str, name: str) -> Tuple[bool, int]:
    """判断 query 是否匹配 name。返回 (是否匹配, 得分)，得分越低越接近。

    匹配层级（分数越低越接近）：
      0  子串包含（"周家"→"周家温"）
      5  单字查询命中名字首字或其同音字（"张"→"章XX"/"张XX"）
      10 整名同音（"张珊"≈"张山"）
      20 纯拼音输入（缩写/全拼前缀，如 zs / zhangsan → 张三）
      30 错别字容错（编辑距离≤1，仅查询双方都≥2 字时启用）

    注意：短输入不能走宽泛的"同首字母同长度"与"编辑距离≤1"（单字与
    任意单字名的编辑距离恒为 1、两字同首字母即命中），否则会大面积误报。
    """
    q = query.strip()
    n = name.strip()
    if not q or not n:
        return False, 999
    # 1) 名字包含查询词（精确/部分命中；不做反向包含，避免单字名被长查询词误带出）
    if q in n:
        return True, 0
    qa, na = q.isascii(), n.isascii()
    # 2) 纯拼音/缩写输入（"zs" / "zhangsan" → "张三"）
    if qa and not na:
        full, initials = pinyin_key(n)
        if initials.startswith(q.lower()) or full.startswith(q.lower()):
            return True, 20
    if not qa and not na:
        qp = pinyin_key(q)[0]
        if len(q) == 1:
            # 3) 单字：整名同音，或名字首字同音（"张"→"章三"）
            if is_homophone(q, n):
                return True, 10
            if pinyin_key(n)[0].startswith(qp):
                return True, 5
        else:
            # 4) 整名同音（"张珊"≈"张山"）
            if is_homophone(q, n):
                return True, 10
            # 5) 错别字容错（双方均≥2 字，避免单字全局误报）
            if len(n) >= 2 and edit_distance(q, n) <= 1:
                return True, 30
    return False, 999


def find_best_person(question: str, persons: List[dict], fallback_name: Optional[str] = None) -> Optional[dict]:
    """在问题文本中识别出最可能的人物。优先精确子串，其次拼音/容错匹配。"""
    if fallback_name:
        for p in persons:
            if p["name"] == fallback_name:
                return p
    # 1) 精确子串（按名字长度从长到短，避免"张三"优先于"张三丰"）
    for p in sorted(persons, key=lambda x: -len(x["name"])):
        if p["name"] and p["name"] in question:
            return p
    # 2) 剥掉关系/疑问词后，对剩余文本做拼音匹配
    residual = question
    for group in REL_WORDS:
        for w in group:
            residual = residual.replace(w, " ")
    for ch in "，。？！、,.?!:：的与和了是谁" " ":
        residual = residual.replace(ch, " ")
    residual = residual.strip()
    if not residual:
        return None
    best, best_score = None, 999
    for p in persons:
        if not p["name"]:
            continue
        ok, score = fuzzy_match(residual, p["name"])
        if ok and score < best_score:
            best, best_score = p, score
    return best


# 关系词表（qa_service 与 find_best_person 共用）
REL_WORDS: List[Tuple[str, ...]] = [
    ("父亲", "爸爸", "老爸", "老爹", "爹", "爸爸大人", "父"),
    ("母亲", "妈妈", "老妈", "老娘", "娘", "母"),
    ("父母", "双亲"),
    ("祖父", "爷爷", "爷爷大人", "阿公"),
    ("祖母", "奶奶", "阿婆"),
    ("外公", "姥爷"),
    ("外婆", "姥姥"),
    ("曾祖父", "太爷爷"),
    ("曾祖母", "太奶奶"),
    ("儿子", "儿子们"),
    ("女儿", "女儿们"),
    ("子女", "孩子", "儿女", "后人", "后代", "子孙", "孙辈", "辈分"),
    ("兄弟", "弟兄"),
    ("哥哥", "大哥", "兄长"),
    ("弟弟", "小弟"),
    ("姐姐", "大姐"),
    ("妹妹", "小妹"),
    ("姐妹", "姊妹"),
    ("兄弟姐妹", "同胞"),
    ("配偶", "另一半"),
    ("丈夫", "老公", "夫君"),
    ("妻子", "老婆", "夫人", "太太", "媳妇"),
    ("祖先", "先祖", "祖宗", "祖辈", "上辈", "长辈"),
    ("出生地", "籍贯", "故乡", "老家", "家乡", "出生于", "哪里人"),
    ("生年", "出生年份", "生辰", "生于", "寿命", "享年", "卒年", "去世"),
    ("简介", "生平", "事迹", "经历"),
    ("第几代", "辈分", "代数", "世代"),
]
