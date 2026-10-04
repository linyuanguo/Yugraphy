<template>
  <div>
    <a-card>
      <template #title>
        <a-space>
          <span>📚 谱系（宗谱）管理</span>
          <span
            style="color: #999"
            title="一个谱系对应一套宗谱/族谱，如「温岭林家」；其下可再分房支。点「档案卷号」进入详情，可管理房支/人物/谱书内容"
            >（宗谱 → 房支 → 人物）</span
          >
        </a-space>
      </template>
      <template #extra>
        <a-space>
          <a-input-search v-model:value="keyword" size="small" placeholder="搜名称 / 档案卷号" allow-clear style="width: 200px" />
          <a-button type="primary" size="small" v-if="auth.canEdit" @click="openCreateLineage">+ 新建谱系</a-button>
        </a-space>
      </template>

      <a-list :data-source="filteredLineages" :loading="loading" :pagination="false" item-layout="horizontal">
        <template #renderItem="{ item }">
          <a-list-item class="lineage-item" @click="goDetail(item)">
            <a-list-item-meta>
              <template #title>
                <a-space>
                  <span class="name" :style="item.code ? 'cursor:pointer' : ''">{{ item.name }}</span>
                  <!-- 档案卷号即入口：点击进入详情子页面 -->
                  <a v-if="item.code" class="code-link" :title="`点击进入 ${item.name} 详情（房支/人物/谱书内容）`">
                    <a-tag color="orange">{{ item.code }} ›</a-tag>
                  </a>
                  <a-tag color="blue">{{ item.person_count }} 人</a-tag>
                  <a-tag
                    v-if="delPending(item)"
                    color="orange"
                    :title="delPendingTip(item)"
                    >⏳ 删除申请审批中</a-tag
                  >
                  <a-tag
                    v-if="delRejected(item)"
                    color="gold"
                    :title="'删除申请已被驳回：' + (delRejected(item)?.review_note || '管理员未填写原因')"
                    >删除申请被驳回</a-tag
                  >
                </a-space>
              </template>
              <template #description>
                <div>{{ item.note || '暂无备注' }}</div>
                <div style="font-size: 12px; color: #bbb">
                  房支：{{ item.branches.length ? item.branches.map((b: Branch) => b.name).join('、') : '无' }}
                </div>
              </template>
            </a-list-item-meta>
            <template #actions>
              <a-space v-if="auth.canEdit" @click.stop>
                <a @click="openEditLineage(item)">编辑</a>
                <template v-if="delPending(item)">
                  <a-tag color="orange" :title="delPendingTip(item)">⏳ 审批中</a-tag>
                </template>
                <template v-else>
                  <a-popconfirm
                    :title="
                      (item.person_count || 0) > 0
                        ? `该谱系下还有 ${item.person_count} 位人物（AI 导入数据）。确认将连同这些人物及其全部关联关系一并删除？不可恢复！`
                        : '删除谱系及其全部房支？'
                    "
                    :ok-text="(item.person_count || 0) > 0 ? '强制删除' : '删除'"
                    :ok-button-props="{ danger: true }"
                    @confirm="removeLineage(item)"
                  >
                    <a style="color: #ff4d4f">删除</a>
                  </a-popconfirm>
                </template>
              </a-space>
            </template>
          </a-list-item>
        </template>
      </a-list>
      <a-empty v-if="!loading && lineages.length === 0" description="还没有谱系，点击右上角新建" />
      <a-empty v-else-if="!loading && lineages.length > 0 && filteredLineages.length === 0" description="没有匹配的谱系" />
    </a-card>

    <!-- 新建/编辑谱系 -->
    <a-modal
      v-model:open="lineageModal"
      :title="editingLineage ? '编辑谱系' : '新建谱系'"
      @ok="saveLineage"
      :confirm-loading="lineageSaving"
    >
      <a-form layout="vertical">
        <a-form-item label="谱系名称" required>
          <a-input v-model:value="lineageForm.name" placeholder="如：温岭林家、陈氏宗谱" />
        </a-form-item>
        <a-form-item label="档案编号">
          <a-input v-model:value="lineageForm.code" placeholder="如：J148-001-001（选填）" />
          <div style="font-size: 12px; color: #999">
            上传扫描件文件名（前 3 段）与编号相同时自动归入本谱系；自动创建的谱系默认以编号为名，可在此改名完善
          </div>
        </a-form-item>
        <a-form-item label="备注">
          <a-textarea v-model:value="lineageForm.note" :rows="2" placeholder="谱系说明（可选）" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  createLineageApi,
  deleteLineageApi,
  listDeleteRequestsApi,
  listLineagesApi,
  updateLineageApi,
} from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { Branch, DeleteRequestItem, Lineage } from '@/types'

const auth = useAuthStore()
const router = useRouter()

const lineages = ref<Lineage[]>([])
const loading = ref(false)
const keyword = ref('')
const filteredLineages = computed(() => {
  const k = keyword.value.trim()
  if (!k) return lineages.value
  return lineages.value.filter((l) => (l.name || '').includes(k) || (l.code || '').includes(k))
})

// ---------- 删除申请状态（操作员删除谱系须管理员审批） ----------
const delReqs = ref<DeleteRequestItem[]>([])
const delPending = (l: Lineage) =>
  delReqs.value.find(
    (r) => r.target_type === 'lineage' && r.target_id === l.lineage_id && r.status === 'pending',
  )
const delRejected = (l: Lineage) =>
  auth.isAdmin || delPending(l)
    ? undefined
    : delReqs.value.find(
        (r) => r.target_type === 'lineage' && r.target_id === l.lineage_id && r.status === 'rejected',
      )
const delPendingTip = (l: Lineage) => {
  const r = delPending(l)
  if (!r) return '删除申请待管理员审批'
  const who = r.submitted_by_name ? `（申请人：${r.submitted_by_name}）` : ''
  return `已有删除申请待管理员审批${who}，通过前该谱系不可再删除`
}
const refreshDelReqs = async () => {
  try {
    delReqs.value = await listDeleteRequestsApi({ target_type: 'lineage' })
  } catch {
    /* 不阻塞主列表 */
  }
}

const lineageModal = ref(false)
const lineageSaving = ref(false)
const editingLineage = ref<Lineage | null>(null)
const lineageForm = reactive({ name: '', note: '', code: '' })

/** 点档案卷号进入谱系详情子页面：房支管理 / 人物管理 / 谱书内容 */
const goDetail = (l: Lineage) => {
  router.push({ path: `/lineages/${l.lineage_id}` })
}

const load = async () => {
  loading.value = true
  try {
    lineages.value = await listLineagesApi()
    await refreshDelReqs()
  } finally {
    loading.value = false
  }
}

const openCreateLineage = () => {
  editingLineage.value = null
  lineageForm.name = ''
  lineageForm.note = ''
  lineageForm.code = ''
  lineageModal.value = true
}

const openEditLineage = (l: Lineage) => {
  editingLineage.value = l
  lineageForm.name = l.name
  lineageForm.note = l.note || ''
  lineageForm.code = l.code || ''
  lineageModal.value = true
}

const saveLineage = async () => {
  if (!lineageForm.name.trim()) {
    message.warning('请填写谱系名称')
    return
  }
  lineageSaving.value = true
  try {
    if (editingLineage.value) {
      await updateLineageApi(editingLineage.value.lineage_id, {
        name: lineageForm.name,
        note: lineageForm.note,
        code: lineageForm.code,
      })
      message.success('谱系已更新')
    } else {
      await createLineageApi({
        name: lineageForm.name,
        note: lineageForm.note,
        code: lineageForm.code,
      })
      message.success('谱系已创建')
    }
    lineageModal.value = false
    await load()
  } finally {
    lineageSaving.value = false
  }
}

const removeLineage = async (l: Lineage) => {
  const force = (l.person_count || 0) > 0
  try {
    const res = await deleteLineageApi(l.lineage_id, force)
    if (res?.requested) {
      message.success('删除申请已提交，待管理员审批通过后才会真正删除')
      await refreshDelReqs()
      return
    }
    message.success(force ? `谱系已删除（连带 ${l.person_count} 位人物）` : '谱系已删除')
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.lineage-item {
  cursor: pointer;
  padding: 10px 12px;
  border-radius: 6px;
  transition: background 0.2s;
}
.lineage-item:hover {
  background: #f5f5f5;
}
.code-link {
  cursor: pointer;
}
</style>
