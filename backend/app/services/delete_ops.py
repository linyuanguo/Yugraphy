"""删除执行与删除审批（09-07 用户拍板）的公共逻辑。

流程：
- 管理员删除「扫描件任务 / 谱系」：直接真正删除（沿用原有行为）。
- 操作员（editor）触发同类删除：不真正删除，先落一条 delete_requests
  （status=pending）到数据库；管理员在「系统设置 → 删除审批」通过/驳回后才
  真正删除。审批执行时若目标已不存在（已被其它途径先删/清理），自动按
  “删除目的已达成”归档为 approved。

真正执行删除的代码全部集中在本模块（任务删除、谱系删除），普通删除接口与
审批通过共用同一份实现，避免两处逻辑漂移。
"""
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.orm import (
    ContentEntry,
    DeleteRequest,
    ImportTask,
    RagVector,
    TaskPageJson,
    User,
)
from app.core.database import driver
from app.services import import_service, lineage_service, task_registry
from app.services.audit_service import log_action

logger = logging.getLogger("genealogy.delete_ops")

TASK = "task"
LINEAGE = "lineage"
TARGET_TYPES = (TASK, LINEAGE)


class DeleteTargetGone(Exception):
    """审批/执行时目标已不存在（任务已删、谱系已删等）。"""


class DeleteNeedsForce(Exception):
    """谱系当前仍有归属人物且未勾选强制删除。"""

    def __init__(self, count: int):
        self.count = count


class DeleteArchived(Exception):
    """任务已被归档为只读档案，删除前须先取回。"""


class PendingDeleteExists(Exception):
    """同一目标已有待审批的删除申请，禁止重复提交。"""

    def __init__(self, request: DeleteRequest, submitter_name: Optional[str]):
        self.request = request
        self.submitter_name = submitter_name


def pending_conflict(db: Session, target_type: str, target_id: str) -> Optional[DeleteRequest]:
    """返回目标上已存在的一条待审批申请；没有则 None。"""
    return (
        db.query(DeleteRequest)
        .filter(
            DeleteRequest.target_type == target_type,
            DeleteRequest.target_id == target_id,
            DeleteRequest.status == "pending",
        )
        .order_by(DeleteRequest.id.asc())
        .first()
    )


def user_display_name(user: Optional[User]) -> str:
    if not user:
        return ""
    return user.full_name or user.username or ""


def _persist_request(
    db: Session,
    actor: User,
    target_type: str,
    target_id: str,
    target_name: str,
    force: bool,
    snapshot: Optional[Dict[str, Any]],
) -> DeleteRequest:
    req = DeleteRequest(
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        force=bool(force),
        snapshot=snapshot or {},
        status="pending",
        submitted_by=actor.id,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def _check_no_conflict(db: Session, target_type: str, target_id: str) -> None:
    dup = pending_conflict(db, target_type, target_id)
    if dup:
        sub = db.query(User).filter(User.id == dup.submitted_by).first()
        raise PendingDeleteExists(dup, user_display_name(sub))


# ============ 提交删除申请（操作员分支） ============

def request_delete_task(db: Session, actor: User, task: ImportTask) -> DeleteRequest:
    """操作员申请删除扫描件任务：只落申请不删除。"""
    _check_no_conflict(db, TASK, task.task_id)
    name = os.path.basename(task.file_path or "") or task.task_id
    snapshot = {
        "file": name,
        "status": task.status or "",
        "stage": task.stage or "",
        "total_pages": task.total_pages or 0,
        "done_pages": task.done_pages or 0,
        "applied": bool(task.applied_at),
    }
    req = _persist_request(db, actor, TASK, task.task_id, name, False, snapshot)
    log_action(
        actor.id,
        "delete_request",
        TASK,
        task.task_id,
        detail=f"提交删除申请：扫描件 {name}（任务状态 {task.status or '未知'}）",
    )
    return req


def request_delete_lineage(db: Session, actor: User, lineage: Dict[str, Any], force: bool) -> DeleteRequest:
    """操作员申请删除谱系：只落申请不删除（force 只记录操作员意图，最终由管理员审批决定）。"""
    _check_no_conflict(db, LINEAGE, lineage["lineage_id"])
    name = lineage["name"] or lineage["lineage_id"]
    snapshot = {
        "name": name,
        "code": lineage.get("code"),
        "person_count": lineage.get("person_count") or 0,
        "branches": len(lineage.get("branches") or []),
        "force": bool(force),
    }
    req = _persist_request(db, actor, LINEAGE, lineage["lineage_id"], name, force, snapshot)
    suffix = "（提交时要求连同人物强制删除）" if force else ""
    log_action(
        actor.id,
        "delete_request",
        LINEAGE,
        lineage["lineage_id"],
        detail=f"提交删除申请：谱系 {name}（含 {snapshot['person_count']} 位人物）{suffix}",
    )
    return req


# ============ 真正执行删除（管理员直接删 / 审批通过共用） ============

async def perform_delete_task(db: Session, task_id: str, actor_id: int) -> Dict[str, Any]:
    """真正删除扫描件任务：记录 + MinIO 页面图 + 本地中间文件 + 页级镜像一并清除。

    正在后台运行（转换/识别/整理）的任务会先取消对应协程（真正打断 AI 调用、
    释放 206 并发槽与任务闸）。若任务已写入图谱，写入的人物/关系不受影响。
    """
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    if not task:
        raise DeleteTargetGone()
    if task.archived_at:
        raise DeleteArchived()
    interrupted = task_registry.cancel_task(task_id)
    removed = await import_service.cleanup_task_pages(task_id)
    await import_service.cleanup_task_local(task_id)
    # 页级扁平化镜像表随任务删除
    db.query(TaskPageJson).filter(TaskPageJson.task_id == task_id).delete(
        synchronize_session=False
    )
    name = os.path.basename(task.file_path or "") or task.task_id
    status = task.status or ""
    db.delete(task)
    db.commit()
    log_action(
        actor_id,
        "delete_import_task",
        TASK,
        task_id,
        detail=(
            f"删除导入任务 {name}（{status}"
            f"{'，已打断后台处理' if interrupted else ''}，共清理 {removed} 个页面图）"
        ),
    )
    return {
        "removed_pages": removed,
        "interrupted": interrupted,
        "target_name": name,
        "status": status,
    }


async def purge_task_written(db: Session, task_id: str) -> Dict[str, int]:
    """取回归档任务时，清理该任务此前「写入图谱」产生的谱系数据与检索向量。

    写入链路（import_service apply）：content_entries（task_id 归属）逐条写库并为其
    生成 rag_vectors；Neo4j 中人物通过 (p)-[:HAS_DOCUMENT]->(d:Document {doc_id=task_id})
    标记来源。

    这里：
    - 删除该任务的 content_entries 与对应 rag_vectors（按 entry_id）；
    - 删除 Neo4j 中「仅由本任务文档贡献、未被其它任务文档引用」的人物
      （其唯一 HAS_DOCUMENT 即本任务）。人物若同时被其它任务文档引用则保留，
      避免误删跨任务共享人物。

    取回后任务回到可编辑状态，重识别/重整理/再写入时会自动重建这些数据与人物。
    """
    rows = db.query(ContentEntry.entry_id).filter(ContentEntry.task_id == task_id).all()
    entry_ids = [r[0] for r in rows]
    removed_vectors = 0
    if entry_ids:
        removed_vectors = (
            db.query(RagVector)
            .filter(RagVector.entry_id.in_(entry_ids))
            .delete(synchronize_session=False)
        )
    removed_entries = (
        db.query(ContentEntry)
        .filter(ContentEntry.task_id == task_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    removed_persons = 0
    async with driver.session() as session:
        rec = await session.run(
            "MATCH (p:Person)-[:HAS_DOCUMENT]->(d:Document {doc_id: $doc_id}) "
            "WHERE NOT EXISTS { (p)-[:HAS_DOCUMENT]->(dd:Document) WHERE dd <> d } "
            "DETACH DELETE p RETURN count(p) AS n",
            doc_id=task_id,
        )
        row = await rec.single()
        removed_persons = row["n"] if row else 0
    # ---- 谱系清理：若该任务归属的卷号谱系在取回后已「空且独占」，则从谱系管理移除 ----
    # 「空」：谱系下已无归属人物（无 BELONGS_TO/IN_BRANCH），且本谱系下也无残留内容条目；
    # 「独占」：没有其它导入任务仍引用该谱系（同一卷号若被多任务共享则保留，避免误删整卷）。
    # 满足才删谱系（连带其空房支），使取回后「谱系管理」里对应卷号列表项一并消失；
    # 人工整理内容 / 跨任务共享人物因不满足「空且独占」而不会被误删。
    removed_lineages = 0
    task = db.query(ImportTask).filter(ImportTask.task_id == task_id).first()
    lineage_id = task.lineage_id if task else None
    if lineage_id:
        # 其它任务是否仍引用该谱系
        other_refs = (
            db.query(ImportTask.task_id)
            .filter(ImportTask.lineage_id == lineage_id, ImportTask.task_id != task_id)
            .count()
        )
        # 该谱系下是否仍有（其它来源的）内容条目 / 检索向量
        residual_entries = (
            db.query(ContentEntry.entry_id)
            .filter(ContentEntry.lineage_id == lineage_id, ContentEntry.task_id != task_id)
            .count()
        )
        if other_refs == 0 and residual_entries == 0:
            # 谱系下已无归属人物/房支时才安全删除（连空房支一起，不删人物）
            async with driver.session() as session:
                rec = await session.run(
                    "MATCH (l:Lineage {lineage_id: $lineage_id}) "
                    "WHERE NOT EXISTS { MATCH (p:Person) "
                    "  WHERE (p)-[:BELONGS_TO]->(l) "
                    "  OR (p)-[:IN_BRANCH]->(:Branch)-[:OF_LINEAGE]->(l) } "
                    "WITH l "
                    "OPTIONAL MATCH (l)<-[:OF_LINEAGE]-(b:Branch) "
                    "WITH l, collect(DISTINCT b) AS bs "
                    "FOREACH (x IN bs | DETACH DELETE x) "
                    "DETACH DELETE l RETURN count(l) AS n",
                    lineage_id=lineage_id,
                )
                row = await rec.single()
                if row and row["n"]:
                    removed_lineages = row["n"]
            if removed_lineages:
                # 当前任务不再指向已删除的谱系，避免悬空归属引用
                task.lineage_id = None
                db.commit()
    return {
        "removed_entries": removed_entries or 0,
        "removed_vectors": removed_vectors or 0,
        "removed_persons": removed_persons or 0,
        "removed_lineages": removed_lineages,
    }


async def perform_delete_lineage(db: Session, lineage_id: str, force: bool, actor_id: int) -> Dict[str, Any]:
    """真正删除谱系：Neo4j 谱系/房支/人物与关联关系 + 内容条目 + 检索向量一并清除。

    人物数 > 0 且未 force 时抛 DeleteNeedsForce（管理员须明确勾选强制删除）。
    """
    items = await lineage_service.list_lineages()
    lineage = next((it for it in items if it["lineage_id"] == lineage_id), None)
    if not lineage:
        raise DeleteTargetGone()
    count = lineage["person_count"] or 0
    if count > 0 and not force:
        raise DeleteNeedsForce(count)
    ok, removed_persons = await lineage_service.delete_lineage(lineage_id, force=force)
    if not ok:
        raise DeleteTargetGone()
    removed_entries = (
        db.query(ContentEntry)
        .filter(ContentEntry.lineage_id == lineage_id)
        .delete(synchronize_session=False)
    )
    removed_vectors = (
        db.query(RagVector)
        .filter(RagVector.lineage_id == lineage_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    name = lineage["name"] or lineage_id
    log_action(
        actor_id,
        "delete_lineage",
        LINEAGE,
        lineage_id,
        detail=(
            f"删除谱系 {name}"
            + (f"（连带删除 {removed_persons} 位人物）" if removed_persons else "")
            + f"；同步清除该谱系内容条目 {removed_entries} 条、检索向量 {removed_vectors} 条"
        ),
    )
    return {
        "target_name": name,
        "removed_persons": removed_persons,
        "removed_entries": removed_entries,
        "removed_vectors": removed_vectors,
    }


# ============ 序列化 / 目标现状（审批页展示） ============

def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def request_to_dict(req: DeleteRequest) -> Dict[str, Any]:
    return {
        "id": req.id,
        "target_type": req.target_type,
        "target_id": req.target_id,
        "target_name": req.target_name,
        "force": bool(req.force),
        "snapshot": req.snapshot or {},
        "reason": req.reason,
        "status": req.status,
        "submitted_by": req.submitted_by,
        "reviewed_by": req.reviewed_by,
        "review_note": req.review_note,
        "created_at": _fmt(req.created_at),
        "reviewed_at": _fmt(req.reviewed_at),
    }


def decorate_names(db: Session, rows: list) -> list:
    """给序列化后的申请行补上 提交人/审批人 显示名。"""
    ids = set()
    for r in rows:
        if r.get("submitted_by"):
            ids.add(r["submitted_by"])
        if r.get("reviewed_by"):
            ids.add(r["reviewed_by"])
    names: Dict[int, str] = {}
    if ids:
        for u in db.query(User).filter(User.id.in_(ids)).all():
            names[u.id] = user_display_name(u)
    for r in rows:
        r["submitted_by_name"] = names.get(r.get("submitted_by") or 0, "")
        r["reviewed_by_name"] = names.get(r.get("reviewed_by") or 0, "")
    return rows


async def current_target_state(db: Session, req: DeleteRequest) -> Dict[str, Any]:
    """审批时目标当前状态（防提交后变化）：任务/谱系是否仍在、人物数等。"""
    if req.target_type == TASK:
        task = db.query(ImportTask).filter(ImportTask.task_id == req.target_id).first()
        if not task:
            return {"exists": False, "kind": TASK, "note": "任务已不存在（可能已被删除或清理）"}
        return {
            "exists": True,
            "kind": TASK,
            "file": os.path.basename(task.file_path or "") or req.target_id,
            "status": task.status or "",
            "stage": task.stage or "",
            "total_pages": task.total_pages or 0,
            "done_pages": task.done_pages or 0,
            "applied": bool(task.applied_at),
            "archived": bool(task.archived_at),
        }
    try:
        items = await lineage_service.list_lineages()
    except Exception as exc:  # noqa: BLE001
        logger.warning("审批前查询谱系 %s 失败: %s", req.target_id, exc)
        return {"exists": None, "kind": LINEAGE, "note": f"查询谱系当前状态失败：{exc}"}
    lineage = next((it for it in items if it["lineage_id"] == req.target_id), None)
    if not lineage:
        return {"exists": False, "kind": LINEAGE, "note": "谱系已不存在（可能已被删除）"}
    return {
        "exists": True,
        "kind": LINEAGE,
        "name": lineage["name"] or req.target_id,
        "code": lineage.get("code"),
        "person_count": lineage["person_count"] or 0,
        "branches": len(lineage.get("branches") or []),
    }
