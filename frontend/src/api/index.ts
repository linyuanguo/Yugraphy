import request from './request'
import type {
  AiDupReviewResult,
  AuditLogPage,
  Branch,
  ChatResponse,
  ContentEntry,
  Dashboard,
  DashDisplayConfig,
  DashboardShare,
  DashboardSharePublic,
  DeleteRequestItem,
  DocumentItem,
  ImportTask,
  Lineage,
  PageReview,
  Person,
  PersonDetail,
  PersonIntro,
  PersonMaterials,
  RagAskResponse,
  RagStatus,
  RagTestResult,
  Relation,
  SystemSettings,
  StorageCleanupResult,
  StorageScan,
  SystemStats,
  TaskDetail,
  TreeData,
  UserInfo,
  VisitInfo,
  VisitSettings,
  VisitShare,
} from '@/types'

// ============ 认证 ============

/** 登录动态码（服务端签发、与用户名无关、60 秒有效一次性）：前端打开登录页即取码并展示，随表单提交 */
export const hotpCodeApi = () =>
  request.post(
    '/auth/hotp-code',
    {},
    // silentError：登录相关失败由登录页自行展示（避免拦截器跳转/toast）
    { headers: { 'Content-Type': 'application/json' }, ...{ silentError: true } },
  ) as Promise<{ ok: boolean; code: string; expires_in: number; server_time: number }>

export const loginApi = (data: {
  username: string
  password: string
  remember?: boolean
  hotpCode: string
}) =>
  request.post(
    '/auth/login',
    new URLSearchParams({
      username: data.username,
      password: data.password,
      remember: data.remember ? 'true' : 'false',
      hotp_code: data.hotpCode,
    }).toString(),
    // silentError：登录失败提示由登录页自行展示（避免拦截器跳转/toast）
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, ...{ silentError: true } },
  ) as Promise<{ access_token: string; token_type: string; expires_in: number; must_change_password?: boolean }>

export const meApi = () => request.get('/auth/me') as Promise<UserInfo>

// ============ 人物 ============
export const listPersonsApi = (params: Record<string, any> = {}) =>
  request.get('/persons', { params }) as Promise<{ total: number; items: Person[] }>

/** 姓名联想搜索（与访客页一致：子串 / 同音 / 拼音缩写 / 错别字容错） */
export const fuzzySearchPersonsApi = (q: string) =>
  request.get('/persons/fuzzy-search', { params: { q } }) as Promise<Person[]>

export const getPersonApi = (id: string) =>
  request.get(`/persons/${id}`) as Promise<Person>

/** 人物的"家族信息"材料（姓名命中传记 + 谱系/房支背景篇目） */
export const personMaterialsApi = (personId: string) =>
  request.get(`/persons/${personId}/materials`) as Promise<PersonMaterials>

// ============ RAG（谱书内容检索 / AI 展示文字） ============
/** 谱书内容向量索引状态（树页/归档文件页提示"正在建立知识索引"用） */
export const ragStatusApi = () => request.get('/rag/status') as Promise<RagStatus>

/** 自然语言检索谱书内容：AI 展示文字 + 命中原文（可限定谱系） */
export const ragAskApi = (question: string, lineageId?: string) =>
  request.post('/rag/ask', { question, lineage_id: lineageId }) as Promise<RagAskResponse>

/** 人物的 AI 展示文字（人物详情抽屉打开即自动生成） */
export const ragPersonIntroApi = (personId: string) =>
  request.post('/rag/person-intro', { person_id: personId }) as Promise<PersonIntro>

/** 检索链路测试：Embedding 余弦召回 vs Rerank 精排（系统设置页 RAG 配置下验证用） */
export const ragTestSearchApi = (question: string, lineageId?: string) =>
  request.post('/rag/test-search', {
    question,
    lineage_id: lineageId || undefined,
  }) as Promise<RagTestResult>

/** 清空并全量重建谱书向量索引（管理员修改 embedding 模型/切片参数后调用） */
export const rebuildRagIndexApi = () => request.post('/rag/rebuild') as Promise<{
  synced: number
  deleted: number
  ready: boolean
  building: boolean
  indexed: number
  total: number
}>

export const createPersonApi = (data: Partial<Person>) =>
  request.post('/persons', data) as Promise<Person>

export const updatePersonApi = (id: string, data: Partial<Person>) =>
  request.put(`/persons/${id}`, data) as Promise<Person>

export const deletePersonApi = (id: string) =>
  request.delete(`/persons/${id}`) as Promise<any>

/** 批量删除人物：传 personIds 精确删除；或传 scope 按谱系/房支删除该分类下全部人物 */
export const batchDeletePersonsApi = (
  personIds: string[],
  scope?: { lineage_id?: string; branch_id?: string },
) =>
  request.post('/persons/batch-delete', {
    person_ids: personIds,
    lineage_id: scope?.lineage_id,
    branch_id: scope?.branch_id,
  }) as Promise<{ deleted: number }>

/** 把若干重复/疑似同名人物合并进主节点（同谱系跨卷人工归并） */
export const mergePersonsApi = (
  primaryId: string,
  secondaryIds: string[],
) =>
  request.post('/persons/merge', {
    primary_id: primaryId,
    secondary_ids: secondaryIds,
  }) as Promise<{ merged: number; secondary_count: number }>

/** 谱系跨卷「疑似同名」AI 裁定：服务端聚簇候选组送 LLM，返回是否同一人的建议（不自动合并） */
export const aiDupReviewApi = (lineageId: string) =>
  request.post('/persons/ai-dup-review', {
    lineage_id: lineageId,
  }) as Promise<AiDupReviewResult>

export const uploadPhotoApi = (id: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request.post(`/persons/${id}/photo`, fd) as Promise<Person>
}

// ============ 关系 ============
export const listRelationsApi = () =>
  request.get('/relations') as Promise<Relation[]>

export const createRelationApi = (data: {
  type: 'parent_child' | 'spouse'
  from_person_id: string
  to_person_id: string
  marriage_date?: string | null
}) => request.post('/relations', data) as Promise<Relation>

export const deleteRelationApi = (relId: string) =>
  request.delete(`/relations/${relId}`) as Promise<any>

// ============ 家谱树 ============
export const getTreeApi = (lineageId?: string) =>
  request.get('/tree', { params: { lineage_id: lineageId } }) as Promise<TreeData>
export const getSubtreeApi = (personId: string, depth = 6) =>
  request.get('/tree/subtree', { params: { person_id: personId, depth } }) as Promise<TreeData>

// ============ 谱系 / 房支 ============
export const listLineagesApi = () => request.get('/lineages') as Promise<Lineage[]>

export const createLineageApi = (data: { name: string; note?: string; code?: string }) =>
  request.post('/lineages', data) as Promise<Lineage>

export const updateLineageApi = (id: string, data: { name?: string; note?: string; code?: string }) =>
  request.put(`/lineages/${id}`, data) as Promise<Lineage>

/** 删除谱系；force=true 为管理员强制删除（连同归属人物及全部关联） */
export const deleteLineageApi = (id: string, force = false) =>
  request.delete(`/lineages/${id}`, { params: force ? { force: true } : {} }) as Promise<any>

export const createBranchApi = (lineageId: string, data: { name: string; note?: string }) =>
  request.post(`/lineages/${lineageId}/branches`, data) as Promise<Branch>

export const updateBranchApi = (id: string, data: { name?: string; note?: string }) =>
  request.put(`/lineages/branches/${id}`, data) as Promise<Branch>

export const deleteBranchApi = (id: string) =>
  request.delete(`/lineages/branches/${id}`) as Promise<any>

// ============ 文件 ============
export const listDocumentsApi = (search?: string) =>
  request.get('/documents', { params: { search } }) as Promise<DocumentItem[]>

export const uploadDocumentApi = (file: File, title?: string, type?: string) => {
  const fd = new FormData()
  fd.append('file', file)
  if (title) fd.append('title', title)
  if (type) fd.append('doc_type', type)
  return request.post('/documents/upload', fd) as Promise<DocumentItem>
}

export const updateDocumentApi = (
  docId: string,
  data: { person_id?: string | null; title?: string | null },
) => request.put(`/documents/${docId}`, data) as Promise<DocumentItem>

export const deleteDocumentApi = (docId: string) =>
  request.delete(`/documents/${docId}`) as Promise<any>

// ============ 导入任务 ============
export const createImportTaskApi = (
  file: File,
  opts: { lineage_id?: string; branch_id?: string } = {},
  onProgress?: (percent: number) => void,
  silent409 = false,
) => {
  const fd = new FormData()
  fd.append('file', file)
  if (opts.lineage_id) fd.append('lineage_id', opts.lineage_id)
  if (opts.branch_id) fd.append('branch_id', opts.branch_id)
  return request.post('/tasks/import', fd, {
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
    // 409 重复文件由页面以常驻提示展示，不弹一闪而过的 toast
    ...(silent409 ? { silentError: true } : {}),
  }) as Promise<ImportTask>
}

/** 任务列表；archived=true 仅返回归档任务（档案库），archived=false 仅返回未归档任务 */
export const listTasksApi = (params: Record<string, any> = {}) =>
  request.get('/tasks', { params }) as Promise<ImportTask[]>

/** 上传前同名预检：names 中存在同名(非失败)导入任务的文件会被标记出来 */
export const precheckImportNamesApi = (names: string[]) =>
  request.post('/tasks/check-names', { names }) as Promise<
    Array<{ name: string; existing: Array<{ task_id: string; status: string; file_size?: number; created_at?: string }> }>
  >

export const cleanupTaskPagesApi = (taskId: string) =>
  request.delete(`/tasks/${taskId}/pages`) as Promise<{ removed: number }>

/** 断点续跑：失败/个别页失败的任务，无需重新上传即可继续 AI 识别 */
export const resumeTaskApi = (taskId: string) =>
  request.post(`/tasks/${taskId}/resume`) as Promise<{ ok: boolean; queued: boolean }>

/** 暂停导入任务（处理中/排队中）：后台在安全点收尾，已识别页保留，可点「继续」恢复 */
export const pauseTaskApi = (taskId: string) =>
  request.post(`/tasks/${taskId}/pause`) as Promise<{ ok: boolean; paused: boolean }>

/** 重新执行卷级整理（第二段：人物归并 + 世系关系推断）。单页重识别/失败页补齐后用于更新整卷结果 */
export const reconsolidateTaskApi = (taskId: string) =>
  request.post(`/tasks/${taskId}/consolidate`) as Promise<{ ok: boolean; queued: boolean }>

/** 整卷重新 AI 识别：全部页面（含失败页）重跑逐页识别，完成后自动重新整理整卷。区别于「重新整理」只重跑第二段归并 */
export const reExtractAllTaskApi = (taskId: string) =>
  request.post(`/tasks/${taskId}/re-extract-all`) as Promise<{ ok: boolean; queued: boolean }>

/** 暂存卷级整理结果的审核（人物/关系勾选与编辑），断点续审 */
export const saveConsolidatedReviewApi = (
  taskId: string,
  // reviewed=true：人工确认「本卷已校对完成」→ 落审核标记，之后才允许写入图谱/归档
  data: { persons: any[]; relations: any[]; reviewed?: boolean },
) => request.post(`/tasks/${taskId}/consolidated-review`, data) as Promise<{ ok: boolean }>

/**
 * 删除任务（09-07 审批流）：
 *  - admin 返回 {deleted:true,...}（记录 + MinIO 页面图一并清除；已写入图谱的数据不受影响）；
 *  - 操作员不真删，返回 {deleted:false, requested:true, request_id} 生成待审批申请。
 */
export const deleteTaskApi = (taskId: string) =>
  request.delete(`/tasks/${taskId}`) as Promise<{
    deleted: boolean
    removed_pages?: number
    requested?: boolean
    request_id?: number
    target_id?: string
    target_name?: string
  }>

/** 归档：已写入图谱的完成任务 → 归档文件（只读，图片保留不删） */
export const archiveTaskApi = (taskId: string) =>
  request.post(`/tasks/${taskId}/archive`) as Promise<{ ok: boolean }>

/** 取回：从归档文件解除只读，恢复可审核/重识别/删除（图片全程保留） */
export const unarchiveTaskApi = (taskId: string) =>
  request.post(`/tasks/${taskId}/unarchive`) as Promise<{ ok: boolean }>

// ============ 删除审批（09-07：操作员删除扫描件/谱系 → 管理员集中审批） ============

/** 申请列表：admin 见全部；操作员只见自己提交的（源页面用来展示「删除审批中」标记） */
export const listDeleteRequestsApi = (
  params: { status?: string; target_type?: string } = {},
) => request.get('/delete-requests', { params }) as Promise<DeleteRequestItem[]>

/** 待审批数量：admin=全局待审总数（顶部铃铛/菜单徽标）；操作员=自己的待审数 */
export const deleteRequestsPendingCountApi = () =>
  request.get('/delete-requests/pending-count') as Promise<{ pending: number }>

/** 申请详情 + 目标当前状态（审批弹窗实时核对） */
export const getDeleteRequestApi = (id: number) =>
  request.get(`/delete-requests/${id}`) as Promise<DeleteRequestItem>

/** 审批通过并真正删除；目标已不存在时返回 already_gone */
export const approveDeleteRequestApi = (
  id: number,
  data: { force?: boolean; note?: string },
) =>
  request.post(`/delete-requests/${id}/approve`, data) as Promise<{
    ok: boolean
    deleted?: boolean
    already_gone?: boolean
    removed_persons?: number
    removed_pages?: number
  }>

/** 驳回删除申请：目标保留，note 为驳回原因（操作员在原页面可见） */
export const rejectDeleteRequestApi = (id: number, data: { note?: string }) =>
  request.post(`/delete-requests/${id}/reject`, data) as Promise<{
    ok: boolean
    status: string
  }>

// ============ 操作日志（仅管理员） ============
export const listAuditLogsApi = (
  params: { action?: string; q?: string; offset?: number; limit?: number } = {},
) => request.get('/settings/audit-logs', { params }) as Promise<AuditLogPage>

export const deleteAuditLogApi = (logId: number) =>
  request.delete(`/settings/audit-logs/${logId}`) as Promise<any>

export const clearAuditLogsApi = () =>
  request.delete('/settings/audit-logs') as Promise<any>

export const getTaskApi = (taskId: string) =>
  request.get(`/tasks/${taskId}`) as Promise<TaskDetail>

/** 单页重识别可能跑很久（审核端整页长超时 REDO_PAGE_TIMEOUT + 自动分块兜底，
 *  服务端 nginx 已放宽到 1800s），此处 axios 超时单独放宽到 15 分钟，
 *  不能沿用默认 300s（会把还在跑的后端请求先掐断）。 */
export const reExtractPageApi = (taskId: string, pageNo: number) =>
  request.post(`/tasks/${taskId}/pages/${pageNo}/re-extract`, null, {
    timeout: 900000,
  }) as Promise<PageReview>

export const savePageReviewApi = (
  taskId: string,
  pageNo: number,
  data: {
    persons: any[]
    relations: any[]
    content?: Array<{ text: string; manual?: boolean }>
    page_notes?: string
    reviewed: boolean
  },
) => request.post(`/tasks/${taskId}/pages/${pageNo}/review`, data) as Promise<{ ok: boolean }>

export const applyTaskApi = (
  taskId: string,
  // force=true 跳过后端「本卷尚未人工审核」拦截（前端须二次确认；后端记审计日志）
  data: { page_no?: number; persons: any[]; relations: any[]; force?: boolean },
) =>
  // 全量写入可能需 1-3 分钟（数千人物逐个幂等入库），单独放宽超时
  request.post(`/tasks/${taskId}/apply`, data, { timeout: 900000 }) as Promise<{
    created_persons: number
    linked_persons: number
    created_relations: number
    skipped: number
    mapping: Record<string, string>
  }>

/**
 * 一键「写入图谱并归档」（后台执行，端点立即返回）：
 *  - 有改动/整卷结果缺失 → 先排队做卷级整理（与其它 AI 任务共享并发闸），整理完自动写入并归档；
 *  - 无改动 → 直接写入图谱并归档为只读档案。
 * 前端轮询任务状态（archived=true 即完成）。
 */
export const applyArchiveTaskApi = (
  taskId: string,
  data: { force?: boolean; archive?: boolean } = {},
) =>
  request.post(`/tasks/${taskId}/apply-archive`, data) as Promise<{
    ok: boolean
    queued: boolean
    consolidate: boolean
  }>

// ============ 谱书内容条目 ============
export const listContentEntriesApi = (
  params: { lineage_id?: string; task_id?: string; type?: string; status?: string } = {},
) => request.get('/content-entries', { params }) as Promise<ContentEntry[]>

export const createContentEntryApi = (data: {
  lineage_id?: string
  branch_id?: string
  type: string
  title?: string
  text: string
}) => request.post('/content-entries', data) as Promise<ContentEntry>

export const updateContentEntryApi = (
  entryId: string,
  data: { type?: string; title?: string; text?: string; status?: 'active' | 'archived' },
) => request.put(`/content-entries/${entryId}`, data) as Promise<ContentEntry>

export const deleteContentEntryApi = (entryId: string) =>
  request.delete(`/content-entries/${entryId}`) as Promise<any>

// ============ 用户 ============
export const listUsersApi = (search?: string) =>
  request.get('/users', { params: { search } }) as Promise<UserInfo[]>

export const createUserApi = (data: any) => request.post('/users', data) as Promise<UserInfo>
export const updateUserApi = (id: number, data: any) =>
  request.put(`/users/${id}`, data) as Promise<UserInfo>
export const deleteUserApi = (id: number) => request.delete(`/users/${id}`) as Promise<any>
export const changePasswordApi = (old_password: string, new_password: string) =>
  request.post('/users/me/password', null, {
    params: { old_password, new_password },
  }) as Promise<{ ok: boolean }>

// ============ 系统设置（模型配置 / 访客问答参数） ============
export const getSystemSettingsApi = () =>
  request.get('/settings') as Promise<SystemSettings>

export const updateSystemSettingsApi = (data: Partial<SystemSettings>) =>
  request.put('/settings', data) as Promise<SystemSettings>

export const pruneAuditLogsApi = () =>
  request.post('/settings/audit-logs/prune') as Promise<{ deleted: number }>

// ============ 存储清理（孤儿数据扫描 / 人工确认删除） ============
export const getStorageScanApi = (refresh = false) =>
  request.get('/settings/storage-scan', {
    params: { refresh: refresh ? 1 : 0 },
  }) as Promise<StorageScan>

export const cleanupStorageApi = (keys: string[]) =>
  request.post('/settings/storage-cleanup', { keys }) as Promise<StorageCleanupResult>

// ============ 概览 / 仪表盘 ============
export const getDashboardApi = () => request.get('/dashboard/overview') as Promise<Dashboard>

export const getDashDisplayConfigApi = () =>
  request.get('/dashboard/display-config') as Promise<DashDisplayConfig>

export const updateDashDisplayConfigApi = (data: {
  refresh_seconds?: number
  switch_seconds?: number
  roll_numbers?: boolean
}) => request.put('/dashboard/display-config', data) as Promise<DashDisplayConfig>

/** 服务器运行状态（概览页「服务器运行状态」卡片，内部专用） */
export const getSystemStatsApi = () =>
  request.get('/dashboard/system-stats') as Promise<SystemStats>

/** 仪表盘分享：公开大屏数据（<站点根>/d<share_code>，入参为去掉 d 前缀的 share_code） */
export const getDashboardSharePublicApi = (code: string) =>
  request.get(`/dashboard/s/${encodeURIComponent(code)}`) as Promise<DashboardSharePublic>

// ============ 分享访问 · 谱系分享（原图谱树分享，管理端） ============
export const listVisitSharesApi = () =>
  request.get('/visit/shares') as Promise<VisitShare[]>

export const createVisitShareApi = (data: {
  name: string
  note?: string
  allow_search: boolean
  allow_chat: boolean
  chat_model?: string | null
  expires_days?: number | null
  /** 自定义到期时刻（ISO 无时区字符串）；与 expires_days 二选一，给了以 expires_at 为准 */
  expires_at?: string | null
}) => request.post('/visit/shares', data) as Promise<VisitShare>

export const revokeVisitShareApi = (id: number) =>
  request.delete(`/visit/shares/${id}`) as Promise<any>

/** 彻底删除已废弃的分享记录（仅管理端清理用） */
export const deleteVisitShareRecordApi = (id: number) =>
  request.delete(`/visit/shares/${id}/record`) as Promise<any>

export const updateVisitShareApi = (id: number, data: {
  name?: string
  note?: string
  allow_search?: boolean
  allow_chat?: boolean
  chat_model?: string | null
  /** 新到期时间（ISO 无时区字符串）；传 null = 永久；不传 = 保持不变 */
  expires_at?: string | null
}) => request.put(`/visit/shares/${id}`, data) as Promise<VisitShare>

export const getVisitSettingsApi = () =>
  request.get('/visit/settings') as Promise<VisitSettings>

export const updateVisitSettingsApi = (data: Partial<VisitSettings>) =>
  request.put('/visit/settings', data) as Promise<VisitSettings>

// ============ 访客公开接口（短链码校验；?code=<share_code>） ============
export const visitInfoApi = (code: string) =>
  request.get('/visit/v/info', { params: { code } }) as Promise<VisitInfo>

/** 访客只读：谱系列表（3D 谱系画布一级）；需该短链有效 */
export const visitLineagesApi = (code: string) =>
  request.get('/visit/v/lineages', { params: { code } }) as Promise<Lineage[]>

/** 访客只读：谱系/全库人物树；可传 lineage_id 限定某谱系（3D 画布二级） */
export const visitTreeApi = (code: string, lineageId?: string) =>
  request.get('/visit/v/tree', {
    params: lineageId ? { code, lineage_id: lineageId } : { code },
  }) as Promise<TreeData>

export const visitSubtreeApi = (code: string, personId: string, depth = 4) =>
  request.get('/visit/v/subtree', {
    params: { code, person_id: personId, depth },
  }) as Promise<TreeData>

export const visitPersonApi = (code: string, personId: string) =>
  request.get(`/visit/v/person/${personId}`, {
    params: { code },
  }) as Promise<PersonDetail>

/** 访客只读：人物的"家族信息"材料（需该分享开放搜索） */
export const visitPersonMaterialsApi = (code: string, personId: string) =>
  request.get(`/visit/v/person/${personId}/materials`, {
    params: { code },
  }) as Promise<PersonMaterials>

export const visitSearchApi = (code: string, q: string) =>
  request.get('/visit/v/search', { params: { code, q } }) as Promise<Person[]>

export const visitChatApi = (code: string, question: string) =>
  request.post('/visit/v/chat', { question }, { params: { code } }) as Promise<ChatResponse>

/** 访客：谱书内容索引状态（搜索框提示用） */
export const visitRagStatusApi = (code: string) =>
  request.get('/visit/v/rag-status', { params: { code } }) as Promise<RagStatus>

/** 访客：自然语言检索谱书内容（需该分享开放搜索） */
export const visitRagAskApi = (code: string, question: string, lineageId?: string) =>
  request.post(
    '/visit/v/rag-ask',
    { question, lineage_id: lineageId },
    { params: { code } },
  ) as Promise<RagAskResponse>

/** 访客：人物的 AI 展示文字（需该分享开放搜索） */
export const visitRagPersonIntroApi = (code: string, personId: string) =>
  request.post(
    '/visit/v/person-intro',
    { person_id: personId },
    { params: { code } },
  ) as Promise<PersonIntro>

// ============ 分享访问 · 仪表盘分享（管理端） ============
export const listDashboardSharesApi = () =>
  request.get('/dashboard/shares') as Promise<DashboardShare[]>

export const createDashboardShareApi = (data: {
  name: string
  note?: string
  expires_days?: number | null
}) => request.post('/dashboard/shares', data) as Promise<DashboardShare>

export const updateDashboardShareApi = (id: number, data: {
  name?: string
  note?: string
  /** 新到期时间（ISO 无时区字符串）；传 null = 永久；不传 = 保持不变 */
  expires_at?: string | null
}) => request.put(`/dashboard/shares/${id}`, data) as Promise<DashboardShare>

export const revokeDashboardShareApi = (id: number) =>
  request.delete(`/dashboard/shares/${id}`) as Promise<any>

/** 彻底删除已废弃的仪表盘分享记录（仅管理端清理用） */
export const deleteDashboardShareRecordApi = (id: number) =>
  request.delete(`/dashboard/shares/${id}/record`) as Promise<any>
