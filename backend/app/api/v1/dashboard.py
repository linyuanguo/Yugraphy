"""概览/仪表盘：跨库汇总统计（Neo4j 图谱 + PG 任务/内容），供首页 BI 卡片展示。

数据来源：
- Neo4j：谱系 / 房支 / 人物总数、世代分布、房支人数 TOP、性别分布
- PG：导入任务状态分布、页数进度、失败页数、谱书内容条目、检索向量、最近任务

只读接口，任一数据源异常时降级为 0 / 空列表，不影响页面其它部分。
"""
import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.database import driver, get_db
from app.core.security import require_module_read, require_module_write
from app.models.orm import (
    AppSetting,
    ContentEntry,
    DashboardShare,
    ImportTask,
    RagVector,
    User,
)
from app.models.schemas import (
    DashDisplayConfig,
    DashDisplayConfigUpdate,
    DashboardOut,
    DashboardShareCreate,
    DashboardShareOut,
    DashboardSharePublicOut,
    DashboardShareUpdate,
    DashboardSlice,
    DashboardTaskItem,
    SysCpuOut,
    SysDatabaseOut,
    SysDiskOut,
    SysMemoryOut,
    SysMinioOut,
    SysNeo4jOut,
    SysNetworkOut,
    SystemStatsOut,
    SysVectorOut,
)
from app.services import system_service
from app.services.audit_service import log_action

router = APIRouter(prefix="/dashboard", tags=["概览"])

logger = logging.getLogger("genealogy.dashboard")


# ============ 大屏展示参数（内部概览 + /d 公开大屏共用一份全局配置） ============
_DISPLAY_CFG_KEY = "dash_display_cfg"
_DISPLAY_CFG_DEFAULTS = {"refresh_seconds": 30, "switch_seconds": 20, "roll_numbers": True}


def _load_display_config(db: Session) -> dict:
    """读取全局大屏参数（未配置 = 内置默认），数值规整到合理区间。"""
    out = dict(_DISPLAY_CFG_DEFAULTS)
    row = db.query(AppSetting).filter(AppSetting.key == _DISPLAY_CFG_KEY).first()
    if row and row.value:
        try:
            data = json.loads(row.value)
            if isinstance(data, dict):
                for k in ("refresh_seconds", "switch_seconds"):
                    try:
                        out[k] = int(data.get(k, out[k]))
                    except (TypeError, ValueError):
                        pass
                if isinstance(data.get("roll_numbers"), bool):
                    out["roll_numbers"] = data["roll_numbers"]
        except (TypeError, ValueError):
            logger.warning("大屏展示参数解析失败，使用默认值: %s", row.value)
    # 0 = 关闭；非 0 需在合理区间（刷新 5~3600s，轮播 3~3600s）
    out["refresh_seconds"] = max(0, min(3600, out["refresh_seconds"]))
    out["switch_seconds"] = max(0, min(3600, out["switch_seconds"]))
    if 0 < out["refresh_seconds"] < 5:
        out["refresh_seconds"] = 5
    if 0 < out["switch_seconds"] < 3:
        out["switch_seconds"] = 3
    return out


def _save_display_config(db: Session, data: DashDisplayConfigUpdate) -> dict:
    cfg = _load_display_config(db)
    if data.refresh_seconds is not None:
        cfg["refresh_seconds"] = data.refresh_seconds
    if data.switch_seconds is not None:
        cfg["switch_seconds"] = data.switch_seconds
    if data.roll_numbers is not None:
        cfg["roll_numbers"] = data.roll_numbers
    # 校验区间并二次规整
    if cfg["refresh_seconds"] not in (0,) and not 5 <= cfg["refresh_seconds"] <= 3600:
        raise HTTPException(status_code=400, detail="自动刷新间隔须为 0（关闭）或 5~3600 秒")
    if cfg["switch_seconds"] not in (0,) and not 3 <= cfg["switch_seconds"] <= 3600:
        raise HTTPException(status_code=400, detail="面板轮播间隔须为 0（关闭）或 3~3600 秒")
    cfg["refresh_seconds"] = int(cfg["refresh_seconds"])
    cfg["switch_seconds"] = int(cfg["switch_seconds"])
    row = db.query(AppSetting).filter(AppSetting.key == _DISPLAY_CFG_KEY).first()
    value = json.dumps(cfg, ensure_ascii=False)
    if row is None:
        db.add(AppSetting(key=_DISPLAY_CFG_KEY, value=value))
    else:
        row.value = value
    db.commit()
    return cfg


@router.get("/display-config", response_model=DashDisplayConfig)
def get_display_config(
    db: Session = Depends(get_db),
    _: User = Depends(require_module_read("dashboard")),
):
    """读取全局大屏展示参数（概览页右上角设置弹窗回显用）。"""
    return DashDisplayConfig(**_load_display_config(db))


@router.put("/display-config", response_model=DashDisplayConfig)
def update_display_config(
    data: DashDisplayConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("dashboard")),
):
    """更新全局大屏展示参数，内部概览页与全部 /d 公开大屏即时生效。"""
    cfg = _save_display_config(db, data)
    log_action(
        current_user.id,
        "update_dashboard_display_config",
        "app_setting",
        "dashboard",
        "更新大屏展示参数",
    )
    return DashDisplayConfig(**cfg)


async def _count_node(label: str) -> int:
    """统计图谱节点数（label 为内部固定常量，无注入风险）。"""
    try:
        async with driver.session() as session:
            res = await session.run(f"MATCH (n:{label}) RETURN count(n) AS c")
            rec = await res.single()
            return int(rec["c"]) if rec else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("图谱节点统计失败 %s: %s", label, exc)
        return 0


async def _graph_slices() -> Dict[str, List[DashboardSlice]]:
    """世代分布 / 房支人数 TOP10 / 性别分布。"""
    generations: List[DashboardSlice] = []
    branches_top: List[DashboardSlice] = []
    genders: List[DashboardSlice] = []

    try:
        async with driver.session() as session:
            res = await session.run(
                "MATCH (p:Person) WHERE p.generation IS NOT NULL "
                "RETURN toString(p.generation) AS k, count(*) AS c ORDER BY k"
            )
            generations = [
                DashboardSlice(label=f"第{r['k']}代", value=int(r["c"]))
                for r in (await res.data())
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("世代分布统计失败: %s", exc)

    try:
        async with driver.session() as session:
            res = await session.run(
                "MATCH (p:Person)-[:IN_BRANCH]->(b:Branch) "
                "OPTIONAL MATCH (b)-[:OF_LINEAGE]->(l:Lineage) "
                "RETURN coalesce(l.name, '') + '·' + b.name AS k, count(p) AS c "
                "ORDER BY c DESC LIMIT 10"
            )
            branches_top = [
                DashboardSlice(label=str(r["k"]), value=int(r["c"]))
                for r in (await res.data())
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("房支分布统计失败: %s", exc)

    try:
        async with driver.session() as session:
            res = await session.run(
                "MATCH (p:Person) "
                "RETURN coalesce(p.gender, 'unknown') AS k, count(*) AS c ORDER BY c DESC"
            )
            genders = [
                DashboardSlice(label=str(r["k"]), value=int(r["c"]))
                for r in (await res.data())
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("性别分布统计失败: %s", exc)

    return {
        "generations": generations,
        "branches_top": branches_top,
        "genders": genders,
    }


@router.get("/overview", response_model=DashboardOut)
async def dashboard_overview(
    db: Session = Depends(get_db),
    _: User = Depends(require_module_read("dashboard")),
):
    """首页概览：一次请求返回全部卡片与分布数据（操作员需被授予「概览」模块）。"""
    lineages = await _count_node("Lineage")
    branches = await _count_node("Branch")
    persons = await _count_node("Person")
    slices = await _graph_slices()

    status_counts = dict(
        db.query(ImportTask.status, func.count(ImportTask.id))
        .group_by(ImportTask.status)
        .all()
    )
    pages_total, pages_done = (
        db.query(
            func.coalesce(func.sum(ImportTask.total_pages), 0),
            func.coalesce(func.sum(ImportTask.done_pages), 0),
        ).first()
        or (0, 0)
    )
    # 失败页数：各任务 result.failed_pages 数组长度之和
    failed_pages = (
        db.execute(
            text(
                "SELECT coalesce(sum(jsonb_array_length(result->'failed_pages')), 0) "
                "FROM import_tasks "
                "WHERE result IS NOT NULL "
                "AND jsonb_typeof(result->'failed_pages') = 'array'"
            )
        ).scalar()
        or 0
    )
    entries = (
        db.query(func.count(ContentEntry.id))
        .filter(ContentEntry.status == "active")
        .scalar()
        or 0
    )
    vectors = db.query(func.count(RagVector.id)).scalar() or 0

    recent = (
        db.query(ImportTask)
        .order_by(ImportTask.updated_at.desc())
        .limit(6)
        .all()
    )
    recent_items = [
        DashboardTaskItem(
            task_id=t.task_id,
            name=os.path.basename(t.file_path or "") or t.task_id[:12],
            status=t.status or "",
            done_pages=int(t.done_pages or 0),
            total_pages=int(t.total_pages or 0),
            updated_at=t.updated_at.isoformat() if t.updated_at else None,
        )
        for t in recent
    ]

    cfg = _load_display_config(db)

    return DashboardOut(
        lineages=lineages,
        branches=branches,
        persons=persons,
        content_entries=int(entries),
        vectors=int(vectors),
        tasks_total=sum(status_counts.values()),
        tasks_done=int(status_counts.get("done", 0)),
        tasks_failed=int(status_counts.get("failed", 0)),
        tasks_running=int(
            status_counts.get("running", 0) + status_counts.get("pending", 0)
        ),
        pages_total=int(pages_total or 0),
        pages_done=int(pages_done or 0),
        failed_pages=int(failed_pages),
        generations=slices["generations"],
        branches_top=slices["branches_top"],
        genders=slices["genders"],
        recent_tasks=recent_items,
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        refresh_seconds=int(cfg["refresh_seconds"]),
        switch_seconds=int(cfg["switch_seconds"]),
        roll_numbers=bool(cfg["roll_numbers"]),
    )


@router.get("/system-stats", response_model=SystemStatsOut)
async def system_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_module_read("dashboard")),
):
    """服务器运行状态：CPU / 内存 / 磁盘容量与 IO / 网络 + MinIO / PostgreSQL / Neo4j / 向量索引。

    概览页「服务器运行状态」卡片与其详情抽屉的数据源。仅在内部概览页使用，
    不进 /d 公开大屏（避免对外暴露主机信息）。
    """
    # 主机指标含 /proc 读取与 0.15s 短采样 → 放线程池；MinIO 遍历同理
    host = await asyncio.to_thread(system_service.collect_host)
    minio = await asyncio.to_thread(system_service.minio_stats)
    return SystemStatsOut(
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        uptime_h=float(host.get("uptime_h") or 0.0),
        cpu=SysCpuOut(**host["cpu"]),
        memory=SysMemoryOut(**host["memory"]),
        disk=SysDiskOut(**host["disk"]),
        network=SysNetworkOut(**host["network"]),
        minio=SysMinioOut(**minio),
        database=SysDatabaseOut(**system_service.postgres_stats(db)),
        neo4j=SysNeo4jOut(**(await system_service.neo4j_stats())),
        vector=SysVectorOut(**system_service.vector_stats(db)),
    )


# ============ 仪表盘分享（管理端 + 公开大屏页） ============
_DASH_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去掉易混淆 0/O/1/I


def _gen_dash_code(db: Session) -> str:
    """生成不与现存仪表盘分享冲突的 6 位短链码。"""
    for _ in range(12):
        code = "".join(
            random.SystemRandom().choice(_DASH_ALPHABET) for _ in range(6)
        )
        exists = (
            db.query(DashboardShare.id)
            .filter(DashboardShare.share_code == code)
            .first()
        )
        if not exists:
            return code
    raise HTTPException(status_code=500, detail="短链码生成失败，请重试")


def _check_dash_code(code: str, db: Session) -> DashboardShare:
    """按短链码校验仪表盘分享有效性（公开访问 <站点根>/d<share_code> 的唯一入口）。"""
    if not code:
        raise HTTPException(status_code=401, detail="分享链接无效")
    share = (
        db.query(DashboardShare)
        .filter(
            DashboardShare.share_code == code.upper(),
            DashboardShare.revoked == False,  # noqa: E712
        )
        .first()
    )
    if not share:
        raise HTTPException(status_code=401, detail="分享链接无效")
    if share.expires_at and share.expires_at < datetime.now():
        raise HTTPException(status_code=401, detail="分享链接已过期")
    return share


@router.get("/shares", response_model=List[DashboardShareOut])
def list_dash_shares(
    db: Session = Depends(get_db),
    # 大屏分享属于「分享访问」，授予以 visit 或 dashboard 任一模块的操作员
    _: User = Depends(require_module_write("dashboard", "visit", "visit_dash")),
):
    """仪表盘分享列表（管理端「分享访问 → 仪表盘分享」）。"""
    return (
        db.query(DashboardShare)
        .order_by(DashboardShare.created_at.desc())
        .all()
    )


@router.post("/shares", response_model=DashboardShareOut, status_code=201)
def create_dash_share(
    data: DashboardShareCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("dashboard", "visit", "visit_dash")),
):
    share = DashboardShare(
        share_code=_gen_dash_code(db),
        name=data.name,
        note=data.note,
        expires_at=(
            datetime.now() + timedelta(days=data.expires_days)
            if data.expires_days
            else None
        ),
        revoked=False,
        created_by=current_user.id,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    log_action(
        current_user.id,
        "create_dashboard_share",
        "dashboard_share",
        str(share.id),
        f"创建仪表盘分享 {share.name}",
    )
    return share


@router.put("/shares/{share_id}", response_model=DashboardShareOut)
def update_dash_share(
    share_id: int,
    data: DashboardShareUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("dashboard", "visit", "visit_dash")),
):
    share = db.query(DashboardShare).filter(DashboardShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="分享不存在")
    if share.revoked:
        raise HTTPException(status_code=400, detail="已撤销的分享不可编辑")
    if data.name is not None:
        share.name = data.name
    if data.note is not None:
        share.note = data.note
    if "expires_at" in data.model_fields_set:
        exp = data.expires_at
        if exp is not None:
            if exp.tzinfo is not None:
                exp = exp.astimezone().replace(tzinfo=None)
            if exp <= datetime.now():
                raise HTTPException(status_code=400, detail="到期时间需晚于当前时间")
        share.expires_at = exp
    db.commit()
    db.refresh(share)
    log_action(
        current_user.id,
        "update_dashboard_share",
        "dashboard_share",
        str(share_id),
        f"更新仪表盘分享 {share.name}",
    )
    return share


@router.delete("/shares/{share_id}", status_code=204)
def revoke_dash_share(
    share_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("dashboard", "visit", "visit_dash")),
):
    share = db.query(DashboardShare).filter(DashboardShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="分享不存在")
    share.revoked = True
    db.commit()
    log_action(
        current_user.id,
        "revoke_dashboard_share",
        "dashboard_share",
        str(share_id),
        f"撤销仪表盘分享 {share.name}",
    )


@router.delete("/shares/{share_id}/record", status_code=204)
def delete_dash_share_record(
    share_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_write("dashboard", "visit", "visit_dash")),
):
    """彻底删除仪表盘分享记录（清理已撤销/废弃记录，链接随之永久失效）。"""
    share = db.query(DashboardShare).filter(DashboardShare.id == share_id).first()
    if not share:
        raise HTTPException(status_code=404, detail="分享不存在")
    name = share.name
    db.delete(share)
    db.commit()
    log_action(
        current_user.id,
        "delete_dashboard_share",
        "dashboard_share",
        str(share_id),
        f"删除仪表盘分享 {name}",
    )


@router.get("/s/{code}", response_model=DashboardSharePublicOut)
async def dashboard_share_view(
    code: str,
    db: Session = Depends(get_db),
):
    """仪表盘分享的公开大屏数据（<站点根>/d<share_code>，仅统计信息，不含内部任务/进度）。"""
    share = _check_dash_code(code, db)
    lineages = await _count_node("Lineage")
    branches = await _count_node("Branch")
    persons = await _count_node("Person")
    slices = await _graph_slices()
    entries = (
        db.query(func.count(ContentEntry.id))
        .filter(ContentEntry.status == "active")
        .scalar()
        or 0
    )
    cfg = _load_display_config(db)
    return DashboardSharePublicOut(
        name=share.name,
        note=share.note,
        lineages=lineages,
        branches=branches,
        persons=persons,
        content_entries=int(entries),
        generations=slices["generations"],
        branches_top=slices["branches_top"],
        genders=slices["genders"],
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        refresh_seconds=int(cfg["refresh_seconds"]),
        switch_seconds=int(cfg["switch_seconds"]),
        roll_numbers=bool(cfg["roll_numbers"]),
    )
