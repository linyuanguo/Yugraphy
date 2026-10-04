"""人物相关谱书材料检索服务。

给"家谱树/访客分享页"点开某个人物时,展示该人的家族信息(阶段 1:结构化 + 文本命中召回;
阶段 2 将在此基础上叠加向量检索):

- 谱系级篇目:人物所在谱系的 源流/迁徙/凡例/家规家训/字辈/序跋/祠祭/坟茔 等家族背景内容
  (全谱共享,不特指某个人;有房支归属时优先该房支的内容);
- 人物命中:传记/艺文/其他 等直接谈人的篇目,其标题或正文中出现该人物姓名
  (姓名匹配为朴素子串,繁体/异体/称呼变体等语义召回留给阶段 2 向量检索兜底)。

内容数据源:content_entries(PG)。AI 识别结果在任务「写入图谱」(apply)时聚合入库,
因此人物必须有谱系归属(apply 后)才能命中谱系级内容。
"""
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.orm import ContentEntry

# 谱系级"家族背景"类型:与该谱系整体相关(人物点击后作为家族背景展示)
LINEAGE_LEVEL_TYPES = {
    "源流", "迁徙", "凡例", "家规家训", "字辈", "序跋", "祠祭", "坟茔",
}
# 人物级类型:直接谈某人的内容(传记/赞/行略等),做姓名命中召回
PERSON_LEVEL_TYPES = {"传记", "艺文", "其他"}

# 上限:避免一本几百条内容全量返回
LINEAGE_LEVEL_LIMIT = 30  # 谱系/房支背景最多返回条数
PERSON_HIT_LIMIT = 40  # 姓名命中人物篇目最多返回条数


def _norm(s: str) -> str:
    """空白归一 + 小写(全半角宽字符暂不转换,繁简留给阶段 2 向量检索)。"""
    return "".join((s or "").split()).lower()


def _entry_payload(
    e: ContentEntry, scope: str, hit_name: Optional[str] = None
) -> Dict:
    return {
        "entry_id": e.entry_id,
        "type": e.type,
        "title": e.title or "",
        "text": e.text,
        "page_no": e.page_no,
        "task_id": e.task_id,
        "scope": scope,  # lineage=谱系背景 / branch=房支背景 / person=人物命中
        "hit_name": hit_name,
    }


def query_person_materials(
    name: str,
    lineage_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    db: Optional[Session] = None,
) -> List[Dict]:
    """按人物姓名/谱系/房支召回相关谱书材料,返回已排序的条目列表。

    scope 说明:
      - branch: 房支(IN_BRANCH)直接归属的条目;
      - lineage: 谱系(BELONGS_TO)直接归属的条目(房支类内容只在房支级出);
      - person:  传记等篇目标题/正文命中人物姓名。
    顺序:先人物命中(最相关),再房支/谱系背景。
    """
    own = db or _session()
    try:
        entries: List[Dict] = []
        name_key = _norm(name)
        # ① 人物命中:传记/艺文/其他 中标题或正文出现姓名
        if name_key:
            ptypes = list(PERSON_LEVEL_TYPES)
            rows = (
                own.query(ContentEntry)
                .filter(
                    ContentEntry.status == "active",
                    ContentEntry.type.in_(ptypes),
                    ContentEntry.text.isnot(None),
                )
                .order_by(ContentEntry.page_no, ContentEntry.id)
                .all()
            )
            hit_rows = []
            for r in rows:
                if name_key in _norm(r.title) or name_key in _norm(r.text):
                    hit_rows.append(r)
            for r in hit_rows[:PERSON_HIT_LIMIT]:
                entries.append(_entry_payload(r, "person", hit_name=name))

        # 人物可能只挂房支(IN_BRANCH)而无谱系直挂:用房支条目反推谱系
        if lineage_id is None and branch_id:
            r = (
                own.query(ContentEntry.lineage_id)
                .filter(
                    ContentEntry.branch_id == branch_id,
                    ContentEntry.lineage_id.isnot(None),
                )
                .limit(1)
                .first()
            )
            if r:
                lineage_id = r[0]

        # ② 房支级背景:优先精确房支归属
        if branch_id:
            brows = (
                own.query(ContentEntry)
                .filter(
                    ContentEntry.status == "active",
                    ContentEntry.branch_id == branch_id,
                    ContentEntry.type.in_(list(LINEAGE_LEVEL_TYPES)),
                )
                .order_by(ContentEntry.type, ContentEntry.page_no, ContentEntry.id)
                .limit(LINEAGE_LEVEL_LIMIT)
                .all()
            )
            for r in brows:
                entries.append(_entry_payload(r, "branch"))

        # ③ 谱系级背景(房支内容可略,避免重复)
        if lineage_id:
            lrows = (
                own.query(ContentEntry)
                .filter(
                    ContentEntry.status == "active",
                    ContentEntry.lineage_id == lineage_id,
                    ContentEntry.type.in_(list(LINEAGE_LEVEL_TYPES)),
                    # 该谱系下已归到房支的条目不再重复展示(背景留谱系直挂)
                    ContentEntry.branch_id.is_(None),
                )
                .order_by(ContentEntry.type, ContentEntry.page_no, ContentEntry.id)
                .limit(LINEAGE_LEVEL_LIMIT)
                .all()
            )
            for r in lrows:
                entries.append(_entry_payload(r, "lineage"))

        # 人物命中在前,背景在后;组内按类型→页码稳定排序
        order = {"person": 0, "branch": 1, "lineage": 2}
        entries.sort(
            key=lambda x: (
                order.get(x["scope"], 9),
                x["type"],
                x["page_no"] or 0,
            )
        )
        return entries
    finally:
        if db is None:
            own.close()


def _session():
    from app.core.database import SessionLocal

    return SessionLocal()
