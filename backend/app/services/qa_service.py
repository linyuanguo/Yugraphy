"""图谱问答引擎：规则解析（确定性、快、稳）+ 可选 LLM 增强（prompt 可配置）。

解析流程：
1. 读取 AppSetting 中的问答设置（是否启用 LLM、prompt、模型名、欢迎语）
2. 用 pinyin_utils 从问题中识别目标人物与意图（father/mother/children/spouse…）
3. 执行对应 Cypher 查询，组装自然语言回答 + 可渲染子图
4. 规则解析失败且启用 LLM 时，调用 206 的 SGLang 把问题转成 {person, intent} 再执行
"""
import asyncio
import json
import logging
import re
from typing import Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import driver
from app.models.orm import AppSetting
from app.services import person_service
from app.utils.pinyin_utils import REL_WORDS, find_best_person

logger = logging.getLogger("genealogy.qa")

SETTING_KEYS = ("qa_enable_llm", "qa_llm_prompt", "qa_llm_model", "qa_welcome")

INTENT_LABEL: Dict[str, str] = {
    "father": "父亲", "mother": "母亲", "parents": "父母",
    "grandfather": "祖父", "grandmother": "祖母",
    "son": "儿子", "daughter": "女儿", "children": "子女",
    "brother": "兄弟", "sister": "姐妹", "sibling": "兄弟姐妹",
    "spouse": "配偶", "husband": "丈夫", "wife": "妻子",
    "ancestor": "祖先", "descendant": "后代",
    "birthplace": "出生地", "lifetime": "生卒年",
    "biography": "简介", "generation": "世代",
}

# 意图 → 匹配词（规则层）
INTENT_KEYWORDS: Dict[str, List[str]] = {
    "father": ["父亲", "爸爸", "老爸", "老爹", "爹"],
    "mother": ["母亲", "妈妈", "老妈", "老娘", "娘"],
    "parents": ["父母", "双亲"],
    "grandfather": ["祖父", "爷爷", "阿公"],
    "grandmother": ["祖母", "奶奶", "阿婆"],
    "son": ["儿子"],
    "daughter": ["女儿"],
    "children": ["子女", "孩子", "儿女", "后人", "后代", "子孙", "孙辈"],
    "brother": ["兄弟", "哥哥", "弟弟", "兄长", "小弟"],
    "sister": ["姐妹", "姐姐", "妹妹", "姊妹"],
    "sibling": ["兄弟姐妹", "同胞"],
    "spouse": ["配偶", "另一半"],
    "husband": ["丈夫", "老公", "夫君"],
    "wife": ["妻子", "老婆", "夫人", "太太", "媳妇"],
    "ancestor": ["祖先", "先祖", "祖宗", "祖辈", "上辈", "长辈"],
    "descendant": ["后代", "子孙", "后人", "曾孙", "重孙"],
    "birthplace": ["出生地", "籍贯", "故乡", "老家", "家乡", "出生于", "哪里人"],
    "lifetime": ["生年", "出生年份", "生辰", "生于", "寿命", "享年", "卒年", "去世", "活了多久"],
    "biography": ["简介", "生平", "事迹", "经历"],
    "generation": ["第几代", "辈分", "代数", "世代"],
}

DEFAULT_LLM_PROMPT = """你是家谱知识库的意图识别器。请把下面的问题解析为 JSON（只输出 JSON，不要其他文字）：
{"person": "问题中的人物姓名（原样），没提到则为空字符串", "intent": "意图，取值必须是以下之一: father|mother|parents|grandfather|grandmother|son|daughter|children|brother|sister|sibling|spouse|husband|wife|ancestor|descendant|birthplace|lifetime|biography|generation|unknown"}
问题：{question}"""

DEFAULT_WELCOME = "您好，欢迎查询族谱。您可以这样问：「张三的父亲是谁」「李四的配偶」「王五有哪些后代」「某某的出生地」……"


# ============ 设置读写 ============
def get_qa_settings(db: Session, model_override: Optional[str] = None) -> dict:
    """读取问答设置。

    model_override：某个访客分享指定了专用模型（VisitShare.chat_model）时传入，
    该分享的问答模型与端点随之切换（空/None 则跟随系统全局 qa_llm_model）。
    """
    rows = db.query(AppSetting).filter(AppSetting.key.in_(SETTING_KEYS)).all()
    d = {r.key: r.value for r in rows}
    model = (
        model_override
        or d.get("qa_llm_model")
        or settings.QWEN_MODEL_NAME
    )
    # 若最终使用的模型在「模型配置」里配了独立 API 地址/密钥则用其，否则回退服务器环境变量
    api_base = api_key = None
    mrow = db.query(AppSetting).filter(AppSetting.key == "llm_models").first()
    if mrow and mrow.value:
        try:
            for m in json.loads(mrow.value):
                if isinstance(m, dict) and m.get("name") == model:
                    api_base = str(m.get("api_base") or "").strip() or None
                    api_key = str(m.get("api_key") or "").strip() or None
                    break
        except (TypeError, ValueError):
            pass
    return {
        "qa_enable_llm": d.get("qa_enable_llm", "0") == "1",
        "qa_llm_prompt": d.get("qa_llm_prompt") or DEFAULT_LLM_PROMPT,
        "qa_llm_model": model,
        "qa_welcome": d.get("qa_welcome") or DEFAULT_WELCOME,
        "llm_api_base": api_base,
        "llm_api_key": api_key,
    }


def set_qa_setting(db: Session, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


# ============ 规则解析 ============
def _detect_intent(question: str) -> Optional[str]:
    for intent, words in INTENT_KEYWORDS.items():
        for w in words:
            if w in question:
                return intent
    return None


# ============ 图谱查询 ============
async def _query_related(person_id: str, intent: str) -> List[dict]:
    """根据意图查询相关人物。"""
    async with driver.session() as session:
        if intent == "father":
            cypher = "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(x:Person) RETURN x"
            rec = await session.run(cypher, id=person_id)
        elif intent == "mother":
            cypher = "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(x:Person) WHERE x.gender='female' RETURN x"
            rec = await session.run(cypher, id=person_id)
        elif intent == "parents":
            cypher = "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(x:Person) RETURN DISTINCT x"
            rec = await session.run(cypher, id=person_id)
        elif intent == "grandfather":
            cypher = (
                "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(par:Person)<-[:PARENT_OF]-(x:Person) "
                "WHERE x.gender='male' RETURN DISTINCT x"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent == "grandmother":
            cypher = (
                "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(par:Person)<-[:PARENT_OF]-(x:Person) "
                "WHERE x.gender='female' RETURN DISTINCT x"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent in ("son", "daughter"):
            gender = "male" if intent == "son" else "female"
            cypher = (
                "MATCH (p:Person {person_id:$id})-[:PARENT_OF]->(x:Person) "
                f"WHERE x.gender='{gender}' RETURN DISTINCT x"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent == "children":
            cypher = (
                "MATCH (p:Person {person_id:$id})-[:PARENT_OF]->(x:Person) "
                "RETURN DISTINCT x ORDER BY x.birth_year"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent in ("brother", "sister"):
            gender = "male" if intent == "brother" else "female"
            cypher = (
                "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(par:Person)-[:PARENT_OF]->(x:Person) "
                f"WHERE x.person_id <> $id AND x.gender='{gender}' RETURN DISTINCT x"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent == "sibling":
            cypher = (
                "MATCH (p:Person {person_id:$id})<-[:PARENT_OF]-(par:Person)-[:PARENT_OF]->(x:Person) "
                "WHERE x.person_id <> $id RETURN DISTINCT x ORDER BY x.birth_year"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent in ("spouse", "husband", "wife"):
            extra = ""
            if intent == "husband":
                extra = " AND x.gender='male'"
            elif intent == "wife":
                extra = " AND x.gender='female'"
            cypher = (
                "MATCH (p:Person {person_id:$id})-[:SPOUSE_OF]-(x:Person) "
                f"WHERE true {extra} RETURN DISTINCT x"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent == "ancestor":
            cypher = (
                "MATCH path=(x:Person)-[:PARENT_OF*1..10]->(p:Person {person_id:$id}) "
                "RETURN x, length(path) AS d ORDER BY d LIMIT 100"
            )
            rec = await session.run(cypher, id=person_id)
        elif intent == "descendant":
            cypher = (
                "MATCH path=(p:Person {person_id:$id})-[:PARENT_OF*1..10]->(x:Person) "
                "RETURN x, length(path) AS d ORDER BY d LIMIT 100"
            )
            rec = await session.run(cypher, id=person_id)
        else:
            return []

        if intent in ("ancestor", "descendant"):
            seen = set()
            persons = []
            async for r in rec:
                node = person_service.node_to_dict(r["x"])
                if node["person_id"] not in seen:
                    seen.add(node["person_id"])
                    persons.append(node)
            return persons

        return [person_service.node_to_dict(r["x"]) async for r in rec]


async def _build_subgraph(person: dict, related: List[dict]) -> Optional[dict]:
    """给 中心人物+相关人物 之间的边，供前端渲染高亮子图。"""
    if not related:
        return None
    ids = {person["person_id"], *(p["person_id"] for p in related)}
    edges = []
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (a:Person)-[r:PARENT_OF|SPOUSE_OF]->(b:Person) "
            "WHERE a.person_id IN $ids AND b.person_id IN $ids "
            "RETURN r.rel_id AS rel_id, type(r) AS type, "
            "a.person_id AS source, b.person_id AS target, r.marriage_date AS marriage_date",
            ids=list(ids),
        )
        async for row in rec:
            edges.append(
                {
                    "id": row["rel_id"] or f"e-{row['source']}-{row['target']}",
                    "source": row["source"],
                    "target": row["target"],
                    "type": row["type"],
                    "marriage_date": row["marriage_date"],
                }
            )
    return {"nodes": [person, *related], "edges": edges}


def _fmt_person(p: dict) -> str:
    years = f"{p.get('birth_year') or '?'} — {p.get('death_year') or '?'}"
    return f"{p['name']}（{years}）"


def _compose_answer(intent: str, person: dict, related: List[dict]) -> str:
    label = INTENT_LABEL.get(intent, "相关人物")
    if intent in ("birthplace", "lifetime", "biography", "generation"):
        return related[0]["answer"] if related else "暂未记录该信息。"
    if not related:
        return f"抱歉，未查询到 {person['name']} 的{label}信息。"
    names = "、".join(_fmt_person(p) for p in related[:20])
    if len(related) > 20:
        names += f" 等 {len(related)} 人"
    return f"{person['name']}的{label}：{names}"


async def _query_property(person: dict, intent: str) -> List[dict]:
    """属性类意图：出生地/生卒年/简介/世代。返回 [{answer}]。"""
    if intent == "birthplace":
        val = person.get("birth_place")
    elif intent == "lifetime":
        val = (
            f"{person.get('birth_year') or '?'} 年 — {person.get('death_year') or '?'} 年"
            if person.get("birth_year") or person.get("death_year")
            else None
        )
    elif intent == "biography":
        val = person.get("biography")
    elif intent == "generation":
        gen = person.get("generation")
        val = f"第 {gen} 代" if gen is not None else None
    else:
        val = None
    return [{"answer": str(val)}] if val else []


# ============ LLM 增强（可选） ============
async def _llm_parse(question: str, qa: dict) -> Optional[dict]:
    """让 206 的 LLM 把问题解析为 {person, intent}。失败返回 None。"""
    prompt = qa["qa_llm_prompt"].replace("{question}", question)
    payload = {
        "model": qa["qa_llm_model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 256,
    }
    base = (qa.get("llm_api_base") or settings.SGLANG_URL).rstrip("/")
    key = qa.get("llm_api_key") or settings.SGLANG_API_KEY
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        intent = str(data.get("intent", "")).strip()
        if intent not in INTENT_LABEL:
            intent = "unknown"
        return {"person": str(data.get("person", "")).strip(), "intent": intent}
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 意图解析失败: %s", exc)
        return None


# ============ 主入口 ============
async def answer_question(
    question: str, db: Session, model_override: Optional[str] = None
) -> dict:
    """图谱问答。

    model_override：访客分享指定了专用模型（share.chat_model）时，该分享按此模型解析；
    同时只要某分享显式指定了模型，即使全局「启用 LLM 增强」关闭也对其启用 LLM 兜底。
    """
    qa = get_qa_settings(db, model_override)
    persons_all, _ = await person_service.list_persons(limit=5000)

    intent = _detect_intent(question)
    person = find_best_person(question, persons_all)

    # 规则未解析出人物/意图 → LLM 增强（可选；某分享显式指定模型时视为启用）
    if (not intent or not person) and (qa["qa_enable_llm"] or model_override):
        parsed = await _llm_parse(question, qa)
        if parsed:
            intent = intent or parsed["intent"]
            if parsed["person"]:
                for p in persons_all:
                    if p["name"] == parsed["person"]:
                        person = p
                        break
            if not person and parsed["person"]:
                person = find_best_person(parsed["person"], persons_all)

    if not person:
        return {
            "intent": intent or "unknown",
            "question": question,
            "answer": "抱歉，我没能识别出您想问哪位人物。请尝试如「张三的父亲是谁」这样的问法。",
            "person": None,
            "persons": [],
            "tree": None,
        }

    if not intent or intent == "unknown":
        return {
            "intent": "unknown",
            "question": question,
            "answer": f"找到了「{person['name']}」。点击下方人物标签即可在族谱中定位；也可以继续问我他（她）的父亲/母亲/配偶/子女/祖先/后代/出生地等。",
            "person": person,
            "persons": [person],
            "tree": None,
        }

    # 属性类意图
    if intent in ("birthplace", "lifetime", "biography", "generation"):
        props = await _query_property(person, intent)
        return {
            "intent": intent,
            "question": question,
            "answer": _compose_answer(intent, person, props),
            "person": person,
            "persons": [],
            "tree": None,
        }

    related = await _query_related(person["person_id"], intent)
    subgraph = await _build_subgraph(person, related)
    return {
        "intent": intent,
        "question": question,
        "answer": _compose_answer(intent, person, related),
        "person": person,
        "persons": related,
        "tree": subgraph,
    }
