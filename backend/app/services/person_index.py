"""全量人名搜索索引缓存。

家谱树联想 / 访客搜索每次 fuzzy 匹配都会全量拉取 Neo4j 人物（约 1s+），
此处把全量 Person 列表缓存在内存；任何人物/谱系写入后调用
invalidate_person_index() 使缓存失效，下次搜索自动重建。
TTL 作为兜底，防止长期运行出现数据漂移。
"""
import time
from typing import List, Optional

_TTL = 300.0
_state: dict = {"persons": None, "built_at": 0.0}


async def get_persons_index() -> Optional[List[dict]]:
    """返回全量人物列表（含谱系/房支字段）。首次或失效后自动重建。"""
    persons = _state["persons"]
    if persons is not None and time.time() - _state["built_at"] < _TTL:
        return persons
    # 延迟导入，避免与 person_service 形成导入环
    from app.services import person_service

    persons, _ = await person_service.list_persons(limit=5000)
    _state["persons"] = persons
    _state["built_at"] = time.time()
    return persons


def invalidate_person_index() -> None:
    """人物增删改后调用，使索引下次访问时重建。"""
    _state["persons"] = None
