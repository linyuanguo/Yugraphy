<template>
  <div>
    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 12px"
      message="记录系统内的重要操作（上传/重复文件拦截/续跑识别/删除等）。仅管理员可查看与删除。"
    />

    <a-card size="small" style="margin-bottom: 12px">
      <a-space wrap align="center">
        <span class="lbl">日志保留：</span>
        <a-select
          v-model:value="retentionMode"
          style="width: 180px"
          :options="retentionOptions"
        />
        <template v-if="retentionMode === 'custom'">
          <a-input-number
            v-model:value="retentionDays"
            :min="1"
            :max="3650"
            :precision="0"
            style="width: 120px"
          />
          <span class="hint-txt">天</span>
        </template>
        <a-button type="primary" size="small" :loading="savingRetention" @click="saveRetention">
          保存设置
        </a-button>
        <a-popconfirm
          title="按当前保留策略立即清理过期日志？"
          :ok-text="retentionMode === '' ? '未设保留期，无需清理' : '立即清理'"
          :ok-button-props="{ disabled: retentionMode === '' }"
          @confirm="pruneNow"
        >
          <a-button danger size="small">立即清理</a-button>
        </a-popconfirm>
      </a-space>
      <div class="hint-txt" style="margin-top: 6px">
        系统每 6 小时自动清理一次超过保留期的日志；选择「永久」则一直保存。改动需点击「保存设置」生效。
      </div>
    </a-card>

    <a-space style="margin-bottom: 12px" wrap>
      <a-select
        v-model:value="filterAction"
        placeholder="全部动作"
        allow-clear
        style="width: 220px"
        :options="actionOptions"
        @change="reload"
      />
      <a-input
        v-model:value="filterQ"
        placeholder="搜索详情 / 对象 / 动作"
        allow-clear
        style="width: 240px"
        @press-enter="reload"
        @change="onQChange"
      >
        <template #prefix>🔍</template>
      </a-input>
      <a-button @click="reload">刷新</a-button>
      <a-popconfirm title="确定清空全部操作日志？该操作也会记入日志，且不可恢复" @confirm="clearAll">
        <a-button danger>清空全部日志</a-button>
      </a-popconfirm>
    </a-space>

    <a-table
      :data-source="items"
      :loading="loading"
      row-key="id"
      size="small"
      :columns="columns"
      :pagination="{
        current: page,
        pageSize: limit,
        total,
        showSizeChanger: false,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      @change="onPageChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'time'">
          {{ formatTime(record.created_at) }}
        </template>
        <template v-else-if="column.key === 'user'">
          {{ record.username || (record.user_id ? `#${record.user_id}` : '系统') }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-tag :color="actionColor(record.action)" :title="record.action">{{ actionLabel(record.action) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'target'">
          <span v-if="record.target_id" class="muted">{{ record.target_type }}: {{ record.target_id }}</span>
          <span v-else class="muted">—</span>
        </template>
        <template v-else-if="column.key === 'detail'">
          <span :title="record.detail || ''">{{ record.detail || '—' }}</span>
        </template>
        <template v-else-if="column.key === 'ip'">
          <span class="muted">{{ record.ip_address || '—' }}</span>
        </template>
        <template v-else-if="column.key === 'op'">
          <a-popconfirm title="确认删除该条日志？" @confirm="removeOne(record.id)">
            <a style="color: #ff4d4f">删除</a>
          </a-popconfirm>
        </template>
      </template>
    </a-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  clearAuditLogsApi,
  deleteAuditLogApi,
  getSystemSettingsApi,
  listAuditLogsApi,
  pruneAuditLogsApi,
  updateSystemSettingsApi,
} from '@/api'
import type { AuditLogItem } from '@/types'

const ACTION_LABELS: Record<string, string> = {
  login: '登录',
  logout: '退出',
  update_system_settings: '更新系统设置',
  duplicate_file: '重复文件拦截',
  create_import_task: '上传识别任务',
  convert_images: '转换图片',
  ai_extract: 'AI 识别',
  ai_consolidate: 'AI 整理',
  resume_import_task: '续跑识别任务',
  re_extract_page: '单页重新识别',
  re_extract_all: '整卷重新识别',
  apply_task: '写入图谱',
  save_page_review: '暂存页审核',
  cleanup_task_pages: '清理任务图片',
  archive_import_task: '归档文件',
  unarchive_import_task: '从归档文件取回',
  delete_import_task: '删除识别任务',
  delete_lineage: '删除谱系',
  create_lineage: '新建谱系',
  update_lineage: '更新谱系',
  create_user: '新建用户',
  update_user: '更新用户',
  delete_user: '删除用户',
  reset_password: '重置密码',
  delete_audit_log: '删除操作日志',
  clear_audit_logs: '清空操作日志',
  prune_audit_logs: '清理过期日志',
}

const ACTION_COLORS: Record<string, string> = {
  login: 'green',
  logout: 'default',
  update_system_settings: 'purple',
  duplicate_file: 'orange',
  create_import_task: 'blue',
  convert_images: 'geekblue',
  ai_extract: 'purple',
  ai_consolidate: 'magenta',
  resume_import_task: 'cyan',
  re_extract_page: 'cyan',
  re_extract_all: 'purple',
  apply_task: 'geekblue',
  save_page_review: 'blue',
  cleanup_task_pages: 'default',
  archive_import_task: 'purple',
  unarchive_import_task: 'cyan',
  delete_import_task: 'red',
  delete_lineage: 'red',
  delete_audit_log: 'red',
  clear_audit_logs: 'red',
  prune_audit_logs: 'red',
  create_lineage: 'green',
  update_lineage: 'blue',
}

const actionLabel = (a: string) => ACTION_LABELS[a] || a
const actionColor = (a: string) => ACTION_COLORS[a] || 'default'

/** 后端容器为 UTC，存库/返回的时间串无时区后缀（如 2026-09-04T12:46:43）。
 *  这里按 UTC 解析后转浏览器本地时区显示，与主机系统时间保持一致。 */
const pad2 = (n: number) => String(n).padStart(2, '0')
const formatTime = (s?: string) => {
  if (!s) return '—'
  const hasTz = /[zZ]$|[+-]\d{2}:\d{2}$/.test(s)
  const d = new Date(hasTz ? s : s.replace(' ', 'T') + 'Z')
  if (Number.isNaN(d.getTime())) return s.replace('T', ' ').slice(0, 19)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

const columns = [
  { title: '时间', key: 'time', width: 150 },
  { title: '操作人', key: 'user', width: 100 },
  { title: '动作', key: 'action', width: 140 },
  { title: '对象', key: 'target', width: 180, ellipsis: true },
  { title: '详情', key: 'detail', ellipsis: true },
  { title: 'IP', key: 'ip', width: 130 },
  { title: '操作', key: 'op', width: 70 },
]

// ---------- 保留策略 ----------
const retentionOptions = [
  { value: '', label: '永久保留' },
  { value: '7', label: '1 周' },
  { value: '30', label: '1 个月' },
  { value: '90', label: '3 个月' },
  { value: '180', label: '半年' },
  { value: 'custom', label: '自定义天数' },
]
const retentionMode = ref('') // '' | '7' | '30' | '90' | '180' | 'custom'
const retentionDays = ref(90)
const savingRetention = ref(false)

const loadRetention = async () => {
  try {
    const st = await getSystemSettingsApi()
    const days = (st.audit_retention_days || '').trim()
    if (!days) retentionMode.value = ''
    else if (['7', '30', '90', '180'].includes(days)) retentionMode.value = days
    else {
      retentionMode.value = 'custom'
      const n = Number(days)
      retentionDays.value = Number.isInteger(n) && n > 0 ? n : 90
    }
  } catch {
    /* request 已提示 */
  }
}

const saveRetention = async () => {
  savingRetention.value = true
  try {
    let value = retentionMode.value
    if (value === 'custom') {
      const n = Math.floor(Number(retentionDays.value))
      if (!Number.isInteger(n) || n < 1 || n > 3650) {
        message.error('请输入 1~3650 之间的整数天数')
        return
      }
      value = String(n)
    }
    await updateSystemSettingsApi({ audit_retention_days: value })
    message.success(
      value === '' ? '已设置：日志永久保留' : `已设置：日志保留 ${value} 天后自动清理`,
    )
  } finally {
    savingRetention.value = false
  }
}

const pruneNow = async () => {
  if (retentionMode.value === '') {
    message.info('当前为永久保留，没有需要清理的过期日志')
    return
  }
  try {
    const res = await pruneAuditLogsApi()
    message.success(`已按保留策略清理 ${res.deleted} 条过期日志`)
    page.value = 1
    load()
  } catch {
    /* request 已提示 */
  }
}

// ---------- 列表 ----------
const items = ref<AuditLogItem[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const limit = ref(20)
const filterAction = ref<string | undefined>(undefined)
const filterQ = ref('')
const extraActions = ref<string[]>([])

const actionOptions = computed(() => {
  const keys = new Set<string>(Object.keys(ACTION_LABELS))
  extraActions.value.forEach((a) => keys.add(a))
  return [...keys].sort().map((k) => ({ value: k, label: actionLabel(k) }))
})

let qTimer: number | undefined
const onQChange = () => {
  if (qTimer) window.clearTimeout(qTimer)
  qTimer = window.setTimeout(reload, 400)
}

const onPageChange = (p: any) => {
  page.value = p.current || 1
  load()
}

const reload = () => {
  page.value = 1
  load()
}

const load = async () => {
  loading.value = true
  try {
    const data = await listAuditLogsApi({
      action: filterAction.value || undefined,
      q: filterQ.value || undefined,
      offset: (page.value - 1) * limit.value,
      limit: limit.value,
    })
    items.value = data.items
    total.value = data.total
    const seen = new Set(extraActions.value)
    data.items.forEach((it: AuditLogItem) => {
      if (!ACTION_LABELS[it.action] && !seen.has(it.action)) {
        seen.add(it.action)
        extraActions.value = [...extraActions.value, it.action]
      }
    })
  } catch {
    /* request 已提示 */
  } finally {
    loading.value = false
  }
}

const removeOne = async (id: number) => {
  await deleteAuditLogApi(id)
  message.success('日志已删除')
  if (items.value.length === 1 && page.value > 1) page.value -= 1
  load()
}

const clearAll = async () => {
  await clearAuditLogsApi()
  message.success('日志已清空')
  page.value = 1
  load()
}

onMounted(() => {
  loadRetention()
  load()
})
</script>

<style scoped>
.lbl {
  color: rgba(0, 0, 0, 0.85);
}
.hint-txt {
  font-size: 12px;
  color: #999;
}
.muted {
  color: #999;
  font-size: 12px;
}
</style>
