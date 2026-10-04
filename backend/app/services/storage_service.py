"""存储清理：扫描孤儿数据，人工在「系统设置 → 存储清理」确认后再删除。

扫描四类「没用的数据」：
  1. local_dir       —— 本地孤儿任务目录（UPLOAD_DIR/tasks/{task_id}，PG 里已无该任务）
  2. minio_prefix    —— MinIO 孤儿页面图前缀（scans/{task_id}/，PG 里已无该任务）
  3. loose_file      —— 上传根目录散落文件（手动放的源 PDF、临时诊断脚本等）
  4. orphan_content  —— 孤儿谱书内容条目 + 检索向量（content_entries 的 lineage_id
                        在图谱中已无对应谱系，如谱系已删除后残留的文字与向量）

安全性：运行中的任务一定在 PG import_tasks 里有记录，因此天然不会被判为
孤儿；删除前会再次校验「该 task_id 不在库中」，避免扫描到删除之间的竞态误删。

扫描结果缓存在 AppSetting(storage_scan_result)，后台每 24 小时自动扫一次，
页面打开时直接展示上次结果，也可点「立即扫描」实时重扫。
"""
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import func

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.orm import AppSetting, ContentEntry, ImportTask, RagVector
from app.services import file_service

logger = logging.getLogger("genealogy.storage")

SCAN_RESULT_KEY = "storage_scan_result"
SCAN_INTERVAL_SECONDS = 24 * 3600

# 名称白名单：防止 key 里夹带 ../ 等路径穿越
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,200}$")


def _dir_stat(path: str) -> Tuple[int, int]:
    """返回 (总字节数, 文件数)。"""
    total = 0
    count = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
            count += 1
    return total, count


def _mtime_iso(path: str) -> str:
    try:
        ts = os.path.getmtime(path)
    except OSError:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _known_task_ids() -> set:
    with SessionLocal() as db:
        return {t for (t,) in db.query(ImportTask.task_id).all()}


_sync_driver = None


def _sync_neo4j():
    """独立同步 Neo4j 连接（扫描在线程池中执行）。

    不能复用 app.core.database.driver：那是绑定主事件循环的 async driver，
    在工作线程里 await 会报「Future attached to a different loop」。
    """
    global _sync_driver
    if _sync_driver is None:
        from neo4j import GraphDatabase

        _sync_driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _sync_driver


def _existing_lineage_ids(ids: List[str]) -> Set[str]:
    """返回图谱（Neo4j）中仍存在的 lineage_id 集合，用于判定孤儿谱书内容。

    查询失败时返回全部入参（即一律不判为孤儿），避免误删。
    """
    if not ids:
        return set()
    try:
        with _sync_neo4j().session() as session:
            rec = session.run(
                "UNWIND $ids AS id MATCH (l:Lineage {lineage_id: id}) "
                "RETURN collect(DISTINCT l.lineage_id) AS ids",
                ids=ids,
            ).single()
            return set(rec["ids"]) if rec and rec["ids"] else set()
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询图谱谱系失败（跳过孤儿内容判定）: %s", exc)
        return set(ids)


def scan_orphans() -> Dict:
    """扫描孤儿数据并缓存结果（供页面展示 / 人工勾选删除）。"""
    known = _known_task_ids()
    items: List[Dict] = []
    tasks_dir = os.path.join(settings.UPLOAD_DIR, "tasks")

    # 1) 本地孤儿任务目录
    if os.path.isdir(tasks_dir):
        for name in sorted(os.listdir(tasks_dir)):
            path = os.path.join(tasks_dir, name)
            if not os.path.isdir(path) or name in known:
                continue
            size, count = _dir_stat(path)
            items.append(
                {
                    "key": f"local:{name}",
                    "kind": "local_dir",
                    "name": name,
                    "path": path,
                    "size": size,
                    "files": count,
                    "mtime": _mtime_iso(path),
                    "note": "孤儿任务目录（库中已无对应任务记录）",
                }
            )

    # 2) MinIO 孤儿页面图前缀
    try:
        client = file_service._client()  # noqa: SLF001
        prefixes = set()
        for obj in client.list_objects(
            settings.MINIO_BUCKET, prefix="scans/", recursive=True
        ):
            parts = (obj.object_name or "").split("/")
            if len(parts) >= 2 and parts[1]:
                prefixes.add(parts[1])
        for tid in sorted(prefixes):
            if tid in known:
                continue
            total = 0
            count = 0
            latest = ""
            for obj in client.list_objects(
                settings.MINIO_BUCKET, prefix=f"scans/{tid}/", recursive=True
            ):
                total += obj.size or 0
                count += 1
                if obj.last_modified:
                    latest = obj.last_modified.isoformat()
            items.append(
                {
                    "key": f"minio:{tid}",
                    "kind": "minio_prefix",
                    "name": tid,
                    "path": f"scans/{tid}/",
                    "size": total,
                    "files": count,
                    "mtime": latest,
                    "note": "孤儿页面图（MinIO，库中已无对应任务记录）",
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("扫描 MinIO 孤儿页面图失败: %s", exc)

    # 3) 上传根目录散落文件（源 PDF / 临时脚本等）
    try:
        for name in sorted(os.listdir(settings.UPLOAD_DIR)):
            path = os.path.join(settings.UPLOAD_DIR, name)
            if os.path.isdir(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            items.append(
                {
                    "key": f"file:{name}",
                    "kind": "loose_file",
                    "name": name,
                    "path": path,
                    "size": size,
                    "files": 1,
                    "mtime": _mtime_iso(path),
                    "note": "上传根目录散落文件（源 PDF / 临时脚本，非任务数据）",
                }
            )
    except OSError as exc:
        logger.warning("扫描上传根目录失败: %s", exc)

    # 4) 孤儿谱书内容条目 + 检索向量（图谱中已无该谱系，如谱系删除后残留）
    #    仅判定有 lineage_id 的条目；无归属的人工补录（lineage_id 为空）不判孤儿
    try:
        with SessionLocal() as db:
            rows = (
                db.query(ContentEntry.lineage_id, func.count(ContentEntry.id))
                .filter(ContentEntry.lineage_id.isnot(None))
                .group_by(ContentEntry.lineage_id)
                .all()
            )
        lids = [r[0] for r in rows if r[0]]
        alive = _existing_lineage_ids(lids)
        for lid, cnt in rows:
            if not lid or lid in alive:
                continue
            items.append(
                {
                    "key": f"content:{lid}",
                    "kind": "orphan_content",
                    "name": lid,
                    "path": f"content_entries + rag_vectors（lineage_id={lid}）",
                    "size": 0,  # PG 行，不占文件空间
                    "files": int(cnt),
                    "mtime": "",
                    "note": "孤儿谱书内容与检索向量（图谱中已无该谱系；条目不会被图谱浏览看到，但会占检索索引）",
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("扫描孤儿谱书内容失败: %s", exc)

    result = {
        "items": items,
        "total_size": sum(i["size"] for i in items),
        "total_items": len(items),
        "scanned_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _save_result(result)
    return result


def _save_result(result: Dict) -> None:
    try:
        with SessionLocal() as db:
            row = (
                db.query(AppSetting)
                .filter(AppSetting.key == SCAN_RESULT_KEY)
                .first()
            )
            value = json.dumps(result, ensure_ascii=False)
            if row is None:
                db.add(AppSetting(key=SCAN_RESULT_KEY, value=value))
            else:
                row.value = value
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存存储扫描结果失败: %s", exc)


def load_result() -> Optional[Dict]:
    try:
        with SessionLocal() as db:
            row = (
                db.query(AppSetting)
                .filter(AppSetting.key == SCAN_RESULT_KEY)
                .first()
            )
            if not row or not row.value:
                return None
            return json.loads(row.value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取存储扫描结果失败: %s", exc)
        return None


async def remove_items(keys: List[str]) -> Dict:
    """按 key 删除孤儿数据（再次校验，绝不删在库任务的数据）。

    返回 {removed: [...], skipped: [...], freed: 释放字节数}。
    """
    known = _known_task_ids()
    removed: List[str] = []
    skipped: List[Dict[str, str]] = []
    freed = 0

    for key in keys or []:
        kind, _sep, name = key.partition(":")
        if not name or not SAFE_NAME_RE.match(name):
            skipped.append({"key": key, "reason": "名称不合法，已跳过"})
            continue

        try:
            if kind == "local":
                if name in known:
                    skipped.append({"key": key, "reason": "任务记录仍存在，跳过"})
                    continue
                path = os.path.join(settings.UPLOAD_DIR, "tasks", name)
                if not os.path.isdir(path):
                    skipped.append({"key": key, "reason": "目录已不存在"})
                    continue
                size, _ = _dir_stat(path)
                await _rmtree_async(path)
                freed += size
                removed.append(key)

            elif kind == "minio":
                if name in known:
                    skipped.append({"key": key, "reason": "任务记录仍存在，跳过"})
                    continue
                prefix = f"scans/{name}/"
                # 先统计再删，便于汇报释放空间
                size = 0
                try:
                    client = file_service._client()  # noqa: SLF001
                    for obj in client.list_objects(
                        settings.MINIO_BUCKET, prefix=prefix, recursive=True
                    ):
                        size += obj.size or 0
                except Exception:  # noqa: BLE001
                    pass
                n = await file_service.remove_prefix(prefix)
                if not n:
                    skipped.append({"key": key, "reason": "对象已不存在"})
                    continue
                freed += size
                removed.append(key)

            elif kind == "file":
                path = os.path.join(settings.UPLOAD_DIR, name)
                if not os.path.isfile(path):
                    skipped.append({"key": key, "reason": "文件已不存在"})
                    continue
                size = os.path.getsize(path)
                os.remove(path)
                freed += size
                removed.append(key)

            elif kind == "content":
                # 删除前再次校验：图谱里若又出现该谱系（扫描后被重建）则绝不删
                if name in _existing_lineage_ids([name]):
                    skipped.append({"key": key, "reason": "该谱系仍存在，跳过"})
                    continue
                with SessionLocal() as db:
                    entry_ids = [
                        e[0]
                        for e in db.query(ContentEntry.entry_id)
                        .filter(ContentEntry.lineage_id == name)
                        .all()
                    ]
                    if not entry_ids:
                        skipped.append({"key": key, "reason": "条目已不存在"})
                        continue
                    db.query(RagVector).filter(
                        RagVector.entry_id.in_(entry_ids)
                    ).delete(synchronize_session=False)
                    db.query(ContentEntry).filter(
                        ContentEntry.entry_id.in_(entry_ids)
                    ).delete(synchronize_session=False)
                    db.commit()
                removed.append(key)
                logger.info("清理孤儿谱书内容：谱系 %s，共 %d 条", name, len(entry_ids))

            else:
                skipped.append({"key": key, "reason": f"未知类型 {kind}"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("清理 %s 失败: %s", key, exc)
            skipped.append({"key": key, "reason": f"删除失败：{exc}"})

    return {"removed": removed, "skipped": skipped, "freed": freed}


async def _rmtree_async(path: str) -> None:
    """目录删除放线程池，避免大目录阻塞事件循环。"""
    import asyncio

    await asyncio.get_event_loop().run_in_executor(
        None, lambda: shutil.rmtree(path, ignore_errors=True)
    )
