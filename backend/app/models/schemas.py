"""Pydantic 模型（请求/响应）。"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============ 认证 ============
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 0  # 有效期（秒），前端据此控制登录态
    must_change_password: bool = False  # 为 True：本次登录后须先修改密码才能继续操作


class LoginRequest(BaseModel):
    username: str
    password: str


class CodeRequest(BaseModel):
    """登录动态码获取请求（服务端签发，与用户名无关、短时有效）。

    username 仅作可选扩展（留痕/未来按账号限流），可为空——前端打开登录页
    即取码，此时用户名尚未输入。
    """

    username: Optional[str] = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: str
    is_active: bool
    must_change_password: bool = False  # 操作员首次登录须先改密
    permissions: Optional[List[str]] = None  # 操作员可访问模块（None=全部；admin/viewer 忽略）
    created_at: Optional[datetime] = None


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=3, max_length=100)  # 允许初始弱密码（如 123456）；弱密码或操作员 → 首次登录须强制改密
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: str = "editor"  # admin=管理员 / editor=操作员（viewer 保留兼容）
    permissions: Optional[List[str]] = None  # 操作员可访问模块（None=全部）


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=3, max_length=100)
    permissions: Optional[List[str]] = None  # 操作员可访问模块（None=全部）


# ============ 谱系 / 房支 ============
class LineageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: Optional[str] = None
    # 档案编号（如 J148-001-001）：上传文件名匹配同一编号时自动归入该谱系
    code: Optional[str] = Field(default=None, max_length=64)


class LineageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    note: Optional[str] = None
    code: Optional[str] = Field(default=None, max_length=64)


class LineageOut(BaseModel):
    lineage_id: str
    name: str
    code: Optional[str] = None
    note: Optional[str] = None
    person_count: int = 0
    branches: List[Dict[str, Any]] = []
    created_at: Optional[str] = None


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: Optional[str] = None


class BranchUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    note: Optional[str] = None


class BranchOut(BaseModel):
    branch_id: str
    lineage_id: str
    name: str
    note: Optional[str] = None
    person_count: int = 0
    created_at: Optional[str] = None


# ============ 人物 ============
class PersonBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    gender: str = "unknown"  # male / female / unknown
    birth_year: Optional[int] = None
    birth_date: Optional[str] = None
    death_year: Optional[int] = None
    death_date: Optional[str] = None
    birth_place: Optional[str] = None
    death_place: Optional[str] = None
    photo_url: Optional[str] = None
    biography: Optional[str] = None
    notes: Optional[str] = None
    generation: Optional[int] = None
    is_alive: Optional[bool] = None


class PersonCreate(PersonBase):
    lineage_id: Optional[str] = None  # 谱系归属（可选）
    branch_id: Optional[str] = None  # 房支归属（可选，需属于该谱系）


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    birth_year: Optional[int] = None
    birth_date: Optional[str] = None
    death_year: Optional[int] = None
    death_date: Optional[str] = None
    birth_place: Optional[str] = None
    death_place: Optional[str] = None
    photo_url: Optional[str] = None
    biography: Optional[str] = None
    notes: Optional[str] = None
    generation: Optional[int] = None
    is_alive: Optional[bool] = None
    lineage_id: Optional[str] = None  # None=不改动，"" = 移除归属
    branch_id: Optional[str] = None  # None=不改动，"" = 移除归属


class PersonOut(PersonBase):
    person_id: str
    lineage_id: Optional[str] = None
    lineage_name: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None


class PersonDetailOut(PersonOut):
    """人物详情（访客视图）：附父母/子女/配偶完整信息。"""

    parents: List[PersonOut] = []
    children: List[PersonOut] = []
    spouses: List[PersonOut] = []


class PersonListResponse(BaseModel):
    total: int
    items: List[PersonOut]


# ============ 关系 ============
class RelationCreate(BaseModel):
    type: str = Field(pattern="^(parent_child|spouse)$")  # 语义类型
    from_person_id: str
    to_person_id: str
    marriage_date: Optional[str] = None
    relation_type: Optional[str] = None  # Neo4j 原生类型覆盖


class RelationOut(BaseModel):
    rel_id: str
    type: str  # PARENT_OF / SPOUSE_OF
    from_person_id: str
    from_name: str
    to_person_id: str
    to_name: str
    marriage_date: Optional[str] = None


# ============ 家谱树 ============
class TreeNode(BaseModel):
    person_id: str
    name: str
    gender: str
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    birth_place: Optional[str] = None
    death_place: Optional[str] = None
    biography: Optional[str] = None
    notes: Optional[str] = None
    generation: Optional[int] = None
    photo_url: Optional[str] = None
    lineage_name: Optional[str] = None
    branch_name: Optional[str] = None


class TreeEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str  # PARENT_OF / SPOUSE_OF
    marriage_date: Optional[str] = None


class TreeData(BaseModel):
    nodes: List[TreeNode]
    edges: List[TreeEdge]


# ============ 文件 ============
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doc_id: str
    person_id: Optional[str] = None
    title: Optional[str] = None
    type: Optional[str] = None
    file_path: str
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    url: Optional[str] = None  # 通过 /files/ 访问的 URL
    created_at: Optional[datetime] = None


class DocumentLinkPerson(BaseModel):
    person_id: Optional[str] = None
    title: Optional[str] = None


# ============ 导入任务 ============
class ImportTaskOut(BaseModel):
    task_id: str
    file_path: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    total_pages: int = 0
    done_pages: int = 0
    failed_pages: int = 0  # 识别完成但个别页失败的数量（可重试/续跑）
    status: str = "pending"
    stage: str = ""  # converting=转图中 / extracting=AI 识别中 / done / failed
    # 暂停原因（status=paused 时有效）：manual=人工暂停 / priority_ai=整卷整理让位给 AI 识别
    pause_reason: Optional[str] = None
    error_msg: Optional[str] = None
    lineage_id: Optional[str] = None
    lineage_name: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    applied: bool = False  # 已成功写入图谱（新任务由 applied_at 判定；老任务按 Neo4j Document 存在性补判）
    applied_at: Optional[datetime] = None
    # 人工审核状态（写入图谱 / 归档的前置闸）：整卷结果被人工保存过一次才算「已审核」；
    # 老任务（无整卷结果）按逐页暂存覆盖判断。重识别/重新整理后自动失效。
    reviewed: bool = False
    reviewed_at: Optional[str] = None
    reviewed_by_name: Optional[str] = None
    reviewed_pages: int = 0  # 已暂存（人工看过）的页数
    review_total_pages: int = 0
    # 新管线任务缺整卷结果/结果失效（页面无 relations 且无 consolidated 或置了 stale）：
    # 前端据此对未审核的 done 卷显示「🔄 重新整理」，补齐整卷结果后才能进整卷审核并标记已审核。
    needs_reconsolidate: bool = False
    # 能否「断点续跑」：页面图仍可用（MinIO/页镜像有已转页面）。
    # false = 原始扫描件与页面图都已丢失，只能重新上传——前端据此只给「重新上传/删除」，
    # 不再显示「断点续跑」（旧版所有 failed 都显示续跑，与 error_msg「请重新上传」自相矛盾）。
    can_resume: bool = False
    archived: bool = False  # 已归档为只读档案：图片保留，禁止删除/清理/审核写入等写操作
    archived_at: Optional[datetime] = None
    archived_by: Optional[int] = None  # 执行归档操作的用户 id
    archived_by_name: Optional[str] = None  # 归档操作人用户名（展示用）
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PageReviewData(BaseModel):
    """扁平化页级审核数据：ai_meta / notes / persons / content（不再分篇目分类）。"""

    page_no: int
    image_url: str
    # AI 检测统计（人数/段落数/字数/耗时/低置信数），由后端识别落盘时计算
    ai_meta: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None  # 页备注（疑点/类型词），人工可编辑
    persons: List[Dict[str, Any]]
    content: List[Dict[str, Any]] = []  # 纯文本段落 [{text, manual?}]，条目不带 type/title
    relations: List[Dict[str, Any]] = []  # 兼容旧任务；新任务恒空
    page_notes: Optional[str] = None  # 兼容旧任务；新任务改 notes
    reviewed: bool = False
    failed: bool = False  # AI 识别失败（可单页重试/续跑补齐）
    error: Optional[str] = None



class ContentEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_id: str
    task_id: Optional[str] = None
    lineage_id: Optional[str] = None
    branch_id: Optional[str] = None
    type: str
    title: Optional[str] = None
    text: str
    page_no: Optional[int] = None
    source: str = "ai"
    status: str = "active"
    task_name: Optional[str] = None  # 来源任务文件名（查询时补充）
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ContentEntryUpdate(BaseModel):
    """校对谱书内容条目（类型/标题/正文/状态均可改）。"""

    type: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    status: Optional[str] = None  # active/archived


class ContentEntryCreate(BaseModel):
    """人工补录谱书内容条目（无任务来源，直接归属谱系）。"""

    lineage_id: Optional[str] = None
    branch_id: Optional[str] = None
    type: str
    title: Optional[str] = None
    text: str


class PersonMaterialItem(BaseModel):
    """人物相关的单条谱书材料（人物命中 / 谱系背景）。"""

    entry_id: str
    type: str
    title: str = ""
    text: str
    page_no: Optional[int] = None
    task_id: Optional[str] = None
    scope: str = "lineage"  # person=姓名命中 / branch=房支 / lineage=谱系
    hit_name: Optional[str] = None  # scope=person 时命中的姓名


class PersonMaterialsOut(BaseModel):
    """点开某人物时的"家族信息"材料（家谱树 / 访客分享页共用）。"""

    person_id: str
    name: str
    lineage_id: Optional[str] = None
    lineage_name: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    person_entries: List[PersonMaterialItem] = []  # 传记等姓名命中的篇目
    background_entries: List[PersonMaterialItem] = []  # 谱系/房支背景内容
    total: int = 0


# ============ RAG（谱书内容语义检索 / AI 展示文字） ============
class RagAskRequest(BaseModel):
    """谱书内容检索请求（树页/访客/档案文件页共用；人物抽屉的 AI 展示走 person-intro）。"""

    question: str = Field(min_length=1, max_length=300)
    lineage_id: Optional[str] = None  # 限定在某谱系内检索（可选）


class RagTestRequest(BaseModel):
    """系统设置页「检索链路测试」请求：对比 Embedding 余弦召回与 Rerank 精排。"""

    question: str = Field(min_length=1, max_length=300)
    lineage_id: Optional[str] = None  # 限定在某谱系内测试（可选）


class RagHitOut(BaseModel):
    """一条检索命中（谱书原文）。"""

    entry_id: str
    type: str
    title: str = ""
    text: str = ""
    page_no: Optional[int] = None
    lineage_id: Optional[str] = None
    lineage_name: Optional[str] = None
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    score: float = 0.0
    # 命中条目对应的扫描件页面图（任务被删除或条目不源于扫描页时为 None，仅文字展示）
    task_id: Optional[str] = None
    task_name: Optional[str] = None
    image_url: Optional[str] = None
    thumb_url: Optional[str] = None


class RagAskResponse(BaseModel):
    """AI 家谱搜索展示文字 + 命中原文。"""

    question: str
    answer: str  # LLM 生成的展示文字（失败时降级为原文摘要）
    hits: List[RagHitOut] = []
    source: str = "keyword"  # vector=向量检索 / keyword=关键词兜底 / none=无命中
    ready: bool = False  # 向量索引是否就绪（未就绪时 building 提示用户稍后自动升级）
    building: bool = False
    total: int = 0


class RagStatusOut(BaseModel):
    """谱书内容索引状态（前端提示用）。"""

    ready: bool
    building: bool
    indexed: int
    total: int


class PersonIntroRequest(BaseModel):
    person_id: str


class PersonIntroOut(BaseModel):
    """人物的 AI 展示文字（无谱书材料时为 None）。"""

    person_id: str
    name: str
    text: Optional[str] = None
    sources: int = 0
    lineage_name: Optional[str] = None


class ImportTaskDetail(ImportTaskOut):
    pages: List[PageReviewData] = []
    summary: Optional[Dict[str, Any]] = None
    # 卷级整理（第二段）结果：{persons:[...], relations:[...], failed_chunks, done_at}
    consolidated: Optional[Dict[str, Any]] = None
    consolidation_stale: bool = False  # 单页重识别等导致卷级整理已过期


class ReviewItem(BaseModel):
    name: str
    gender: Optional[str] = "unknown"
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    birth_place: Optional[str] = None
    biography: Optional[str] = None
    notes: Optional[str] = None
    confidence: Optional[float] = None
    confirmed: bool = True


class ReviewRelationItem(BaseModel):
    type: str  # parent_child / spouse
    from_name: str
    to_name: str
    marriage_date: Optional[str] = None
    confidence: Optional[float] = None
    confirmed: bool = True


class ReviewSaveRequest(BaseModel):
    """保存某页审核结果（暂存，扁平化结构）。"""

    # page_no 为冗余字段：实际以 URL path 的 page_no 为准，故给默认值避免前端
    # body 不带该字段时被 422（Field required）。此前 required 导致保存/退出全失败。
    page_no: int = 0
    persons: List[ReviewItem] = []
    relations: List[ReviewRelationItem] = []  # 兼容旧前端；新结构不再使用
    content: List[Dict[str, Any]] = []  # 纯文本段落 [{text, manual?}]
    notes: Optional[str] = None  # 页备注
    page_notes: Optional[str] = None  # 兼容旧前端
    reviewed: bool = True


class ApplyRequest(BaseModel):
    """写入图谱：person_id 可指定（人工匹配已有节点），不指定则按姓名匹配/新建。"""

    page_no: Optional[int] = None
    persons: List[ReviewItem] = []
    relations: List[ReviewRelationItem] = []
    doc_id: Optional[str] = None
    # 跳过「本卷尚未人工审核」拦截（前端二次确认后才允许；后端会记审计日志）
    force: bool = False


class ApplyResponse(BaseModel):
    created_persons: int
    linked_persons: int
    created_relations: int
    skipped: int
    mapping: Dict[str, str]  # name -> person_id


class PersonBatchDeleteRequest(BaseModel):
    """批量删除人物（连同其全部关联关系）。支持两种模式：
    1) person_ids 精确删除；2) lineage_id / branch_id 按分类删除该分类下全部人物。"""

    person_ids: List[str] = []
    lineage_id: Optional[str] = None
    branch_id: Optional[str] = None


class PersonMergeRequest(BaseModel):
    """把若干重复/同名人物节点合并进主节点（用于同谱系跨卷人工归并）。

    primary_id 保留为主；secondary_ids 的属性在 primary 为空时补入，
    其全部关系重连到 primary 后删除该节点。不可自合并、主从不可重叠。
    """

    primary_id: str
    secondary_ids: List[str]


class AiDupReviewRequest(BaseModel):
    """谱系跨卷「疑似同名」AI 裁定：只需谱系 id，候选组由服务端按归一化名聚簇。

    AI 只返回判断建议（是否同一人、建议保留谁），不自动合并，仍由人工确认后调 merge。
    """

    lineage_id: str


class AiDupVerdict(BaseModel):
    """AI 对某一组疑似同名（组键=归一化姓名）的裁定结果。AI 只给建议，不自动合并。"""

    key: str
    same: bool = True
    keep_person_id: Optional[str] = None  # same=True 时建议保留的主人物
    keep_name: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""


class AiDupReviewResult(BaseModel):
    total_groups: int = 0  # 服务端按归一化名检出的疑似同名组总数
    groups: List[AiDupVerdict] = []
    error: str = ""  # 非空 = 本次分析整体失败（如模型服务不可用）


# ============ 访客分享 / 问答 ============
class VisitShareCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: Optional[str] = None
    allow_search: bool = True
    allow_chat: bool = False
    chat_model: Optional[str] = Field(default=None, max_length=100)  # 空=该分享问答跟随系统全局模型
    expires_days: Optional[int] = Field(default=None, ge=1, le=3650)  # None=永不过期
    # 二选一：指定 expires_days（预设档）或 expires_at（自定义到期时刻）；两者都给了以 expires_at 为准
    expires_at: Optional[datetime] = None


class VisitShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    share_code: Optional[str] = None  # 短链码：访问路径 <站点根>/<share_code>（旧记录为 NULL 时懒补齐）
    name: str
    note: Optional[str] = None
    allow_search: bool = True
    allow_chat: bool = False
    chat_model: Optional[str] = None
    expires_at: Optional[datetime] = None
    revoked: bool = False
    created_at: Optional[datetime] = None


class DashboardShareCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: Optional[str] = None
    expires_days: Optional[int] = Field(default=None, ge=1, le=3650)  # None=永不过期


class DashboardShareUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    note: Optional[str] = None
    expires_at: Optional[datetime] = None  # 显式传 null 表示设为永不过期


class DashboardShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    share_code: str  # 短链码：访问路径 <站点根>/d<share_code>
    name: str
    note: Optional[str] = None
    expires_at: Optional[datetime] = None
    revoked: bool = False
    created_at: Optional[datetime] = None


class VisitShareUpdate(BaseModel):
    """编辑访客分享（token 不可改；expires_at 显式传 null 表示设为永不过期）"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    note: Optional[str] = None
    allow_search: Optional[bool] = None
    allow_chat: Optional[bool] = None
    chat_model: Optional[str] = Field(default=None, max_length=100)
    expires_at: Optional[datetime] = None


class VisitSettingsOut(BaseModel):
    qa_enable_llm: bool = False
    qa_llm_prompt: str = ""
    qa_llm_model: str = ""
    qa_welcome: str = ""


class VisitSettingsUpdate(BaseModel):
    qa_enable_llm: Optional[bool] = None
    qa_llm_prompt: Optional[str] = None
    qa_llm_model: Optional[str] = None
    qa_welcome: Optional[str] = None


# ============ 系统设置（模型配置 / 访客问答参数） ============
class LlmModelItem(BaseModel):
    """可配置的大模型条目（供问答「启用 AI 增强解析」下拉选择）。

    api_base / api_key：该模型对应的 OpenAI 兼容服务地址与密钥（可选）。
    服务器部署的默认模型（builtin=True）走系统环境变量，api 字段不可配置。
    """

    name: str = Field(min_length=1, max_length=100)  # 实际请求 model 名
    label: Optional[str] = None  # 显示名（缺省用 name）
    note: Optional[str] = None  # 备注
    api_base: Optional[str] = None  # API 地址，如 https://dashscope.aliyuncs.com/compatible-mode
    api_key: Optional[str] = None  # API 密钥（内网后台明文展示/保存）
    builtin: bool = False  # 内置部署模型（api 由环境变量提供，前端锁定不可改）


class SystemSettingsOut(BaseModel):
    qa_enable_llm: bool = False
    qa_llm_prompt: str = ""
    qa_llm_model: str = ""
    qa_welcome: str = ""
    llm_models: List[LlmModelItem] = []
    audit_retention_days: str = ""  # 操作日志保留天数；空 = 永久保留
    # 谱系识别提示词（扫描件 AI 导入逐页提取用）；空/未配置 = 服务端代码默认模板
    scan_prompt: str = ""
    # 整卷整理提示词（AI 第二段卷级归并用）；空/未配置 = 服务端代码默认模板
    consolidate_prompt: str = ""
    # RAG：embedding/reranker 模型与切片/检索参数（键见 rag_service._RT_DEFAULTS）
    rag_config: Dict[str, Any] = {}
    # 向量数据库登记列表（内置引擎恒在首条）；vector_db_active 为当前默认库 id
    vector_dbs: List[Dict[str, Any]] = []
    vector_db_active: str = "builtin_pg"
    # HTTPS IP 白名单（CIDR/网段/单 IP 列表）；空 = 未启用（不限制 IP），非空 = 仅命中可访问
    https_ip_whitelist: List[str] = []
    https_ip_whitelist_enabled: bool = False


class SystemSettingsUpdate(BaseModel):
    qa_enable_llm: Optional[bool] = None
    qa_llm_prompt: Optional[str] = None
    qa_llm_model: Optional[str] = None
    qa_welcome: Optional[str] = None
    llm_models: Optional[List[LlmModelItem]] = None
    audit_retention_days: Optional[str] = Field(default=None, max_length=20)
    scan_prompt: Optional[str] = None
    consolidate_prompt: Optional[str] = None
    rag_config: Optional[Dict[str, Any]] = None
    vector_dbs: Optional[List[Dict[str, Any]]] = None
    vector_db_active: Optional[str] = None


# ============ 存储清理（孤儿数据扫描 / 人工确认删除） ============
class StorageItemOut(BaseModel):
    """一项「没用的数据」（孤儿任务目录 / 孤儿页面图 / 散落文件 / 孤儿内容与向量）。"""

    key: str  # 形如 local:<task_id> / minio:<task_id> / file:<name> / content:<lineage_id>
    kind: str  # local_dir / minio_prefix / loose_file / orphan_content
    name: str
    path: str
    size: int = 0  # 字节
    files: int = 0
    mtime: str = ""  # UTC ISO（前端按本地时区展示）
    note: str = ""


class StorageScanOut(BaseModel):
    items: List[StorageItemOut] = []
    total_size: int = 0
    total_items: int = 0
    scanned_at: Optional[str] = None  # UTC ISO；None 表示尚未扫描过


class StorageCleanupRequest(BaseModel):
    keys: List[str] = []


class StorageCleanupOut(BaseModel):
    removed: List[str] = []
    skipped: List[dict] = []
    freed: int = 0


# ============ 概览 / 仪表盘 ============
class DashDisplayConfig(BaseModel):
    """大屏/仪表盘全局展示参数（内部概览页与 /d 公开大屏共用一份）。"""

    refresh_seconds: int = 30  # 数据自动刷新间隔（秒），0 = 关闭自动刷新
    switch_seconds: int = 20  # 面板自动聚焦/轮播间隔（秒），0 = 关闭
    roll_numbers: bool = True  # KPI 数字滚动动画


class DashDisplayConfigUpdate(BaseModel):
    refresh_seconds: Optional[int] = None
    switch_seconds: Optional[int] = None
    roll_numbers: Optional[bool] = None


class DashboardSlice(BaseModel):
    """一个分布项（世代/房支/性别）。"""

    label: str
    value: int = 0


class DashboardTaskItem(BaseModel):
    """最近导入任务（概览下方列表）。"""

    task_id: str
    name: str = ""
    status: str = ""
    done_pages: int = 0
    total_pages: int = 0
    updated_at: Optional[str] = None


class DashboardOut(BaseModel):
    """首页概览聚合数据（图谱 + 任务 + 内容 + 向量）。"""

    lineages: int = 0
    branches: int = 0
    persons: int = 0
    content_entries: int = 0
    vectors: int = 0
    tasks_total: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    tasks_running: int = 0  # running + pending（处理中/排队中）
    pages_total: int = 0
    pages_done: int = 0
    failed_pages: int = 0
    generations: List[DashboardSlice] = []
    branches_top: List[DashboardSlice] = []
    genders: List[DashboardSlice] = []
    recent_tasks: List[DashboardTaskItem] = []
    generated_at: Optional[str] = None
    # 全局大屏展示参数（与 /d 公开大屏共用，管理端可在概览页右上角调整）
    refresh_seconds: int = 30
    switch_seconds: int = 20
    roll_numbers: bool = True


class DashboardSharePublicOut(BaseModel):
    """仪表盘分享的公开页数据（只含对外统计数据，不含内部任务/导入进度）。"""

    name: str
    note: Optional[str] = None
    lineages: int = 0
    branches: int = 0
    persons: int = 0
    content_entries: int = 0
    generations: List[DashboardSlice] = []
    branches_top: List[DashboardSlice] = []
    genders: List[DashboardSlice] = []
    generated_at: Optional[str] = None
    # 大屏展示参数（跟随全局配置；公开页无需登录即可自动按此刷新）
    refresh_seconds: int = 30
    switch_seconds: int = 20
    roll_numbers: bool = True


# ============ 服务器运行状态（概览页卡片 / 详情抽屉） ============
class SysCpuOut(BaseModel):
    percent: float = 0.0
    cores: int = 0
    load1: float = 0.0
    load5: float = 0.0
    load15: float = 0.0
    history: List[float] = []  # 最近 N 次采样（迷你折线）


class SysMemoryOut(BaseModel):
    total_gb: float = 0.0
    used_gb: float = 0.0
    available_gb: float = 0.0
    percent: float = 0.0
    swap_total_gb: float = 0.0
    swap_used_gb: float = 0.0
    history: List[float] = []


class SysDiskItem(BaseModel):
    mount: str = ""
    device: str = ""
    total_gb: float = 0.0
    used_gb: float = 0.0
    free_gb: float = 0.0
    percent: float = 0.0


class SysDiskOut(BaseModel):
    items: List[SysDiskItem] = []
    read_kbps: float = 0.0
    write_kbps: float = 0.0
    read_iops: float = 0.0
    write_iops: float = 0.0
    busy: float = 0.0  # 最忙块设备的繁忙度 %
    history: List[float] = []  # 读写合计 KB/s


class SysNetworkOut(BaseModel):
    rx_kbps: float = 0.0
    tx_kbps: float = 0.0
    history: List[float] = []


class SysMinioBucketOut(BaseModel):
    name: str = ""
    objects: int = 0
    size_bytes: int = 0


class SysMinioOut(BaseModel):
    ok: bool = False
    endpoint: str = ""
    latency_ms: float = 0.0
    objects: int = 0
    objects_capped: bool = False  # 触及单桶计数上限时真实数量 ≥ objects
    size_gb: float = 0.0
    buckets: List[SysMinioBucketOut] = []
    error: Optional[str] = None


class SysDatabaseOut(BaseModel):
    ok: bool = False
    version: str = ""
    size_gb: float = 0.0
    connections: int = 0
    active: int = 0
    idle_in_tx: int = 0
    uptime_h: float = 0.0
    latency_ms: float = 0.0
    error: Optional[str] = None


class SysNeo4jOut(BaseModel):
    ok: bool = False
    latency_ms: float = 0.0
    error: Optional[str] = None


class SysVectorOut(BaseModel):
    embedding_ok: bool = False
    embedding_url: str = ""
    embedding_latency_ms: float = 0.0
    rerank_ok: bool = False
    rerank_url: str = ""
    rerank_latency_ms: float = 0.0
    indexed: int = 0
    total: int = 0
    ready: bool = False
    building: bool = False
    last_updated: Optional[str] = None
    error: Optional[str] = None


class SystemStatsOut(BaseModel):
    """服务器运行状态（内部概览专用，不进 /d 公开大屏）。"""

    generated_at: str = ""
    uptime_h: float = 0.0
    cpu: SysCpuOut = SysCpuOut()
    memory: SysMemoryOut = SysMemoryOut()
    disk: SysDiskOut = SysDiskOut()
    network: SysNetworkOut = SysNetworkOut()
    minio: SysMinioOut = SysMinioOut()
    database: SysDatabaseOut = SysDatabaseOut()
    neo4j: SysNeo4jOut = SysNeo4jOut()
    vector: SysVectorOut = SysVectorOut()


# ============ 操作日志（audit_logs） ============
class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None  # 关联出的登录名（非 ORM 字段，手动拼装）
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    detail: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: Optional[datetime] = None


class AuditLogPage(BaseModel):
    items: List[AuditLogOut] = []
    total: int = 0


class VisitInfo(BaseModel):
    name: str
    allow_search: bool
    allow_chat: bool
    welcome: str = ""


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=200)


class ChatResponse(BaseModel):
    intent: str
    question: str
    answer: str
    person: Optional[PersonOut] = None
    persons: List[PersonOut] = []
    tree: Optional[TreeData] = None
