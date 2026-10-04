"""SQLAlchemy ORM 模型（PostgreSQL）。"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    full_name = Column(String(100))
    email = Column(String(100))
    role = Column(String(20), default="viewer")  # admin / editor / viewer
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)  # 操作员新建/重置密码后为 True：下次登录须先改密
    permissions = Column(JSONB, nullable=True)  # 操作员可访问模块清单（None=全部；admin/viewer 忽略该字段）
    # 登录态版本号：每次登录 +1 并写入 JWT（claim tv）；校验不一致即判定为「已在别处登录」，
    # 使之前签发的 token 全部失效。 admin 重置/停用账号后也可借此踢线。
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class FileMetadata(Base):
    __tablename__ = "file_metadata"

    id = Column(Integer, primary_key=True)
    doc_id = Column(String(50), unique=True, nullable=False)
    person_id = Column(String(50))
    title = Column(String(200))
    type = Column(String(50))
    file_path = Column(String(500), nullable=False)
    file_size = Column(BigInteger)
    mime_type = Column(String(100))
    uploaded_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.now)

    uploader = relationship("User")


class ImportTask(Base):
    __tablename__ = "import_tasks"
    __table_args__ = (UniqueConstraint("task_id"),)

    id = Column(Integer, primary_key=True)
    task_id = Column(String(50), unique=True, nullable=False)
    file_path = Column(String(500), nullable=False)
    file_type = Column(String(20))
    file_size = Column(BigInteger)  # 字节数
    file_sha256 = Column(String(64))  # 内容 sha256，用于上传重复文件检测（改名不影响）
    total_pages = Column(Integer, default=0)
    done_pages = Column(Integer, default=0)
    status = Column(String(20), default="pending")  # pending/running/done/failed
    stage = Column(String(20), default="")  # converting/extracting/done/failed
    # 暂停原因（仅 status=paused 时有意义）：manual=人工/运维暂停；
    # priority_ai=整卷整理为给 AI 识别让位而自动暂停（识别优先，识别完自动续跑）。
    # 用于区分二者，避免「人工暂停的整理任务」被让位自动续跑逻辑误恢复。
    pause_reason = Column(String(20))
    result = Column(JSONB)
    error_msg = Column(Text)
    lineage_id = Column(String(50))  # 识别结果写入时自动归属的谱系
    branch_id = Column(String(50))  # 归属房支（可选）
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    applied_at = Column(DateTime)  # 最近一次成功写入图谱时间（归档资格判定用）
    archived_at = Column(DateTime)  # 归档时间；非空 = 已归档只读（页面图片保留不删，禁止写操作）
    archived_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))  # 执行归档操作的用户


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    action = Column(String(50), nullable=False)
    target_type = Column(String(50))
    target_id = Column(String(50))
    detail = Column(Text)
    ip_address = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)


class VisitShare(Base):
    """图谱树分享：管理后台生成的访客只读入口。

    访问短链 = <站点根>/<share_code>（6 位随机短码，全站唯一入口）。
    token 字段仅作数据库内部唯一凭据保留，不再对外提供任何 /visit?token= 形态链接。
    """

    __tablename__ = "visit_shares"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    # 短链码：图谱树分享访问路径 <根>/<share_code>；历史记录为 NULL 时懒补齐
    share_code = Column(String(12), unique=True, nullable=True, index=True)
    name = Column(String(100), nullable=False)
    note = Column(Text)
    allow_search = Column(Boolean, default=True)
    allow_chat = Column(Boolean, default=False)
    chat_model = Column(String(100))  # 可选：本分享问答专用的 LLM 模型名（空=跟随系统全局设置）
    expires_at = Column(DateTime)  # None = 永不过期
    revoked = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.now)

    creator = relationship("User")


class DashboardShare(Base):
    """仪表盘分享：把「概览大屏」统计以公开链接分享给外部访客。

    访问短链 = <站点根>/d<share_code>（share_code 为 6 位随机短码，URL 加 'd' 前缀
    与图谱树分享区分；公开页只展示谱系/人物/分布等统计数据，不暴露内部任务信息）。
    """

    __tablename__ = "dashboard_shares"

    id = Column(Integer, primary_key=True)
    share_code = Column(String(12), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    note = Column(Text)
    expires_at = Column(DateTime)  # None = 永不过期
    revoked = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.now)

    creator = relationship("User")


class AppSetting(Base):
    """系统级 key-value 设置（访客问答 prompt 等）。"""

    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class RagVector(Base):
    """谱书文字内容条目的向量副本（RAG 语义检索用）。

    content_entries 的 active 条目经 embedding 服务（206:30010）向量化后落这里，
    由 rag_service 增量同步；检索时全量余弦扫描（条目量级小，未来量大可换 pgvector）。
    """

    __tablename__ = "rag_vectors"

    id = Column(Integer, primary_key=True)
    entry_id = Column(String(50), unique=True, nullable=False, index=True)
    lineage_id = Column(String(50), index=True)
    branch_id = Column(String(50))
    type = Column(String(50), nullable=False)
    title = Column(String(200), default="")
    text_head = Column(Text)  # 原文开头片段（仅用于展示与校验，全文仍查 content_entries）
    dim = Column(Integer, default=0)
    vector = Column(JSONB)  # [float,...]
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ContentEntry(Base):
    """谱书文字内容条目（源流/迁徙/家规家训/凡例/传记/艺文等）。

    由 AI 识别结果在任务「写入图谱」时聚合而来，也可手工补充；
    按谱系聚合供管理员校对，后续供访客图谱问答/展示使用。
    """

    __tablename__ = "content_entries"

    id = Column(Integer, primary_key=True)
    entry_id = Column(String(50), unique=True, nullable=False)
    task_id = Column(String(50), index=True)  # 来源任务（人工补充可为空）
    lineage_id = Column(String(50), index=True)
    branch_id = Column(String(50))
    type = Column(String(50), nullable=False)  # 见 vision_service.ENTRY_TYPES
    title = Column(String(200), default="")
    text = Column(Text, nullable=False)
    page_no = Column(Integer)  # 来源页码（可对照原图核对）
    source = Column(String(20), default="ai")  # ai=识别聚合 / manual=人工补充
    status = Column(String(20), default="active")  # active/archived
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class TaskPageJson(Base):
    """扫描页级扁平化 AI 结果（ai_meta/notes/persons/content），独立于任务主表 JSONB。

    import_tasks.result.pages 与 scan 界面共用同一份 pages（任务主流程、单页重识别、
    页审核暂存、插图 pass 都会同步写回）；本表为该 pages 的可检索镜像：
    每任务每页一行，data(jsonb) 含 {ai_meta,notes,persons,content,reviewed,failed,
    illustrations}，GIN(jsonb_path_ops) 支持按 JSON 字段过滤（低置信人名/备注疑点/
    正文关键词）与整卷统计。页面数据变化时同步 upsert 单行（见 import_service）。
    """

    __tablename__ = "task_pages_json"
    __table_args__ = (
        UniqueConstraint("task_id", "page_no", name="uq_task_pages_json_task_page"),
        Index(
            "ix_task_pages_json_data_gin",
            "data",
            postgresql_using="gin",
            postgresql_ops={"data": "jsonb_path_ops"},
        ),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(String(50), nullable=False, index=True)
    page_no = Column(Integer, nullable=False)
    data = Column(JSONB, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DeleteRequest(Base):
    """删除申请（09-07 审批流）：操作员删除「扫描件任务 / 谱系」时先落一条
    pending 申请，管理员在系统设置 → 删除审批 通过/驳回后才真正删除。

    status: pending=待审批 / approved=已通过(含目标已不存在自动归档) /
            rejected=已驳回
    snapshot: 提交时的对象快照（任务状态/页数/谱系人物数/档案编号等，供审批页展示）
    """

    __tablename__ = "delete_requests"

    id = Column(Integer, primary_key=True)
    target_type = Column(String(20), nullable=False)  # task / lineage
    target_id = Column(String(50), nullable=False)
    target_name = Column(String(300), default="")  # 提交时的对象名快照
    force = Column(Boolean, default=False)  # 谱系：提交时是否要求连同人物强制删除
    snapshot = Column(JSONB, nullable=True)
    reason = Column(Text)  # 操作员提交时填写的原因（预留，当前可为空）
    status = Column(String(20), default="pending", nullable=False, index=True)
    submitted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    review_note = Column(Text)  # 管理员审批意见 / 驳回原因
    created_at = Column(DateTime, default=datetime.now)
    reviewed_at = Column(DateTime)  # 审批时间

    submitter = relationship("User", foreign_keys=[submitted_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
