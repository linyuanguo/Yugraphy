export interface Person {
  person_id: string
  name: string
  gender: 'male' | 'female' | 'unknown'
  birth_year?: number | null
  birth_date?: string | null
  death_year?: number | null
  death_date?: string | null
  birth_place?: string | null
  death_place?: string | null
  photo_url?: string | null
  biography?: string | null
  notes?: string | null
  generation?: number | null
  is_alive?: boolean | null
  lineage_id?: string | null
  lineage_name?: string | null
  branch_id?: string | null
  branch_name?: string | null
  parents?: string[]
  children?: string[]
}

/** AI 对某一组疑似同名（组键=归一化姓名）的裁定：AI 只给建议，不自动合并 */
export interface AiDupVerdict {
  key: string
  same: boolean
  keep_person_id?: string | null
  keep_name?: string | null
  confidence: number
  reason: string
}

export interface AiDupReviewResult {
  total_groups: number
  groups: AiDupVerdict[]
  error: string
}

// ============ 谱系 / 房支 ============
export interface Branch {
  branch_id: string
  lineage_id: string
  name: string
  note?: string | null
  person_count?: number
  created_at?: string | null
}

export interface Lineage {
  lineage_id: string
  name: string
  /** 档案编号，如 J148-001-001；上传文件名匹配该编号时自动归入 */
  code?: string | null
  note?: string | null
  person_count?: number
  branches: Branch[]
  created_at?: string | null
}

export interface Relation {
  rel_id: string
  type: 'PARENT_OF' | 'SPOUSE_OF'
  from_person_id: string
  from_name: string
  to_person_id: string
  to_name: string
  marriage_date?: string | null
}

export interface TreeEdge {
  id: string
  source: string
  target: string
  type: 'PARENT_OF' | 'SPOUSE_OF'
  marriage_date?: string | null
}

export interface TreeData {
  nodes: Person[]
  edges: TreeEdge[]
}

export interface DocumentItem {
  id: number
  doc_id: string
  person_id?: string | null
  title?: string | null
  type?: string | null
  file_path: string
  file_size?: number | null
  mime_type?: string | null
  url: string
  created_at?: string | null
}

export interface ImportTask {
  task_id: string
  file_path: string
  file_type?: string | null
  total_pages: number
  done_pages: number
  /** 个别页识别失败的数量（任务仍完成，可在审核界面重试或点「重试失败页」） */
  failed_pages?: number
  status: 'pending' | 'running' | 'done' | 'failed' | 'paused'
  /** 暂停原因：manual=人工暂停 / priority_ai=整卷整理让位给 AI 识别（识别优先） */
  pause_reason?: string | null
  stage?: string // converting=转图中 / extracting=AI 识别中 / consolidating=整卷整理中 / done / failed
  error_msg?: string | null
  lineage_id?: string | null
  lineage_name?: string | null
  branch_id?: string | null
  branch_name?: string | null
  /** 已成功写入图谱（新任务看标记；老任务后端按图谱 Document 补判） */
  applied?: boolean
  applied_at?: string | null
  /** 人工审核状态：整卷人物/关系保存过（或老任务逐页全部暂存过）才算已审核；写入图谱/归档的前置条件 */
  reviewed?: boolean
  reviewed_at?: string | null
  reviewed_by_name?: string | null
  /** 已人工暂存（看过）的页数 / 总页数 */
  reviewed_pages?: number
  review_total_pages?: number
  /** 新管线任务缺整卷结果/失效（页面无 relations 且无 consolidated 或置了 stale）：
   *  未审核的 done 卷据此显示「🔄 重新整理」；补齐整卷结果后才能进整卷审核并标记已审核 */
  needs_reconsolidate?: boolean
  /** 页面图仍可用 → 可断点续跑；false 表示原件与页图都丢了，只能重新上传 */
  can_resume?: boolean
  /** 已归档为只读档案：图片保留、禁止写操作，可在「归档文件 → AI 识别归档」取回 */
  archived?: boolean
  archived_at?: string | null
  /** 执行归档操作的用户 id / 用户名（展示用） */
  archived_by?: number | null
  archived_by_name?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ReviewPersonItem {
  name: string
  gender: 'male' | 'female' | 'unknown'
  birth_year?: number | null
  death_year?: number | null
  birth_place?: string | null
  biography?: string | null
  notes?: string | null
  confidence?: number | null
  confirmed: boolean
}

export interface ReviewRelationItem {
  type: 'parent_child' | 'spouse'
  from_name: string
  to_name: string
  marriage_date?: string | null
  confidence?: number | null
  confirmed: boolean
}

/** 页正文段落（新扁平识别链路）：整页纯文本段落，可人工补录；不再区分 type/title */
export interface ReviewContentPara {
  text: string
  /** 审核页人工补录的段落标记（展示绿色「人工」tag；随写入图谱并入本页正文） */
  manual?: boolean
}

export interface PageReview {
  page_no: number
  image_url: string
  persons: ReviewPersonItem[]
  relations: ReviewRelationItem[]
  /** 新扁平识别链路：整页原文段落（新版任务主字段；旧 entries 任务本字段为空） */
  content?: ReviewContentPara[]
  page_notes?: string | null
  /** 后端重识别返回的页面备注（新扁平链路用 notes 字段；page_notes 为旧别名） */
  notes?: string | null
  reviewed: boolean
  /** AI 识别失败标记（该页未提取出内容，可「重新识别本页」/续跑补识别） */
  failed?: boolean
  error?: string | null
}

export interface TaskDetail extends ImportTask {
  pages: PageReview[]
  /** 卷级整理（第二段）结果：整卷去重人物与世系关系 */
  consolidated?: {
    persons: Array<
      ReviewPersonItem & { pages?: number[]; confidence?: number | null }
    >
    relations: Array<ReviewRelationItem & { pages?: number[] }>
    failed_chunks?: number
    done_at?: string
    final?: boolean
    /** 人工审核标记：整卷人物/关系被人工保存过一次（写入图谱/归档的前置闸） */
    reviewed?: boolean
    reviewed_at?: string | null
    reviewed_by?: number | null
    reviewed_by_name?: string | null
  } | null
  /** 单页重识别等导致卷级整理已过期，需要重新整理 */
  consolidation_stale?: boolean
}

// ============ 谱书内容（content_entries） ============
export interface ContentEntry {
  id: number
  entry_id: string
  task_id?: string | null
  lineage_id?: string | null
  branch_id?: string | null
  type: string
  title?: string | null
  text: string
  page_no?: number | null
  source: 'ai' | 'manual'
  status: 'active' | 'archived'
  task_name?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface UserInfo {
  id: number
  username: string
  full_name?: string | null
  email?: string | null
  role: 'admin' | 'editor' | 'viewer'
  is_active: boolean
  /** 操作员新建/重置密码后为 true：登录后须先修改密码才能操作 */
  must_change_password?: boolean
  /** 操作员可访问模块清单（null=全部；admin/viewer 忽略） */
  permissions?: string[] | null
  created_at?: string | null
}

// ============ 访客 ============
export interface VisitShare {
  id: number
  /** 短链码：图谱树分享访问链接 = <站点根>/<share_code>（短链为唯一访问形态） */
  share_code?: string | null
  name: string
  note?: string | null
  allow_search: boolean
  allow_chat: boolean
  /** 该分享问答专用模型名（空=跟随系统全局设置） */
  chat_model?: string | null
  expires_at?: string | null
  revoked: boolean
  created_at?: string | null
}

export interface VisitSettings {
  qa_enable_llm: boolean
  qa_llm_prompt: string
  qa_llm_model: string
  qa_welcome: string
}

// ============ 系统设置 ============
/** RAG（谱书内容检索）配置：embedding/reranker 模型与切片/检索参数。
 *  留空 = 使用服务器 .env/内置默认；键与后端 rag_service._RT_DEFAULTS 一致。 */
export interface RagConfig {
  embedding_url: string
  embedding_model: string
  /** 空 = 匿名 + 服务器内置密钥兜底 */
  embedding_api_key: string
  rerank_url: string
  rerank_model: string
  /** 单次批量嵌入条数 */
  embedding_batch: number
  /** 每条参与向量化的文本最大长度（"切片"上限，字符） */
  max_emb_chars: number
  /** 余弦召回候选数（供 rerank 精排） */
  top_candidates: number
  /** 默认返回命中数 */
  search_limit: number
  /** 命中原文返回/展示截断长度（字符） */
  hit_text_chars: number
}

/** 向量数据库条目：内置引擎恒在首条；其余为接入登记（预留）的外部库 */
export interface VectorDb {
  id: string
  name: string
  /** builtin = 当前默认引擎（不可删改）；external = 登记的外部库 */
  kind?: 'builtin' | 'external'
  /** 引擎类型展示名（如 Milvus / Chroma / pgvector …） */
  type?: string
  endpoint?: string
  api_key?: string
  note?: string
  builtin?: boolean
}

export interface LlmModel {
  name: string
  label?: string | null
  note?: string | null
  /** API 地址（OpenAI 兼容，如 dashscope / 自建网关）；内置默认模型不可配 */
  api_base?: string | null
  /** API 密钥 */
  api_key?: string | null
  /** 服务器部署的内置默认模型：api 由环境变量提供，前端锁定不可改 */
  builtin?: boolean
}

export interface SystemSettings extends VisitSettings {
  llm_models: LlmModel[]
  /** 操作日志保留天数；空字符串 = 永久保留 */
  audit_retention_days: string
  /** 谱系识别提示词（扫描件 AI 导入逐页提取用）；空/未配置 = 服务端默认模板 */
  scan_prompt: string
  /** 整卷整理提示词（AI 第二段卷级归并用）；空/未配置 = 服务端默认模板 */
  consolidate_prompt: string
  /** 族谱内容检索（RAG）模型与切片/检索参数 */
  rag_config: RagConfig
  /** 向量数据库清单（内置引擎恒在首条） */
  vector_dbs: VectorDb[]
  /** 当前默认向量数据库 id */
  vector_db_active: string
  /** HTTPS IP 白名单（CIDR/网段/单 IP）；空 = 未启用（不限制 IP），非空 = 仅命中可访问 */
  https_ip_whitelist?: string[]
  /** HTTPS IP 白名单是否已启用（= 是否填了 IP 段） */
  https_ip_whitelist_enabled?: boolean
}

// ============ 存储清理（孤儿数据） ============
export interface StorageItem {
  /** 形如 local:<task_id> / minio:<task_id> / file:<filename> */
  key: string
  /** local_dir=孤儿任务目录 / minio_prefix=孤儿页面图 / loose_file=散落文件 / orphan_content=孤儿内容与向量 */
  kind: 'local_dir' | 'minio_prefix' | 'loose_file' | 'orphan_content'
  name: string
  path: string
  /** 字节 */
  size: number
  files: number
  /** UTC ISO 时间 */
  mtime: string
  note: string
}

export interface StorageScan {
  items: StorageItem[]
  total_size: number
  total_items: number
  /** UTC ISO；null = 尚未扫描过 */
  scanned_at: string | null
}

export interface StorageCleanupResult {
  removed: string[]
  skipped: { key: string; reason: string }[]
  freed: number
}

// ============ 操作日志 ============
export interface AuditLogItem {
  id: number
  user_id?: number | null
  username?: string | null
  action: string
  target_type?: string | null
  target_id?: string | null
  detail?: string | null
  ip_address?: string | null
  created_at?: string | null
}

export interface AuditLogPage {
  items: AuditLogItem[]
  total: number
}

export interface VisitInfo {
  name: string
  allow_search: boolean
  allow_chat: boolean
  welcome: string
}

export interface ChatResponse {
  intent: string
  question: string
  answer: string
  person: Person | null
  persons: Person[]
  tree: TreeData | null
}

export interface PersonDetail extends Omit<Person, 'parents' | 'children'> {
  parents: Person[]
  children: Person[]
  spouses: Person[]
}

/** 人物相关谱书材料（人物命中传记 / 谱系背景篇目，apply 后从 content_entries 召回） */
export interface PersonMaterialItem {
  entry_id: string
  type: string
  title: string
  text: string
  page_no?: number | null
  task_id?: string | null
  /** person=姓名命中传记 / branch=房支背景 / lineage=谱系背景 */
  scope: 'person' | 'branch' | 'lineage'
  hit_name?: string | null
}

export interface PersonMaterials {
  person_id: string
  name: string
  lineage_id?: string | null
  lineage_name?: string | null
  branch_id?: string | null
  branch_name?: string | null
  person_entries: PersonMaterialItem[]
  background_entries: PersonMaterialItem[]
  total: number
}

// ============ RAG（谱书内容检索 / AI 展示文字） ============
export interface RagHit {
  entry_id: string
  type: string
  title: string
  text: string
  page_no?: number | null
  lineage_id?: string | null
  lineage_name?: string | null
  branch_id?: string | null
  branch_name?: string | null
  score: number
  /** 命中条目对应的扫描件页面图（任务在存且有页码时提供；可点击看原图） */
  task_id?: string | null
  task_name?: string | null
  image_url?: string | null
  thumb_url?: string | null
}

/** 检索链路测试命中（系统设置页对比 Embedding 召回 vs Rerank 精排） */
export interface RagTestHit {
  rank: number
  /** 精排阶段：Rerank 前在召回列表里的名次（用于展示排序移动） */
  from_rank?: number
  entry_id: string
  type: string
  title: string
  text: string
  page_no?: number | null
  lineage_id?: string | null
  branch_id?: string | null
  lineage_name?: string
  branch_name?: string
  /** 召回阶段：余弦相似度 */
  sim?: number
  /** 精排阶段：Rerank 分数（rerank_used=false 时为 null） */
  rerank_score?: number | null
}

/** 系统设置页「检索链路测试」结果 */
export interface RagTestResult {
  question: string
  /** 向量索引是否就绪 */
  ready: boolean
  building: boolean
  total: number
  embedding_model: string
  rerank_model: string
  vector_dim: number | null
  rerank_used: boolean
  error?: string | null
  /** ① Embedding 余弦召回（top_candidates 候选，含相似度） */
  recall: RagTestHit[]
  /** ② Rerank 精排后的顺序（含精排分数与原召回名次） */
  ranked: RagTestHit[]
}

/** AI 家谱搜索展示文字 + 命中原文（树页 / 访客 / 归档文件页共用） */
export interface RagAskResponse {
  question: string
  answer: string
  hits: RagHit[]
  source: 'vector' | 'keyword' | 'none'
  ready: boolean
  building: boolean
  total: number
}

/** 谱书内容向量索引状态（未就绪时提示"正在建立知识索引"） */
export interface RagStatus {
  ready: boolean
  building: boolean
  indexed: number
  total: number
}

/** 人物 AI 展示文字（基于谱书记载生成；text=null 表示无材料未生成） */
export interface PersonIntro {
  person_id: string
  name: string
  text: string | null
  sources: number
  lineage_name?: string | null
}

// ============ 概览 / 仪表盘 ============
/** 概览分布项（世代 / 房支 / 性别） */
export interface DashboardSlice {
  label: string
  value: number
}

/** 概览里的「最近导入任务」行 */
export interface DashboardTaskItem {
  task_id: string
  name: string
  status: string
  done_pages: number
  total_pages: number
  /** UTC ISO */
  updated_at: string | null
}

/** 大屏/仪表盘全局展示参数（内部概览与 /d 公开大屏共用一份） */
export interface DashDisplayConfig {
  /** 数据自动刷新间隔（秒），0 = 关闭自动刷新 */
  refresh_seconds: number
  /** 面板自动聚焦/轮播间隔（秒），0 = 关闭 */
  switch_seconds: number
  /** KPI 数字滚动动画 */
  roll_numbers: boolean
}

/** 首页概览聚合数据（图谱 + 任务 + 内容 + 向量） */
export interface Dashboard {
  lineages: number
  branches: number
  persons: number
  content_entries: number
  vectors: number
  tasks_total: number
  tasks_done: number
  tasks_failed: number
  /** running + pending（处理中/排队中） */
  tasks_running: number
  pages_total: number
  pages_done: number
  failed_pages: number
  generations: DashboardSlice[]
  branches_top: DashboardSlice[]
  genders: DashboardSlice[]
  recent_tasks: DashboardTaskItem[]
  generated_at: string | null
  refresh_seconds: number
  switch_seconds: number
  roll_numbers: boolean
}

// ============ 服务器运行状态（概览页「服务器运行状态」卡片） ============
/** CPU（percent 为使用率；history 为最近若干次采样，供迷你折线） */
export interface SysCpu {
  percent: number
  cores: number
  load1: number
  load5: number
  load15: number
  history: number[]
}

export interface SysMemory {
  total_gb: number
  used_gb: number
  available_gb: number
  percent: number
  swap_total_gb: number
  swap_used_gb: number
  history: number[]
}

export interface SysDiskItem {
  mount: string
  device: string
  total_gb: number
  used_gb: number
  free_gb: number
  percent: number
}

export interface SysDisk {
  items: SysDiskItem[]
  read_kbps: number
  write_kbps: number
  read_iops: number
  write_iops: number
  /** 最忙块设备的繁忙度 % */
  busy: number
  history: number[]
}

export interface SysNetwork {
  rx_kbps: number
  tx_kbps: number
  history: number[]
}

export interface SysMinioBucket {
  name: string
  objects: number
  size_bytes: number
}

export interface SysMinio {
  ok: boolean
  endpoint: string
  latency_ms: number
  objects: number
  /** 触及单桶计数上限：真实数量 ≥ objects */
  objects_capped: boolean
  size_gb: number
  buckets: SysMinioBucket[]
  error?: string | null
}

export interface SysDatabase {
  ok: boolean
  version: string
  size_gb: number
  connections: number
  active: number
  idle_in_tx: number
  uptime_h: number
  latency_ms: number
  error?: string | null
}

export interface SysNeo4j {
  ok: boolean
  latency_ms: number
  error?: string | null
}

export interface SysVector {
  embedding_ok: boolean
  embedding_url: string
  embedding_latency_ms: number
  rerank_ok: boolean
  rerank_url: string
  rerank_latency_ms: number
  indexed: number
  total: number
  ready: boolean
  building: boolean
  last_updated?: string | null
  error?: string | null
}

export interface SystemStats {
  generated_at: string
  uptime_h: number
  cpu: SysCpu
  memory: SysMemory
  disk: SysDisk
  network: SysNetwork
  minio: SysMinio
  database: SysDatabase
  neo4j: SysNeo4j
  vector: SysVector
}

/** 仪表盘分享（管理端记录）：访问链接 = <站点根>/d<share_code> */
export interface DashboardShare {
  id: number
  share_code: string
  name: string
  note?: string | null
  expires_at?: string | null
  revoked: boolean
  created_at?: string | null
}

/** 仪表盘分享的公开大屏数据（仅统计信息，不含内部任务/导入进度） */
export interface DashboardSharePublic {
  name: string
  note?: string | null
  lineages: number
  branches: number
  persons: number
  content_entries: number
  generations: DashboardSlice[]
  branches_top: DashboardSlice[]
  genders: DashboardSlice[]
  generated_at: string | null
  /** 跟随全局大屏展示参数（公开页自动按此刷新） */
  refresh_seconds: number
  switch_seconds: number
  roll_numbers: boolean
}

// ============ 删除审批（操作员删除扫描件/谱系 → 管理员审批，09-07） ============
/** 提交申请时的对象快照（不同 target_type 字段略不同） */
export interface DeleteSnapshot {
  file?: string
  status?: string
  stage?: string
  total_pages?: number
  done_pages?: number
  applied?: boolean
  name?: string
  code?: string | null
  person_count?: number
  branches?: number
  force?: boolean
}

/** 审批时后端实时核对的目标当前状态（approve 弹窗用） */
export interface DeleteTargetState {
  exists: boolean | null
  kind?: 'task' | 'lineage'
  note?: string
  file?: string
  status?: string
  stage?: string
  total_pages?: number
  done_pages?: number
  applied?: boolean
  archived?: boolean
  name?: string
  code?: string | null
  person_count?: number
  branches?: number
}

export interface DeleteRequestItem {
  id: number
  target_type: 'task' | 'lineage'
  target_id: string
  target_name: string
  /** 谱系申请：提交时是否勾选「连同人物一并删除」 */
  force: boolean
  snapshot?: DeleteSnapshot | null
  reason?: string | null
  status: 'pending' | 'approved' | 'rejected'
  submitted_by?: number | null
  submitted_by_name?: string
  reviewed_by?: number | null
  reviewed_by_name?: string
  review_note?: string | null
  created_at?: string | null
  reviewed_at?: string | null
  /** 详情接口（GET /delete-requests/:id）额外返回 */
  current?: DeleteTargetState | null
}
