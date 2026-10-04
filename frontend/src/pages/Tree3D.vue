<template>
  <div class="tree3d-page">
    <!-- 头部：访客(分享短链) / 内部 两套标题 -->
    <a-card :bordered="false" class="t3d-head">
      <div class="t3d-head-row">
        <div class="t3d-title">
          <template v-if="guestMode">
            <span>🧊 {{ guestName || '谱系浏览' }}</span>
            <a-tag color="green">3D 谱系 · 访客</a-tag>
          </template>
          <template v-else>
            <template v-if="level === 'lineages'">🧊 3D 谱系 · 卷总览（一级）</template>
            <template v-else-if="level === 'persons'">🧊 {{ current?.name || '谱系' }} · 人物 3D（二级）</template>
            <template v-else>🧊 {{ current?.name || '' }} · 房支聚合 3D</template>
            <a-tag v-if="level !== 'lineages'" color="blue" class="t3d-lin-tag">{{ current?.code || '' }}</a-tag>
          </template>
          <a-tag v-if="guestMode && guestAllowSearch" color="blue">支持搜索</a-tag>
          <a-tag v-if="guestMode && guestAllowChat" color="purple">支持问答</a-tag>
        </div>
        <div class="t3d-actions">
          <a-select
            v-if="showSearch"
            v-model:value="searchText"
            show-search
            :filter-option="false"
            placeholder="🔍 搜索人物并定位"
            style="width: 240px"
            allow-clear
            :not-found-content="searchOptions.length ? undefined : '输入姓名搜索'"
            @search="onSearch"
            @change="onPick"
          >
            <a-select-option v-for="o in searchOptions" :key="o.value" :value="o.value">
              {{ o.label }}
            </a-select-option>
          </a-select>
          <a-button v-if="!guestMode && level === 'lineages'" @click="reloadLineages">刷新</a-button>
          <a-button v-if="level !== 'lineages'" @click="backToLineages">← 全部谱系</a-button>
        </div>
      </div>
      <div v-if="level === 'lineages'" class="t3d-desc">
        <template v-if="guestMode">
          节点=谱系卷，球体越大表示收录人物越多；点节点进入该卷人物 3D，再点人物节点查看信息。
          {{ guestAllowChat ? '可点「问答」向 AI 询问世系关系。' : '' }}
        </template>
        <template v-else>
          节点=谱系卷（档案编号），球体越大表示收录人物越多；点节点进入该卷人物 3D，再点人物节点查看简卡。
          若在审核中发现人物关系缺失，请到「扫描件导入」复核补录。
        </template>
      </div>
    </a-card>

    <a-card :bordered="false" class="t3d-main">
      <div v-if="loading" class="t3d-loading"><a-spin tip="加载中" /></div>
      <div v-else-if="guestError" class="t3d-empty">
        <a-result status="warning" title="分享链接无效或已过期" :sub-title="guestError" />
      </div>
      <div v-else-if="!graphNodes.length" class="t3d-empty">
        <a-empty v-if="level === 'lineages'" :description="guestMode ? '暂无可浏览的谱系' : '暂无谱系卷，请先到「扫描件导入」识别并写入图谱'" />
        <a-empty v-else description="该谱系下暂无人（可到「扫描件导入」写入后再看）" />
      </div>
      <template v-else>
        <div class="t3d-canvas" ref="canvasWrapRef">
          <Force3D
            v-if="!loading"
            ref="force3dRef"
            :nodes="graphNodes"
            :edges="graphEdges"
            @node-click="onNodeClick"
          />
        </div>
        <a-card v-if="sel" size="small" class="t3d-side">
          <template #title>
            <div class="t3d-side-title">
              <span>{{ sel.name }}</span>
              <a-button
                v-if="!guestMode"
                type="primary"
                size="small"
                @click="openPerson(sel)"
              >编辑资料</a-button>
            </div>
          </template>
          <div class="t3d-side-item"><label>性别</label><span>{{ genderText(sel) }}</span></div>
          <div v-if="sel.birth_year || sel.death_year" class="t3d-side-item">
            <label>生卒</label><span>{{ yearsText(sel) }}</span>
          </div>
          <div v-if="sel.birth_place" class="t3d-side-item"><label>出生地</label><span>{{ sel.birth_place }}</span></div>
          <div class="t3d-side-item"><label>谱系</label><span>{{ guestMode ? (current?.name || '—') : (sel.lineage_name || sel.lineage_id || '—') }}</span></div>
          <div class="t3d-side-item"><label>房支</label><span>{{ guestMode ? (sel.branch_name || '—') : (sel.branch_name || '—') }}</span></div>
          <div v-if="sel.generation" class="t3d-side-item"><label>世代</label><span>{{ sel.generation }} 世</span></div>
          <div v-if="sel.biography" class="t3d-side-item t3d-bio"><label>生平</label><span>{{ sel.biography }}</span></div>
          <div class="t3d-side-ops">
            <a-button size="small" @click="force3dRef?.focusNode(sel.person_id)">🎯 视角聚焦</a-button>
            <a-button v-if="guestMode" size="small" style="margin-left: 8px" @click="openGuestDetail(sel)">查看关系</a-button>
          </div>
        </a-card>
      </template>
    </a-card>

    <!-- 访客：AI 问答（allow_chat） -->
    <a-modal
      v-model:open="chatOpen"
      title="🤖 谱系问答（AI）"
      :footer="null"
      width="560px"
    >
      <div v-if="chatLog.length" class="chat-log">
        <div v-for="(c, i) in chatLog" :key="i" :class="['chat-line', c.role]">
          <span class="chat-who">{{ c.role === 'me' ? '我' : 'AI' }}</span>
          <div class="chat-text">{{ c.text }}</div>
        </div>
        <div v-if="chatLoading" class="chat-line ai"><span class="chat-who">AI</span><div class="chat-text">思考中…</div></div>
      </div>
      <div v-else class="chat-hint">可以问：某人的父母/配偶/子女、世系关系等。</div>
      <div class="chat-input">
        <a-input
          v-model:value="chatQuestion"
          placeholder="输入你的问题…"
          :disabled="chatLoading"
          @press-enter="askChat"
        />
        <a-button type="primary" :loading="chatLoading" @click="askChat">提问</a-button>
      </div>
    </a-modal>

    <!-- 访客：人物关系详情（visitPersonApi 公开） -->
    <a-modal v-model:open="detailOpen" :title="`${sel?.name || ''} · 直系关系`" :footer="null" width="560px">
      <a-spin :spinning="detailLoading">
        <div v-if="detailRel" class="rel-block">
          <template v-for="(g, gi) in relGroups" :key="gi">
            <template v-if="detailRel[g.key]?.length">
              <div class="rel-title">{{ g.label }}</div>
              <div v-for="(p, pi) in detailRel[g.key]" :key="pi" class="rel-row">
                <b>{{ p.name }}</b>
                <span v-if="p.birth_year || p.death_year" class="rel-years">（{{ p.birth_year ?? '?' }}—{{ p.death_year ?? '?' }}）</span>
                <span v-if="p.branch_name" class="rel-branch">· {{ p.branch_name }}</span>
              </div>
            </template>
          </template>
          <a-empty v-if="!detailRel" description="暂无数据" />
        </div>
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import {
  fuzzySearchPersonsApi,
  getTreeApi,
  listLineagesApi,
  visitChatApi,
  visitInfoApi,
  visitLineagesApi,
  visitPersonApi,
  visitSearchApi,
  visitTreeApi,
} from '@/api'
import Force3D, { type FEdge, type FNode } from '@/components/Force3D.vue'
import type { Lineage, Person, PersonDetail } from '@/types'

const router = useRouter()
const route = useRoute()

/** 访客短链形态：route.params.code 为 6 位短码（谱系分享 3D）；否则为内部 3D 谱系 */
const guestCode = computed(() => {
  const raw = route.params.code
  const c = String(Array.isArray(raw) ? raw[0] : raw || '').toUpperCase()
  return c.length === 6 ? c : ''
})
const guestMode = computed(() => !!guestCode.value)

const level = ref<'lineages' | 'persons' | 'branch'>('lineages')
const lineages = ref<Lineage[]>([])
const current = ref<Lineage | null>(null)
const personNodes = ref<Person[]>([])
const graphNodes = ref<FNode[]>([])
const graphEdges = ref<FEdge[]>([])
const loading = ref(false)
const guestError = ref('')
const guestName = ref('')
const guestAllowSearch = ref(false)
const guestAllowChat = ref(false)
const sel = ref<Person | null>(null)
const force3dRef = ref<InstanceType<typeof Force3D> | null>(null)
const searchText = ref<string>('')
const searchOptions = ref<{ value: string; label: string }[]>([])
/** 访客问答 */
const chatOpen = ref(false)
const chatQuestion = ref('')
const chatLoading = ref(false)
const chatLog = ref<{ role: 'me' | 'ai'; text: string }[]>([])
/** 访客关系详情 */
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailRel = ref<PersonDetail | null>(null)

const showSearch = computed(() => !guestMode.value || guestAllowSearch.value)

const G = {
  male: '#3f7fd6',
  female: '#d87aa2',
  unknown: '#9aa5b1',
}
const BRANCH_PALETTE = [
  '#4bacc6', '#ed7d31', '#70ad47', '#ffc000', '#8064a2', '#c00000',
  '#2f5597', '#548235', '#9e480e', '#f2a7c2', '#37a39c', '#7b6cd6',
]
function branchColor(id: string): string {
  let h = 3
  for (let i = 0; i < id.length; i++) h = (h * 33 + id.charCodeAt(i)) >>> 0
  return BRANCH_PALETTE[h % BRANCH_PALETTE.length]
}
function genderOf(p: Person): 'male' | 'female' | 'unknown' {
  const g = (p.gender || 'unknown').toLowerCase()
  return g === 'male' || g === 'female' ? g : 'unknown'
}
function yearsText(p: Person) {
  const a = p.birth_year ? String(p.birth_year) : '？'
  const b = p.death_year ? String(p.death_year) : '？'
  return `${a} — ${b}`
}
function genderText(p: Person) {
  const g = genderOf(p)
  return g === 'male' ? '男' : g === 'female' ? '女' : '未知'
}

function personFNode(p: Person, c = G[genderOf(p)]): FNode {
  return { id: p.person_id, label: p.name, sub: yearsText(p), color: c }
}

function lineageSize(ln: Lineage): number {
  if (ln.person_count) return ln.person_count
  return (ln.branches || []).reduce((s, b) => s + (b.person_count || 0), 0)
}

function buildLineageGraph() {
  level.value = 'lineages'
  current.value = null
  sel.value = null
  graphNodes.value = lineages.value.map((ln, i) => ({
    id: ln.lineage_id,
    label: ln.name || '未命名谱系',
    sub: `人物 ${lineageSize(ln)} · ${ln.code || ''}`,
    size: Math.max(1, lineageSize(ln)),
    color: branchColor(String(i)),
  }))
  graphEdges.value = []
}

async function reloadLineages() {
  loading.value = true
  guestError.value = ''
  try {
    const data = guestMode.value
      ? await visitLineagesApi(guestCode.value)
      : await listLineagesApi()
    lineages.value = data || []
    buildLineageGraph()
  } catch {
    if (guestMode.value) guestError.value = '链接不存在、已失效或已过期，请联系分享方。'
    else message.error('加载谱系失败，请重试')
  } finally {
    loading.value = false
  }
}

/** 二级：加载某谱系全部人物为 3D 力导 */
async function openLineage(ln: Lineage) {
  loading.value = true
  sel.value = null
  try {
    const tree = guestMode.value
      ? await visitTreeApi(guestCode.value, ln.lineage_id)
      : await getTreeApi(ln.lineage_id)
    personNodes.value = tree.nodes || []
    allEdgesRaw = (tree.edges || []) as typeof allEdgesRaw
    current.value = ln
    if (personNodes.value.length > 400) {
      level.value = 'branch'
      buildBranchAgg()
    } else {
      level.value = 'persons'
      buildPersonGraph(personNodes.value)
    }
  } catch {
    message.error('加载该谱系人物失败')
  } finally {
    loading.value = false
  }
}

function buildPersonGraph(persons: Person[]) {
  const idSet = new Set(persons.map((p) => p.person_id))
  graphNodes.value = persons.map((p) => personFNode(p))
  graphEdges.value = collectEdges(idSet)
}

/** 该谱系全部边（服务端 tree 接口已带） */
let allEdgesRaw: { id: string; source: string; target: string; type: string }[] = []
function collectEdges(idSet: Set<string>): FEdge[] {
  const out: FEdge[] = []
  for (const e of allEdgesRaw) {
    if (!idSet.has(e.source) || !idSet.has(e.target)) continue
    if (e.type === 'SPOUSE_OF') out.push({ source: e.source, target: e.target, color: '#e3a5bb' })
    else out.push({ source: e.source, target: e.target, color: '#7fa8d9' })
  }
  return out
}

/**
 * 大卷降级：按房支聚合为组节点。访客/公开接口不带 branch_id（TreeNode 无该字段），
 * 故统一用「房支名」作为分组键，兼容两种来源。
 */
interface BranchGroup {
  id: string
  name: string
  count: number
  members: Person[]
}
let branchGroups: BranchGroup[] = []
function branchKeyOf(p: Person): string {
  return p.branch_name || (p.branch_id ? '未命名房支' : '未分房支')
}
function buildBranchAgg() {
  const groups = new Map<string, BranchGroup>()
  for (const p of personNodes.value) {
    const key = branchKeyOf(p)
    let g = groups.get(key)
    if (!g) {
      g = { id: '', name: key, count: 0, members: [] }
      groups.set(key, g)
    }
    g.count++
    g.members.push(p)
  }
  branchGroups = [...groups.values()]
    .sort((a, b) => b.count - a.count)
    .map((g, i) => ({ ...g, id: `br:${i}` }))
  graphNodes.value = branchGroups.map((g) => ({
    id: g.id,
    label: g.name,
    sub: `人物 ${g.count}`,
    size: Math.max(1, g.count),
    color: branchColor(g.name),
  }))
  graphEdges.value = []
}

async function openBranchNode(id: string) {
  const g = branchGroups.find((x) => x.id === id)
  if (!g || !g.members.length) return
  if (g.members.length > 400) {
    message.warning('该房支人物过多，3D 已保持聚合')
    return
  }
  level.value = 'persons'
  buildPersonGraph(g.members)
}

function backToLineages() {
  buildLineageGraph()
}

function openPerson(p: Person) {
  router.push(`/persons/${p.person_id}`)
}

/** 访客：公开人物详情（父母/子女/配偶） */
type RelKey = keyof Pick<PersonDetail, 'parents' | 'spouses' | 'children'>
const relGroups: { key: RelKey; label: string }[] = [
  { key: 'parents', label: '父母' },
  { key: 'spouses', label: '配偶' },
  { key: 'children', label: '子女' },
]
async function openGuestDetail(p: Person) {
  detailOpen.value = true
  detailLoading.value = true
  detailRel.value = null
  try {
    detailRel.value = await visitPersonApi(guestCode.value, p.person_id)
  } catch {
    message.error('获取关系详情失败')
  } finally {
    detailLoading.value = false
  }
}

function onNodeClick(id: string) {
  if (level.value === 'lineages') {
    const ln = lineages.value.find((x) => x.lineage_id === id)
    if (ln) void openLineage(ln)
    return
  }
  if (level.value === 'branch') {
    if (id.startsWith('br:')) void openBranchNode(id)
    return
  }
  const p = personNodes.value.find((x) => x.person_id === id)
  if (p) sel.value = p
}

const searchPersons = ref<Person[]>([])

async function onSearch(q: string) {
  if (!q || q.trim().length < 1) {
    searchOptions.value = []
    searchPersons.value = []
    return
  }
  try {
    const hits = guestMode.value
      ? ((await visitSearchApi(guestCode.value, q.trim())) as unknown as Person[])
      : ((await fuzzySearchPersonsApi(q.trim())) as Person[])
    searchPersons.value = Array.isArray(hits) ? hits : []
    searchOptions.value = searchPersons.value.slice(0, 20).map((p) => ({
      value: p.person_id,
      label: `${p.name}（${p.lineage_name || p.lineage_id || ''}${p.branch_name ? ' · ' + p.branch_name : ''}）`,
    }))
  } catch {
    searchOptions.value = []
    searchPersons.value = []
  }
}

async function onPick(id: string) {
  if (!id) return
  const p = searchPersons.value.find((x) => x.person_id === id)
  searchText.value = ''
  searchOptions.value = []
  searchPersons.value = []
  if (!p) {
    message.warning('未找到该人物，可能已被删除')
    return
  }
  await gotoPerson(p)
}

/** 从人物列表/搜索落地：切到对应谱系并 3D 定位该人物 */
async function gotoPerson(p: Person) {
  const needLn = lineages.value.find(
    (x) => x.lineage_id === p.lineage_id || (p.lineage_name && x.name === p.lineage_name),
  )
  if (!needLn) {
    message.warning('该人物所属谱系未在列表（可能未写入图谱）')
    return
  }
  if (level.value === 'lineages' || current.value?.lineage_id !== needLn.lineage_id) {
    await openLineage(needLn)
    // 若落到了房支聚合态，展开全集以便定位（过大的卷保留聚合）
    if (level.value === 'branch') {
      if (personNodes.value.length > 1200) {
        message.warning('该谱系过于庞大，已保持聚合，请在画布中手动缩放定位')
        return
      }
      level.value = 'persons'
      buildPersonGraph(personNodes.value)
    }
  } else if (level.value === 'branch') {
    if (personNodes.value.length > 1200) {
      message.warning('该谱系过于庞大，已保持聚合，请在画布中手动缩放定位')
      return
    }
    level.value = 'persons'
    buildPersonGraph(personNodes.value)
  }
  const target = personNodes.value.find((x) => x.person_id === p.person_id)
  if (target) {
    sel.value = target
    await new Promise((r) => setTimeout(r, 160))
    force3dRef.value?.focusNode(p.person_id)
  }
}

/** 内部：来自 /tree3d?person=xxx 的定位请求 */
async function focusOnPersonQuery() {
  const pid = String(route.query.person || '')
  if (!pid || guestMode.value) return
  try {
    const hits = (await fuzzySearchPersonsApi(pid)) as Person[]
    const p = hits.find((x) => x.person_id === pid) || hits[0]
    if (!p) {
      message.warning('未找到该人物，可能已被删除')
      return
    }
    await gotoPerson(p)
  } catch {
    /* request 已提示 */
  }
}

/** 访客问答 */
async function askChat() {
  const q = chatQuestion.value.trim()
  if (!q) return
  chatQuestion.value = ''
  chatLog.value.push({ role: 'me', text: q })
  chatLoading.value = true
  try {
    const res = (await visitChatApi(guestCode.value, q)) as { answer?: string; reply?: string }
    chatLog.value.push({ role: 'ai', text: res?.answer || res?.reply || '暂无回答，请换个问法。' })
  } catch {
    chatLog.value.push({ role: 'ai', text: '问答服务暂不可用，请稍后再试。' })
  } finally {
    chatLoading.value = false
  }
}

onMounted(async () => {
  if (guestMode.value) {
    loading.value = true
    try {
      const info = await visitInfoApi(guestCode.value)
      guestName.value = info.name
      guestAllowSearch.value = info.allow_search
      guestAllowChat.value = info.allow_chat
    } catch {
      guestError.value = '链接不存在、已失效或已过期，请联系分享方。'
      loading.value = false
      return
    } finally {
      loading.value = false
    }
    await reloadLineages()
  } else {
    await reloadLineages()
    // /tree3d?person=xxx：来自人物列表「3D 定位」的跳转
    await focusOnPersonQuery()
  }
})
</script>

<style scoped>
.tree3d-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.t3d-head-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.t3d-title {
  font-size: 17px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.t3d-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.t3d-desc {
  margin-top: 8px;
  color: #8a8a8a;
  font-size: 13px;
}
.t3d-main {
  position: relative;
  padding: 12px;
}
.t3d-canvas {
  height: 62vh;
  min-height: 480px;
  position: relative;
}
.t3d-loading {
  padding: 60px 0;
  text-align: center;
}
.t3d-empty {
  padding: 48px 0;
  text-align: center;
}
.t3d-side {
  position: absolute;
  right: 18px;
  top: 18px;
  width: 292px;
  z-index: 20;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.16);
}
.t3d-side-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 15px;
}
.t3d-side-item {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13px;
}
.t3d-side-item label {
  color: #999;
  flex-shrink: 0;
  min-width: 44px;
}
.t3d-bio {
  max-height: 150px;
  overflow: auto;
}
.t3d-side-ops {
  margin-top: 10px;
  text-align: center;
}
.chat-log {
  max-height: 320px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}
.chat-line {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.chat-line.me {
  flex-direction: row-reverse;
}
.chat-who {
  flex-shrink: 0;
  font-size: 12px;
  background: #f0f0f0;
  border-radius: 4px;
  padding: 2px 6px;
  color: #666;
}
.chat-line.me .chat-who {
  background: #1677ff;
  color: #fff;
}
.chat-text {
  background: #f6f8fa;
  border-radius: 8px;
  padding: 8px 10px;
  max-width: 78%;
  line-height: 1.6;
}
.chat-line.me .chat-text {
  background: #e6f4ff;
}
.chat-hint {
  color: #999;
  font-size: 13px;
  padding: 12px 0;
  text-align: center;
}
.chat-input {
  display: flex;
  gap: 8px;
}
.rel-title {
  font-weight: 600;
  margin: 10px 0 4px;
  color: #555;
}
.rel-row {
  padding: 3px 0;
  color: #333;
  border-bottom: 1px dashed #f0f0f0;
}
.rel-years {
  color: #999;
}
.rel-branch {
  color: #aaa;
}
</style>
