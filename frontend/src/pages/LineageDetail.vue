<template>
  <div class="ld-wrap">
    <a-page-header
      style="padding: 0 0 12px"
      :title="lineage?.name || '谱系详情'"
      @back="router.push('/lineages')"
    >
      <template #sub-title>
        <a-space wrap>
          <a-tag v-if="lineage?.code" color="orange">{{ lineage.code }}</a-tag>
          <a-tag color="blue">{{ lineage?.person_count || 0 }} 人</a-tag>
          <span v-if="lineage?.note" style="color: #888">{{ lineage.note }}</span>
        </a-space>
      </template>
      <template #tags>
        <a-tag v-if="auth.canEdit" color="processing" @click="openEditLineage" style="cursor: pointer">⚙ 编辑谱系信息</a-tag>
      </template>
    </a-page-header>

    <a-tabs v-model:active-key="tab" type="card" class="ld-tabs">
      <!-- ===== 房支管理 ===== -->
      <a-tab-pane key="branches" :tab="`🏠 房支管理（${lineage?.branches.length || 0}）`">
        <div class="pane-head">
          <span class="muted">房支：一个大支系下的分支，人物可归入房支</span>
          <a-button size="small" type="primary" ghost v-if="auth.canEdit" @click="openCreateBranch">+ 新建房支</a-button>
        </div>
        <a-list :data-source="lineage?.branches || []" size="small" :pagination="false">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-space>
                <span style="font-weight: 500">{{ item.name }}</span>
                <span class="muted" style="font-size: 12px">{{ item.note || '' }}</span>
                <a-tag color="green" v-if="item.person_count">{{ item.person_count }} 人</a-tag>
              </a-space>
              <template #actions>
                <a-space v-if="auth.canEdit">
                  <a @click="openEditBranch(item)">编辑</a>
                  <a-popconfirm title="删除房支后，该房支下的人物将失去房支归属（人物不会被删除）。确认？" @confirm="removeBranch(item)">
                    <a style="color: #ff4d4f">删除</a>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-list-item>
          </template>
          <template #empty>
            <a-empty description="暂无房支" :image="simpleEmpty" />
          </template>
        </a-list>
      </a-tab-pane>

      <!-- ===== 人物管理（内嵌可编辑，不跳转 /persons） ===== -->
      <a-tab-pane key="persons" :tab="`👤 人物管理（${lineage?.person_count || 0}）`">
        <div class="pane-head">
          <span class="muted">本谱系共 {{ lineage?.person_count || 0 }} 位人物；筛选/编辑/删除与家谱树同源</span>
          <a-space>
            <a-select
              v-model:value="personBranchFilter"
              size="small"
              style="width: 150px"
              allow-clear
              placeholder="按房支筛选"
            >
              <a-select-option v-for="b in lineage?.branches || []" :key="b.branch_id" :value="b.branch_id">
                {{ b.name }}
              </a-select-option>
            </a-select>
            <a-input-search v-model:value="personSearch" size="small" placeholder="搜姓名" allow-clear style="width: 160px" />
            <a-button size="small" v-if="auth.canEdit" @click="openCreatePerson">+ 新增人物</a-button>
            <a-button
              v-if="auth.canEdit"
              size="small"
              type="primary"
              ghost
              :disabled="!dupClusters.length"
              :danger="!!dupClusters.length"
              @click="openMergeModal"
            >
              疑似同名合并（{{ dupClusters.length }}）
            </a-button>
            <a-button
              v-if="auth.canEdit"
              size="small"
              :disabled="!dupClusters.length"
              :loading="aiReviewing"
              title="把候选组送 AI 裁定是否同一人（仅出建议，合并仍由你确认）"
              @click="runAiReview"
            >
              🤖 AI 智能判断
            </a-button>
          </a-space>
        </div>
        <div v-if="dupClusters.length" class="dup-band">
          <span>
            按「去空格/大小写」归并后检出 {{ dupClusters.length }} 组疑似同名的重复人物
            （多因同谱系跨卷写入写法略异）。点「🤖 AI 智能判断」可让 AI 给出每组是否同一人的建议，
            再人工确认后合并为一，避免家谱树出现重复节点。
          </span>
        </div>
        <a-table
          :data-source="filteredPersons"
          :columns="personCols"
          row-key="person_id"
          size="small"
          :pagination="{ pageSize: 20, showSizeChanger: true }"
          :loading="personsLoading"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <a @click="openEditPerson(record)">{{ record.name }}</a>
            </template>
            <template v-else-if="column.key === 'branch'">
              <a-tag v-if="record.branch_name" color="green">{{ record.branch_name }}</a-tag>
              <span v-else class="muted">未归房</span>
            </template>
            <template v-else-if="column.key === 'birth_death'">
              <span v-if="record.birth_year || record.death_year" style="font-size: 12px">
                {{ record.birth_year ? record.birth_year : '?' }}
                ~ {{ record.death_year ? record.death_year : '?' }}
              </span>
              <span v-else class="muted">不详</span>
            </template>
            <template v-else-if="column.key === 'gen'">
              <span>{{ record.generation != null ? `第${record.generation}代` : '-' }}</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-space v-if="auth.canEdit" @click.stop>
                <a @click="openEditPerson(record)">编辑</a>
                <a-popconfirm title="删除该人物？会同时移除其谱系/房支归属与关联关系，不可恢复！" @confirm="removePerson(record)">
                  <a style="color: #ff4d4f">删除</a>
                </a-popconfirm>
              </a-space>
            </template>
          </template>
        </a-table>
      </a-tab-pane>

      <!-- ===== 谱书内容（AI 识别聚合 + 人工补录） ===== -->
      <a-tab-pane key="content" :tab="`📄 谱书内容（${entries.length}）`">
        <div class="pane-head">
          <a-space wrap>
            <a-select v-model:value="entryTypeFilter" size="small" style="width: 118px" allow-clear placeholder="全部类型">
              <a-select-option v-for="t in ENTRY_TYPES" :key="t" :value="t">{{ t }}</a-select-option>
            </a-select>
            <a-select v-model:value="entryStatusFilter" size="small" style="width: 104px" allow-clear placeholder="全部状态">
              <a-select-option value="active">已收录</a-select-option>
              <a-select-option value="archived">已归档</a-select-option>
            </a-select>
            <span class="muted">{{ filteredEntries.length }} 条</span>
          </a-space>
          <a-space>
            <a-button size="small" v-if="auth.canEdit" @click="openCreateEntry">+ 补录</a-button>
            <a-button size="small" @click="loadEntries">刷新</a-button>
          </a-space>
        </div>
        <div v-if="entriesLoading" class="pane-center"><a-spin /></div>
        <div v-else-if="!filteredEntries.length" class="pane-empty">
          <a-empty :image="simpleEmpty" description="暂无谱书内容" />
          <div class="hint">
            本谱扫描件 AI 识别完成后，在「扫描件导入 → 审核」点
            <b>写入图谱</b>，识别出的源流、迁徙、家规家训、传记、艺文等内容将按页码自动聚合到这里，可对照原图逐条校对。
          </div>
        </div>
        <a-list v-else :data-source="filteredEntries" size="small" :pagination="false" class="entry-list">
          <template #renderItem="{ item }">
            <a-list-item class="entry-list-item">
              <a-list-item-meta>
                <template #title>
                  <a-space wrap>
                    <a-tag :color="entryColor(item.type)" size="small">{{ item.type }}</a-tag>
                    <span style="font-weight: 600">{{ item.title || '（无标题）' }}</span>
                    <a-tag v-if="item.status === 'archived'" size="small" color="default">已归档</a-tag>
                  </a-space>
                </template>
                <template #description>
                  <div class="entry-text">{{ item.text }}</div>
                  <div class="entry-meta">
                    {{ item.task_name ? `来源：${item.task_name}` : item.source === 'manual' ? '人工补录' : '来源任务' }}
                    <template v-if="item.page_no"> · 原书第 {{ item.page_no }} 页</template>
                  </div>
                </template>
              </a-list-item-meta>
              <template #actions v-if="auth.canEdit">
                <a-space @click.stop>
                  <a @click="openEditEntry(item)">编辑</a>
                  <a v-if="item.status === 'active'" @click="archiveEntry(item, true)">归档</a>
                  <a v-else @click="archiveEntry(item, false)">恢复</a>
                  <a-popconfirm title="删除该条谱书内容？删除后不可恢复" @confirm="removeEntry(item)">
                    <a style="color: #ff4d4f">删除</a>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-list-item>
          </template>
        </a-list>
      </a-tab-pane>
    </a-tabs>

    <!-- 编辑谱系 -->
    <a-modal v-model:open="lineageModal" title="编辑谱系" @ok="saveLineage" :confirm-loading="lineageSaving">
      <a-form layout="vertical">
        <a-form-item label="谱系名称" required>
          <a-input v-model:value="lineageForm.name" />
        </a-form-item>
        <a-form-item label="档案编号">
          <a-input v-model:value="lineageForm.code" />
        </a-form-item>
        <a-form-item label="备注">
          <a-textarea v-model:value="lineageForm.note" :rows="2" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 新建/编辑房支 -->
    <a-modal v-model:open="branchModal" :title="editingBranch ? '编辑房支' : '新建房支'" @ok="saveBranch" :confirm-loading="branchSaving">
      <a-form layout="vertical">
        <a-form-item label="房支名称" required>
          <a-input v-model:value="branchForm.name" placeholder="如：长房、二房、三房" />
        </a-form-item>
        <a-form-item label="备注">
          <a-input v-model:value="branchForm.note" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 新建/编辑人物（内嵌，不跳 /persons） -->
    <a-modal v-model:open="personModal" :title="editingPerson ? '编辑人物' : '新增人物'" @ok="savePerson" :confirm-loading="personSaving" width="520px">
      <a-form layout="vertical">
        <a-form-item label="姓名" required>
          <a-input v-model:value="personForm.name" placeholder="人物姓名" />
        </a-form-item>
        <a-form-item label="性别">
          <a-select v-model:value="personForm.gender" style="width: 180px">
            <a-select-option value="male">男</a-select-option>
            <a-select-option value="female">女</a-select-option>
            <a-select-option value="unknown">未知</a-select-option>
          </a-select>
        </a-form-item>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="出生年">
              <a-input-number v-model:value="personForm.birth_year" style="width: 100%" :precision="0" placeholder="如 1850" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="卒年">
              <a-input-number v-model:value="personForm.death_year" style="width: 100%" :precision="0" placeholder="如 1920" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="房支归属">
          <a-select v-model:value="personForm.branch_id" style="width: 100%" allow-clear placeholder="未归房">
            <a-select-option v-for="b in lineage?.branches || []" :key="b.branch_id" :value="b.branch_id">{{ b.name }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="简介/备注">
          <a-textarea v-model:value="personForm.biography" :rows="3" placeholder="人物简介或备注（可选）" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 新建/校对谱书内容 -->
    <a-modal v-model:open="entryModal" :title="editingEntry ? '校对谱书内容' : '人工补录谱书内容'" @ok="saveEntry" :confirm-loading="entrySaving" ok-text="保存">
      <a-form layout="vertical">
        <a-form-item label="内容类型" required>
          <a-select v-model:value="entryForm.type">
            <a-select-option v-for="t in ENTRY_TYPES" :key="t" :value="t">{{ t }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="标题（可空）">
          <a-input v-model:value="entryForm.title" placeholder="如：字辈排行诗、卷一源流考" />
        </a-form-item>
        <a-form-item label="正文" required>
          <a-textarea v-model:value="entryForm.text" :rows="6" placeholder="谱书原文/要点摘录" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 疑似同名合并 -->
    <a-modal
      v-model:open="mergeModal"
      title="疑似同名合并（跨卷去重）"
      :width="760"
      :footer="null"
      destroy-on-close
    >
      <div v-if="!dupClusters.length" class="pane-empty">
        <a-empty description="暂未检出疑似同名的重复人物" :image="simpleEmpty" />
      </div>
      <div v-else class="merge-groups">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 8px; flex-wrap: wrap">
          <a-alert
            type="warning"
            show-icon
            style="flex: 1; min-width: 320px"
            message="按归一化姓名自动分组，供你人工确认；点「🤖 AI 智能判断」可让 AI 建议是否同一人（仅建议，合并仍由你点按钮确认）。"
          />
          <a-button size="small" :loading="aiReviewing" :disabled="!dupClusters.length" @click="runAiReview">
            🤖 AI 智能判断
          </a-button>
        </div>
        <div v-for="(g, gi) in dupClusters" :key="gi" class="merge-group">
          <div class="merge-group-head">
            <span class="merge-key">「{{ g.key }}」</span>
            <a-radio-group
              v-model:value="g.keeper"
              size="small"
              :options="g.members.map((m) => ({ label: m.label, value: m.person_id }))"
            />
          </div>
          <div v-if="aiVerdicts[g.key]" class="ai-verdict" :class="aiVerdicts[g.key].same ? 'ok' : 'no'">
            <template v-if="aiVerdicts[g.key].same">
              <span class="ai-mark">✅ AI 裁定</span>
              疑似同一人，建议合并（置信 {{ Math.round(aiVerdicts[g.key].confidence * 100) }}%）
              <span v-if="aiVerdicts[g.key].reason" class="ai-reason">· {{ aiVerdicts[g.key].reason }}</span>
            </template>
            <template v-else>
              <span class="ai-mark">⚠️ AI 裁定</span>
              判断为不同人，不建议合并
              <span v-if="aiVerdicts[g.key].reason" class="ai-reason">· {{ aiVerdicts[g.key].reason }}</span>
            </template>
          </div>
          <div v-else-if="aiRan" class="ai-verdict pending">
            <span class="ai-mark">⏳ 待人工判断</span>
            AI 本次未给出裁定，请结合生卒/房支人工确认
          </div>
          <div class="merge-group-actions">
            <span class="muted">保留上面的那一位，其余并入：</span>
            <a-button
              size="small"
              type="primary"
              :loading="mergingKeys.has(g.key)"
              @click="doMerge(g)"
            >
              合并为「{{ g.members.find((m) => m.person_id === g.keeper)?.name || '' }}」
            </a-button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { Empty } from 'ant-design-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  aiDupReviewApi,
  createBranchApi,
  createContentEntryApi,
  createPersonApi,
  deleteBranchApi,
  deleteContentEntryApi,
  deletePersonApi,
  listContentEntriesApi,
  listLineagesApi,
  listPersonsApi,
  mergePersonsApi,
  updateBranchApi,
  updateContentEntryApi,
  updateLineageApi,
  updatePersonApi,
} from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { AiDupVerdict, Branch, ContentEntry, Lineage, Person } from '@/types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const simpleEmpty = Empty.PRESENTED_IMAGE_SIMPLE

const lineageId = computed(() => String(route.params.lineageId || ''))
const lineage = ref<Lineage | null>(null)
const loading = ref(false)

const tab = ref<'branches' | 'persons' | 'content'>('branches')

// ============ 谱系 ============
const lineageModal = ref(false)
const lineageSaving = ref(false)
const lineageForm = reactive({ name: '', note: '', code: '' })
const openEditLineage = () => {
  lineageForm.name = lineage.value?.name || ''
  lineageForm.note = lineage.value?.note || ''
  lineageForm.code = lineage.value?.code || ''
  lineageModal.value = true
}
const saveLineage = async () => {
  if (!lineageForm.name.trim()) {
    message.warning('请填写谱系名称')
    return
  }
  lineageSaving.value = true
  try {
    await updateLineageApi(lineage.value!.lineage_id, {
      name: lineageForm.name,
      note: lineageForm.note,
      code: lineageForm.code,
    })
    message.success('谱系已更新')
    lineageModal.value = false
    await load()
  } finally {
    lineageSaving.value = false
  }
}

// ============ 房支 ============
const branchModal = ref(false)
const branchSaving = ref(false)
const editingBranch = ref<Branch | null>(null)
const branchForm = reactive({ name: '', note: '' })
const openCreateBranch = () => {
  editingBranch.value = null
  branchForm.name = ''
  branchForm.note = ''
  branchModal.value = true
}
const openEditBranch = (b: Branch) => {
  editingBranch.value = b
  branchForm.name = b.name
  branchForm.note = b.note || ''
  branchModal.value = true
}
const saveBranch = async () => {
  if (!branchForm.name.trim()) {
    message.warning('请填写房支名称')
    return
  }
  branchSaving.value = true
  try {
    if (editingBranch.value) {
      await updateBranchApi(editingBranch.value.branch_id, { name: branchForm.name, note: branchForm.note })
      message.success('房支已更新')
    } else {
      await createBranchApi(lineage.value!.lineage_id, { name: branchForm.name, note: branchForm.note })
      message.success('房支已创建')
    }
    branchModal.value = false
    await load()
  } finally {
    branchSaving.value = false
  }
}
const removeBranch = async (b: Branch) => {
  try {
    await deleteBranchApi(b.branch_id)
    message.success('房支已删除')
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

// ============ 人物（内嵌可编辑） ============
const persons = ref<Person[]>([])
const personsLoading = ref(false)
const personSearch = ref('')
const personBranchFilter = ref<string | undefined>()
const filteredPersons = computed(() =>
  persons.value.filter(
    (p) =>
      (!personBranchFilter.value || p.branch_id === personBranchFilter.value) &&
      (!personSearch.value || p.name.includes(personSearch.value.trim())),
  ),
)
const personCols = [
  { title: '姓名', key: 'name' },
  { title: '性别', key: 'gender' },
  { title: '房支', key: 'branch' },
  { title: '生卒年', key: 'birth_death' },
  { title: '世代', key: 'gen' },
  { title: '操作', key: 'action', width: 130 },
]
const personModal = ref(false)
const personSaving = ref(false)
const editingPerson = ref<Person | null>(null)
const personForm = reactive({
  name: '',
  gender: 'unknown' as 'male' | 'female' | 'unknown',
  birth_year: undefined as number | undefined,
  death_year: undefined as number | undefined,
  branch_id: undefined as string | undefined,
  biography: '',
})
const loadPersons = async () => {
  if (!lineage.value) {
    persons.value = []
    return
  }
  personsLoading.value = true
  // 人物集变化（切谱系/合并/新增后）：旧 AI 裁定不再可靠，清空待下次重判
  aiVerdicts.value = {}
  aiRan.value = false
  try {
    const res = await listPersonsApi({ lineage_id: lineage.value.lineage_id, limit: 5000 })
    persons.value = res.items || []
  } finally {
    personsLoading.value = false
  }
}
const openCreatePerson = () => {
  editingPerson.value = null
  personForm.name = ''
  personForm.gender = 'unknown'
  personForm.birth_year = undefined
  personForm.death_year = undefined
  personForm.branch_id = undefined
  personForm.biography = ''
  personModal.value = true
}
const openEditPerson = (p: Person) => {
  editingPerson.value = p
  personForm.name = p.name
  personForm.gender = p.gender
  personForm.birth_year = p.birth_year ?? undefined
  personForm.death_year = p.death_year ?? undefined
  personForm.branch_id = p.branch_id || undefined
  personForm.biography = p.biography || ''
  personModal.value = true
}
const savePerson = async () => {
  if (!personForm.name.trim()) {
    message.warning('请填写姓名')
    return
  }
  personSaving.value = true
  try {
    const base = {
      name: personForm.name.trim(),
      gender: personForm.gender,
      birth_year: personForm.birth_year ?? null,
      death_year: personForm.death_year ?? null,
      branch_id: personForm.branch_id ?? null,
      biography: personForm.biography || null,
    }
    if (editingPerson.value) {
      await updatePersonApi(editingPerson.value.person_id, base)
      message.success('人物已更新')
    } else {
      await createPersonApi({ ...base, lineage_id: lineage.value!.lineage_id })
      message.success('人物已新增')
    }
    personModal.value = false
    await loadPersons()
    await load()
  } finally {
    personSaving.value = false
  }
}
const removePerson = async (p: Person) => {
  try {
    await deletePersonApi(p.person_id)
    message.success('人物已删除')
    await loadPersons()
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

// ============ 疑似同名合并（跨卷去重） ============
// 归一化姓名须与后端 _match_or_create_person 一致：去空格/全角空格/制表符 + 忽略大小写
const normName = (name: string) =>
  String(name || '')
    .trim()
    .toLowerCase()
    .replace(/[ \u3000\t]/g, '')
const mergeModal = ref(false)
/** 正在执行合并的疑似组 key 集合：仅用于对应「合并」按钮的 loading，类型安全 */
const mergingKeys = ref(new Set<string>())
type DupCluster = { key: string; keeper: string; members: { person_id: string; name: string; label: string }[] }
const dupClusters = computed<DupCluster[]>(() => {
  const map = new Map<string, { person_id: string; name: string; label: string }[]>()
  for (const p of persons.value) {
    const key = normName(p.name)
    if (!key) continue
    if (!map.has(key)) map.set(key, [])
    const bd =
      p.birth_year || p.death_year ? `（${p.birth_year || '?'}~${p.death_year || '?'}）` : ''
    map.get(key)!.push({
      person_id: p.person_id,
      name: p.name,
      label: `${p.name}${bd ? bd : ''}${p.branch_name ? ' · ' + p.branch_name : ''}`,
    })
  }
  const clusters: DupCluster[] = []
  for (const [key, members] of map) {
    if (members.length < 2) continue
    clusters.push({ key, keeper: members[0].person_id, members })
  }
  return clusters
})
const openMergeModal = () => {
  mergeModal.value = true
}
const doMerge = async (g: DupCluster) => {
  const secondary = g.members.filter((m) => m.person_id !== g.keeper)
  if (!secondary.length) return
  const keeperName = g.members.find((m) => m.person_id === g.keeper)?.name || ''
  mergingKeys.value.add(g.key)
  try {
    const res = await mergePersonsApi(g.keeper, secondary.map((m) => m.person_id))
    message.success(
      `已将 ${res.merged} 位并入「${keeperName}」（共 ${res.secondary_count} 位重复）`,
    )
    await loadPersons()
    await load()
    // 合并后人物集变化：旧 AI 裁定不再可靠，清空待下次重判
    aiVerdicts.value = {}
    aiRan.value = false
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '合并失败')
  } finally {
    mergingKeys.value.delete(g.key)
  }
}

/** AI 裁定状态：逐组判断（key → 判定），仅建议不自动合并 */
const aiVerdicts = ref<Record<string, AiDupVerdict>>({})
const aiReviewing = ref(false)
const aiRan = ref(false)
const runAiReview = async () => {
  const lid = lineage.value?.lineage_id
  if (!lid) {
    message.info('谱系信息尚未加载，请稍后重试')
    return
  }
  aiReviewing.value = true
  try {
    const res = await aiDupReviewApi(lid)
    const map: Record<string, AiDupVerdict> = {}
    for (const g of res.groups || []) map[g.key] = g
    aiVerdicts.value = map
    aiRan.value = true
    // same 组预选 AI 建议保留者（该成员须在当前组内）
    for (const cl of dupClusters.value) {
      const v = map[cl.key]
      if (v?.same && v.keep_person_id && cl.members.some((m) => m.person_id === v.keep_person_id)) {
        cl.keeper = v.keep_person_id
      }
    }
    const judged = (res.groups || []).filter((g) => g.confidence > 0).length
    const sameN = (res.groups || []).filter((g) => g.same && g.confidence > 0).length
    if (res.error) {
      message.warning(`AI 分析未全部完成：${res.error}（未判定的组仍可人工合并）`)
    } else {
      message.success(`AI 裁定完成：共 ${res.total_groups} 组，判定 ${judged} 组（其中 ${sameN} 组建议合并）。仅为建议，请核对后再点合并`, 5)
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || 'AI 智能判断失败（模型服务暂不可用？）')
  } finally {
    aiReviewing.value = false
  }
}

// ============ 谱书内容 ============
const ENTRY_TYPES = ['源流', '迁徙', '家规家训', '凡例', '传记', '艺文', '祠祭', '字辈', '序跋', '坟茔', '其他']
const ENTRY_TYPE_COLORS: Record<string, string> = {
  源流: 'geekblue',
  迁徙: 'cyan',
  家规家训: 'volcano',
  凡例: 'purple',
  传记: 'gold',
  艺文: 'magenta',
  祠祭: 'orange',
  字辈: 'green',
  序跋: 'blue',
  坟茔: 'lime',
  其他: 'default',
}
const entryColor = (t?: string | null) => ENTRY_TYPE_COLORS[t || ''] || 'default'
const entries = ref<ContentEntry[]>([])
const entriesLoading = ref(false)
const entryTypeFilter = ref<string | undefined>()
const entryStatusFilter = ref<string | undefined>()
const filteredEntries = computed(() =>
  entries.value.filter(
    (e) =>
      (!entryTypeFilter.value || e.type === entryTypeFilter.value) &&
      (!entryStatusFilter.value || e.status === entryStatusFilter.value),
  ),
)
const entryModal = ref(false)
const entrySaving = ref(false)
const editingEntry = ref<ContentEntry | null>(null)
const entryForm = reactive({ type: '源流', title: '', text: '' })
const loadEntries = async () => {
  if (!lineage.value) {
    entries.value = []
    return
  }
  entriesLoading.value = true
  try {
    entries.value = await listContentEntriesApi({ lineage_id: lineage.value.lineage_id })
  } finally {
    entriesLoading.value = false
  }
}
const openCreateEntry = () => {
  editingEntry.value = null
  entryForm.type = '源流'
  entryForm.title = ''
  entryForm.text = ''
  entryModal.value = true
}
const openEditEntry = (e: ContentEntry) => {
  editingEntry.value = e
  entryForm.type = e.type
  entryForm.title = e.title || ''
  entryForm.text = e.text
  entryModal.value = true
}
const saveEntry = async () => {
  if (!entryForm.type) {
    message.warning('请选择内容类型')
    return
  }
  if (!entryForm.text.trim()) {
    message.warning('请填写正文')
    return
  }
  entrySaving.value = true
  try {
    if (editingEntry.value) {
      await updateContentEntryApi(editingEntry.value.entry_id, { type: entryForm.type, title: entryForm.title, text: entryForm.text })
      message.success('已保存校对结果')
    } else {
      await createContentEntryApi({ lineage_id: lineage.value!.lineage_id, type: entryForm.type, title: entryForm.title, text: entryForm.text })
      message.success('已补录')
    }
    entryModal.value = false
    await loadEntries()
  } finally {
    entrySaving.value = false
  }
}
const archiveEntry = async (e: ContentEntry, archived: boolean) => {
  try {
    await updateContentEntryApi(e.entry_id, { status: archived ? 'archived' : 'active' })
    message.success(archived ? '已归档（可从「全部状态」恢复）' : '已恢复')
    await loadEntries()
  } catch {
    /* 拦截器已提示 */
  }
}
const removeEntry = async (e: ContentEntry) => {
  try {
    await deleteContentEntryApi(e.entry_id)
    message.success('已删除')
    await loadEntries()
  } catch {
    /* 拦截器已提示 */
  }
}

// ============ 装载 ============
const load = async () => {
  if (!lineageId.value) return
  loading.value = true
  try {
    const all = await listLineagesApi()
    lineage.value = all.find((l: Lineage) => l.lineage_id === lineageId.value) || null
    if (!lineage.value) {
      message.warning('谱系不存在或已删除')
      router.replace('/lineages')
      return
    }
    if (tab.value === 'persons') await loadPersons()
    if (tab.value === 'content') await loadEntries()
  } finally {
    loading.value = false
  }
}
watch(tab, async (t) => {
  if (!lineage.value) return
  if (t === 'persons') await loadPersons()
  else if (t === 'content') await loadEntries()
})
watch(lineageId, async (id) => {
  if (id) await load()
})
onMounted(load)
</script>

<style scoped>
.ld-wrap {
  padding: 4px;
}
.ld-tabs :deep(.ant-tabs-nav) {
  margin-bottom: 12px;
}
.muted {
  color: #999;
}
.pane-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  gap: 8px;
  flex-wrap: wrap;
}
.pane-center {
  text-align: center;
  padding: 24px 0;
}
.pane-empty .hint {
  color: #999;
  font-size: 12px;
  margin-top: 4px;
  padding: 0 8px;
}
.entry-list {
  max-height: calc(100vh - 330px);
  overflow-y: auto;
}
.entry-list-item {
  padding: 8px 2px !important;
}
.entry-text {
  white-space: pre-wrap;
  line-height: 1.7;
  color: #333;
  font-size: 13px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  padding: 6px 8px;
  margin: 4px 0;
}
.entry-meta {
  color: #999;
  font-size: 12px;
}
.dup-band {
  background: #fffbe6;
  border: 1px solid #ffe58f;
  color: #ad6800;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
}
.merge-groups {
  max-height: 60vh;
  overflow-y: auto;
}
.merge-group {
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
  background: #fff;
}
.merge-group-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.merge-key {
  font-weight: 600;
  color: #d4380d;
  min-width: 90px;
}
.merge-group-actions {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.ai-verdict {
  margin-top: 8px;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  line-height: 1.6;
}
.ai-verdict.ok {
  background: #f6ffed;
  border: 1px solid #b7eb8f;
  color: #237804;
}
.ai-verdict.no {
  background: #fff7e6;
  border: 1px solid #ffd591;
  color: #ad4e00;
}
.ai-verdict.pending {
  background: #fafafa;
  border: 1px dashed #d9d9d9;
  color: #8c8c8c;
}
.ai-mark {
  font-weight: 600;
}
.ai-reason {
  color: inherit;
  opacity: 0.85;
}
</style>
