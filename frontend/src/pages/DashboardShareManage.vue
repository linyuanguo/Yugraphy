<template>
  <div>
    <a-card :bordered="false">
      <template #title>📊 大屏分享</template>
      <template #extra>
        <a-button type="primary" @click="openCreate">＋ 生成大屏分享</a-button>
      </template>

      <a-table
        :data-source="shares"
        :columns="columns"
        row-key="id"
        :loading="loading"
        :pagination="{ pageSize: 8 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <div class="s-name">{{ record.name }}</div>
            <div v-if="record.note" class="s-note">{{ record.note }}</div>
          </template>
          <template v-else-if="column.key === 'link'">
            <a-typography-text code copyable>
              {{ origin }}/d{{ record.share_code }}
            </a-typography-text>
            <div class="s-note">访客打开即见大屏统计（不含内部任务/导入进度）</div>
          </template>
          <template v-else-if="column.key === 'expires'">
            {{ record.expires_at ? new Date(record.expires_at).toLocaleString() : '永久' }}
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="record.revoked ? 'default' : 'green'">
              {{ record.revoked ? '已撤销' : '有效' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'created_at'">
            {{ record.created_at ? new Date(record.created_at).toLocaleString() : '—' }}
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-space v-if="!record.revoked">
              <a-button size="small" @click="openEdit(record)">编辑</a-button>
              <a-button size="small" @click="copyShort(record)">复制链接</a-button>
              <a-button size="small" type="primary" @click="preview(record)">预览</a-button>
              <a-popconfirm title="撤销后该链接立即失效，确认撤销？" @confirm="revoke(record)">
                <a-button size="small" danger>撤销</a-button>
              </a-popconfirm>
            </a-space>
            <a-space v-else>
              <a-tag>链接已失效</a-tag>
              <a-popconfirm
                title="彻底删除该条记录？链接已永久失效，删除后仅留审计日志。"
                ok-text="删除"
                ok-button-props="{ danger: true }"
                @confirm="removeRecord(record)"
              >
                <a-button size="small" danger>删除记录</a-button>
              </a-popconfirm>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-alert
      type="info"
      show-icon
      style="margin: 16px 0"
      message="大屏分享对外展示谱系规模、世代/房支/性别分布等统计；任务进度、谱书原文、人物详情等内部数据不会外泄。"
    />

    <a-modal
      v-model:open="createOpen"
      :title="editing ? '编辑大屏分享' : '生成大屏分享'"
      :confirm-loading="creating"
      @ok="handleModalOk"
    >
      <a-form layout="vertical">
        <a-form-item label="分享名称" required>
          <a-input v-model:value="form.name" placeholder="如：张氏族谱数据大屏" />
        </a-form-item>
        <a-form-item label="备注说明">
          <a-input v-model:value="form.note" placeholder="可选，说明用途/对象" />
        </a-form-item>
        <a-form-item v-if="!editing" label="有效期">
          <a-radio-group v-model:value="form.expiresDays">
            <a-radio :value="7">7 天</a-radio>
            <a-radio :value="30">30 天</a-radio>
            <a-radio :value="365">1 年</a-radio>
            <a-radio :value="'custom'">自定义</a-radio>
            <a-radio :value="null">永久</a-radio>
          </a-radio-group>
          <div v-if="form.expiresDays === 'custom'" style="margin-top: 8px">
            <a-input-number
              v-model:value="form.customDays"
              :min="1"
              :max="3650"
              :step="1"
              addon-after="天"
              style="width: 180px"
            />
            <div class="s-model-hint">自定义有效期 1~3650 天，到期当日仍可访问，次日 0 点起失效。</div>
          </div>
        </a-form-item>
        <a-form-item v-if="editing" label="有效期修改">
          <a-radio-group v-model:value="form.expireMode">
            <a-radio value="keep">保持不变（当前到期：{{ currentExpiresText }}）</a-radio>
            <a-radio value="forever">设为永久有效</a-radio>
            <a-radio value="date">重新指定到期日期</a-radio>
          </a-radio-group>
          <div v-if="form.expireMode === 'date'" style="margin-top: 8px">
            <a-date-picker
              v-model:value="form.expireDate"
              style="width: 100%"
              :disabled-date="disabledDate"
              placeholder="选择到期日期"
            />
            <div class="s-model-hint">所选日当天仍可访问，次日 0 点起失效。</div>
          </div>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { message } from 'ant-design-vue'
import {
  createDashboardShareApi,
  deleteDashboardShareRecordApi,
  listDashboardSharesApi,
  revokeDashboardShareApi,
  updateDashboardShareApi,
} from '@/api'
import type { DashboardShare } from '@/types'

const loading = ref(false)
const shares = ref<DashboardShare[]>([])
const columns = [
  { title: '名称', key: 'name' },
  { title: '访问链接', key: 'link', width: 250 },
  { title: '有效期至', key: 'expires', width: 160 },
  { title: '状态', key: 'status', width: 80 },
  { title: '创建时间', key: 'created_at', width: 160 },
  { title: '操作', key: 'actions', width: 190 },
]
const origin = window.location.origin

const createOpen = ref(false)
const creating = ref(false)
const editing = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({
  name: '',
  note: '',
  // 创建态有效期档位：数字天 / 'custom' 自定义 / null 永久
  expiresDays: null as number | 'custom' | null,
  customDays: 30 as number | null,
  // 编辑态有效期：keep=保持不变 / forever=设为永久 / date=重设到期日期
  expireMode: 'keep' as 'keep' | 'forever' | 'date',
  expireDate: null as Dayjs | null,
})
const currentExpiresText = ref('永久')

const loadShares = async () => {
  loading.value = true
  try {
    shares.value = await listDashboardSharesApi()
  } finally {
    loading.value = false
  }
}

const resetForm = () => {
  form.name = ''
  form.note = ''
  form.expiresDays = null
  form.customDays = 30
  form.expireMode = 'keep'
  form.expireDate = null
}

const openCreate = () => {
  resetForm()
  editing.value = false
  editingId.value = null
  createOpen.value = true
}

/** Modal 确定按钮统一入口：编辑态→updateShare，否则→createShare */
const handleModalOk = () => {
  if (editing.value) return updateShare()
  return createShare()
}

const openEdit = (record: DashboardShare) => {
  resetForm()
  editing.value = true
  editingId.value = record.id
  form.name = record.name
  form.note = record.note || ''
  form.expireMode = 'keep'
  currentExpiresText.value = record.expires_at
    ? new Date(record.expires_at).toLocaleString()
    : '永久'
  createOpen.value = true
}

const disabledDate = (current: Dayjs) => current && current < dayjs().startOf('day')

/** 失败兜底提示：带业务状态码的错误已由 request 层统一提示；404/网络异常在 request 层静默，需在此补提示 */
const toastApiError = (e: any, prefix: string) => {
  const status = e?.response?.status
  if (!status || status === 404) {
    const det = e?.response?.data?.detail
    const msg = typeof det === 'string' && det ? det : e?.message || '网络异常'
    message.error(`${prefix}：${msg}`)
  }
  console.error(prefix, e)
}

const resolveExpiresDays = (): number | null => {
  if (form.expiresDays === 'custom') {
    return Math.round(form.customDays as number)
  }
  return form.expiresDays as number | null
}

const createShare = async () => {
  if (!form.name.trim()) {
    message.error('请填写分享名称')
    return
  }
  if (form.expiresDays === 'custom' && !form.customDays) {
    message.error('请填写自定义有效天数')
    return
  }
  creating.value = true
  try {
    const share = await createDashboardShareApi({
      name: form.name.trim(),
      note: form.note || undefined,
      expires_days: resolveExpiresDays(),
    })
    message.success('大屏分享已生成，链接已复制')
    createOpen.value = false
    await loadShares()
    await copyShort(share)
  } catch (e) {
    toastApiError(e, '创建失败')
  } finally {
    creating.value = false
  }
}

const updateShare = async () => {
  if (!editingId.value) return
  if (!form.name.trim()) {
    message.error('请填写分享名称')
    return
  }
  if (form.expireMode === 'date' && !form.expireDate) {
    message.error('请选择到期日期')
    return
  }
  creating.value = true
  try {
    await updateDashboardShareApi(editingId.value, {
      name: form.name.trim(),
      note: form.note,
      expires_at:
        form.expireMode === 'forever'
          ? null
          : form.expireMode === 'date'
            ? dayjs(form.expireDate).endOf('day').format('YYYY-MM-DD HH:mm:ss')
            : undefined,
    })
    message.success('分享已更新')
    createOpen.value = false
    await loadShares()
  } catch (e) {
    toastApiError(e, '更新失败')
  } finally {
    creating.value = false
  }
}

const shareLink = (record: DashboardShare) => `${origin}/d${record.share_code}`

const copyUrl = async (url: string) => {
  try {
    await navigator.clipboard.writeText(url)
    message.success('已复制：' + url)
  } catch {
    window.prompt('请手动复制以下链接：', url)
  }
}

const copyShort = async (record: DashboardShare) => {
  await copyUrl(shareLink(record))
}

const preview = (record: DashboardShare) => {
  window.open(shareLink(record), '_blank')
}

const revoke = async (record: DashboardShare) => {
  await revokeDashboardShareApi(record.id)
  message.success('已撤销该分享')
  await loadShares()
}

const removeRecord = async (record: DashboardShare) => {
  await deleteDashboardShareRecordApi(record.id)
  message.success('记录已删除')
  await loadShares()
}

onMounted(() => {
  loadShares()
})
</script>

<style scoped>
.s-name {
  font-weight: 600;
}
.s-note {
  font-size: 12px;
  color: #999;
}
.s-model-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #999;
}
</style>
