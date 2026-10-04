<template>
  <div class="visit-page">
    <!-- 顶部：深蓝描金牌匾 + 搜索 -->
    <header class="v-header">
      <div class="v-brand">
        <div class="v-logo">📜</div>
        <div>
          <div class="v-name">{{ info.name || '族谱 · 在线浏览' }}</div>
          <div class="v-sub">家族谱系 · 访客浏览</div>
        </div>
      </div>
      <div class="v-search">
        <div class="search-box">
          <a-input
            v-model:value="searchText"
            allow-clear
            placeholder="🔍 搜人物 / 家谱内容（同音、模糊均可）"
            @press-enter="doSearch"
          />
          <a-button class="search-btn" type="primary" @click="doSearch">搜索</a-button>
          <a-tooltip title="在谱书原文中语义检索（如：始祖源流、迁居何处、家规）">
            <a-button
              v-if="info.allow_search"
              class="search-btn ghost"
              type="primary"
              @click="openRagDrawer()"
            >
              ✨ 家谱内容
            </a-button>
          </a-tooltip>
        </div>
        <!-- 联想候选浮层 -->
        <div v-if="suggestOpen" class="search-drop">
          <div v-if="searching" class="sd-tip">正在检索…</div>
          <template v-else>
            <div v-for="p in suggestList" :key="p.person_id" class="sd-item" @click="pickPerson(p)">
              <span class="sd-name">{{ p.name }}</span>
              <span class="sd-year">{{ p.birth_year ?? '?' }} — {{ p.death_year ?? '?' }}</span>
            </div>
            <div v-if="!suggestList.length" class="sd-empty">
              {{ searchText.trim() ? '未找到相关人物' : '输入姓名开始搜索' }}
            </div>
          </template>
          <div
            v-if="info.allow_search && searchText.trim()"
            class="sd-rag"
            @click="openRagDrawer(searchText.trim())"
          >
            ✨ 在谱书原文中检索「{{ searchText.trim() }}」
          </div>
        </div>
      </div>
      <div class="v-right">
        <span v-if="info.allow_chat" class="v-chip">💬 支持问答</span>
        <span v-if="info.allow_search" class="v-chip">🔎 支持搜索</span>
      </div>
    </header>

    <!-- 主体：宣纸画框式谱系画布 -->
    <main class="v-main">
      <button v-if="subtreeMode" class="v-back-full" @click="backFullTree">🌳 返回全景</button>
      <div class="v-frame">
        <div ref="containerRef" class="v-canvas"></div>
        <i class="v-corner tl"></i>
        <i class="v-corner tr"></i>
        <i class="v-corner bl"></i>
        <i class="v-corner br"></i>
        <div class="v-seal" aria-hidden="true">
          <span>谱</span><span>系</span>
        </div>
      </div>
      <div class="v-legend">
        <span class="lg"><i class="lg-line"></i>亲缘（父母→子女）</span>
        <span class="lg"><i class="lg-line lg-spouse"></i>配偶</span>
        <span class="lg"><i class="lg-box lg-male"></i>男</span>
        <span class="lg"><i class="lg-box lg-female"></i>女</span>
        <span class="lg"><i class="lg-box lg-unknown"></i>未知</span>
        <span class="lg"><i class="lg-branch"></i>顶条=房支</span>
        <span class="lg lg-check">
          <a-checkbox v-model:checked="showIsolated">显示孤立散点</a-checkbox>
        </span>
      </div>
      <div v-if="loading" class="v-loading">
        <a-spin size="large" />
        <div class="v-loading-text">正在展开族谱长卷…</div>
      </div>
      <div v-if="errorMsg" class="v-error">
        <a-result status="warning" title="链接无效或已过期" :sub-title="errorMsg">
          <template #extra>
            <a-button type="primary" @click="backHome">返回首页</a-button>
          </template>
        </a-result>
      </div>
    </main>

    <AppCopyright mode="share" />

    <!-- 人物详情抽屉 -->
    <a-drawer v-model:open="drawerOpen" :width="400" title="人物详情" class="v-drawer">
      <template v-if="detail">
        <div class="d-head">
          <a-avatar :size="64" :style="{ background: genderColor(detail.gender), fontSize: '26px' }">
            {{ (detail.name || '?').slice(0, 1) }}
          </a-avatar>
          <div>
            <div class="d-name">{{ detail.name }}</div>
            <div class="d-tags">
              <a-tag :color="genderColor(detail.gender)">{{ genderText(detail.gender) }}</a-tag>
              <a-tag v-if="detail.generation">第 {{ detail.generation }} 代</a-tag>
            </div>
          </div>
        </div>
        <a-descriptions :column="1" size="small" bordered class="d-desc">
          <a-descriptions-item label="生卒">{{ detail.birth_year ?? '?' }} — {{ detail.death_year ?? '?' }}</a-descriptions-item>
          <a-descriptions-item label="出生地">{{ detail.birth_place || '—' }}</a-descriptions-item>
          <a-descriptions-item label="逝地">{{ detail.death_place || '—' }}</a-descriptions-item>
          <a-descriptions-item label="简介">{{ detail.biography || '—' }}</a-descriptions-item>
        </a-descriptions>
        <div v-if="detail.parents?.length" class="d-sec">
          <div class="d-sec-title">👴 父母</div>
          <div class="d-chips">
            <a-tag v-for="p in detail.parents" :key="p.person_id" color="blue" class="d-chip" @click="jumpPerson(p)">
              {{ p.name }}{{ p.birth_year ? `（${p.birth_year}）` : '' }}
            </a-tag>
          </div>
        </div>
        <div v-if="detail.spouses?.length" class="d-sec">
          <div class="d-sec-title">💑 配偶</div>
          <div class="d-chips">
            <a-tag v-for="p in detail.spouses" :key="p.person_id" color="magenta" class="d-chip" @click="jumpPerson(p)">
              {{ p.name }}{{ p.birth_year ? `（${p.birth_year}）` : '' }}
            </a-tag>
          </div>
        </div>
        <div v-if="detail.children?.length" class="d-sec">
          <div class="d-sec-title">👶 子女</div>
          <div class="d-chips">
            <a-tag v-for="p in detail.children" :key="p.person_id" color="green" class="d-chip" @click="jumpPerson(p)">
              {{ p.name }}{{ p.birth_year ? `（${p.birth_year}）` : '' }}
            </a-tag>
          </div>
        </div>
        <a-button v-if="detail.children?.length" block class="d-btn" @click="focusSubtree(detail.person_id)">
          以「{{ detail.name }}」为中心查看支系
        </a-button>

        <!-- AI 人物展示文字（打开抽屉自动生成；与家族文献同受"谱书内容"权限控制） -->
        <div v-if="aiIntroOpen && info.allow_search" class="d-sec">
          <a-spin :spinning="aiIntroLoading">
            <div v-if="aiIntroLoading" class="ai-intro-ph">AI 正在研读谱书记载，为「{{ detail.name }}」撰写展示文字…</div>
            <template v-else>
              <div v-if="aiIntro && aiIntro.text" class="ai-text-card">
                <div class="ai-card-top">
                  <span class="ai-badge">✨ AI 人物展示</span>
                  <span v-if="aiIntro.sources" class="ai-hint">依据 {{ aiIntro.sources }} 条谱书记载</span>
                </div>
                <div class="ai-text">{{ aiIntro.text }}</div>
              </div>
              <div v-if="!aiIntro" class="d-mat-empty">
                暂无与该人物匹配的谱书记载，无法生成 AI 展示文字。
              </div>
            </template>
          </a-spin>
        </div>

        <!-- 家族文献：姓名命中的传记篇目 + 所在谱系/房支背景内容 -->
        <div v-if="materials" class="d-sec d-mat">
          <div class="d-sec-title">📜 家族文献</div>
          <a-spin :spinning="materialsLoading">
            <div v-if="materials.total === 0" class="d-mat-empty">
              暂无相关谱书内容（人物「写入图谱」后自动关联谱书原文）。
            </div>
            <template v-else>
              <div v-if="materials.person_entries.length">
                <div class="d-mat-sec-title">🧑 与「{{ materials.name }}」相关</div>
                <div v-for="(it, i) in materials.person_entries" :key="'p' + i" class="d-mat-item">
                  <div class="d-mat-item-head">
                    <a-tag color="geekblue" style="font-size: 11px">{{ it.type }}</a-tag>
                    <span class="d-mat-item-title">{{ it.title || '（无标题）' }}</span>
                    <span v-if="it.page_no" class="d-mat-item-page">p.{{ it.page_no }}</span>
                  </div>
                  <div class="d-mat-item-text">{{ it.text }}</div>
                </div>
              </div>
              <div v-if="materials.background_entries.length">
                <div class="d-mat-sec-title">
                  📖 {{ materials.lineage_name || materials.lineage_id || '所属谱系' }}背景
                </div>
                <div v-for="(it, i) in materials.background_entries" :key="'b' + i" class="d-mat-item">
                  <div class="d-mat-item-head">
                    <a-tag style="font-size: 11px">{{ it.type }}</a-tag>
                    <span class="d-mat-item-title">{{ it.title || '（无标题）' }}</span>
                    <span v-if="it.page_no" class="d-mat-item-page">p.{{ it.page_no }}</span>
                  </div>
                  <div class="d-mat-item-text">{{ it.text }}</div>
                </div>
              </div>
            </template>
          </a-spin>
        </div>
      </template>
    </a-drawer>

    <!-- ✨ 族谱知识检索（AI 展示文字 + 命中原文） -->
    <a-drawer v-model:open="ragOpen" :width="ragWidth" title="✨ 家谱知识全量检索（AI）" class="v-drawer">
      <div class="rag-head">
        <a-input
          v-model:value="ragQuestion"
          placeholder="问家谱内容，如：始祖源流 / 迁居何处 / 家规有哪些"
          allow-clear
          @press-enter="runRagAsk"
        />
        <a-button type="primary" :loading="ragLoading" @click="runRagAsk">检索</a-button>
      </div>
      <a-alert
        v-if="ragResult && ragResult.total === 0 && !ragLoading"
        type="info"
        show-icon
        message="未检索到相关内容，试试换个说法（如「源流」「始祖」「迁徙」「家规」）。"
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
        <div v-for="(h, i) in ragResult.hits" :key="h.entry_id" class="d-mat-item">
          <div class="d-mat-item-head">
            <a-tag color="geekblue" style="font-size: 11px">{{ h.type }}</a-tag>
            <span class="d-mat-item-title">{{ h.title || '（无标题）' }}</span>
            <span v-if="h.page_no" class="d-mat-item-page">p.{{ h.page_no }}</span>
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

    <!-- 图谱问答浮窗 -->
    <div v-if="info.allow_chat" class="qa-wrap">
      <div v-if="qaOpen" class="qa-panel">
        <div class="qa-head">
          <span>💬 族谱问答</span>
          <a-button type="text" size="small" @click="qaOpen = false">✕</a-button>
        </div>
        <div ref="qaBodyRef" class="qa-body">
          <div v-for="(m, i) in messages" :key="i" :class="['qa-msg', m.role]">
            <div class="qa-bubble">{{ m.content }}</div>
            <div v-if="m.persons && m.persons.length" class="qa-persons">
              <a-tag
                v-for="p in m.persons.slice(0, 12)"
                :key="p.person_id"
                class="qa-chip"
                :color="genderColor(p.gender)"
                @click="focusPerson(p.person_id)"
              >
                {{ p.name }}{{ p.birth_year ? `（${p.birth_year}）` : '' }}
              </a-tag>
              <span v-if="m.persons.length > 12" class="qa-more">… 共 {{ m.persons.length }} 人</span>
            </div>
            <a-button
              v-if="m.tree && m.tree.nodes.length"
              size="small"
              class="qa-see"
              type="primary"
              ghost
              @click="showChatGraph(m)"
            >
              🗺️ 在图谱中查看
            </a-button>
          </div>
          <div v-if="qaLoading" class="qa-msg assistant">
            <div class="qa-bubble qa-thinking">正在查询族谱…</div>
          </div>
        </div>
        <div class="qa-input">
          <a-input
            v-model:value="qaInput"
            placeholder="例如：张三的父亲是谁？"
            @pressEnter="sendQa"
          />
          <a-button type="primary" :loading="qaLoading" @click="sendQa">发送</a-button>
        </div>
        <div class="qa-tip">{{ info.welcome }}</div>
      </div>
      <button class="qa-fab" @click="qaOpen = !qaOpen" :title="qaOpen ? '收起问答' : '图谱问答'">
        {{ qaOpen ? '✕' : '💬' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import G6 from '@antv/g6'
import {
  visitChatApi,
  visitInfoApi,
  visitPersonApi,
  visitPersonMaterialsApi,
  visitRagAskApi,
  visitRagPersonIntroApi,
  visitSearchApi,
  visitSubtreeApi,
  visitTreeApi,
} from '@/api'
import type {
  ChatResponse,
  Person,
  PersonDetail,
  PersonIntro,
  PersonMaterials,
  RagAskResponse,
  TreeData,
  VisitInfo,
} from '@/types'

const route = useRoute()
const router = useRouter()

/** 图谱树分享访问链接 = <站点根>/<share_code>；公开接口统一用 code 短链码鉴权（旧 /s/、/visit?token= 形态已下线） */
const shareCode = computed(() => String(route.params.code || '').toUpperCase())
const info = ref<VisitInfo>({ name: '', allow_search: true, allow_chat: false, welcome: '' })
const errorMsg = ref('')
const loading = ref(true)

/** 入口：直接以 share_code 加载（无码/无效/过期由加载结果与拦截器提示） */
const enterView = async () => {
  await loadTree()
}

const containerRef = ref<HTMLDivElement>()
const nodes = ref<Person[]>([])
const edges = ref<{ id: string; source: string; target: string; type: string }[]>([])
let graph: any = null

const drawerOpen = ref(false)
const detail = ref<PersonDetail | null>(null)

// 家族文献（抽屉内按需加载）
const materials = ref<PersonMaterials | null>(null)
const materialsLoading = ref(false)

// AI 人物展示文字（打开抽屉自动生成：基于谱书记载）
const aiIntro = ref<PersonIntro | null>(null)
const aiIntroLoading = ref(false)
const aiIntroOpen = ref(false)

// 搜索（输入即时联想 + 回车/按钮显式搜索）
const searchText = ref('')
const suggestOpen = ref(false)
const searching = ref(false)
const suggestList = ref<Person[]>([])
let suggestTimer: any = null

const runSearch = async (q: string) => {
  searching.value = true
  suggestOpen.value = true
  try {
    suggestList.value = await visitSearchApi(shareCode.value, q)
  } catch {
    suggestList.value = []
  } finally {
    searching.value = false
  }
}

// 输入 1 个字起即联想检索
watch(searchText, (v) => {
  clearTimeout(suggestTimer)
  const q = (v || '').trim()
  if (!q) {
    suggestOpen.value = false
    suggestList.value = []
    return
  }
  suggestTimer = setTimeout(() => runSearch(q), 260)
})

// 回车 / 搜索按钮：检索并展示候选人；唯一命中直接跳转放大
const doSearch = async () => {
  if (!info.value.allow_search) return
  clearTimeout(suggestTimer)
  const q = searchText.value.trim()
  if (!q) {
    message.info('请输入要搜索的姓名或家谱内容')
    return
  }
  await runSearch(q)
  if (!suggestList.value.length) {
    suggestOpen.value = false
    openRagDrawer(q) // 无人名命中 → 在谱书原文里做家谱内容检索
    return
  }
  // 回车 / 按钮：直接跳到最匹配的第一位（候选浮层仍保留，可再点其它人名）
  focusPerson(suggestList.value[0].person_id)
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
  if (!info.value.allow_search) return
  suggestOpen.value = false
  ragAutoRetried = false
  // 默认 40% 屏宽（记忆用户拖出的宽度，仅在无值或超屏时重置）
  if (!ragWidth.value || ragWidth.value > window.innerWidth - 100) {
    ragWidth.value = Math.round(window.innerWidth * 0.4)
  }
  if (q !== undefined && q !== null) ragQuestion.value = q
  ragOpen.value = true
  ragResult.value = null
  if (q) runRagAsk()
}

const runRagAsk = async () => {
  const q = ragQuestion.value.trim()
  if (!q) {
    message.info('请输入要检索的家谱内容（如：始祖源流迁居何处）')
    return
  }
  ragLoading.value = true
  expandedIdx.value = []
  try {
    ragResult.value = await visitRagAskApi(shareCode.value, q)
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
    /* 错误由拦截器提示（如未开放搜索 403） */
  } finally {
    ragLoading.value = false
  }
}

// 点击候选人：关闭浮层并跳转放大定位
const pickPerson = (p: Person) => {
  suggestOpen.value = false
  focusPerson(p.person_id)
}

// 问答
const qaOpen = ref(false)
const qaInput = ref('')
const qaLoading = ref(false)
const qaBodyRef = ref<HTMLDivElement>()
const messages = ref<Array<{ role: string; content: string; persons?: Person[]; tree?: TreeData | null }>>([])

const genderText = (g?: string) => (g === 'male' ? '男' : g === 'female' ? '女' : '未知')
const genderColor = (g?: string) => (g === 'male' ? '#2f6db3' : g === 'female' ? '#c25a70' : '#8c8c8c')

// 房支彩条配色（稳定哈希 → 每房支一色，标识不同房）
const BRANCH_PALETTE = ['#b7791f', '#7f5f3a', '#2f6db3', '#8c6a5a', '#4a7d5f', '#a05c7f', '#6b6fbf', '#b3593f']
const branchColor = (bid?: string | null) => {
  if (!bid) return ''
  let h = 0
  for (let i = 0; i < bid.length; i++) h = (h * 31 + bid.charCodeAt(i)) >>> 0
  return BRANCH_PALETTE[h % BRANCH_PALETTE.length]
}

// ============ 图谱美化：默认隐藏孤立散点 / 序言噪声（可开关显示全部） ============
const showIsolated = ref(false)
const NOISE_NAMES = new Set(['黄帝', '後稷', '后稷', '帝喾', '姜嫄', '周敦颐', '太王', '亶父', '古公亶父'])

const visibleData = (data: TreeData, focusId?: string): TreeData => {
  if (showIsolated.value || !data || !data.nodes.length) return data
  const connected = new Set<string>()
  data.edges.forEach((e) => {
    connected.add(e.source)
    connected.add(e.target)
  })
  const keepNodes = data.nodes.filter((n) => {
    if (focusId && n.person_id === focusId) return true // 聚焦人物恒保留
    if (!connected.has(n.person_id)) return false // 孤立散点（无边）
    return !NOISE_NAMES.has(n.name || '')
  })
  if (!keepNodes.length) return data // 极端：全部被过滤 → 回退全量，避免空画布
  const ids = new Set(keepNodes.map((n) => n.person_id))
  return {
    nodes: keepNodes,
    edges: data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
  }
}

// ============ G6 节点（古典书签卡片） ============
G6.registerNode(
  'v-node',
  {
    draw(cfg: any, group: any) {
      const color = cfg.gender === 'male' ? '#2f6db3' : cfg.gender === 'female' ? '#c25a70' : '#8c8c8c'
      const w = 132
      const h = 56
      const key = group.addShape('rect', {
        attrs: {
          x: -w / 2,
          y: -h / 2,
          width: w,
          height: h,
          radius: 10,
          fill: cfg.hl ? '#fff7e6' : '#fdfbf5',
          stroke: cfg.hl ? '#d48806' : color,
          lineWidth: cfg.hl ? 3.5 : 2,
          shadowColor: cfg.hl ? 'rgba(212,136,6,0.5)' : 'rgba(74,55,40,0.16)',
          shadowBlur: cfg.hl ? 14 : 8,
          shadowOffsetY: 2.5,
          cursor: 'pointer',
        },
        name: 'key',
      })
      // 左侧性别色条
      group.addShape('rect', {
        attrs: {
          x: -w / 2,
          y: -h / 2,
          width: 5,
          height: h,
          radius: [10, 0, 0, 10],
          fill: cfg.hl ? '#d48806' : color,
        },
        name: 'stripe',
      })
      // 底部房支彩条（有人归属房支时显示，同房支同色）
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
          name: 'bstrip',
        })
      }
      group.addShape('text', {
        attrs: {
          x: 3,
          y: -7,
          text: cfg.name || '?',
          textAlign: 'center',
          fontSize: 15,
          fontWeight: 600,
          fill: '#4a3728',
          fontFamily: "'PingFang SC','Microsoft YaHei',sans-serif",
        },
      })
      group.addShape('text', {
        attrs: {
          x: 3,
          y: 13,
          text: `${cfg.birth_year ?? '?'} — ${cfg.death_year ?? '?'}`,
          textAlign: 'center',
          fontSize: 11,
          fill: '#8a7a66',
          fontFamily: "'PingFang SC','Microsoft YaHei',sans-serif",
        },
      })
      if (cfg.generation) {
        group.addShape('text', {
          attrs: {
            x: w / 2 - 10,
            y: -h / 2 + 4,
            text: `${cfg.generation}代`,
            textAlign: 'right',
            fontSize: 10,
            fill: '#c9a45c',
          },
        })
      }
      return key
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

const nodeMap = computed(() => {
  const m: Record<string, Person> = {}
  nodes.value.forEach((n) => (m[n.person_id] = n))
  return m
})

const renderGraph = (data: TreeData, focusId?: string) => {
  if (!containerRef.value) return
  data = visibleData(data, focusId) // 默认过滤孤立散点/序言噪声（可开关）
  lastShownData.value = data
  const width = containerRef.value.clientWidth || 900
  const height = containerRef.value.clientHeight || 640
  if (graph) {
    graph.destroy()
    graph = null
  }
  nodes.value = data.nodes
  edges.value = data.edges

  graph = new G6.Graph({
    container: containerRef.value,
    width,
    height,
    fitView: true,
    fitViewPadding: [46, 46, 46, 46],
    modes: { default: ['drag-canvas', 'zoom-canvas'] },
    layout: { type: 'dagre', rankdir: 'TB', align: 'UL', nodesep: 32, ranksep: 76 },
    defaultNode: { type: 'v-node' },
    defaultEdge: {
      type: 'cubic-vertical',
      style: { stroke: '#b8a48f', lineWidth: 1.8, endArrow: { path: G6.Arrow.triangle(6, 8, 0), d: 0 } },
    },
  })

  graph.data({
    nodes: data.nodes.map((n) => ({
      id: n.person_id,
      ...n,
      type: 'v-node',
      size: [132, 56],
      hl: n.person_id === focusId,
    })),
    edges: data.edges.map((e) => {
      const isSpouse = e.type === 'SPOUSE_OF'
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'cubic-vertical',
        style: {
          stroke: isSpouse ? '#c25a70' : '#8a6d4b',
          lineWidth: isSpouse ? 1.6 : 2,
          lineDash: isSpouse ? [5, 4] : undefined,
          endArrow: isSpouse ? false : { path: G6.Arrow.triangle(6, 8, 0), d: 0 },
        },
      }
    }),
  })
  graph.render()

  graph.on('node:click', (evt: any) => {
    const id = evt.item.getModel().person_id
    clearNodeHl()
    setNodeHl(evt.item, true)
    openPerson(id)
  })
  graph.on('canvas:click', () => clearNodeHl())
  graph.on('node:mouseenter', (evt: any) => {
    const k = evt.item?.getContainer?.().find((e: any) => e.get('name') === 'key')
    if (k && k.attr().lineWidth <= 2) k.attr({ shadowBlur: 14, lineWidth: 2.6 })
  })
  graph.on('node:mouseleave', (evt: any) => {
    const k = evt.item?.getContainer?.().find((e: any) => e.get('name') === 'key')
    if (k && k.attr().lineWidth <= 2.6) k.attr({ shadowBlur: 8, lineWidth: 2 })
  })

  if (focusId) centerOn(focusId)
  window.dispatchEvent(new Event('resize'))
}

// 显示孤立散点开关：用最后渲染的数据重绘（含最新过滤状态）
const lastShownData = ref<TreeData | null>(null)
watch(showIsolated, () => {
  if (lastShownData.value) renderGraph(lastShownData.value)
})

const loadTree = async () => {
  if (!shareCode.value) {
    errorMsg.value = '分享链接无效'
    loading.value = false
    return
  }
  try {
    info.value = await visitInfoApi(shareCode.value)
    const data = await visitTreeApi(shareCode.value)
    loading.value = false
    await nextTick()
    fullTreeData.value = data
    subtreeMode.value = false
    renderGraph(data)
    if (!data.nodes.length) message.info('谱系数据暂为空')
  } catch (e: any) {
    loading.value = false
    errorMsg.value = e?.response?.data?.detail || '无法访问族谱数据'
  }
}

// 古典书签卡片高亮（边框+色条变金色），与 renderGraph 中 hl 配置一致
const setNodeHl = (item: any, on: boolean) => {
  const model = item.getModel()
  const color = model.gender === 'male' ? '#2f6db3' : model.gender === 'female' ? '#c25a70' : '#8c8c8c'
  const key = item.getContainer().find((e: any) => e.get('name') === 'key')
  const stripe = item.getContainer().find((e: any) => e.get('name') === 'stripe')
  if (key) {
    key.attr({
      fill: on ? '#fff7e6' : '#fdfbf5',
      stroke: on ? '#d48806' : color,
      lineWidth: on ? 3.5 : 2,
      shadowColor: on ? 'rgba(212,136,6,0.5)' : 'rgba(74,55,40,0.16)',
      shadowBlur: on ? 14 : 8,
    })
  }
  if (stripe) stripe.attr({ fill: on ? '#d48806' : color })
}

const clearNodeHl = () => {
  if (!graph) return
  graph.getNodes().forEach((n: any) => setNodeHl(n, false))
}

const centerOn = (id: string) => {
  if (!graph) return
  const item = graph.findById(id)
  if (!item) {
    message.warning('该人物不在当前视图中')
    return
  }
  clearNodeHl()
  setNodeHl(item, true)
  graph.focusItem(item, true)
  // 比例过大时缩回，便于同时看到该人及相邻亲缘（不无脑放大到看不清上下文）
  if (graph.getZoom() > 0.6) graph.zoomTo(0.6, true)
}

// 整树缓存与"局部聚焦"状态：全谱上千人时，在大画布上平移+缩放定位不明显，
// 与家谱树页一致——点人名时切换到以该人为中心的局部子图，稳定实现"跳到+放大+定位"
const fullTreeData = ref<TreeData | null>(null)
const subtreeMode = ref(false)

const focusPerson = async (pid: string) => {
  const big = (fullTreeData.value?.nodes.length ?? 0) > 700
  if (nodeMap.value[pid] && (!big || subtreeMode.value)) {
    centerOn(pid)
    return
  }
  try {
    const data = await visitSubtreeApi(shareCode.value, pid, 4)
    if (!data.nodes.length) {
      message.warning('未找到该人物')
      return
    }
    subtreeMode.value = true
    renderGraph(data, pid)
  } catch {
    /* 已提示 */
  }
}

const backFullTree = () => {
  if (!fullTreeData.value) return
  subtreeMode.value = false
  renderGraph(fullTreeData.value)
}

const focusSubtree = (pid: string) => focusPerson(pid)

const openPerson = async (pid: string) => {
  try {
    const d = await visitPersonApi(shareCode.value, pid)
    detail.value = d
    drawerOpen.value = true
    // 家族文献与 AI 展示文字并行加载；未开放"谱书内容"权限时静默跳过
    materials.value = null
    aiIntro.value = null
    aiIntroOpen.value = info.value.allow_search
    aiIntroLoading.value = false
    materialsLoading.value = false
    if (!info.value.allow_search) return
    aiIntroLoading.value = true
    materialsLoading.value = true
    visitPersonMaterialsApi(shareCode.value, pid)
      .then((m) => (materials.value = m))
      .catch(() => (materials.value = null))
      .finally(() => (materialsLoading.value = false))
    visitRagPersonIntroApi(shareCode.value, pid)
      .then((r) => (aiIntro.value = r))
      .catch(() => (aiIntro.value = null))
      .finally(() => (aiIntroLoading.value = false))
  } catch {
    /* 已提示 */
  }
}

const jumpPerson = (p: Person) => {
  drawerOpen.value = false
  focusPerson(p.person_id)
}

// ============ 图谱问答 ============
const scrollQa = () => {
  nextTick(() => {
    if (qaBodyRef.value) qaBodyRef.value.scrollTop = qaBodyRef.value.scrollHeight
  })
}

const sendQa = async () => {
  const q = qaInput.value.trim()
  if (!q || qaLoading.value) return
  messages.value.push({ role: 'user', content: q })
  qaInput.value = ''
  qaLoading.value = true
  scrollQa()
  try {
    const res = await visitChatApi(shareCode.value, q)
    messages.value.push({
      role: 'assistant',
      content: res.answer,
      persons: res.persons || [],
      tree: res.tree,
    })
  } catch {
    messages.value.push({ role: 'assistant', content: '抱歉，查询出错了，请稍后再试。' })
  } finally {
    qaLoading.value = false
    scrollQa()
  }
}

const showChatGraph = (m: { tree?: TreeData | null }) => {
  if (!m.tree) return
  renderGraph(m.tree, m.tree.nodes[0]?.person_id)
  message.success('已在图谱中定位相关人物')
}

const backHome = () => router.push('/login')

const onResize = () => {
  if (graph && containerRef.value) {
    graph.changeSize(containerRef.value.clientWidth, containerRef.value.clientHeight)
  }
}

onMounted(() => {
  void enterView()
  window.addEventListener('resize', onResize)
})

// SPA 内切换不同分享短链（/A1B2C3 → /D4E5F6）时重新加载
watch(
  () => route.params.code,
  () => {
    loading.value = true
    errorMsg.value = ''
    void enterView()
  },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (graph) {
    graph.destroy()
    graph = null
  }
})
</script>

<style scoped>
/* ============ 页面整体：宣纸底 ============ */
.visit-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background-color: #f2e9d3;
  background-image:
    radial-gradient(1400px 640px at 50% -12%, rgba(255, 254, 246, 0.95), rgba(255, 254, 246, 0) 62%),
    radial-gradient(900px 520px at 6% 108%, rgba(168, 121, 58, 0.1), transparent 62%),
    radial-gradient(900px 520px at 96% 105%, rgba(47, 109, 179, 0.06), transparent 58%);
  font-family: 'PingFang SC', 'Microsoft YaHei', 'Songti SC', 'Noto Serif SC', serif;
  color: #4a3728;
}

/* ============ 顶栏：深蓝描金 ============ */
.v-header {
  position: relative;
  z-index: 12;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 12px 30px;
  background: linear-gradient(180deg, #24507a 0%, #1a3a5c 55%, #153050 100%);
  box-shadow:
    0 3px 18px rgba(21, 48, 80, 0.45),
    inset 0 1px 0 rgba(255, 255, 255, 0.14);
}
.v-header::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  background: linear-gradient(90deg, transparent, #c9a45c 20%, #ecd69f 50%, #c9a45c 80%, transparent);
}
.v-brand {
  display: flex;
  align-items: center;
  gap: 13px;
  color: #fff;
  min-width: 0;
}
.v-logo {
  flex: none;
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  border: 1.5px solid rgba(230, 208, 154, 0.85);
  box-shadow: inset 0 0 10px rgba(230, 208, 154, 0.25);
}
.v-name {
  font-size: 21px;
  font-weight: 700;
  letter-spacing: 3px;
  color: #f3e6c4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.v-sub {
  margin-top: 2px;
  font-size: 11.5px;
  color: #a7c0dc;
  letter-spacing: 2px;
}
.v-search {
  position: relative;
  flex: 1;
  display: flex;
  justify-content: center;
}
.search-box {
  position: relative;
  z-index: 1001;
  display: flex;
  gap: 8px;
  width: min(520px, 100%);
}
.search-box :deep(.ant-input-affix-wrapper),
.search-box :deep(.ant-input) {
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.45);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.14);
  background: rgba(255, 255, 255, 0.97);
}
.search-btn {
  border-radius: 20px;
  border: none;
  background: linear-gradient(135deg, #d4af6a, #b98a2f) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
}
/* 联想候选浮层 */
.search-drop {
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  width: 100%;
  max-width: 520px;
  max-height: 320px;
  overflow-y: auto;
  background: #fffdf6;
  border: 1px solid #d8c294;
  border-radius: 12px;
  box-shadow: 0 10px 30px rgba(21, 48, 80, 0.35);
  z-index: 1000;
}
.sd-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 9px 14px;
  cursor: pointer;
  border-bottom: 1px dashed #efe4cc;
  transition: background 0.15s;
}
.sd-item:hover {
  background: #f4ead2;
}
.sd-item:last-child {
  border-bottom: none;
}
.sd-name {
  font-weight: 600;
  color: #4a3728;
}
.sd-year {
  color: #8a7a66;
  font-size: 12px;
  white-space: nowrap;
}
.sd-tip,
.sd-empty {
  padding: 14px;
  text-align: center;
  color: #a89a86;
}
.v-right {
  display: flex;
  gap: 8px;
  flex: none;
}
.v-chip {
  font-size: 12px;
  color: #eee2c8;
  border: 1px solid rgba(230, 208, 154, 0.5);
  border-radius: 20px;
  padding: 3px 13px;
  background: rgba(255, 255, 255, 0.09);
  letter-spacing: 1px;
}

/* ============ 主体画布 ============ */
.v-main {
  flex: 1;
  position: relative;
  min-height: 0;
  padding: 26px 30px 30px;
}
/* 局部聚焦时的"返回全景" */
.v-back-full {
  position: absolute;
  top: 10px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  padding: 6px 20px;
  border: 1px solid #d8c294;
  border-radius: 20px;
  background: rgba(255, 253, 246, 0.96);
  color: #6b5233;
  font-size: 13px;
  line-height: 1.4;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(74, 55, 40, 0.2);
}
.v-back-full:hover {
  background: #f4ead2;
}
.v-frame {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 520px;
  border: 1px solid #d8c294;
  border-radius: 18px;
  background: linear-gradient(165deg, #fdfaf0, #f5ecd7 70%, #f0e5cb);
  box-shadow:
    0 6px 30px rgba(122, 96, 52, 0.18),
    inset 0 0 0 4px rgba(255, 253, 246, 0.65),
    inset 0 0 40px rgba(168, 121, 58, 0.06);
  overflow: hidden;
}
/* 纸面网格（衬托谱线） */
.v-frame::before {
  content: '';
  position: absolute;
  inset: 13px;
  border-radius: 12px;
  background-image:
    linear-gradient(rgba(138, 109, 75, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(138, 109, 75, 0.05) 1px, transparent 1px);
  background-size: 26px 26px;
  pointer-events: none;
}
.v-canvas {
  position: absolute;
  inset: 13px;
}
/* 金色角饰 */
.v-corner {
  position: absolute;
  width: 34px;
  height: 34px;
  pointer-events: none;
  z-index: 2;
}
.v-corner.tl { top: 5px; left: 5px; border-top: 2px solid #b08d4f; border-left: 2px solid #b08d4f; border-top-left-radius: 12px; }
.v-corner.tr { top: 5px; right: 5px; border-top: 2px solid #b08d4f; border-right: 2px solid #b08d4f; border-top-right-radius: 12px; }
.v-corner.bl { bottom: 5px; left: 5px; border-bottom: 2px solid #b08d4f; border-left: 2px solid #b08d4f; border-bottom-left-radius: 12px; }
.v-corner.br { bottom: 5px; right: 5px; border-bottom: 2px solid #b08d4f; border-right: 2px solid #b08d4f; border-bottom-right-radius: 12px; }

/* 右上角朱砂谱系印 */
.v-seal {
  position: absolute;
  top: 34px;
  right: 44px;
  z-index: 2;
  width: 56px;
  height: 56px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  line-height: 1.05;
  font-family: 'Songti SC', 'STKaiti', 'KaiTi', serif;
  font-weight: 700;
  font-size: 21px;
  letter-spacing: 1px;
  color: #f6e6c6;
  background: linear-gradient(150deg, #c0432f, #a12f23);
  border: 2px solid #7d2218;
  border-radius: 8px;
  box-shadow:
    inset 0 0 0 2px rgba(246, 230, 198, 0.35),
    0 3px 10px rgba(122, 34, 24, 0.4);
  transform: rotate(-10deg);
  opacity: 0.92;
  pointer-events: none;
  user-select: none;
}

/* 图例 */
.v-legend {
  position: absolute;
  left: 48px;
  bottom: 44px;
  z-index: 5;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 9px 18px;
  background: rgba(253, 250, 241, 0.94);
  border: 1px solid #ddcb9f;
  border-radius: 26px;
  font-size: 12px;
  color: #6b5d4a;
  box-shadow: 0 3px 14px rgba(122, 96, 52, 0.14);
}
.lg {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.lg-line {
  display: inline-block;
  width: 22px;
  height: 3px;
  background: #8a6d4b;
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
.lg-male { background: #2f6db3; }
.lg-female { background: #c25a70; }
.lg-unknown { background: #8c8c8c; }

/* 加载 / 错误 */
.v-loading {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: center;
  justify-content: center;
  color: #8a7a66;
  background: rgba(242, 233, 211, 0.72);
  backdrop-filter: blur(1px);
}
.v-loading-text {
  letter-spacing: 3px;
  font-size: 14px;
}
.v-error {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(242, 233, 211, 0.9);
}

/* ============ 详情抽屉（仿古米白） ============ */
.v-drawer :deep(.ant-drawer-content) {
  background: #fdf9ee;
}
.v-drawer :deep(.ant-drawer-header) {
  background: linear-gradient(180deg, #fdfaf1, #f4ead2);
  border-bottom: 1px solid #e4d5ac;
}
.v-drawer :deep(.ant-drawer-title) {
  color: #4a3728;
  font-weight: 700;
  letter-spacing: 2px;
  font-family: 'Songti SC', 'Noto Serif SC', serif;
}
.d-head {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-bottom: 18px;
}
.d-name {
  font-size: 23px;
  font-weight: 700;
  color: #4a3728;
  letter-spacing: 1px;
  font-family: 'Songti SC', 'Noto Serif SC', serif;
}
.d-tags {
  margin-top: 4px;
}
.d-desc {
  background: #fffdf6;
}
.d-desc :deep(.ant-descriptions-item-label) {
  background: #f7efdc !important;
  color: #6b5d4a;
  font-weight: 600;
  width: 90px;
}
.d-sec {
  margin-top: 18px;
}
.d-sec-title {
  font-size: 14px;
  font-weight: 600;
  color: #4a3728;
  margin-bottom: 8px;
  letter-spacing: 1px;
}
.d-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.d-chip {
  cursor: pointer;
  border-radius: 12px;
  padding: 2px 10px;
  transition: all 0.2s;
}
.d-chip:hover {
  transform: scale(1.05);
}
.d-btn {
  margin-top: 20px;
}

/* ============ 问答浮窗 ============ */
.qa-wrap {
  position: fixed;
  right: 26px;
  bottom: 26px;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 12px;
}
.qa-fab {
  width: 58px;
  height: 58px;
  border-radius: 50%;
  border: 2px solid rgba(246, 230, 198, 0.7);
  font-size: 26px;
  color: #fff;
  background: linear-gradient(135deg, #c9a45c, #a8793a);
  box-shadow: 0 6px 22px rgba(168, 121, 58, 0.5);
  cursor: pointer;
  transition: transform 0.2s;
}
.qa-fab:hover {
  transform: scale(1.08);
}
.qa-panel {
  width: 400px;
  height: 520px;
  display: flex;
  flex-direction: column;
  background: #fffdf8;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 12px 44px rgba(31, 58, 95, 0.3);
  border: 1px solid #d8c294;
}
.qa-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 16px;
  background: linear-gradient(120deg, #1f3a5f, #2c4e78);
  color: #f3e6c4;
  font-weight: 600;
  letter-spacing: 1px;
}
.qa-head :deep(.ant-btn) {
  color: #f3e6c4;
}
.qa-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.qa-msg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.qa-msg.user {
  align-items: flex-end;
}
.qa-bubble {
  max-width: 82%;
  padding: 9px 13px;
  border-radius: 12px;
  font-size: 13.5px;
  line-height: 1.6;
  word-break: break-word;
  background: #f2ead9;
  color: #4a3728;
  border: 1px solid #e3d6bd;
}
.qa-msg.user .qa-bubble {
  background: linear-gradient(135deg, #2c4e78, #1f3a5f);
  color: #fff;
  border: none;
}
.qa-thinking {
  color: #8a7a66;
  font-style: italic;
}
.qa-persons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
  max-width: 90%;
}
.qa-chip {
  cursor: pointer;
  border-radius: 10px;
  transition: all 0.2s;
}
.qa-chip:hover {
  transform: scale(1.06);
}
.qa-more {
  font-size: 12px;
  color: #8a7a66;
  align-self: center;
}
.qa-see {
  margin-top: 8px;
}
.qa-input {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid #eee3cd;
  background: #fffdf6;
}
.qa-tip {
  padding: 6px 14px;
  font-size: 11.5px;
  color: #a89a86;
  background: #faf6ec;
  border-top: 1px dashed #e8dcc2;
}

/* ============ 窄屏适配 ============ */
@media (max-width: 960px) {
  .v-header {
    flex-wrap: wrap;
    padding: 12px 16px;
    gap: 10px;
  }
  .v-search {
    order: 3;
    flex: none;
    width: 100%;
    justify-content: flex-start;
  }
  .search-box {
    width: 100%;
  }
  .v-name {
    font-size: 18px;
    letter-spacing: 1px;
  }
  .v-logo {
    width: 44px;
    height: 44px;
    font-size: 22px;
  }
  .v-main {
    padding: 14px 14px 18px;
  }
  .v-legend {
    left: 26px;
    bottom: 30px;
    gap: 10px;
    font-size: 11px;
    padding: 7px 14px;
    max-width: calc(100% - 52px);
  }
  .v-seal {
    width: 44px;
    height: 44px;
    font-size: 16px;
    top: 22px;
    right: 22px;
  }
  .qa-wrap {
    right: 14px;
    bottom: 14px;
  }
  .qa-panel {
    width: calc(100vw - 32px);
    max-width: 400px;
    height: 60vh;
  }
}

/* ============ 家族文献（人物详情抽屉） ============ */
.d-mat-empty {
  color: #9a8a6a;
  font-size: 12px;
  line-height: 1.7;
  padding: 2px;
}
.d-mat-sec-title {
  font-size: 13px;
  font-weight: 700;
  color: #6b4a1d;
  margin: 6px 0;
}
.d-mat-item {
  border: 1px solid #e6d6b8;
  background: #fdf8ee;
  border-radius: 6px;
  padding: 7px 9px;
  margin-bottom: 7px;
}
.d-mat-item-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}
.d-mat-item-title {
  font-weight: 600;
  font-size: 12px;
  flex: 1;
  min-width: 0;
}
.d-mat-item-page {
  color: #b7791f;
  font-size: 11px;
  white-space: nowrap;
}
.d-mat-item-text {
  font-size: 12px;
  line-height: 1.7;
  color: #4a4030;
  max-height: 84px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ============ 家谱内容检索（AI）新增样式 ============ */
/* 搜索行内"家谱内容"按钮：金框透明底 */
.search-btn.ghost {
  background: rgba(255, 255, 255, 0.12) !important;
  border: 1px solid rgba(230, 208, 154, 0.75) !important;
  color: #f3e6c4 !important;
  box-shadow: none;
}
.search-btn.ghost:hover {
  background: rgba(255, 255, 255, 0.22) !important;
}
/* 下拉尾部"在谱书原文中检索"入口 */
.sd-rag {
  padding: 10px 14px;
  border-top: 1px dashed #d8c294;
  background: #fbf3de;
  color: #a4630f;
  font-size: 12.5px;
  cursor: pointer;
  text-align: center;
  transition: background 0.15s;
}
.sd-rag:hover {
  background: #f6e7c3;
}
/* 图例房支示意 */
.lg-branch {
  display: inline-block;
  width: 18px;
  height: 4px;
  background: linear-gradient(90deg, #b7791f, #4a7d5f, #a05c7f);
  border-radius: 2px;
}
.lg-check {
  color: #6b4a1d;
  cursor: default;
}

/* AI 展示文字卡（人物抽屉 / 检索抽屉共用） */
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

/* 检索抽屉 */
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
.rag-hit-meta {
  display: flex;
  gap: 10px;
  margin: 2px 0 4px;
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
  color: #4a4030;
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
