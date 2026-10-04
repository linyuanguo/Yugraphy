<template>
  <div class="tree-page">
    <a-card :bordered="false" class="tree-card">
      <template #title>
        <a-space>
          <span>🌳 家谱树</span>
          <a-select
            v-model:value="lineageId"
            style="width: 180px"
            placeholder="全部谱系"
            allow-clear
            @change="onLineageChange"
          >
            <a-select-option v-for="l in lineages" :key="l.lineage_id" :value="l.lineage_id">
              {{ l.name }}（{{ l.person_count }} 人）
            </a-select-option>
          </a-select>
          <span ref="searchAnchorRef" class="tree-search">
            <a-input
              v-model:value="treeSearchText"
              allow-clear
              placeholder="🔍 搜人物 / 家谱内容"
              @press-enter="doTreeSearch"
            />
            <a-button size="small" type="primary" @click="doTreeSearch">搜索</a-button>
          </span>
          <a-tooltip title="在谱书原文中语义检索（如：徐氏源流迁居何处）">
            <a-button size="small" type="primary" ghost @click="openRagDrawer()">✨ 家谱内容</a-button>
          </a-tooltip>
          <!-- 联想浮层 Teleport 到 body（脱离 a-card title 容器，避免被父级 overflow/transform 裁剪）+ position:fixed 保证任意视口尺寸下可见 -->
          <Teleport to="body">
            <div v-if="treeSuggestOpen" class="tree-drop" :style="{ top: dropPos.top + 'px', left: dropPos.left + 'px' }">
              <template v-if="treeSuggestList.length">
                <div
                  v-for="p in treeSuggestList"
                  :key="p.person_id"
                  class="td-item"
                  @click="locateTreePerson(p)"
                >
                  <span class="td-name">{{ p.name }}</span>
                  <span v-if="p.birth_year" class="td-year">{{ p.birth_year }}</span>
                </div>
              </template>
              <div v-else class="td-empty">未找到相关人物</div>
              <div v-if="treeSearchText.trim()" class="td-rag" @click="openRagDrawer(treeSearchText.trim())">
                🔎 在谱书原文中检索「{{ treeSearchText.trim() }}」
              </div>
            </div>
          </Teleport>
        </a-space>
      </template>
      <template #extra>
        <a-space>
          <a-button v-if="subtreeActive" size="small" type="primary" ghost @click="backToFull">🌳 返回整谱</a-button>
          <a-button size="small" @click="fitView">适应画布</a-button>
          <a-button size="small" @click="zoom(1.2)">放大</a-button>
          <a-button size="small" @click="zoom(0.8)">缩小</a-button>
          <a-button size="small" @click="downloadPng">导出图片</a-button>
        </a-space>
      </template>
      <div class="legend">
        <span class="lg-left">
          <span class="lg-item"><i class="lg-line"></i>亲缘（父母→子女）</span>
          <span class="lg-item"><i class="lg-line lg-spouse"></i>配偶</span>
          <span class="lg-item"><i class="lg-box lg-male"></i>男</span>
          <span class="lg-item"><i class="lg-box lg-female"></i>女</span>
          <span class="lg-item"><i class="lg-box lg-unknown"></i>未知</span>
          <span class="lg-item"><i class="lg-branch"></i>顶部彩条=房支</span>
        </span>
        <span class="lg-right">
          <a-checkbox v-model:checked="showIsolated">显示孤立散点</a-checkbox>
          <span class="lg-tip">默认隐藏无关联散点/序言噪声人物，画面更清晰</span>
        </span>
      </div>
      <div ref="containerRef" class="graph-container"></div>
      <a-empty v-if="!loading && nodes.length === 0" description="暂无人物数据，请先添加人物" style="margin-top: 40px" />
    </a-card>

    <a-drawer v-model:open="drawerOpen" :width="380" title="人物详情">
      <template v-if="selected">
        <div class="detail-head">
          <a-avatar :size="64" :src="selected.photo_url" :style="{ background: genderColor(selected.gender), fontSize: '26px' }">
            {{ (selected.name || '?').slice(0, 1) }}
          </a-avatar>
          <div>
            <div class="detail-name">{{ selected.name }}</div>
            <a-tag :color="genderColor(selected.gender)">{{ genderText(selected.gender) }}</a-tag>
            <a-tag v-if="selected.generation">第 {{ selected.generation }} 代</a-tag>
          </div>
        </div>
        <a-descriptions :column="1" size="small" bordered style="margin-top: 16px">
          <a-descriptions-item label="生卒">{{ selected.birth_year ?? '?' }} — {{ selected.death_year ?? '?' }}</a-descriptions-item>
          <a-descriptions-item label="出生地">{{ selected.birth_place || '—' }}</a-descriptions-item>
          <a-descriptions-item label="逝地">{{ selected.death_place || '—' }}</a-descriptions-item>
          <a-descriptions-item label="谱系">
            <template v-if="selected.lineage_name">
              {{ selected.lineage_name }}<a-tag v-if="selected.branch_name" style="margin-left: 6px">{{ selected.branch_name }}</a-tag>
            </template>
            <span v-else>—</span>
          </a-descriptions-item>
          <a-descriptions-item label="简介">{{ selected.biography || '—' }}</a-descriptions-item>
          <a-descriptions-item label="备注">{{ selected.notes || '—' }}</a-descriptions-item>
        </a-descriptions>

        <!-- AI 人物展示文字（打开抽屉自动生成：依据谱书记载 + 家族背景） -->
        <div v-if="aiIntroOpen" class="ai-intro-wrap" style="margin-top: 18px">
          <a-spin :spinning="aiIntroLoading">
            <div v-if="aiIntroLoading" class="ai-intro-ph">AI 正在研读谱书记载，为「{{ selected.name }}」撰写展示文字…</div>
            <template v-else>
              <div v-if="aiIntro && aiIntro.text" class="ai-text-card">
                <div class="ai-card-top">
                  <span class="ai-badge">✨ AI 人物展示</span>
                  <span v-if="aiIntro.sources" class="ai-hint">依据 {{ aiIntro.sources }} 条谱书记载</span>
                </div>
                <div class="ai-text">{{ aiIntro.text }}</div>
              </div>
              <div v-if="!aiIntro" class="materials-empty">
                暂无与该人物匹配的谱书记载，无法生成 AI 展示文字（「写入图谱」后自动关联其谱系的源流/传记等原文）。
              </div>
            </template>
          </a-spin>
        </div>

        <!-- 家族文献：人物姓名命中的传记篇目 + 所在谱系/房支的背景内容 -->
        <div class="materials" style="margin-top: 18px">
          <div class="materials-title">📜 家族文献</div>
          <a-spin :spinning="materialsLoading">
            <div v-if="materials && materials.total === 0" class="materials-empty">
              暂无相关谱书内容。该人物的识别结果「写入图谱」后，会自动关联
              其所在谱系的源流/家规/传记等原文（可按下方操作先写一卷再回来看）。
            </div>
            <template v-if="materials && materials.total > 0">
              <div v-if="materials.person_entries.length" class="mat-sec">
                <div class="mat-sec-title">
                  🧑 与「{{ materials.name }}」相关（{{ materials.person_entries.length }} 篇）
                </div>
                <div
                  v-for="(it, i) in materials.person_entries"
                  :key="'p' + i"
                  class="mat-item"
                >
                  <div class="mat-item-head">
                    <a-tag color="geekblue">{{ it.type }}</a-tag>
                    <span class="mat-item-title">{{ it.title || '（无标题）' }}</span>
                    <span v-if="it.page_no" class="mat-item-page">p.{{ it.page_no }}</span>
                  </div>
                  <div class="mat-item-text">{{ it.text }}</div>
                </div>
              </div>
              <div v-if="materials.background_entries.length" class="mat-sec">
                <div class="mat-sec-title">
                  📖 {{ materials.lineage_name || materials.lineage_id || '所属谱系' }}
                  背景（{{ materials.background_entries.length }} 篇）
                </div>
                <div
                  v-for="(it, i) in materials.background_entries"
                  :key="'b' + i"
                  class="mat-item"
                >
                  <div class="mat-item-head">
                    <a-tag>{{ it.type }}</a-tag>
                    <span class="mat-item-title">{{ it.title || '（无标题）' }}</span>
                    <span v-if="it.page_no" class="mat-item-page">p.{{ it.page_no }}</span>
                  </div>
                  <div class="mat-item-text">{{ it.text }}</div>
                </div>
              </div>
            </template>
          </a-spin>
        </div>

        <div style="margin-top: 16px">
          <a-space>
            <a-button type="primary" @click="editPerson">编辑人物</a-button>
            <a-button @click="focusNode(selected.person_id)">以其为中心（放大聚焦）</a-button>
          </a-space>
        </div>
      </template>
    </a-drawer>

    <!-- ✨ 族谱知识检索（AI 展示文字 + 命中原文） -->
    <a-drawer v-model:open="ragOpen" :width="ragWidth" title="✨ 家谱知识全量检索（AI）">
      <div class="rag-head">
        <a-input
          v-model:value="ragQuestion"
          placeholder="问家谱内容，如：徐氏源流迁居何处 / 始祖是谁 / 家规有哪些"
          allow-clear
          @press-enter="runRagAsk"
        />
        <a-button type="primary" :loading="ragLoading" @click="runRagAsk">检索</a-button>
      </div>
      <a-alert
        v-if="ragResult && ragResult.total === 0 && !ragLoading"
        type="info"
        show-icon
        message="未检索到相关内容，试试换个说法（如「源流」「始祖」「迁徙」），或限定谱系后再试。"
        style="margin-top: 12px"
      />
      <template v-if="ragResult && ragResult.total > 0">
        <a-alert
          v-if="ragResult.source !== 'vector'"
          type="warning"
          show-icon
          style="margin-top: 12px"
          :message="
            ragResult.building
              ? '正在建立谱书知识索引（首次约需数十秒），当前为关键词结果，完成后自动升级为语义检索'
              : '当前为关键词检索结果（向量索引暂不可用），可稍后重试获得更智能的答案'
          "
        />
        <div v-if="ragResult.answer" class="ai-text-card" style="margin-top: 12px">
          <div class="ai-card-top">
            <span class="ai-badge">AI 展示</span>
            <span class="ai-hint">基于谱书原文生成 · 仅供参考</span>
          </div>
          <div class="ai-text">{{ ragResult.answer }}</div>
        </div>
        <div class="rag-hits-title">📄 原文命中（{{ ragResult.total }}）</div>
        <div v-for="(h, i) in ragResult.hits" :key="h.entry_id" class="rag-hit">
          <div class="rag-hit-head">
            <a-tag color="geekblue">{{ h.type }}</a-tag>
            <span class="rag-hit-title">{{ h.title || '（无标题）' }}</span>
            <span v-if="h.page_no" class="rag-hit-page">p.{{ h.page_no }}</span>
          </div>
          <div class="rag-hit-meta">
            <span v-if="h.lineage_name" class="rag-hit-lg">📕 {{ h.lineage_name }}</span>
            <span v-if="h.branch_name" class="rag-hit-br">{{ h.branch_name }}</span>
          </div>
          <!-- 命中条目对应的扫描页图（点击放大看原图核对） -->
          <div v-if="h.image_url" class="rag-hit-img">
            <a-image
              :src="h.thumb_url || h.image_url"
              :preview="{ src: h.image_url, mask: '🔍 查看原图' }"
              :width="150"
              style="border: 1px solid #f0f0f0; border-radius: 6px"
            />
            <span class="rag-hit-src" :title="h.task_name || ''">
              {{ h.task_name || '扫描件' }} · 第 {{ h.page_no }} 页
            </span>
          </div>
          <div class="rag-hit-text" :class="{ expand: isHitExp(i) }">{{ h.text }}</div>
          <div v-if="(h.text || '').length > 140" class="rag-hit-more" @click="toggleHit(i)">
            {{ isHitExp(i) ? '收起' : '展开全文' }}
          </div>
        </div>
      </template>
      <a-empty
        v-else-if="!ragLoading"
        description="输入问题开始检索谱书内容（可搜：源流、迁徙、始祖、家规、人物事迹……）"
        style="margin-top: 40px"
      />
    </a-drawer>

    <!-- 抽屉宽度拖拽把手（fixed 贴抽屉左缘，左右拖动调宽窄） -->
    <div
      v-if="ragOpen"
      class="rag-resizer"
      :style="{ left: resizerLeft + 'px' }"
      @mousedown.prevent="startRagResize"
    >
      <span class="grip"></span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import G6 from '@antv/g6'
import {
  fuzzySearchPersonsApi,
  getSubtreeApi,
  getTreeApi,
  listLineagesApi,
  personMaterialsApi,
  ragAskApi,
  ragPersonIntroApi,
} from '@/api'
import type { Lineage, Person, PersonIntro, PersonMaterials, RagAskResponse, TreeData, TreeEdge } from '@/types'

const route = useRoute()
const router = useRouter()

const containerRef = ref<HTMLDivElement>()
const nodes = ref<Person[]>([])
const edges = ref<TreeEdge[]>([])
const loading = ref(true)

const lineages = ref<Lineage[]>([])
const lineageId = ref<string>()

const drawerOpen = ref(false)
const selected = ref<Person | null>(null)

// 家族文献 + AI 人物展示（抽屉内按需加载；AI 展示打开即自动生成）
const materials = ref<PersonMaterials | null>(null)
const materialsLoading = ref(false)
const aiIntro = ref<PersonIntro | null>(null)
const aiIntroLoading = ref(false)
const aiIntroOpen = ref(false)
watch(
  [drawerOpen, selected],
  async ([open, sel]) => {
    materials.value = null
    aiIntro.value = null
    aiIntroOpen.value = false
    if (!open || !sel?.person_id) return
    aiIntroOpen.value = true
    aiIntroLoading.value = true
    materialsLoading.value = true
    try {
      try {
        materials.value = await personMaterialsApi(sel.person_id)
      } catch {
        materials.value = null
      }
      try {
        aiIntro.value = await ragPersonIntroApi(sel.person_id)
      } catch {
        aiIntro.value = null // 无谱书记载 404 / 服务异常均静默
      }
    } finally {
      materialsLoading.value = false
      aiIntroLoading.value = false
    }
  },
  { immediate: false },
)

let graph: any = null

// 局部聚焦标记（用于"返回整谱"）
const subtreeActive = ref(false)

const nodeMap = computed(() => {
  const m: Record<string, Person> = {}
  nodes.value.forEach((n) => (m[n.person_id] = n))
  return m
})

// ============ 人物搜索（输入即联想 / 回车与按钮搜索） ============
// 与访客分享页一致走后端检索：子串 / 同音 / 拼音缩写 / 错别字容错，
// 本地 includes 对繁体、异体、同音字常匹配不上
const treeSearchText = ref('')
const treeSuggestOpen = ref(false)
const treeSuggestList = ref<Person[]>([])
let treeSuggestTimer: any = null
let treeSearchSeq = 0
// 浮层定位锚点与位置（浮层已 Teleport 到 body + position:fixed）
const searchAnchorRef = ref<HTMLElement>()
const dropPos = ref({ top: 0, left: 0 })
const updateDropPos = () => {
  const el = searchAnchorRef.value
  if (!el) return
  const r = el.getBoundingClientRect()
  if (r.width) dropPos.value = { top: Math.round(r.bottom + 6), left: Math.round(r.left) }
}

const runTreeSuggest = async (q: string) => {
  const seq = ++treeSearchSeq
  try {
    const list = await fuzzySearchPersonsApi(q)
    if (seq !== treeSearchSeq) return // 丢弃过期响应，避免联想结果错乱
    treeSuggestList.value = list
    treeSuggestOpen.value = true
    // 浮层 Teleport 到 body 且 position:fixed，需要在打开时同步刷新一次位置
    await nextTick()
    updateDropPos()
  } catch {
    /* 错误由请求拦截器统一提示 */
  }
}

// 输入即联想
watch(treeSearchText, (v) => {
  clearTimeout(treeSuggestTimer)
  const q = (v || '').trim()
  if (!q) {
    treeSearchSeq++
    treeSuggestOpen.value = false
    treeSuggestList.value = []
    return
  }
  treeSuggestTimer = setTimeout(() => runTreeSuggest(q), 250)
})

// 回车 / 搜索按钮：检索并展示候选人；唯一命中直接跳转定位
const doTreeSearch = async () => {
  clearTimeout(treeSuggestTimer)
  const q = treeSearchText.value.trim()
  if (!q) {
    message.info('请输入要搜索的姓名或家谱内容')
    return
  }
  await runTreeSuggest(q)
  if (treeSuggestList.value.length === 1) locateTreePerson(treeSuggestList.value[0])
  else if (!treeSuggestList.value.length) openRagDrawer(q) // 无人名命中 → 家谱内容检索
}

// 点击候选人：关闭浮层并跳到该人名
const locateTreePerson = (p: Person) => {
  treeSuggestOpen.value = false
  focusNode(p.person_id)
}

// 返回整谱（重拉当前谱系整谱）
const backToFull = () => {
  treeSearchText.value = ''
  treeSuggestOpen.value = false
  loadTree()
}

// ============ ✨ 家谱内容检索（RAG：AI 展示文字 + 命中原文） ============
const ragOpen = ref(false)
const ragQuestion = ref('')
const ragLoading = ref(false)
const ragResult = ref<RagAskResponse | null>(null)
const expandedIdx = ref<number[]>([])
let ragAutoRetried = false
// 抽屉宽度（px，打开默认 40% 屏宽；把手拖拽调整后记住）
const ragWidth = ref(0)
const resizerLeft = computed(() => Math.round(window.innerWidth - ragWidth.value - 7))
const startRagResize = (e: MouseEvent) => {
  e.preventDefault()
  const startX = e.clientX
  const startW = ragWidth.value
  const onMove = (ev: MouseEvent) => {
    const minW = Math.round(window.innerWidth * 0.28)
    const maxW = window.innerWidth - 100
    ragWidth.value = Math.min(maxW, Math.max(minW, startW + (startX - ev.clientX)))
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

const isHitExp = (i: number) => expandedIdx.value.includes(i)
const toggleHit = (i: number) => {
  expandedIdx.value = expandedIdx.value.includes(i)
    ? expandedIdx.value.filter((x) => x !== i)
    : [...expandedIdx.value, i]
}

const openRagDrawer = (q?: string) => {
  treeSuggestOpen.value = false
  ragAutoRetried = false
  // 默认 40% 屏宽（记忆用户拖出的宽度，仅在无值或超屏时重置）
  if (!ragWidth.value || ragWidth.value > window.innerWidth - 100) {
    ragWidth.value = Math.round(window.innerWidth * 0.4)
  }
  if (q !== undefined && q !== null) ragQuestion.value = q
  ragOpen.value = true
  // 关闭再打开时清理旧结果
  ragResult.value = null
  if (q) runRagAsk()
}

const runRagAsk = async () => {
  const q = ragQuestion.value.trim()
  if (!q) {
    message.info('请输入要检索的家谱内容（如：徐氏源流迁居何处）')
    return
  }
  ragLoading.value = true
  expandedIdx.value = []
  try {
    ragResult.value = await ragAskApi(q, lineageId.value || undefined)
    // 首次建索引中：等待后自动重试一次以升级为语义检索
    if (
      !ragResult.value.ready &&
      !ragResult.value.total &&
      ragResult.value.building &&
      !ragAutoRetried
    ) {
      ragAutoRetried = true
      setTimeout(() => {
        if (ragOpen.value) runRagAsk()
      }, 9000)
    }
  } catch {
    /* 错误由请求拦截器统一提示 */
  } finally {
    ragLoading.value = false
  }
}

// ============ 图谱美化：默认隐藏孤立散点 / 序言噪声，可开关显示全部 ============
const showIsolated = ref(false)
let lastFullData: TreeData | null = null
// 序言/世系表噪声古人（通常以"始祖之父"链挂在谱首；保留"显示孤立散点"可恢复）
const NOISE_NAMES = new Set(['黄帝', '後稷', '后稷', '帝喾', '姜嫄', '周敦颐', '太王', '亶父', '古公亶父'])

const visibleData = (data: TreeData, focusId?: string): TreeData => {
  if (showIsolated.value || !data || !data.nodes.length) return data
  const connected = new Set<string>()
  data.edges.forEach((e) => {
    connected.add(e.source)
    connected.add(e.target)
  })
  const nodes = data.nodes.filter((n) => {
    if (focusId && n.person_id === focusId) return true // 聚焦人物恒保留
    if (!connected.has(n.person_id)) return false // 孤立散点（无边）
    return !NOISE_NAMES.has(n.name || '')
  })
  if (!nodes.length) return data // 极端：全部被过滤 → 回退全量，避免空画布
  const ids = new Set(nodes.map((n) => n.person_id))
  const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
  return { nodes, edges }
}

watch(showIsolated, () => {
  if (!lastFullData) return
  const vis = visibleData(lastFullData)
  nodes.value = vis.nodes
  edges.value = vis.edges
  renderGraph(vis)
})

const genderText = (g?: string) => (g === 'male' ? '男' : g === 'female' ? '女' : '未知')
// 古典系配色（与访客分享页一致）
const G_MALE = '#2f6db3'
const G_FEMALE = '#c25a70'
const G_UNKNOWN = '#8c8c8c'
const G_GOLD = '#d48806'
// 聚焦定位时的画布缩放上限（原统一 0.75 会放大过大，下调便于看更大的上下文）
const TREE_FOCUS_ZOOM = 0.45
const genderColor = (g?: string) => (g === 'male' ? G_MALE : g === 'female' ? G_FEMALE : G_UNKNOWN)
// 房支彩条配色（稳定哈希 → 每房支一色，标识不同房）
const BRANCH_PALETTE = ['#b7791f', '#7f5f3a', '#2f6db3', '#8c6a5a', '#4a7d5f', '#a05c7f', '#6b6fbf', '#b3593f']
const branchColor = (bid?: string | null) => {
  if (!bid) return ''
  let h = 0
  for (let i = 0; i < bid.length; i++) h = (h * 31 + bid.charCodeAt(i)) >>> 0
  return BRANCH_PALETTE[h % BRANCH_PALETTE.length]
}

G6.registerNode(
  'person-node',
  {
    draw(cfg: any, group: any) {
      const color = cfg.gender === 'male' ? G_MALE : cfg.gender === 'female' ? G_FEMALE : G_UNKNOWN
      const isFocus = !!cfg.focus
      const w = 132
      const h = 54
      // 节点卡片（柔和阴影 + 圆角；聚焦人物用金色描边高亮）
      const keyShape = group.addShape('rect', {
        attrs: {
          x: -w / 2,
          y: -h / 2,
          width: w,
          height: h,
          radius: 10,
          fill: isFocus ? '#fff7e6' : '#fdfbf5',
          stroke: isFocus ? G_GOLD : color,
          lineWidth: isFocus ? 3 : 2,
          shadowColor: isFocus ? 'rgba(212,136,6,0.45)' : 'rgba(74,55,40,0.16)',
          shadowBlur: isFocus ? 14 : 7,
          shadowOffsetY: 2,
          cursor: 'pointer',
        },
        name: 'key-shape',
      })
      // 左侧性别色条
      group.addShape('rect', {
        attrs: {
          x: -w / 2,
          y: -h / 2,
          width: 5,
          height: h,
          radius: [10, 0, 0, 10],
          fill: isFocus ? G_GOLD : color,
        },
        name: 'gender-stripe',
      })
      // 底部房支彩条（仅有人归属房支时显示；同房支同色）
      const bc = branchColor(cfg.branch_id)
      if (bc) {
        group.addShape('rect', {
          attrs: {
            x: -w / 2 + 3,
            y: h / 2 - 4,
            width: w - 8,
            height: 4,
            fill: bc,
            opacity: 0.9,
          },
          name: 'branch-stripe',
        })
      }
      group.addShape('text', {
        attrs: {
          x: 4,
          y: -7,
          text: cfg.name || '?',
          textAlign: 'center',
          fontSize: 15,
          fontWeight: 600,
          fill: '#4a3728',
          fontFamily: "'PingFang SC','Microsoft YaHei',sans-serif",
        },
        name: 'name-text',
      })
      group.addShape('text', {
        attrs: {
          x: 4,
          y: 13,
          text: `${cfg.birth_year ?? '?'} — ${cfg.death_year ?? '?'}`,
          textAlign: 'center',
          fontSize: 11,
          fill: '#8a7a66',
          fontFamily: "'PingFang SC','Microsoft YaHei',sans-serif",
        },
        name: 'years-text',
      })
      // 世代徽章（右上角，仅有人有值）
      if (cfg.generation) {
        group.addShape('text', {
          attrs: {
            x: w / 2 - 6,
            y: -h / 2 + 3,
            text: `${cfg.generation}代`,
            textAlign: 'right',
            fontSize: 9.5,
            fill: '#c9a45c',
            fontFamily: "'PingFang SC','Microsoft YaHei',sans-serif",
          },
          name: 'gen-badge',
        })
      }
      return keyShape
    },
    getAnchorPoints() {
      return [
        [0.5, 0],
        [0.5, 1],
      ]
    },
  },
  'single-node',
)

const loadTree = async (personId?: string) => {
  loading.value = true
  try {
    let data: TreeData
    if (personId) {
      data = await getSubtreeApi(personId, 6)
    } else {
      data = await getTreeApi(lineageId.value)
    }
    lastFullData = data
    const vis = visibleData(data, personId || undefined)
    nodes.value = vis.nodes
    edges.value = vis.edges
    subtreeActive.value = !!personId
    await nextTick()
    renderGraph(vis, personId || undefined)
  } catch {
    /* 错误已提示 */
  } finally {
    loading.value = false
  }
}

const onLineageChange = () => {
  treeSearchText.value = ''
  treeSuggestOpen.value = false
  loadTree()
}

// 对节点卡片做「聚焦/取消聚焦」的金色高亮（不依赖 state，直接改关键 shape 属性）
const setNodeFocus = (item: any, on: boolean) => {
  const key = item?.getContainer?.().find((e: any) => e.get('name') === 'key-shape')
  if (!key) return
  const color = item.getModel().gender === 'male' ? G_MALE : item.getModel().gender === 'female' ? G_FEMALE : G_UNKNOWN
  key.attr({
    stroke: on ? G_GOLD : color,
    fill: on ? '#fff7e6' : '#fdfbf5',
    lineWidth: on ? 3 : 2,
    shadowColor: on ? 'rgba(212,136,6,0.45)' : 'rgba(74,55,40,0.16)',
    shadowBlur: on ? 14 : 7,
  })
}
const clearAllFocus = () => graph?.getNodes().forEach((n: any) => setNodeFocus(n, false))

const renderGraph = (data: TreeData, focusId?: string) => {
  if (!containerRef.value) return
  const width = containerRef.value.clientWidth || 900
  const height = containerRef.value.clientHeight || 620

  if (graph) {
    graph.destroy()
    graph = null
  }

  const nCount = data.nodes.length
  const big = nCount > 300
  graph = new G6.Graph({
    container: containerRef.value,
    width,
    height,
    fitView: true,
    fitViewPadding: big ? [24, 24, 24, 24] : [52, 52, 52, 52],
    modes: {
      default: ['drag-canvas', 'zoom-canvas', 'drag-node'],
    },
    layout: {
      type: 'dagre',
      rankdir: 'TB',
      align: 'UL',
      nodesep: big ? 26 : 42,
      ranksep: big ? 64 : 84,
    },
    defaultNode: { type: 'person-node' },
    defaultEdge: { type: 'cubic-vertical' },
  })

  graph.data({
    nodes: data.nodes.map((n) => ({
      id: n.person_id,
      ...n,
      type: 'person-node',
      size: [132, 54],
      focus: n.person_id === focusId,
    })),
    edges: data.edges.map((e) => {
      const isSpouse = e.type === 'SPOUSE_OF'
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'cubic-vertical',
        style: {
          stroke: isSpouse ? 'rgba(194,90,112,0.72)' : '#b39a7e',
          lineWidth: isSpouse ? 1.6 : 2,
          lineDash: isSpouse ? [6, 5] : undefined,
          endArrow: isSpouse ? false : { path: G6.Arrow.triangle(7, 9, 0), d: 0 },
        },
      }
    }),
  })
  graph.render()

  graph.on('node:click', (evt: any) => {
    const id = evt.item.getModel().person_id
    clearAllFocus()
    setNodeFocus(evt.item, true)
    selected.value = nodeMap.value[id] || null
    drawerOpen.value = true
  })
  graph.on('canvas:click', clearAllFocus)
  graph.on('node:mouseenter', (evt: any) => {
    const key = evt.item?.getContainer?.().find((e: any) => e.get('name') === 'key-shape')
    if (key && !evt.item.getModel().focus) key.attr({ shadowBlur: 13, lineWidth: 2.5 })
  })
  graph.on('node:mouseleave', (evt: any) => {
    const key = evt.item?.getContainer?.().find((e: any) => e.get('name') === 'key-shape')
    if (key && !evt.item.getModel().focus) key.attr({ shadowBlur: 7, lineWidth: 2 })
  })

  // 指定焦点人物：居中定位 + 高亮；画布比例过大时缩回（避免局部子图 fit 后过大的观感）
  if (focusId) {
    const item = graph.findById(focusId)
    if (item) {
      graph.focusItem(item, false)
      if (graph.getZoom() > TREE_FOCUS_ZOOM) graph.zoomTo(TREE_FOCUS_ZOOM, false)
    }
  }
}

const fitView = () => graph && graph.fitView(52)
const zoom = (r: number) => graph && graph.zoom(r)

// 放大定位到某个人物（高亮 + 居中 + 自动放大）
const centerOn = (id: string) => {
  if (!graph) return
  const item = graph.findById(id)
  if (!item) {
    message.warning('该人物不在当前视图中')
    return
  }
  clearAllFocus()
  setNodeFocus(item, true)
  graph.focusItem(item, true)
  // 只在大过上限时缩回，避免"放大过大"；比例适中时保持原视野
  if (graph.getZoom() > TREE_FOCUS_ZOOM) graph.zoomTo(TREE_FOCUS_ZOOM, true)
  selected.value = nodeMap.value[id] || null
}

// 搜索定位：视图不大时直接在画布放大定位；谱系很大时自动切换为「以该人为核心」的局部子图
const focusNode = async (id: string) => {
  if (!graph) return
  if (nodes.value.length <= 400 && graph.findById(id)) {
    centerOn(id)
    return
  }
  try {
    const data = await getSubtreeApi(id, 6)
    lastFullData = data
    const vis = visibleData(data, id)
    nodes.value = vis.nodes
    edges.value = vis.edges
    subtreeActive.value = true
    await nextTick()
    renderGraph(vis, id)
    const p = data.nodes.find((n) => n.person_id === id)
    if (p) message.success(`已聚焦「${p.name}」及其附近 ${Math.max(0, data.nodes.length - 1)} 位关联人物`)
  } catch {
    /* request 已提示 */
  }
}

const downloadPng = () => {
  if (!graph) return
  const dataUrl = graph.toFullDataURL('image/png', { backgroundColor: '#fff' })
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = '家谱树.png'
  a.click()
}

const editPerson = () => {
  if (selected.value) router.push(`/persons/${selected.value.person_id}`)
}

const onResize = () => {
  if (graph && containerRef.value) {
    graph.changeSize(containerRef.value.clientWidth, containerRef.value.clientHeight)
  }
}

onMounted(async () => {
  const q = route.query.person as string | undefined
  try {
    lineages.value = await listLineagesApi()
  } catch {
    /* 忽略 */
  }
  // 家谱树基于谱系展示：未指定聚焦人物时默认加载第一个谱系（避免全库混杂）
  if (!q && lineages.value.length) {
    lineageId.value = lineages.value[0].lineage_id
  }
  loadTree(q)
  window.addEventListener('resize', onResize)
  window.addEventListener('resize', updateDropPos)
  window.addEventListener('scroll', updateDropPos, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('resize', updateDropPos)
  window.removeEventListener('scroll', updateDropPos, true)
  if (graph) {
    graph.destroy()
    graph = null
  }
})
</script>

<style scoped>
/* 搜索：输入框 + 按钮 + 联想候选浮层 */
.tree-search {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tree-search :deep(.ant-input-affix-wrapper),
.tree-search :deep(.ant-input) {
  width: 180px;
  border-radius: 16px;
}
/* 浮层已 Teleport 到 body 并 position:fixed（位置由 inline top/left 控制）。
   纯白背景 + 深色边框阴影，与下方米色画布形成强对比，保证任何背景上都清晰可见 */
.tree-drop {
  position: fixed;
  width: 268px;
  max-height: 380px;
  overflow-y: auto;
  background: #ffffff;
  border: 1px solid #d9b45b;
  border-radius: 10px;
  box-shadow: 0 8px 28px rgba(30, 20, 5, 0.3);
  z-index: 9999;
}
.td-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f0e6d2;
  transition: background 0.15s;
}
.td-item:hover {
  background: #fff7e6;
}
.td-item:last-child {
  border-bottom: none;
}
.td-name {
  font-weight: 600;
  color: #26221a;
}
.td-year {
  color: #7a6a50;
  font-size: 12px;
}
.td-empty {
  padding: 12px;
  text-align: center;
  color: #8a7a60;
}
/* 下拉底部："在族谱原文中检索"入口（点击打开 AI 家谱内容检索抽屉） */
.td-rag {
  padding: 10px 12px;
  border-top: 1px dashed #e5d5b4;
  background: #fffdf5;
  color: #a4630f;
  font-size: 12.5px;
  cursor: pointer;
  text-align: center;
  transition: background 0.15s;
}
.td-rag:hover {
  background: #fff1d6;
}
.tree-card {
  height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
}
.graph-container {
  flex: 1;
  min-height: 560px;
  border-radius: 10px;
  border: 1px solid #ece0c8;
  background-color: #faf6ec;
  /* 细腻米色网格：纸感谱系氛围 */
  background-image:
    linear-gradient(rgba(138, 109, 75, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(138, 109, 75, 0.055) 1px, transparent 1px);
  background-size: 26px 26px;
  overflow: hidden;
}
.legend {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 18px;
  padding: 6px 0 10px;
  font-size: 12px;
  color: #6b5d4a;
}
.lg-left {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
}
.lg-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.lg-tip {
  color: #a99a82;
  font-size: 11px;
}
.lg-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.lg-line {
  display: inline-block;
  width: 22px;
  height: 3px;
  background: #b39a7e;
  border-radius: 2px;
}
.lg-spouse {
  background: #c25a70;
  border-top: 2px dashed #c25a70;
  height: 0;
}
.lg-box {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 3px;
}
.lg-male {
  background: #2f6db3;
}
.lg-female {
  background: #c25a70;
}
.lg-unknown {
  background: #8c8c8c;
}
.lg-branch {
  display: inline-block;
  width: 16px;
  height: 4px;
  background: linear-gradient(90deg, #b7791f, #4a7d5f, #a05c7f);
  border-radius: 2px;
}
.detail-head {
  display: flex;
  gap: 14px;
  align-items: center;
}
.detail-name {
  font-size: 20px;
  font-weight: 700;
}

/* ===== AI 展示文字（人物抽屉 / RAG 检索抽屉共用风格） ===== */
.ai-intro-ph {
  color: #a08963;
  font-size: 13px;
  line-height: 1.8;
  padding: 6px 2px;
}
.ai-text-card {
  position: relative;
  border: 1px solid #d9b45b;
  background: linear-gradient(135deg, #fffdf6, #fdf1dc);
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 3px 10px rgba(183, 121, 31, 0.14);
}
.ai-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 7px;
  gap: 8px;
}
.ai-badge {
  display: inline-block;
  background: linear-gradient(135deg, #b7791f, #d4a53c);
  color: #fff;
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 10px;
  letter-spacing: 1px;
  flex-shrink: 0;
}
.ai-hint {
  color: #a08963;
  font-size: 11px;
}
.ai-text {
  font-size: 13.5px;
  line-height: 1.85;
  color: #4a3728;
  white-space: pre-wrap;
  word-break: break-word;
  text-align: justify;
}

/* ===== 家族文献（抽屉内） ===== */
.materials-title {
  font-size: 15px;
  font-weight: 700;
  color: #59360f;
  border-left: 4px solid #b7791f;
  padding-left: 8px;
  margin-bottom: 10px;
}
.materials-empty {
  color: #999;
  font-size: 12px;
  line-height: 1.7;
  padding: 4px 2px;
}
.mat-sec {
  margin-bottom: 14px;
}
.mat-sec-title {
  font-size: 13px;
  font-weight: 600;
  color: #59360f;
  margin-bottom: 6px;
}
.mat-item {
  border: 1px solid #f0e3cd;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  background: #fdf9f1;
}
.mat-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.mat-item-title {
  font-weight: 600;
  font-size: 13px;
  flex: 1;
  min-width: 0;
}
.mat-item-page {
  color: #b7791f;
  font-size: 12px;
  white-space: nowrap;
}
.mat-item-text {
  font-size: 13px;
  line-height: 1.7;
  color: #444;
  max-height: 96px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ===== 族谱知识检索抽屉 ===== */
.rag-head {
  display: flex;
  gap: 8px;
}
.rag-head :deep(.ant-input) {
  border-radius: 14px;
}
.rag-hits-title {
  font-size: 14px;
  font-weight: 700;
  color: #59360f;
  border-left: 4px solid #b7791f;
  padding-left: 8px;
  margin: 16px 0 10px;
}
.rag-hit {
  border: 1px solid #f0e3cd;
  border-radius: 8px;
  padding: 9px 11px;
  margin-bottom: 10px;
  background: #fdf9f1;
}
.rag-hit-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.rag-hit-title {
  font-weight: 600;
  font-size: 13px;
  flex: 1;
  min-width: 0;
}
.rag-hit-page {
  color: #b7791f;
  font-size: 12px;
  white-space: nowrap;
}
.rag-hit-meta {
  display: flex;
  gap: 10px;
  margin-bottom: 5px;
}
.rag-hit-lg {
  color: #6b5d4a;
  font-size: 12px;
}
.rag-hit-br {
  color: #a05c7f;
  font-size: 12px;
}
.rag-hit-img {
  margin: 6px 0 2px;
}
.rag-hit-src {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: #8c8c8c;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}
.rag-hit-text {
  font-size: 13px;
  line-height: 1.75;
  color: #444;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 148px;
  overflow: hidden;
}
.rag-hit-text.expand {
  max-height: none;
}
.rag-hit-more {
  margin-top: 6px;
  color: #a4630f;
  font-size: 12px;
  cursor: pointer;
  user-select: none;
}
.rag-hit-more:hover {
  text-decoration: underline;
}

/* ===== 族谱知识检索抽屉：宽度拖拽把手 ===== */
.rag-resizer {
  position: fixed;
  top: calc(50% - 105px);
  width: 16px;
  height: 210px;
  z-index: 1100;
  cursor: col-resize;
  display: flex;
  align-items: center;
  justify-content: center;
  user-select: none;
  /* 透明加宽热区，方便盲点到、按住拖动 */
}
.rag-resizer::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 8px;
  background: transparent;
}
.rag-resizer:hover::before {
  background: rgba(183, 121, 31, 0.10);
}
.rag-resizer .grip {
  width: 6px;
  height: 160px;
  border-radius: 4px;
  background: rgba(183, 121, 31, 0.8);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.8);
  transition: background 0.15s;
}
.rag-resizer:hover .grip {
  background: rgba(183, 121, 31, 1);
}
</style>
