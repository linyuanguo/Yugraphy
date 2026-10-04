<template>
  <div>
    <a-row :gutter="12">
      <!-- 左侧：按谱系分类导航 -->
      <a-col :xs="24" :sm="24" :md="8" :lg="7" :xl="5">
        <a-card size="small" class="side-card">
          <template #title>📂 按谱系分类</template>
          <a-spin :spinning="catLoading">
            <div class="cat-tree">
              <div class="cat-row all" :class="{ active: !lineageId && !branchId }" @click="selectAll">
                <span class="cat-name">全部人物</span>
                <a-tag v-if="totalAll > 0" size="small" color="default">{{ totalAll }}</a-tag>
              </div>
              <template v-for="l in lineages" :key="l.lineage_id">
                <div
                  class="cat-row lineage"
                  :class="{ active: lineageId === l.lineage_id && !branchId }"
                  @click="selectLineage(l)"
                >
                  <span class="cat-name" :title="l.code || l.name">{{ l.name }}</span>
                  <a-tag v-if="l.person_count" size="small" color="blue">{{ l.person_count }}</a-tag>
                </div>
                <div
                  v-for="b in l.branches"
                  :key="b.branch_id"
                  class="cat-row branch"
                  :class="{ active: branchId === b.branch_id }"
                  @click="selectBranch(l, b)"
                >
                  <span class="cat-name branch-name">└ {{ b.name }}</span>
                  <a-tag v-if="b.person_count" size="small">{{ b.person_count }}</a-tag>
                </div>
              </template>
            </div>
            <a-empty
              v-if="!catLoading && !lineages.length"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              description="暂无谱系，可在谱系管理中新建"
              style="margin: 16px 0"
            />
          </a-spin>
        </a-card>
      </a-col>

      <!-- 右侧：人物列表 -->
      <a-col :xs="24" :sm="24" :md="16" :lg="17" :xl="19">
        <a-card>
          <template #title>
            <a-space>
              <span>👥 {{ currentTitle }}</span>
              <a-tag v-if="pagination.total > 0 || !loading">{{ pagination.total }} 人</a-tag>
            </a-space>
          </template>
          <a-alert
            type="info"
            show-icon
            class="list-tip"
            message="此处展示的是已「审核写入图谱」的人物。识别中 / 未审核写入的人物不会出现在这里；点击左侧谱系或房支即可分类查看，勾选（表头可全选当前页，翻页勾选会保留）后可批量删除。"
          />
          <div class="toolbar">
            <a-space wrap>
              <a-input-search
                v-model:value="search"
                placeholder="按姓名搜索"
                style="width: 220px"
                allow-clear
                @search="load(1, true)"
              />
              <a-select v-model:value="gender" style="width: 120px" allow-clear placeholder="性别" @change="load(1, true)">
                <a-select-option value="male">男</a-select-option>
                <a-select-option value="female">女</a-select-option>
              </a-select>
              <a-select v-model:value="generation" style="width: 120px" allow-clear placeholder="代数" @change="load(1, true)">
                <a-select-option v-for="g in 12" :key="g" :value="g">第 {{ g }} 代</a-select-option>
              </a-select>
            </a-space>
            <a-space>
              <a-button type="primary" @click="$router.push('/persons/new')" v-if="auth.canEdit">
                + 新增人物
              </a-button>
              <a-popconfirm
                v-if="auth.canEdit"
                title="删除选中的全部人物？将连同其全部关联关系一并删除，不可恢复！"
                ok-text="删除"
                cancel-text="取消"
                :ok-button-props="{ danger: true }"
                :disabled="!selectedRowKeys.length"
                @confirm="batchDelete"
              >
                <a-button danger :disabled="!selectedRowKeys.length">
                  删除选中（{{ selectedRowKeys.length }}）
                </a-button>
              </a-popconfirm>
              <a-popconfirm
                v-if="auth.canEdit && lineageId"
                :title="deleteScopeTitle"
                ok-text="全部删除"
                cancel-text="取消"
                :ok-button-props="{ danger: true }"
                :disabled="loading"
                @confirm="deleteScope"
              >
                <a-button danger>🗑 删除当前分类全部人物</a-button>
              </a-popconfirm>
            </a-space>
          </div>

          <a-table
            :data-source="items"
            :columns="columns"
            :loading="loading"
            :pagination="pagination"
            row-key="person_id"
            :row-selection="rowSelection"
            size="middle"
            @change="onTableChange"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'name'">
                <a-space>
                  <a-avatar :size="28" :src="record.photo_url" :style="{ background: genderColor(record.gender) }">
                    {{ record.name.slice(0, 1) }}
                  </a-avatar>
                  <a @click="openEdit(record.person_id)">{{ record.name }}</a>
                </a-space>
              </template>
              <template v-else-if="column.key === 'gender'">
                <a-tag :color="genderColor(record.gender)">{{ genderText(record.gender) }}</a-tag>
              </template>
              <template v-else-if="column.key === 'life'">
                {{ record.birth_year ?? '?' }} — {{ record.death_year ?? (record.is_alive ? '至今' : '?') }}
              </template>
              <template v-else-if="column.key === 'lineage'">
                <template v-if="record.branch_name">
                  <a-tag color="cyan">{{ record.branch_name }}</a-tag>
                </template>
                <span v-else class="muted">—</span>
              </template>
              <template v-else-if="column.key === 'parents'">
                <span v-if="record.parents && record.parents.length">{{ record.parents.length }} 位</span>
                <span v-else class="muted">—</span>
              </template>
              <template v-else-if="column.key === 'children'">
                <span v-if="record.children && record.children.length">{{ record.children.length }} 位</span>
                <span v-else class="muted">—</span>
              </template>
              <template v-else-if="column.key === 'action'">
                <a-space>
                  <a @click="openEdit(record.person_id)">编辑</a>
                  <a @click="viewInTree(record.person_id)">3D</a>
                  <a-popconfirm title="确认删除该人物？" @confirm="remove(record)">
                    <a v-if="auth.canEdit" style="color: #ff4d4f">删除</a>
                  </a-popconfirm>
                </a-space>
              </template>
            </template>
          </a-table>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Empty, message } from 'ant-design-vue'
import { batchDeletePersonsApi, deletePersonApi, listLineagesApi, listPersonsApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { Branch, Lineage, Person } from '@/types'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const items = ref<Person[]>([])
const loading = ref(false)
const search = ref('')
const gender = ref<string>()
const generation = ref<number>()
const lineageId = ref<string>()
const branchId = ref<string>()
const lineages = ref<Lineage[]>([])
const catLoading = ref(false)
/** 「全部人物」视图下的总数（用于左侧计数展示） */
const totalAll = ref(0)
const pagination = reactive({
  current: 1,
  pageSize: 20,
  total: 0,
  showSizeChanger: true,
  pageSizeOptions: ['10', '20', '50', '100', '200'],
  showTotal: (t: number) => `共 ${t} 人`,
})
const selectedRowKeys = ref<string[]>([])

/** 表格多选：翻页保留已勾选，可跨页累积后一次删除 */
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  preserveSelectedRowKeys: true,
  onChange: (keys: any[]) => {
    selectedRowKeys.value = keys
  },
}))

const columns = [
  { title: '姓名', key: 'name', dataIndex: 'name' },
  { title: '性别', key: 'gender', dataIndex: 'gender', width: 70 },
  { title: '生卒', key: 'life', width: 140 },
  { title: '出生地', key: 'birth_place', dataIndex: 'birth_place', ellipsis: true },
  { title: '代数', key: 'generation', dataIndex: 'generation', width: 60 },
  { title: '房支', key: 'lineage', width: 120 },
  { title: '父母', key: 'parents', width: 70 },
  { title: '子女', key: 'children', width: 70 },
  { title: '操作', key: 'action', width: 150 },
]

const genderText = (g: string) => (g === 'male' ? '男' : g === 'female' ? '女' : '未知')
const genderColor = (g: string) => (g === 'male' ? '#1677ff' : g === 'female' ? '#eb2f96' : '#999')

const currentTitle = computed(() => {
  const l = lineages.value.find((x) => x.lineage_id === lineageId.value)
  if (branchId.value && l) {
    const b = l.branches.find((x) => x.branch_id === branchId.value)
    return `${l.name} · ${b ? b.name : '未知房支'}`
  }
  if (l) return l.name
  return '全部人物'
})

const load = async (page = pagination.current, clearSelection = false) => {
  if (clearSelection) selectedRowKeys.value = []
  loading.value = true
  try {
    const data = await listPersonsApi({
      search: search.value || undefined,
      gender: gender.value,
      generation: generation.value,
      lineage_id: lineageId.value,
      branch_id: branchId.value,
      limit: pagination.pageSize,
      offset: (page - 1) * pagination.pageSize,
    })
    items.value = data.items
    pagination.total = data.total
    pagination.current = page
    if (!lineageId.value && !branchId.value) totalAll.value = data.total
  } finally {
    loading.value = false
  }
}

/** 分页变化：换页加载；修改每页条数时同步 pageSize 并回到第 1 页重新请求 */
const onTableChange = (pag: any) => {
  const sizeChanged = pag.pageSize && pag.pageSize !== pagination.pageSize
  if (sizeChanged) {
    pagination.pageSize = pag.pageSize
    load(1, true)
  } else {
    load(pag.current)
  }
}

// ============ 左侧分类切换 ============
const selectAll = () => {
  lineageId.value = undefined
  branchId.value = undefined
  load(1, true)
}
const selectLineage = (l: Lineage) => {
  lineageId.value = l.lineage_id
  branchId.value = undefined
  load(1, true)
}
const selectBranch = (l: Lineage, b: Branch) => {
  lineageId.value = l.lineage_id
  branchId.value = b.branch_id
  load(1, true)
}

const loadLineages = async () => {
  catLoading.value = true
  try {
    lineages.value = await listLineagesApi()
  } finally {
    catLoading.value = false
  }
}

const openEdit = (id: string) => router.push(`/persons/${id}`)
// 家谱树入口已下线：改为在 3D 谱系画布中定位该人物
const viewInTree = (id: string) => router.push({ path: '/tree3d', query: { person: id } })

const remove = async (record: Person) => {
  try {
    await deletePersonApi(record.person_id)
    message.success('已删除')
    selectedRowKeys.value = selectedRowKeys.value.filter((k) => k !== record.person_id)
    await load()
    loadLineages()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '删除失败，请稍后重试')
  }
}

const batchDelete = async () => {
  const ids = [...selectedRowKeys.value]
  if (!ids.length) {
    message.warning('请先勾选要删除的人物')
    return
  }
  try {
    const n = await batchDeletePersonsApi(ids)
    message.success(`已删除 ${n.deleted} 位人物`)
    selectedRowKeys.value = []
    await load()
    loadLineages()
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 401 || status === 403) {
      message.error('登录已失效或权限不足，请刷新页面重新登录后再试')
    } else if (status) {
      message.error(e?.response?.data?.detail || '删除失败，请稍后重试')
    } else {
      message.error('删除失败：无法连接服务器，请稍后重试')
    }
  }
}

/** 删除当前分类（谱系或房支）下的全部人物；谱系/房支本身保留 */
const deleteScopeTitle = computed(() => {
  const l = lineages.value.find((x) => x.lineage_id === lineageId.value)
  if (branchId.value) {
    const b = l?.branches.find((x) => x.branch_id === branchId.value)
    return `确认删除「${l?.name || ''}」房支「${b?.name || ''}」下的全部人物？谱系与房支保留，人物及其全部关联关系将一并删除且不可恢复！`
  }
  return `确认删除谱系「${l?.name || ''}」下的全部人物（含各房支人物）？谱系本身保留，人物及其全部关联关系将一并删除且不可恢复！`
})

const deleteScope = async () => {
  if (!lineageId.value) return
  try {
    const n = await batchDeletePersonsApi([], {
      lineage_id: lineageId.value,
      branch_id: branchId.value || undefined,
    })
    message.success(`已删除 ${n.deleted} 位人物`)
    selectedRowKeys.value = []
    await load()
    loadLineages()
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 401 || status === 403) {
      message.error('登录已失效或权限不足，请刷新页面重新登录后再试')
    } else if (status) {
      message.error(e?.response?.data?.detail || '删除失败，请稍后重试')
    } else {
      message.error('删除失败：无法连接服务器，请稍后重试')
    }
  }
}

onMounted(async () => {
  await loadLineages()
  // 从「谱系管理 → 人物管理」跳转过来：按 query 预选谱系
  const q = (route.query.lineage as string) || ''
  if (q && lineages.value.some((l) => l.lineage_id === q)) {
    lineageId.value = q
    branchId.value = undefined
  }
  load(1, true)
})
</script>

<style scoped>
.side-card {
  position: sticky;
  top: 0;
}
.cat-tree {
  max-height: calc(100vh - 190px);
  overflow-y: auto;
}
.cat-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.cat-row:hover {
  background: #f0f7ff;
}
.cat-row.active {
  background: #e6f4ff;
}
.cat-row.lineage {
  font-weight: 600;
  margin-top: 2px;
}
.cat-row.branch {
  padding-left: 22px;
  color: #555;
  font-weight: 400;
}
.cat-name {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  margin: 12px 0 16px;
  flex-wrap: wrap;
  gap: 8px;
}
.muted {
  color: #bbb;
}
</style>
