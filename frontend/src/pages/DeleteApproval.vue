<template>
  <div class="delete-approval">
    <a-alert
      type="info"
      show-icon
      class="intro-alert"
      message="操作员删除「扫描件任务 / 谱系」时不会直接删除，而是提交删除申请。管理员在此处通过后才会真正删除；审批时若目标已不存在（已被其它途径删除/清理），系统会自动按「已达成」归档。"
    />

    <div class="toolbar">
      <a-space wrap>
        <a-select
          v-model:value="statusFilter"
          style="width: 130px"
          :options="statusOptions"
          @change="applyFilter"
        />
        <a-select
          v-model:value="typeFilter"
          style="width: 130px"
          :options="typeOptions"
          @change="applyFilter"
        />
        <span class="muted">{{ filteredRows.length }} 条记录</span>
        <a-button size="small" :loading="loading" @click="load">🔄 刷新</a-button>
      </a-space>
    </div>

    <a-table
      :data-source="filteredRows"
      :columns="columns"
      :loading="loading"
      row-key="id"
      size="middle"
      :pagination="{ pageSize: 15, showSizeChanger: false, showTotal: (t: number) => `共 ${t} 条` }"
      class="approval-table"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'type'">
          <a-tag :color="record.target_type === 'task' ? 'blue' : 'green'">
            {{ record.target_type === 'task' ? '扫描件' : '谱系' }}
          </a-tag>
        </template>

        <template v-else-if="column.key === 'target'">
          <div class="obj-cell">
            <div class="obj-name">
              {{ record.target_name || record.target_id }}
              <a-tag v-if="record.force" color="volcano" class="force-mini">强删</a-tag>
            </div>
            <div class="obj-sub">{{ record.target_id }}</div>
          </div>
        </template>

        <template v-else-if="column.key === 'snap'">
          <div v-if="record.snapshot" class="snap-cell">
            <span v-if="record.target_type === 'task'">
              {{ record.snapshot.file || record.target_name }} ·
              {{ record.snapshot.total_pages || 0 }} 页 ·
              状态 {{ record.snapshot.status || '—' }}
              <a-tag v-if="record.snapshot.applied" color="processing" size="small" class="force-mini">已写入</a-tag>
            </span>
            <span v-else>
              {{ record.snapshot.person_count ?? 0 }} 位人物 ·
              {{ record.snapshot.branches ?? 0 }} 个房支
              <template v-if="record.snapshot.code"> · {{ record.snapshot.code }}</template>
            </span>
          </div>
        </template>

        <template v-else-if="column.key === 'who'">
          <div class="sub-cell">{{ record.submitted_by_name || '—' }}</div>
          <div class="muted-time">{{ fmtTime(record.created_at) }}</div>
        </template>

        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
        </template>

        <template v-else-if="column.key === 'result'">
          <template v-if="record.status === 'pending'">
            <span class="muted">待处理</span>
          </template>
          <template v-else>
            <div class="sub-cell">
              {{ record.reviewed_by_name || '—' }}
              <span class="muted">（{{ fmtTime(record.reviewed_at) }}）</span>
            </div>
            <div v-if="record.review_note" class="note-line">{{ record.review_note }}</div>
          </template>
        </template>

        <template v-else-if="column.key === 'ops'">
          <a-space v-if="record.status === 'pending'">
            <a-button type="primary" size="small" @click="openApprove(record)">通过并删除</a-button>
            <a-button danger size="small" @click="openReject(record)">驳回</a-button>
          </a-space>
          <span v-else class="muted">—</span>
        </template>
      </template>
    </a-table>

    <!-- 审批通过弹窗（含目标当前状态实时核对） -->
    <a-modal
      v-model:open="approveOpen"
      title="审批删除申请"
      :confirm-loading="approving"
      ok-text="通过并删除"
      cancel-text="取消"
      :ok-button-props="{
        danger: true,
        disabled: detailLoading || !canApprove,
      }"
      @ok="doApprove"
      width="640px"
    >
      <div v-if="detailLoading" class="detail-loading">加载申请详情…</div>
      <div v-else-if="detail">
        <div class="modal-title">
          #{{ detail.id }} · {{ detail.target_type === 'task' ? '扫描件' : '谱系' }}：
          {{ detail.target_name || detail.target_id }}
        </div>
        <a-descriptions size="small" :column="2" class="modal-desc">
          <a-descriptions-item label="申请人">{{ detail.submitted_by_name || '—' }}</a-descriptions-item>
          <a-descriptions-item label="申请时间">{{ fmtTime(detail.created_at) }}</a-descriptions-item>
          <a-descriptions-item v-if="detail.target_type === 'task'" label="提交时状态">
            {{ detail.snapshot?.status || '—' }}（{{ detail.snapshot?.total_pages || 0 }} 页）
          </a-descriptions-item>
          <a-descriptions-item v-else label="提交时人物">
            {{ detail.snapshot?.person_count ?? 0 }} 位
          </a-descriptions-item>
        </a-descriptions>

        <!-- 当前状态：审批时实时核对 -->
        <template v-if="detail.current">
          <a-alert
            v-if="detail.current.exists === false"
            type="warning"
            show-icon
            class="state-alert"
            message="目标当前已不存在"
            :description="detail.current.note || '通过后将自动按「已达成删除目的」归档。'"
          />
          <a-alert
            v-else-if="detail.current.kind === 'lineage'"
            :type="detail.current.person_count ? 'error' : 'success'"
            show-icon
            class="state-alert"
            :message="detail.current.person_count ? `当前仍归属 ${detail.current.person_count} 位人物（${detail.current.branches ?? 0} 个房支）` : '当前已无归属人物，可直接删除'"
            :description="detail.current.person_count ? '通过将连同这些人物及全部关联一并删除，不可恢复。' : undefined"
          />
          <a-alert
            v-else
            :type="detail.current.status && detail.current.status !== 'failed' ? 'info' : 'warning'"
            show-icon
            class="state-alert"
            :message="`当前状态：${detail.current.status || '未知'}（${detail.current.total_pages || 0} 页）`"
          />
        </template>

        <a-checkbox
          v-if="forceRequired"
          v-model:checked="forceChecked"
          class="force-check"
        >
          我已知晓：强制删除将连带该谱系全部人物 / 房支 / 已入库条目与检索向量一并清除（不可恢复）
        </a-checkbox>

        <a-form layout="vertical" class="note-form">
          <a-form-item label="审批意见（可选）">
            <a-textarea
              v-model:value="reviewNote"
              :rows="2"
              :maxlength="500"
              placeholder="留痕审批意见（驳回时必须填写原因）"
            />
          </a-form-item>
        </a-form>
      </div>
    </a-modal>

    <!-- 驳回弹窗 -->
    <a-modal
      v-model:open="rejectOpen"
      title="驳回删除申请"
      :confirm-loading="rejecting"
      ok-text="确认驳回"
      cancel-text="取消"
      @ok="doReject"
    >
      <p class="muted">驳回后目标保留、操作员可继续编辑。建议填写原因，便于操作员在原删除页面看到并重新申请。</p>
      <a-form layout="vertical">
        <a-form-item label="驳回原因" required>
          <a-textarea
            v-model:value="rejectNote"
            :rows="3"
            :maxlength="500"
            placeholder="如：该卷仍需保留用于比对 / 归档信息不完整，请先补充"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  approveDeleteRequestApi,
  getDeleteRequestApi,
  listDeleteRequestsApi,
  rejectDeleteRequestApi,
} from '@/api'
import type { DeleteRequestItem } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { notifyDeleteApprovalChanged, refreshDeleteApprovalCount } from '@/utils/deleteApprovalCount'

const auth = useAuthStore()

const loading = ref(false)
const rows = ref<DeleteRequestItem[]>([])
const statusFilter = ref<'all' | 'pending' | 'approved' | 'rejected'>('pending')
const typeFilter = ref<'all' | 'task' | 'lineage'>('all')

const statusOptions = [
  { label: '待审批', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '全部', value: 'all' },
]
const typeOptions = [
  { label: '全部类型', value: 'all' },
  { label: '扫描件任务', value: 'task' },
  { label: '谱系', value: 'lineage' },
]

const columns = [
  { title: '类型', key: 'type', width: 84 },
  { title: '删除对象', key: 'target', width: 250 },
  { title: '提交快照', key: 'snap' },
  { title: '申请人', key: 'who', width: 150 },
  { title: '状态', key: 'status', width: 90 },
  { title: '审批结果', key: 'result' },
  { title: '操作', key: 'ops', width: 190 },
]

const filteredRows = computed(() =>
  rows.value.filter((r) => {
    if (statusFilter.value !== 'all' && r.status !== statusFilter.value) return false
    if (typeFilter.value !== 'all' && r.target_type !== typeFilter.value) return false
    return true
  }),
)

const applyFilter = () => {
  /* 本地筛选即可，无需重拉 */
}

const load = async () => {
  loading.value = true
  try {
    rows.value = await listDeleteRequestsApi()
  } finally {
    loading.value = false
  }
}

// ============ 状态标签 ============
const statusLabel = (s: string) => (s === 'pending' ? '待审批' : s === 'approved' ? '已通过' : '已驳回')
const statusColor = (s: string) => (s === 'pending' ? 'orange' : s === 'approved' ? 'green' : 'red')

const fmtTime = (t?: string | null) => {
  if (!t) return ''
  const s = /Z|[+-]\d\d:\d\d$/.test(t) ? t : `${t}Z`
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return t
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// ============ 通过（审批弹窗：先拉实时目标状态再确认） ============
const approveOpen = ref(false)
const approving = ref(false)
const detailLoading = ref(false)
const approveId = ref<number | null>(null)
const detail = ref<DeleteRequestItem | null>(null)
const forceChecked = ref(false)
const reviewNote = ref('')

const forceRequired = computed(() => {
  const cur = detail.value?.current
  return (
    !!detail.value &&
    cur?.kind === 'lineage' &&
    cur.exists &&
    (cur.person_count ?? 0) > 0
  )
})
const canApprove = computed(() => !forceRequired.value || forceChecked.value)

const openApprove = async (row: DeleteRequestItem) => {
  approveId.value = row.id
  detail.value = null
  forceChecked.value = false
  reviewNote.value = ''
  approveOpen.value = true
  detailLoading.value = true
  try {
    detail.value = await getDeleteRequestApi(row.id)
  } finally {
    detailLoading.value = false
  }
}

const doApprove = async () => {
  if (!approveId.value) return
  approving.value = true
  try {
    const res = await approveDeleteRequestApi(approveId.value, {
      force: forceChecked.value || false,
      note: reviewNote.value.trim() || undefined,
    })
    message.success(
      res.already_gone ? '目标已不存在，该申请已按「已达成」归档' : '审批通过，对象已删除',
    )
    approveOpen.value = false
    await load()
    notifyDeleteApprovalChanged()
    await refreshDeleteApprovalCount(auth.user?.role)
  } catch {
    /* 错误提示由请求拦截器统一处理 */
  } finally {
    approving.value = false
  }
}

// ============ 驳回 ============
const rejectOpen = ref(false)
const rejecting = ref(false)
const rejectId = ref<number | null>(null)
const rejectNote = ref('')

const openReject = (row: DeleteRequestItem) => {
  rejectId.value = row.id
  rejectNote.value = ''
  rejectOpen.value = true
}

const doReject = async () => {
  if (!rejectId.value) return
  if (!rejectNote.value.trim()) {
    message.warning('请填写驳回原因，便于操作员了解情况')
    return
  }
  rejecting.value = true
  try {
    await rejectDeleteRequestApi(rejectId.value, { note: rejectNote.value.trim() })
    message.success('已驳回该删除申请')
    rejectOpen.value = false
    await load()
    notifyDeleteApprovalChanged()
    await refreshDeleteApprovalCount(auth.user?.role)
  } catch {
    /* 错误提示由请求拦截器统一处理 */
  } finally {
    rejecting.value = false
  }
}

onMounted(() => {
  load()
  refreshDeleteApprovalCount(auth.user?.role)
})
</script>

<style scoped>
.delete-approval .intro-alert {
  margin-bottom: 14px;
}
.delete-approval .toolbar {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
}
.delete-approval .muted {
  color: #8a94a6;
  font-size: 12px;
}
.obj-cell .obj-name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.obj-cell .obj-sub,
.snap-cell,
.sub-cell {
  color: #8a94a6;
  font-size: 12px;
}
.force-mini {
  margin-left: 4px;
  transform: scale(0.85);
}
.muted-time {
  color: #b0b6bf;
  font-size: 12px;
}
.note-line {
  color: #cf7a2b;
  font-size: 12px;
  line-height: 1.5;
  max-width: 260px;
  white-space: normal;
}
.approval-table :deep(.ant-table-cell) {
  vertical-align: top;
}
.modal-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
}
.modal-desc {
  margin-bottom: 10px;
}
.state-alert {
  margin: 8px 0;
}
.force-check {
  display: block;
  margin: 12px 0 4px;
  color: #cf1322;
  font-weight: 600;
}
.note-form {
  margin-top: 12px;
}
.detail-loading {
  padding: 24px 0;
  text-align: center;
  color: #8a94a6;
}
</style>
