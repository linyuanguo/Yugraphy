<template>
  <div>
    <a-card :bordered="false">
      <template #title>🔗 谱系分享</template>
      <template #extra>
        <a-button type="primary" @click="openCreate">＋ 生成谱系分享</a-button>
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
          <template v-else-if="column.key === 'perms'">
            <div>
              <a-tag v-if="record.allow_search" color="blue">搜索</a-tag>
              <a-tag v-if="record.allow_chat" color="gold">问答</a-tag>
              <a-tag v-if="!record.allow_search && !record.allow_chat" color="default">仅浏览</a-tag>
              <div v-if="record.allow_chat && record.chat_model" class="s-model">
                问答模型：{{ modelLabel(record.chat_model) }}
              </div>
            </div>
          </template>
          <template v-else-if="column.key === 'link'">
            <a-typography-text v-if="record.share_code" code copyable>
              {{ origin }}/{{ record.share_code }}
            </a-typography-text>
            <a-tag v-else color="default">补齐中</a-tag>
            <div class="s-note">短链为唯一访问形态（撤销/过期即失效）</div>
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
      message="图谱问答与模型参数需管理员在「系统设置」菜单（访客问答参数 / 模型配置）调整。"
    />

    <a-modal
      v-model:open="createOpen"
      :title="editing ? '编辑谱系分享' : '生成谱系分享'"
      :confirm-loading="creating"
      @ok="handleModalOk"
    >
      <a-form layout="vertical">
        <a-form-item label="分享名称" required>
          <a-input v-model:value="form.name" placeholder="如：张氏族谱对外展示" />
        </a-form-item>
        <a-form-item label="备注说明">
          <a-input v-model:value="form.note" placeholder="可选，说明用途/对象" />
        </a-form-item>
        <a-form-item v-if="!editing" label="有效期">
          <a-radio-group v-model:value="form.expiresDays">
            <a-radio :value="7">7 天</a-radio>
            <a-radio :value="30">30 天</a-radio>
            <a-radio :value="365">1 年</a-radio>
            <a-radio :value="-1">自定义到期时间</a-radio>
            <a-radio :value="null">永久</a-radio>
          </a-radio-group>
          <div v-if="form.expiresDays === -1" style="margin-top: 8px">
            <a-date-picker
              v-model:value="form.createExpire"
              show-time
              style="width: 100%"
              :disabled-date="disabledDate"
              placeholder="选择到期日期与时刻"
            />
            <div class="s-model-hint">到所选时刻即失效，访客链接无法再访问。</div>
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
        <a-form-item label="访客权限">
          <a-space direction="vertical">
            <a-checkbox v-model:checked="form.allowSearch">允许按姓名搜索（支持同音字/模糊匹配）</a-checkbox>
            <a-checkbox v-model:checked="form.allowChat">允许图谱问答（问父母/子女/配偶/祖先等）</a-checkbox>
          </a-space>
        </a-form-item>
        <a-form-item v-if="form.allowChat" label="问答模型">
          <a-select
            v-model:value="form.chatModel"
            placeholder="选择该链接问答使用的模型"
            :options="chatModelOptions"
            style="width: 100%"
          />
          <div class="s-model-hint">
            仅该访客链接的图谱问答使用所选模型；选择「跟随系统」则使用左侧 系统设置 → 访客问答参数 中的模型。
          </div>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { message } from 'ant-design-vue'
import {
  createVisitShareApi,
  deleteVisitShareRecordApi,
  getSystemSettingsApi,
  listVisitSharesApi,
  revokeVisitShareApi,
  updateVisitShareApi,
} from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { LlmModel, VisitShare } from '@/types'

const auth = useAuthStore()

const loading = ref(false)
const shares = ref<VisitShare[]>([])
const columns = [
  { title: '名称', key: 'name' },
  { title: '权限', key: 'perms', width: 190 },
  { title: '访问链接', key: 'link', width: 230 },
  { title: '有效期至', key: 'expires', width: 160 },
  { title: '状态', key: 'status', width: 80 },
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
  expiresDays: null as number | null,
  allowSearch: true,
  allowChat: false,
  chatModel: '',
  // 编辑态有效期：keep=保持不变 / forever=设为永久 / date=重设到期日期
  expireMode: 'keep' as 'keep' | 'forever' | 'date',
  expireDate: null as Dayjs | null,
  // 新建态「自定义到期时间」：expiresDays === -1 时取该时刻
  createExpire: null as Dayjs | null,
})

// 可选的问答模型列表（来自 系统设置 → 模型配置）
const models = ref<LlmModel[]>([])
const defaultModel = ref('')
const chatModelOptions = computed(() => [
  { value: '', label: defaultModel.value ? `跟随系统（当前：${defaultModel.value}）` : '跟随系统（默认模型）' },
  ...models.value.map((m) => ({ value: m.name, label: m.label || m.name })),
])

const modelLabel = (name: string) => {
  const hit = models.value.find((m) => m.name === name)
  return hit ? hit.label || name : name
}

// 当前编辑记录的原到期时间文本
const currentExpiresText = computed(() => {
  if (!editingId.value) return ''
  const rec = shares.value.find((s) => s.id === editingId.value)
  if (!rec) return ''
  return rec.expires_at ? dayjs(rec.expires_at).format('YYYY-MM-DD HH:mm') : '永久'
})

// 到期日期不允许选今天之前
const disabledDate = (d: Dayjs) =>
  d.startOf('day').valueOf() < dayjs().startOf('day').valueOf()

const loadModels = async () => {
  // 模型列表在「系统设置」(仅管理员)；操作员使用「跟随系统（默认模型）」即可
  if (!auth.isAdmin) return
  try {
    const st = await getSystemSettingsApi()
    models.value = st.llm_models || []
    defaultModel.value = st.qa_llm_model || ''
  } catch {
    /* request 已提示 */
  }
}

const loadShares = async () => {
  loading.value = true
  try {
    shares.value = await listVisitSharesApi()
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  editing.value = false
  editingId.value = null
  form.name = ''
  form.note = ''
  form.expiresDays = 30
  form.allowSearch = true
  form.allowChat = false
  form.chatModel = ''
  form.expireMode = 'keep'
  form.expireDate = null
  form.createExpire = dayjs().add(30, 'day').hour(23).minute(59).second(59)
  createOpen.value = true
}

const openEdit = (record: VisitShare) => {
  editing.value = true
  editingId.value = record.id
  form.name = record.name
  form.note = record.note || ''
  form.expiresDays = null
  form.allowSearch = record.allow_search
  form.allowChat = record.allow_chat
  form.chatModel = record.chat_model || ''
  form.expireMode = 'keep'
  form.expireDate = record.expires_at ? dayjs(record.expires_at) : dayjs().add(30, 'day')
  createOpen.value = true
}

/** Modal 确定按钮统一入口：编辑态→updateShare，否则→createShare */
const handleModalOk = () => {
  if (editing.value) return updateShare()
  return createShare()
}

const updateShare = async () => {
  if (!form.name.trim()) {
    message.error('请填写分享名称')
    return
  }
  if (!editingId.value) return
  creating.value = true
  try {
    await updateVisitShareApi(editingId.value, {
      name: form.name.trim(),
      note: form.note || undefined,
      allow_search: form.allowSearch,
      allow_chat: form.allowChat,
      chat_model: form.allowChat ? form.chatModel || undefined : undefined,
      expires_at:
        form.expireMode === 'keep'
          ? undefined
          : form.expireMode === 'forever'
            ? null
            : form.expireDate
              ? dayjs(form.expireDate).endOf('day').format('YYYY-MM-DD HH:mm:ss')
              : undefined,
    })
    message.success('分享已更新')
    createOpen.value = false
    await loadShares()
  } finally {
    creating.value = false
  }
}

const createShare = async () => {
  if (!form.name.trim()) {
    message.error('请填写分享名称')
    return
  }
  creating.value = true
  try {
    if (form.expiresDays === -1 && !form.createExpire) {
      message.error('请先选择自定义到期时间')
      return
    }
    const share = await createVisitShareApi({
      name: form.name.trim(),
      note: form.note || undefined,
      allow_search: form.allowSearch,
      allow_chat: form.allowChat,
      chat_model: form.allowChat ? form.chatModel || undefined : undefined,
      expires_days: form.expiresDays === -1 ? null : form.expiresDays,
      expires_at:
        form.expiresDays === -1 && form.createExpire
          ? dayjs(form.createExpire).format('YYYY-MM-DD HH:mm:ss')
          : null,
    })
    message.success('谱系分享已生成，链接已复制')
    createOpen.value = false
    await loadShares()
    await copyShort(share)
  } finally {
    creating.value = false
  }
}

const shareLink = (record: VisitShare) =>
  record.share_code ? `${origin}/${record.share_code}` : ''

const copyUrl = async (url: string) => {
  try {
    await navigator.clipboard.writeText(url)
    message.success('已复制：' + url)
  } catch {
    window.prompt('请手动复制以下链接：', url)
  }
}

const copyShort = async (record: VisitShare) => {
  const url = shareLink(record)
  if (!url) {
    message.warning('该分享暂缺链接，请刷新列表后重试')
    return
  }
  await copyUrl(url)
}

const preview = (record: VisitShare) => {
  const url = shareLink(record)
  if (url) window.open(url, '_blank')
}

const revoke = async (record: VisitShare) => {
  await revokeVisitShareApi(record.id)
  message.success('已撤销该分享')
  await loadShares()
}

const removeRecord = async (record: VisitShare) => {
  await deleteVisitShareRecordApi(record.id)
  message.success('记录已删除')
  await loadShares()
}

onMounted(() => {
  loadShares()
  loadModels()
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
.s-model {
  margin-top: 2px;
  font-size: 12px;
  color: #b07c00;
}
.s-model-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #999;
}
</style>
